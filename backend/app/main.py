from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import arxiv, codefinder, config, figures, fulltext, llm, localsearch, related, review_gen, storage

log = logging.getLogger("paperlab")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    # 서버가 백그라운드 작업 도중 종료되면 상태가 고착되므로 시작 시 정리
    for m in storage.list_papers():
        stale = {k: "failed" for k, v in (("pdf_status", "pending"), ("fulltext_status", "pending"), ("code_status", "searching"), ("figures_status", "extracting"), ("related_status", "fetching")) if m.get(k) == v}
        if stale:
            storage.update_meta(m["arxiv_id"], **stale)
    yield


app = FastAPI(title="Paper Lab", lifespan=lifespan)


def _valid_id(arxiv_id: str) -> str:
    # 경로 탈출 방지: 반드시 arXiv ID 형식이어야 한다
    if arxiv.parse_arxiv_id(arxiv_id) is None:
        raise HTTPException(400, "invalid arXiv id")
    return arxiv.base_id(arxiv_id)


def _require(arxiv_id: str) -> str:
    aid = _valid_id(arxiv_id)
    if not storage.exists(aid):
        raise HTTPException(404, "paper not found")
    return aid


@app.get("/api/search")
async def search(q: str = Query(min_length=1), start: int = 0, max_results: int = Query(20, le=50)):
    """키워드 검색. q가 arXiv ID/URL이면 단건 조회로 처리."""
    try:
        aid = arxiv.parse_arxiv_id(q)
        if aid:
            item = await arxiv.fetch_metadata(aid)
            result = {"total": 1, "start": 0, "items": [item]}
        else:
            result = await arxiv.search(q, start, max_results)
    except arxiv.ArxivError as e:
        raise HTTPException(502, str(e))
    for it in result["items"]:
        it["in_library"] = storage.exists(it["arxiv_id"])
    return result


class AddPaper(BaseModel):
    input: str = Field(min_length=1, description="arXiv ID 또는 URL")


async def _fetch_pdf(arxiv_id: str) -> None:
    try:
        await arxiv.download_pdf(arxiv_id, storage.paper_dir(arxiv_id) / "paper.pdf")
        storage.update_meta(arxiv_id, pdf_status="ready")
    except Exception as e:  # noqa: BLE001
        log.warning("pdf download failed for %s: %s", arxiv_id, e)
        if storage.exists(arxiv_id):
            storage.update_meta(arxiv_id, pdf_status="failed")
        return
    await _extract(arxiv_id)
    await _extract_figures(arxiv_id)


async def _extract_figures(arxiv_id: str) -> None:
    """캡션 앵커 방식 그림/표 추출. 사용자가 수정/수동 추가/숨김 처리한 항목은 재추출해도 보존한다."""
    storage.update_meta(arxiv_id, figures_status="extracting")
    try:
        d = storage.paper_dir(arxiv_id) / "figures"
        tmp = d / ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        res = await asyncio.to_thread(figures.extract, storage.paper_dir(arxiv_id) / "paper.pdf", tmp)
        old = _load_figs(arxiv_id)
        keep = [i for i in old.get("items", []) if i.get("source") == "manual" or i.get("edited") or i.get("hidden")]
        keep_ids = {i["id"] for i in keep}
        merged = keep + [i for i in res["items"] if i["id"] not in keep_ids]
        for i in res["items"]:
            if i["file"] and i["id"] not in keep_ids:
                d.mkdir(parents=True, exist_ok=True)
                (tmp / i["file"]).replace(d / i["file"])
        shutil.rmtree(tmp, ignore_errors=True)
        res["items"] = sorted(merged, key=lambda i: (i["page"], i["kind"] != "figure", i["id"]))
        _save_figs(arxiv_id, res)
        storage.update_meta(arxiv_id, figures_status="ready", figure_count=sum(1 for i in res["items"] if i["bbox"] and not i.get("hidden")))
    except Exception as e:  # noqa: BLE001
        log.warning("figure extraction failed for %s: %s", arxiv_id, e)
        if storage.exists(arxiv_id):
            storage.update_meta(arxiv_id, figures_status="failed")


