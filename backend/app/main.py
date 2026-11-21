from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import arxiv, codefinder, config, fulltext, llm, localsearch, review_gen, storage

log = logging.getLogger("paperlab")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    # 서버가 백그라운드 작업 도중 종료되면 상태가 고착되므로 시작 시 정리
    for m in storage.list_papers():
        stale = {k: "failed" for k, v in (("pdf_status", "pending"), ("fulltext_status", "pending"), ("code_status", "searching")) if m.get(k) == v}
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
    meta = storage.update_meta(meta["arxiv_id"], code_status="searching")  # 화면이 완료까지 폴링하도록
    bg.add_task(_fetch_pdf, meta["arxiv_id"])
    bg.add_task(_find_code_bg, meta["arxiv_id"])  # PDF/본문 처리 뒤에 순차 실행
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
            content = await asyncio.to_thread(review_gen.generate_full, meta, ft, tpl)
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
