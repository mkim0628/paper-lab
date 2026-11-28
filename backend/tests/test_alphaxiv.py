import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import alphaxiv, config, storage

LONG = "Self attention lets every position exchange information with every other position in a single layer. " * 3

# alphaXiv /abs 페이지와 같은 구조를 흉내낸 합성 HTML (실제 페이지 문구는 쓰지 않음)
PAGE = f"""<html><head><title>Example Paper Title | alphaXiv</title><script>var x = "<p>script text must be ignored</p>";</script></head><body>
<header><h2>Not the overview</h2></header>
<section><h2>AI Overview</h2><button aria-label="Copy overview">Copy</button>
<div class="markdown-content blog-post mx-auto"><p>{LONG}<button class="cite"><sup>[p<!-- -->2<!-- -->]</sup></button></p>
<figure><picture><source srcset="x"/><img alt="Table of results" src="https://paper-assets.alphaxiv.org/paper-figures/abc/1.png"/></picture></figure>
<img alt="tracker" src="https://evil.example.com/pixel.png"/>
<h2><span><a data-heading-control="true" href="#why-it-works"><svg><path d="M0 0"/></svg></a><span>Why it works</span></span></h2>
<p>The score is <strong>28.4</strong> at <em>2.3</em> × 10<sup>19</sup> FLOPs, with d<sub>k</sub> = 64 and x<sup>n+1</sup> and y<sup>abc</sup>. See <a href="/abs/1409.0473">Bahdanau</a>, <a href="#why-it-works">here</a>, <a href="javascript:alert(1)">bad</a> and <a href="https://example.org/x">ext</a>.<button><sup>[p 8, 9]</sup></button> <button><sup>[p 3–5]</sup></button></p>
<h3>Details</h3><ul><li>first <code>point</code></li><li>second point</li></ul><ol><li>one</li><li>two</li></ol>
<button>Show more UI text must be ignored</button><svg><text>svg text ignored</text></svg>
</div></section>
<section><h2>Discussion</h2><p>A commenter's question that must NOT be imported.</p></section></body></html>"""

EMPTY = "<html><head><title>Other | alphaXiv</title></head><body><section><h2>AI Overview</h2><button>Generate</button></section></body></html>"


def test_parse_page_converts_structure():
    r = alphaxiv.parse_page(PAGE)
    md = r["markdown"]
    assert r["available"] and r["title"] == "Example Paper Title"
    assert md.startswith("Self attention lets every position")
    assert "[p.2]" in md and "[p.8][p.9]" in md and "[p.3]–[p.5]" in md  # 인용 정규화 ([p 2] → [p.2])
    assert "\n## Why it works\n" in md and "### Details" in md  # 제목 앵커/svg 제거
    assert "**28.4**" in md and "*2.3*" in md and "10¹⁹" in md and "d₍k₎" not in md and "d_(k)" in md  # 강조/위첨자/아래첨자
    assert "xⁿ⁺¹" in md and "y^(abc)" in md
    assert "[Bahdanau](https://arxiv.org/abs/1409.0473)" in md and "[ext](https://example.org/x)" in md
    assert "javascript:" not in md and "alert" not in md  # 위험한 링크는 텍스트만 남김
    assert "here" in md and "(#why-it-works)" not in md  # 문서 내부 앵커 링크 제거
    assert "- first `point`" in md and "- second point" in md and "- one" in md
    assert "![Table of results](https://paper-assets.alphaxiv.org/paper-figures/abc/1.png)" in md
    assert "evil.example.com" not in md and r["n_figures"] == 1  # 허용된 CDN 이미지만
    for forbidden in ("script text", "Not the overview", "Show more UI", "svg text", "commenter", "Copy"):
        assert forbidden not in md, forbidden
    assert r["n_citations"] >= 4


def test_parse_page_without_overview_is_unavailable():
    r = alphaxiv.parse_page(EMPTY)
    assert r["available"] is False and r["markdown"] == "" and r["title"] == "Other"
    assert alphaxiv.parse_page("<html>garbage <div")["available"] is False  # 깨진 HTML 에도 예외 없음


def run_fetch(handler, aid="1706.03762"):
    config.ALPHAXIV_MIN_INTERVAL = 0

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await alphaxiv.fetch(aid, client=c)

    return asyncio.run(go())


def test_fetch_is_polite_and_handles_statuses():
    seen = []

    def ok(req):
        seen.append(req)
        return httpx.Response(200, text=PAGE)

    r = run_fetch(ok)
    assert len(seen) == 1  # 요청은 정확히 1건
    assert str(seen[0].url) == "https://www.alphaxiv.org/abs/1706.03762" and seen[0].method == "GET"
    assert "paper-lab" in seen[0].headers["user-agent"]  # 식별 가능한 UA (브라우저 위장 없음)
    assert r["available"] and r["source_url"].endswith("/abs/1706.03762") and r["fetched_at"]
    assert run_fetch(lambda q: httpx.Response(200, text=EMPTY))["reason"].startswith("alphaXiv 에 이 논문의 AI 개요가 아직 없습니다")
    assert run_fetch(lambda q: httpx.Response(404))["available"] is False
    with pytest.raises(alphaxiv.AlphaxivError, match="HTTP 503"):
        run_fetch(lambda q: httpx.Response(503))

    def boom(req):
        raise httpx.ConnectError("x")

    with pytest.raises(alphaxiv.AlphaxivError, match="연결하지 못했습니다"):
        run_fetch(boom)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import codefinder, related

    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    off = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))  # noqa: E731
    monkeypatch.setattr(codefinder, "_client", off)
    monkeypatch.setattr(related, "_client", off)
    from app.main import app

    with TestClient(app) as c:
        yield c


META = {"arxiv_id": "1706.03762", "version": "1706.03762v7", "title": "T", "abstract": "a", "authors": ["A"], "categories": ["cs.CL"],
        "abs_url": "x", "published": "", "updated": "", "primary_category": "cs.CL", "doi": "", "comment": "", "journal_ref": ""}


def test_api_fetch_caches_and_reports_errors(client, monkeypatch):
    storage.create(META)
    assert client.get("/api/papers/1706.03762/alphaxiv").json() == {"fetched": False}  # 자동 요청 없음
    calls = []

    async def fake_fetch(aid, client=None):
        calls.append(aid)
        return {"arxiv_id": aid, "source_url": f"https://www.alphaxiv.org/abs/{aid}", "fetched_at": "2026-01-01T00:00:00+00:00", **alphaxiv.parse_page(PAGE)}

    monkeypatch.setattr(alphaxiv, "fetch", fake_fetch)
    r = client.post("/api/papers/1706.03762/alphaxiv/fetch").json()
    assert r["available"] and r["fetched"] and "## Why it works" in r["markdown"] and calls == ["1706.03762"]
    cached = client.get("/api/papers/1706.03762/alphaxiv").json()
    assert cached["markdown"] == r["markdown"] and calls == ["1706.03762"]  # 조회(GET)는 외부 요청을 하지 않는다
    assert (storage.paper_dir("1706.03762") / "alphaxiv.json").exists()

    async def failing(aid, client=None):
        raise alphaxiv.AlphaxivError("alphaXiv 응답 오류 (HTTP 503)")

    monkeypatch.setattr(alphaxiv, "fetch", failing)
    r = client.post("/api/papers/1706.03762/alphaxiv/fetch")
    assert r.status_code == 502 and "503" in r.json()["detail"]
    assert client.get("/api/papers/1706.03762/alphaxiv").json()["available"] is True  # 실패해도 이전 저장본 유지
    assert client.get("/api/papers/not-an-id/alphaxiv").status_code == 400