def _load_figs(arxiv_id: str) -> dict:
    t = storage.read_text(arxiv_id, "figures/figures.json")
    return json.loads(t) if t else {}


def _save_figs(arxiv_id: str, data: dict) -> None:
    storage.write_text(arxiv_id, "figures/figures.json", json.dumps(data, ensure_ascii=False, indent=1))


async def _extract(arxiv_id: str) -> None:
    try:
        text, pages = await asyncio.to_thread(fulltext.extract, storage.paper_dir(arxiv_id) / "paper.pdf")
        body = re.sub(r"</?page[^>]*>", "", text)
        if len(body.strip()) < 30 * pages:  # 스캔본 등 텍스트가 거의 없는 경우
            raise ValueError("추출된 텍스트가 너무 적습니다 (스캔 PDF?)")
        storage.write_text(arxiv_id, "fulltext.md", text)
        storage.update_meta(arxiv_id, fulltext_status="ready", pages=pages)
    except Exception as e:  # noqa: BLE001
        log.warning("fulltext extraction failed for %s: %s", arxiv_id, e)
        if storage.exists(arxiv_id):
            storage.update_meta(arxiv_id, fulltext_status="failed")


async def _find_code(arxiv_id: str) -> dict:
    storage.update_meta(arxiv_id, code_status="searching")
    try:
        data = await codefinder.find(arxiv_id)
    except Exception as e:  # noqa: BLE001
        log.warning("code search failed for %s: %s", arxiv_id, e)
        storage.update_meta(arxiv_id, code_status="failed")
        raise
    codefinder.save(arxiv_id, data)
    _sync_code_meta(arxiv_id, data)
    return data


def _sync_code_meta(arxiv_id: str, data: dict) -> None:
    """목록 화면용 요약을 meta 에 반영."""
    cands = data.get("candidates", [])
    best = next((c for c in cands if c["classification"] != "weak"), None)
    storage.update_meta(
        arxiv_id,
        code_status="done",
        code={"selected": data.get("selected"), "no_official": data.get("no_official", False),
              "best": best["full_name"] if best else None, "best_class": best["classification"] if best else None},
    )


async def _find_related(arxiv_id: str) -> dict:
    storage.update_meta(arxiv_id, related_status="fetching")
    try:
        data = await related.build(arxiv_id)
    except Exception as e:  # noqa: BLE001
        log.warning("related fetch failed for %s: %s", arxiv_id, e)
        storage.update_meta(arxiv_id, related_status="failed")
        raise
    related.save(arxiv_id, data)
    ok = bool(data["references"] or data["citing"].get("top") or data["similar"])
    storage.update_meta(arxiv_id, related_status="done" if ok else "failed", related_count=len(data["references"]))
    return data


async def _find_related_bg(arxiv_id: str) -> None:
    try:
        await _find_related(arxiv_id)
    except Exception:  # noqa: BLE001
        pass


async def _find_code_bg(arxiv_id: str) -> None:
    try:
        await _find_code(arxiv_id)
    except Exception:  # noqa: BLE001  (이미 로그/상태 기록됨)
        pass


@app.post("/api/papers", status_code=201)
async def add_paper(body: AddPaper, bg: BackgroundTasks):
    aid = arxiv.parse_arxiv_id(body.input)
    if aid is None:
        raise HTTPException(400, "arXiv ID 또는 URL을 입력하세요 (키워드는 검색 탭 사용)")
    if storage.exists(aid):
        raise HTTPException(409, "이미 라이브러리에 있습니다")
    try:
        meta = await arxiv.fetch_metadata(aid)
    except arxiv.ArxivError as e:
        raise HTTPException(502, str(e))
    meta = storage.create(meta)
    meta = storage.update_meta(meta["arxiv_id"], code_status="searching", figures_status="extracting", related_status="fetching")  # 화면이 완료까지 폴링하도록
    bg.add_task(_fetch_pdf, meta["arxiv_id"])
    bg.add_task(_find_code_bg, meta["arxiv_id"])  # PDF/본문 처리 뒤에 순차 실행
    bg.add_task(_find_related_bg, meta["arxiv_id"])
    return meta


