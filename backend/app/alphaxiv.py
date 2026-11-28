"""alphaXiv 의 'AI Overview'(이미 생성되어 공개된 논문 개요)를 가져와 마크다운으로 변환한다. LLM API 키가 필요 없다.

- 사용자가 버튼을 눌렀을 때만 논문당 1건의 GET 요청을 보낸다 (자동/대량 수집 없음, 요청 간 최소 간격, 식별 가능한 User-Agent).
  alphaXiv 의 robots.txt 는 일반 크롤러에 대해 /abs/ 를 허용한다 (구현 시점 확인).
- 비공식 연동이다: 공개 API 가 아니라 웹 페이지의 HTML 구조(`div.markdown-content.blog-post`)에 의존하므로 사이트가 바뀌면 깨질 수 있다.
  깨지면 '개요 없음'으로 처리하고 원인을 알린다 (예외로 앱이 멈추지 않는다).
- 개요는 alphaXiv 가 생성한 외부 AI 요약이다: 출처를 항상 표시하고 로컬에만 저장하며 검증되지 않았음을 알린다.
  다른 사용자의 댓글(Discussion)은 가져오지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from . import config, storage

UA = "paper-lab/0.1 (personal research tool)"
MAX_BYTES = 6_000_000
_VOID = {"br", "img", "input", "hr", "meta", "link", "source", "track", "path", "circle", "rect", "line", "polyline", "polygon"}
_CITE = re.compile(r"^\[p\s*[\d,\s–-]+\]$")
_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_IMG_OK = "https://paper-assets.alphaxiv.org/"


class AlphaxivError(Exception):
    pass


class _Overview(HTMLParser):
    """`div.markdown-content.blog-post` 안의 제목/문단/목록/강조/링크/인용/그림을 마크다운으로 변환."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.root = None  # 컨테이너가 열린 깊이
        self.found = False
        self.blocks: list[tuple[str, str]] = []  # (kind, text)
        self.buf: list[str] = []
        self.kind: str | None = None
        self.lists: list[list] = []  # [type, count]
        self.svg = 0
        self.button = 0
        self.sup = 0
        self.marks: list[tuple[str, int]] = []  # (sup|sub|a:href, buf 위치)
        self.n_images = 0

    @property
    def inside(self) -> bool:
        return self.root is not None and self.depth >= self.root

    # ---- 블록 처리
    def _flush(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self.buf)).strip()
        self.buf = []
        kind, self.kind = self.kind, None
        if not text:
            return
        text = re.sub(r"\[p\s*(\d+)\s*\]", r"[p.\1]", text)
        text = re.sub(r"\[p\s*(\d+)\s*[–-]\s*(\d+)\s*\]", r"[p.\1]–[p.\2]", text)
        text = re.sub(r"\[p\s*(\d+)\s*,\s*(\d+)\s*\]", r"[p.\1][p.\2]", text)
        self.blocks.append((kind or "p", text))

    def _start(self, kind: str) -> None:
        self._flush()
        self.kind = kind

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in _VOID:
            self.depth += 1
        if self.root is None:
            cls = a.get("class") or ""
            if tag == "div" and "blog-post" in cls and "markdown-content" in cls:
                self.root, self.found = self.depth, True
            return
        if not self.inside:
            return
        if tag == "svg":
            self.svg += 1
        elif tag == "button":
            self.button += 1
        elif tag in ("h2", "h3", "h4"):
            self._start(tag)
        elif tag == "p":
            self._start("p")
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append([tag, 0])
        elif tag == "li":
            self._start("li")
            if self.lists:
                self.lists[-1][1] += 1
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "sup":
            self.sup += 1
            self.marks.append(("sup", len(self.buf)))
        elif tag == "sub":
            self.marks.append(("sub", len(self.buf)))
        elif tag == "a":
            self.marks.append((f"a:{a.get('href') or ''}", len(self.buf)))
        elif tag == "br":
            self.buf.append(" ")
        elif tag == "img":
            src = a.get("src") or ""
            if src.startswith(_IMG_OK):  # 논문 그림 이미지(alphaXiv CDN)만 허용
                self._flush()
                alt = re.sub(r"[\[\]\n]", " ", a.get("alt") or "").strip()
                self.blocks.append(("img", f"![{alt}]({src})"))
                self.n_images += 1

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self.inside:
            if tag == "svg":
                self.svg = max(0, self.svg - 1)
            elif tag == "button":
                self.button = max(0, self.button - 1)
            elif tag in ("h2", "h3", "h4", "p", "li"):
                self._flush()
            elif tag in ("ul", "ol"):
                self._flush()
                if self.lists:
                    self.lists.pop()
            elif tag in ("strong", "b"):
                self.buf.append("**")
            elif tag in ("em", "i"):
                self.buf.append("*")
            elif tag == "code":
                self.buf.append("`")
            elif tag in ("sup", "sub", "a") and self.marks:
                kind, pos = self.marks.pop()
                if tag == "sup":
                    self.sup = max(0, self.sup - 1)
                inner = "".join(self.buf[pos:]).strip()
                if kind == "sup" and tag == "sup":
                    rep = inner if _CITE.match(inner) else (inner.translate(_SUP) if all(c in "0123456789+-=()n" for c in inner) else f"^({inner})")
                elif kind == "sub" and tag == "sub":
                    rep = inner.translate(_SUB) if all(c in "0123456789+-=()" for c in inner) else f"_({inner})"
                elif kind.startswith("a:") and tag == "a":
                    href = kind[2:]
                    if href.startswith("/abs/"):
                        href = "https://arxiv.org" + href
                    rep = f"[{inner}]({href})" if inner and re.match(r"https?://", href) else inner
                else:  # 짝이 맞지 않는 경우: 원문 유지
                    self.marks.append((kind, pos))
                    rep = None
                if rep is not None:
                    del self.buf[pos:]
                    self.buf.append(rep)
        self.depth -= 1
        if self.root is not None and self.depth < self.root:
            self._flush()
            self.root = None  # 컨테이너 종료 (이후 Discussion 등은 무시)
            self.done = True

    def handle_data(self, data):
        if not self.inside or self.svg or (self.button and not self.sup):
            return
        if self.kind is None:
            if not data.strip():
                return
            self.kind = "p"
        self.buf.append(re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        out: list[str] = []
        prev = None
        for kind, text in self.blocks:
            if kind == "h2":
                out.append(f"\n## {text}\n")
            elif kind == "h3":
                out.append(f"\n### {text}\n")
            elif kind == "h4":
                out.append(f"\n#### {text}\n")
            elif kind == "li":
                out.append(("" if prev == "li" else "\n") + f"- {text}")
            else:
                out.append(f"\n{text}\n")
            prev = kind
        return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def parse_page(html: str) -> dict:
    """alphaXiv /abs/<id> 페이지 HTML → {available, title, markdown, ...}. 개요가 없으면 available=False."""
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S | re.I)
    title = re.sub(r"\s*\|\s*alphaXiv\s*$", "", re.sub(r"\s+", " ", (re.findall(r"<title>(.*?)</title>", html, re.S) or [""])[0])).strip()
    p = _Overview()
    p.feed(html)
    p.close()
    md = p.markdown() if p.found else ""
    if len(re.sub(r"\W", "", md)) < 200:  # 개요가 아직 생성되지 않은 논문 ('Generate' 버튼만 있는 페이지)
        return {"available": False, "title": title, "markdown": "", "n_figures": 0, "n_citations": 0}
    return {"available": True, "title": title, "markdown": md, "n_figures": p.n_images, "n_citations": len(re.findall(r"\[p\.\d+\]", md))}


