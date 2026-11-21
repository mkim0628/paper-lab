from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import arxiv, config, storage

log = logging.getLogger("paperlab")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.PAPERS_DIR.mkdir(parents=True, exist_ok=True)
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
    bg.add_task(_fetch_pdf, meta["arxiv_id"])
    return meta


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