# ---------- LLM 프로필 (Settings) ----------
class ProfileBody(BaseModel):
    provider: str = "anthropic"
    model: str = Field(min_length=1)
    api_key: str | None = None  # 비우면 기존 키 유지
    base_url: str | None = None
    region: str | None = None
    project_id: str | None = None
    ca_bundle: str | None = None


@app.get("/api/settings/llm")
def get_llm_settings():
    return llm.list_profiles()


@app.put("/api/settings/llm/profiles/{name}")
def put_profile(name: str, body: ProfileBody):
    if not name.strip() or len(name) > 60:
        raise HTTPException(400, "invalid profile name")
    try:
        llm.upsert_profile(name, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return llm.list_profiles()


@app.delete("/api/settings/llm/profiles/{name}")
def del_profile(name: str):
    llm.delete_profile(name)
    return llm.list_profiles()


class ActiveBody(BaseModel):
    name: str


@app.post("/api/settings/llm/active")
def set_active(body: ActiveBody):
    try:
        llm.set_active(body.name)
    except KeyError:
        raise HTTPException(404, "profile not found")
    return llm.list_profiles()


@app.post("/api/settings/llm/test")
async def test_llm():
    try:
        out = await asyncio.to_thread(llm.complete, "Reply with exactly: ok", "ping", 20)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "reply": out[:100], "profile": llm.active_profile()["name"]}


@app.get("/api/local-search")
def local_search(q: str = Query(min_length=1)):
    return localsearch.search(q)


@app.get("/api/papers")
def list_papers():
    return storage.list_papers()