_lock = asyncio.Lock()
_last = 0.0


async def fetch(arxiv_id: str, client: httpx.AsyncClient | None = None) -> dict:
    global _last
    url = f"{config.ALPHAXIV_BASE}/abs/{arxiv_id}"
    async with _lock:  # 요청 간 최소 간격 (예의)
        wait = config.ALPHAXIV_MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        own = client is None
        c = client or httpx.AsyncClient()
        try:
            r = await c.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, follow_redirects=True, timeout=40)
        except httpx.HTTPError as e:
            raise AlphaxivError(f"alphaXiv 에 연결하지 못했습니다: {type(e).__name__}") from e
        finally:
            if own:
                await c.aclose()
    base = {"arxiv_id": arxiv_id, "source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if r.status_code == 404:
        return {**base, "available": False, "title": "", "markdown": "", "n_figures": 0, "n_citations": 0, "reason": "alphaXiv 에 이 논문 페이지가 없습니다"}
    if r.status_code != 200:
        raise AlphaxivError(f"alphaXiv 응답 오류 (HTTP {r.status_code})")
    if len(r.content) > MAX_BYTES:
        raise AlphaxivError("응답이 너무 큽니다")
    res = {**base, **parse_page(r.text)}
    if not res["available"]:
        res["reason"] = "alphaXiv 에 이 논문의 AI 개요가 아직 없습니다 (주로 널리 읽힌 논문에만 생성되어 있음)"
    return res


def load(arxiv_id: str) -> dict | None:
    t = storage.read_text(arxiv_id, "alphaxiv.json")
    return json.loads(t) if t else None


def save(arxiv_id: str, data: dict) -> None:
    storage.write_text(arxiv_id, "alphaxiv.json", json.dumps(data, ensure_ascii=False, indent=1))
