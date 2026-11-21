"""arXiv 검색/조회 클라이언트. ID·URL 파싱, Atom 파싱, PDF 다운로드."""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from . import config

NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}

_NEW_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
_OLD_ID = r"[a-z\-]+(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?"
_ID_RE = re.compile(rf"^(?:{_NEW_ID}|{_OLD_ID})$")
_URL_RE = re.compile(
    rf"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/(?P<id>{_NEW_ID}|{_OLD_ID})(?:\.pdf)?/?(?:[?#].*)?$"
)


class ArxivError(Exception):
    pass


def parse_arxiv_id(text: str) -> str | None:
    """URL / 'arXiv:ID' / 순수 ID 에서 arXiv ID를 뽑는다. 아니면 None (→ 키워드 검색)."""
    t = text.strip()
    if t.lower().startswith("arxiv:"):
        t = t[6:].strip()
    if _ID_RE.match(t):
        return t
    m = _URL_RE.match(t)
    return m.group("id") if m else None


def base_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def dir_name(arxiv_id: str) -> str:
    """파일시스템 안전한 디렉터리 이름 (버전 제외, 구형 ID의 '/' 치환)."""
    return base_id(arxiv_id).replace("/", "_")


def _text(el: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", el.text or "").strip() if el is not None else ""


def parse_feed(xml_text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(xml_text)
    total = int(_text(root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")) or 0)
    out = []
    for e in root.findall("a:entry", NS):
        raw_id = _text(e.find("a:id", NS))
        if "/api/errors" in raw_id:  # arXiv는 오류도 entry로 돌려준다
            raise ArxivError(_text(e.find("a:summary", NS)) or "arXiv error")
        arxiv_id = raw_id.split("/abs/", 1)[-1]
        out.append(
            {
                "arxiv_id": base_id(arxiv_id),
                "version": arxiv_id,
                "title": _text(e.find("a:title", NS)),
                "abstract": _text(e.find("a:summary", NS)),
                "authors": [_text(a.find("a:name", NS)) for a in e.findall("a:author", NS)],
                "published": _text(e.find("a:published", NS)),
                "updated": _text(e.find("a:updated", NS)),
                "categories": [c.get("term") for c in e.findall("a:category", NS) if c.get("term")],
                "primary_category": (e.find("x:primary_category", NS).get("term") if e.find("x:primary_category", NS) is not None else None),
                "comment": _text(e.find("x:comment", NS)),
                "journal_ref": _text(e.find("x:journal_ref", NS)),
                "doi": _text(e.find("x:doi", NS)),
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
            }
        )
    return out, total


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delta = time.monotonic() - self._last
            if delta < config.ARXIV_MIN_INTERVAL:
                await asyncio.sleep(config.ARXIV_MIN_INTERVAL - delta)
            self._last = time.monotonic()


_limiter = _RateLimiter()
_HEADERS = {"User-Agent": "paper-lab/0.1 (personal research tool)"}


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    await _limiter.wait()
    try:
        r = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=60, **kw)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ArxivError(f"arXiv 요청 실패: {e}") from e
    return r


_FIELD_RE = re.compile(r"\b(all|ti|au|abs|cat|co|jr|rn|id):")


async def _query(c: httpx.AsyncClient, q: str, start: int, max_results: int) -> tuple[list[dict], int]:
    r = await _get(c, config.ARXIV_API, params={"search_query": q, "start": start, "max_results": max_results, "sortBy": "relevance"})
    return parse_feed(r.text)


async def search(query: str, start: int = 0, max_results: int = 20, client: httpx.AsyncClient | None = None) -> dict:
    """일반 키워드는 '제목 구문 일치'를 먼저, 이어서 전체 필드 검색 결과를 붙인다.
    (arXiv의 all: 검색만으로는 정확한 제목의 논문이 상위에 오지 않는 경우가 많음)"""
    query = query.strip()
    async with _maybe(client) as c:
        if _FIELD_RE.search(query):  # 사용자가 arXiv 쿼리 문법을 직접 지정
            items, total = await _query(c, query, start, max_results)
            return {"total": total, "start": start, "items": items}
        phrase: list[dict] = []
        if start == 0:
            phrase, _ = await _query(c, f'ti:"{query}"', 0, 5)
        items, total = await _query(c, f"all:{query}", start, max_results)
    seen = {p["arxiv_id"] for p in phrase}
    items = phrase + [i for i in items if i["arxiv_id"] not in seen]
    return {"total": max(total, len(items)), "start": start, "items": items[: max_results + len(phrase)]}


async def fetch_metadata(arxiv_id: str, client: httpx.AsyncClient | None = None) -> dict:
    async with _maybe(client) as c:
        r = await _get(c, config.ARXIV_API, params={"id_list": arxiv_id})
    items, _ = parse_feed(r.text)
    if not items:
        raise ArxivError(f"arXiv에서 {arxiv_id} 를 찾을 수 없습니다")
    return items[0]


async def download_pdf(arxiv_id: str, dest: Path, client: httpx.AsyncClient | None = None) -> None:
    async with _maybe(client) as c:
        r = await _get(c, f"{config.ARXIV_PDF}/{arxiv_id}")
    if not r.content.startswith(b"%PDF"):
        raise ArxivError("PDF가 아닌 응답을 받았습니다")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(dest)


class _maybe:
    """외부에서 client를 주입(테스트)하면 재사용, 아니면 임시 생성."""

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._given = client
        self._own: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._own = httpx.AsyncClient()
        return self._own

    async def __aexit__(self, *exc) -> None:
        if self._own is not None:
            await self._own.aclose()