@app.get("/api/papers/{arxiv_id:path}/pdf")
def get_pdf(arxiv_id: str):
    aid = _require(arxiv_id)
    p = storage.paper_dir(aid) / "paper.pdf"
    if not p.exists():
        raise HTTPException(404, "pdf not available")
    return FileResponse(p, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.post("/api/papers/{arxiv_id:path}/retry-pdf")
async def retry_pdf(arxiv_id: str, bg: BackgroundTasks):
    aid = _require(arxiv_id)
    storage.update_meta(aid, pdf_status="pending")
    bg.add_task(_fetch_pdf, aid)
    return {"ok": True}


# ---------- 그림 / 표 ----------
_FID = re.compile(r"^(figure|table)-[A-Za-z0-9]+$")


def _fid(fid: str) -> str:
    if not _FID.match(fid):
        raise HTTPException(400, "invalid figure id")
    return fid


def _find_fig(data: dict, fid: str) -> dict:
    it = next((i for i in data.get("items", []) if i["id"] == _fid(fid)), None)
    if it is None:
        raise HTTPException(404, "figure not found")
    return it


def _check_bbox(bbox: list[float], data: dict, page: int) -> list[float]:
    if not 1 <= page <= len(data.get("page_sizes", [])):
        raise HTTPException(400, "invalid page")
    w, h = data["page_sizes"][page - 1]
    x0, y0, x1, y1 = bbox
    x0, x1, y0, y1 = max(0, min(x0, x1)), min(w, max(x0, x1)), max(0, min(y0, y1)), min(h, max(y0, y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise HTTPException(400, "영역이 너무 작습니다")
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]


def _render_item(aid: str, it: dict) -> None:
    import pymupdf

    with pymupdf.open(storage.paper_dir(aid) / "paper.pdf") as doc:
        d = storage.paper_dir(aid) / "figures"
        d.mkdir(exist_ok=True)
        it["file"] = f"{it['id']}.png"
        figures.render_clip(doc, it["page"], pymupdf.Rect(*it["bbox"]), d / it["file"])


@app.get("/api/papers/{arxiv_id:path}/figures/{fid}.png")
def figure_png(arxiv_id: str, fid: str):
    aid = _require(arxiv_id)
    it = _find_fig(_load_figs(aid), fid)
    p = storage.paper_dir(aid) / "figures" / (it.get("file") or "")
    if not it.get("file") or not p.exists():
        raise HTTPException(404, "no image for this figure")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-cache"})


@app.get("/api/papers/{arxiv_id:path}/pages/{n:int}.png")
def page_png(arxiv_id: str, n: int, dpi: int = Query(100, ge=50, le=200)):
    aid = _require(arxiv_id)
    pdf = storage.paper_dir(aid) / "paper.pdf"
    if not pdf.exists():
        raise HTTPException(404, "pdf not available")
    try:
        data = figures.render_page(pdf, n, dpi)
    except IndexError:
        raise HTTPException(404, "page out of range")
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/papers/{arxiv_id:path}/figures/extract")
async def reextract_figures(arxiv_id: str):
    aid = _require(arxiv_id)
    if not (storage.paper_dir(aid) / "paper.pdf").exists():
        raise HTTPException(409, "pdf not available")
    await _extract_figures(aid)
    return {**_load_figs(aid), "status": storage.get_meta(aid).get("figures_status")}


class FigPatch(BaseModel):
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    label: str | None = Field(None, max_length=60)
    caption: str | None = Field(None, max_length=4000)
    hidden: bool | None = None


@app.put("/api/papers/{arxiv_id:path}/figures/{fid}")
def patch_figure(arxiv_id: str, fid: str, body: FigPatch):
    aid = _require(arxiv_id)
    data = _load_figs(aid)
    it = _find_fig(data, fid)
    f = body.model_dump(exclude_unset=True)
    if f.get("bbox") is not None:
        it["bbox"] = _check_bbox(f["bbox"], data, it["page"])
        it.update(edited=True, auto=False, side=None)
        _render_item(aid, it)
    for k in ("label", "caption", "hidden"):
        if k in f and f[k] is not None:
            it[k] = f[k]
            it["edited"] = True
    _save_figs(aid, data)
    storage.update_meta(aid, figure_count=sum(1 for i in data["items"] if i["bbox"] and not i.get("hidden")))
    return it


class FigNew(BaseModel):
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    kind: str = Field("figure", pattern="^(figure|table)$")
    label: str | None = Field(None, max_length=60)
    caption: str = Field("", max_length=4000)


@app.post("/api/papers/{arxiv_id:path}/figures", status_code=201)
def add_figure(arxiv_id: str, body: FigNew):
    aid = _require(arxiv_id)
    if not (storage.paper_dir(aid) / "paper.pdf").exists():
        raise HTTPException(409, "pdf not available")
    data = _load_figs(aid) or {"page_sizes": [], "items": [], "stats": {}}
    if not data["page_sizes"]:
        import pymupdf

        with pymupdf.open(storage.paper_dir(aid) / "paper.pdf") as doc:
            data["page_sizes"] = [[round(p.rect.width, 1), round(p.rect.height, 1)] for p in doc]
    bbox = _check_bbox(body.bbox, data, body.page)
    n = 1 + max([int(m.group(1)) for i in data["items"] if (m := re.match(rf"^{body.kind}-m(\d+)$", i["id"]))] + [0])
    it = {
        "id": f"{body.kind}-m{n}", "label": body.label or f"{'Figure' if body.kind == 'figure' else 'Table'} (수동 {n})",
        "kind": body.kind, "page": body.page, "caption": body.caption, "caption_bbox": None, "bbox": bbox, "side": None,
        "source": "manual", "auto": False, "edited": True, "hidden": False, "file": None, "mentions": [],
    }
    _render_item(aid, it)
    data["items"].append(it)
    data["items"].sort(key=lambda i: (i["page"], i["kind"] != "figure", i["id"]))
    _save_figs(aid, data)
    storage.update_meta(aid, figure_count=sum(1 for i in data["items"] if i["bbox"] and not i.get("hidden")))
    return it


@app.delete("/api/papers/{arxiv_id:path}/figures/{fid}", status_code=204)
def delete_figure(arxiv_id: str, fid: str):
    """수동 항목은 삭제, 자동 추출 항목은 숨김 처리(재추출해도 다시 나타나지 않음)."""
    aid = _require(arxiv_id)
    data = _load_figs(aid)
    it = _find_fig(data, fid)
    if it["source"] == "manual":
        data["items"] = [i for i in data["items"] if i["id"] != it["id"]]
        if it.get("file"):
            (storage.paper_dir(aid) / "figures" / it["file"]).unlink(missing_ok=True)
    else:
        it["hidden"] = True
    _save_figs(aid, data)
    storage.update_meta(aid, figure_count=sum(1 for i in data["items"] if i["bbox"] and not i.get("hidden")))


@app.get("/api/papers/{arxiv_id:path}/figures")
def list_figures(arxiv_id: str):
    aid = _require(arxiv_id)
    data = _load_figs(aid) or {"page_sizes": [], "items": [], "stats": {}}
    return {**data, "status": storage.get_meta(aid).get("figures_status")}


# ---------- 관련 연구 ----------
def _annotate(data: dict) -> dict:
    """라이브러리에 이미 있는 논문 표시 (조회 시점 기준)."""
    have = {m["arxiv_id"] for m in storage.list_papers()}

    def mark(lst):
        for p in lst or []:
            p["in_library"] = bool(p.get("arxiv_id")) and arxiv.base_id(p["arxiv_id"]) in have

    mark(data.get("references"))
    mark((data.get("citing") or {}).get("top"))
    mark((data.get("citing") or {}).get("recent"))
    mark(data.get("similar"))
    return data


@app.get("/api/papers/{arxiv_id:path}/related")
def get_related(arxiv_id: str):
    aid = _require(arxiv_id)
    data = related.load(aid) or {"references": [], "citing": {"top": [], "recent": [], "total": None}, "similar": [], "errors": [], "fetched_at": None, "guide": None}
    return {**_annotate(data), "tiers": related.TIERS, "status": storage.get_meta(aid).get("related_status")}


@app.post("/api/papers/{arxiv_id:path}/related/refresh")
async def refresh_related(arxiv_id: str):
    aid = _require(arxiv_id)
    try:
        data = await _find_related(aid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"관련 연구 조회 실패: {e}")
    return {**_annotate(data), "tiers": related.TIERS, "status": storage.get_meta(aid).get("related_status")}


@app.post("/api/papers/{arxiv_id:path}/related/guide")
async def related_guide(arxiv_id: str):
    aid = _require(arxiv_id)
    data = related.load(aid)
    try:
        g = await asyncio.to_thread(related.generate_guide, storage.get_meta(aid), data)
    except (llm.LLMError, related.RelatedError) as e:
        raise HTTPException(502, str(e))
    data["guide"] = g
    related.save(aid, data)
    return g


# ---------- 공개 코드 탐색 ----------
@app.get("/api/papers/{arxiv_id:path}/code")
def get_code(arxiv_id: str):
    aid = _require(arxiv_id)
    return codefinder.load(aid) or {"candidates": [], "hf_models": [], "errors": [], "selected": None, "no_official": False, "searched_at": None}


@app.post("/api/papers/{arxiv_id:path}/code/search")
async def search_code(arxiv_id: str):
    aid = _require(arxiv_id)
    try:
        return await _find_code(aid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"코드 탐색 실패: {e}")


class RepoBody(BaseModel):
    url: str = Field(min_length=3, description="GitHub URL 또는 owner/repo")


@app.post("/api/papers/{arxiv_id:path}/code/add")
async def add_code(arxiv_id: str, body: RepoBody):
    aid = _require(arxiv_id)
    repo = codefinder.parse_repo_url(body.url)
    if not repo:
        raise HTTPException(400, "GitHub URL 또는 owner/repo 형식이 아닙니다")
    try:
        cand = await codefinder.add_manual(aid, repo)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"GitHub 조회 실패: {e}")
    _sync_code_meta(aid, codefinder.load(aid))
    return cand


class SelectBody(BaseModel):
    full_name: str | None = None  # None + no_official=False → 선택 해제
    no_official: bool = False  # 공개 코드 없음 → 직접 구현 경로


@app.put("/api/papers/{arxiv_id:path}/code/select")
def select_code(arxiv_id: str, body: SelectBody):
    aid = _require(arxiv_id)
    data = codefinder.load(aid)
    if body.full_name:
        if body.full_name.lower() not in {c["full_name"].lower() for c in data.get("candidates", [])}:
            raise HTTPException(400, "후보 목록에 없는 repo 입니다 (먼저 '직접 추가' 하세요)")
        if body.no_official:
            raise HTTPException(400, "repo 선택과 '공개 코드 없음'은 함께 지정할 수 없습니다")
    data.update(selected=body.full_name, no_official=body.no_official)
    codefinder.save(aid, data)
    _sync_code_meta(aid, data)
    return data


class GenBody(BaseModel):
    section: int | None = Field(None, ge=1, description="지정하면 해당 번호(## N.) 섹션만 재생성")


@app.post("/api/papers/{arxiv_id:path}/generate-review")
async def generate_review(arxiv_id: str, body: GenBody):
    """LLM 초안 생성. 결과는 저장하지 않고 반환 (프론트가 에디터에 반영→자동저장). 덮어쓰기 전 현재본은 history에 보관."""
    aid = _require(arxiv_id)
    ft = storage.read_text(aid, "fulltext.md")
    if not ft:
        raise HTTPException(409, "본문 텍스트가 아직 없습니다 (PDF 다운로드/추출 필요)")
    meta = storage.get_meta(aid)
    current = storage.read_text(aid, "review.md")
    try:
        if body.section:
            content = await asyncio.to_thread(review_gen.regenerate_section, meta, ft, current, body.section)
        else:
            tpl = storage.REVIEW_TEMPLATE.format(
                title=meta["title"], authors=", ".join(meta["authors"][:6]), arxiv_id=meta["arxiv_id"]
            )
            content = await asyncio.to_thread(review_gen.generate_full, meta, ft, tpl, _load_figs(aid).get("items"))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    storage.snapshot_review(aid)
    meta = storage.update_meta(aid, ai_draft=True)
    return {"content": content, "meta": meta}


@app.post("/api/papers/{arxiv_id:path}/reextract")
async def reextract(arxiv_id: str):
    aid = _require(arxiv_id)
    if not (storage.paper_dir(aid) / "paper.pdf").exists():
        raise HTTPException(409, "pdf not available")
    await _extract(aid)
    return storage.get_meta(aid)


class ReviewBody(BaseModel):
    content: str


@app.get("/api/papers/{arxiv_id:path}/review/history")
def review_history(arxiv_id: str):
    return storage.list_history(_require(arxiv_id))


@app.get("/api/papers/{arxiv_id:path}/review/history/{stamp}")
def review_history_item(arxiv_id: str, stamp: str):
    text = storage.read_history(_require(arxiv_id), stamp)
    if text is None:
        raise HTTPException(404, "version not found")
    return {"content": text}


@app.get("/api/papers/{arxiv_id:path}/review")
def get_review(arxiv_id: str):
    aid = _require(arxiv_id)
    return {"content": storage.read_text(aid, "review.md"), "notes": storage.read_text(aid, "notes.md")}


@app.put("/api/papers/{arxiv_id:path}/review")
def put_review(arxiv_id: str, body: ReviewBody):
    aid = _require(arxiv_id)
    return {"changed": storage.write_text(aid, "review.md", body.content)}


@app.put("/api/papers/{arxiv_id:path}/notes")
def put_notes(arxiv_id: str, body: ReviewBody):
    aid = _require(arxiv_id)
    return {"changed": storage.write_text(aid, "notes.md", body.content)}


class MetaPatch(BaseModel):
    status: str | None = None
    tags: list[str] | None = None
    rating: int | None = Field(None, ge=1, le=5)
    ai_draft: bool | None = None  # False로 바꾸면 "사람이 검증함"


@app.patch("/api/papers/{arxiv_id:path}")
def patch_paper(arxiv_id: str, body: MetaPatch):
    aid = _require(arxiv_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        return storage.update_meta(aid, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/papers/{arxiv_id:path}")
def get_paper(arxiv_id: str):
    return storage.get_meta(_require(arxiv_id))


@app.delete("/api/papers/{arxiv_id:path}", status_code=204)
def delete_paper(arxiv_id: str):
    storage.delete(_require(arxiv_id))


# 빌드된 프론트엔드 서빙 (SPA)
if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = (config.FRONTEND_DIST / full_path).resolve()
        if full_path and f.is_file() and config.FRONTEND_DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
