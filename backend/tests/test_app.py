import pytest
from fastapi.testclient import TestClient

from app import arxiv, config

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence transduction models...  </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:comment>15 pages</arxiv:comment>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/><category term="cs.LG"/>
  </entry>
</feed>"""

ERR_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/api/errors#incorrect_id_format_for_1</id><summary>incorrect id format</summary></entry></feed>"""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1706.03762", "1706.03762"),
        ("1706.03762v7", "1706.03762v7"),
        ("arXiv:2401.12345", "2401.12345"),
        ("https://arxiv.org/abs/1706.03762v5", "1706.03762v5"),
        ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
        ("http://arxiv.org/pdf/2401.12345v2", "2401.12345v2"),
        ("arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),
        ("attention is all you need", None),
        ("../../etc/passwd", None),
        ("https://evil.com/abs/1706.03762", None),
    ],
)
def test_parse_id(text, expected):
    assert arxiv.parse_arxiv_id(text) == expected


def test_parse_feed():
    items, total = arxiv.parse_feed(FEED)
    assert total == 1
    it = items[0]
    assert it["arxiv_id"] == "1706.03762" and it["version"] == "1706.03762v7"
    assert it["title"] == "Attention Is All You Need"
    assert it["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert it["primary_category"] == "cs.CL" and it["comment"] == "15 pages"


def test_parse_feed_error():
    with pytest.raises(arxiv.ArxivError):
        arxiv.parse_feed(ERR_FEED)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    import httpx

    from app import codefinder, related

    monkeypatch.setattr(related, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    monkeypatch.setattr(config, "RELATED_BACKOFF", 0.0)
    # 코드 탐색의 외부 호출은 기본적으로 전부 404 (테스트는 네트워크를 쓰지 않는다)
    monkeypatch.setattr(codefinder, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    items, _ = arxiv.parse_feed(FEED)

    async def fake_meta(aid, client=None):
        if arxiv.base_id(aid) != "1706.03762":
            raise arxiv.ArxivError("not found")
        return items[0]

    async def fake_search(q, start=0, max_results=20, client=None):
        return {"total": 1, "start": start, "items": [dict(items[0])]}

    async def fake_pdf(aid, dest, client=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        import pymupdf

        doc = pymupdf.open()
        for i, txt in enumerate(["Abstract. We propose the Transformer, based solely on attention mechanisms. " * 3, "Results. BLEU 28.4 on WMT14 En-De. " * 5]):
            doc.new_page().insert_textbox((50, 50, 550, 750), txt)
        doc.save(dest)

    monkeypatch.setattr(arxiv, "fetch_metadata", fake_meta)
    monkeypatch.setattr(arxiv, "search", fake_search)
    monkeypatch.setattr(arxiv, "download_pdf", fake_pdf)
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_full_flow(client):
    r = client.get("/api/search", params={"q": "attention"})
    assert r.status_code == 200 and r.json()["items"][0]["in_library"] is False

    r = client.post("/api/papers", json={"input": "https://arxiv.org/abs/1706.03762v7"})
    assert r.status_code == 201 and r.json()["status"] == "to-read"
    assert client.post("/api/papers", json={"input": "1706.03762"}).status_code == 409
    assert client.post("/api/papers", json={"input": "some keywords"}).status_code == 400
    assert client.post("/api/papers", json={"input": "1999.99999"}).status_code == 502

    # background task 완료 후 pdf 사용 가능
    assert client.get("/api/papers/1706.03762").json()["pdf_status"] == "ready"
    assert client.get("/api/papers/1706.03762/pdf").content.startswith(b"%PDF")
    m = client.get("/api/papers/1706.03762").json()
    assert m["fulltext_status"] == "ready" and m["pages"] == 2
    assert client.get("/api/search", params={"q": "attention"}).json()["items"][0]["in_library"] is True

    rv = client.get("/api/papers/1706.03762/review").json()
    assert "## 8. 약점" in rv["content"]
    assert client.put("/api/papers/1706.03762/review", json={"content": "v1"}).json()["changed"] is True
    assert client.put("/api/papers/1706.03762/review", json={"content": "v1"}).json()["changed"] is False
    client.put("/api/papers/1706.03762/review", json={"content": "v2"})
    assert client.get("/api/papers/1706.03762/review").json()["content"] == "v2"
    hist = client.get("/api/papers/1706.03762/review/history").json()
    assert len(hist) == 1  # 60초 이내 연속 저장은 한 번만 보관
    assert "약점" in client.get(f"/api/papers/1706.03762/review/history/{hist[0]}").json()["content"]

    r = client.patch("/api/papers/1706.03762", json={"status": "reviewed", "tags": ["nlp"], "rating": 5})
    assert r.json()["status"] == "reviewed" and r.json()["tags"] == ["nlp"]
    assert client.patch("/api/papers/1706.03762", json={"status": "bogus"}).status_code == 400
    assert len(client.get("/api/papers").json()) == 1

    assert client.delete("/api/papers/1706.03762").status_code == 204
    assert client.get("/api/papers/1706.03762").status_code == 404


def test_path_traversal(client):
    assert client.get("/api/papers/..%2F..%2Fetc/review").status_code in (400, 404)
    assert client.get("/api/papers/not-an-id/pdf").status_code == 400


# ---------------- Phase 2 ----------------
def _add(client):
    assert client.post("/api/papers", json={"input": "1706.03762"}).status_code == 201


def test_fulltext_has_page_tags(client):
    _add(client)
    ft = config.PAPERS_DIR / "1706.03762" / "fulltext.md"
    t = ft.read_text(encoding="utf-8")
    assert '<page n="1">' in t and '<page n="2">' in t and "BLEU 28.4" in t


def test_llm_profiles(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    r = client.get("/api/settings/llm").json()
    assert r["active"] is None and r["profiles"] == []

    body = {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-SECRET1234"}
    r = client.put("/api/settings/llm/profiles/external", json=body).json()
    assert r["active"] == "external"  # 첫 프로필은 자동 활성
    prof = r["profiles"][0]
    assert "api_key" not in prof and prof["has_api_key"] and prof["api_key_masked"].endswith("1234")
    assert "SECRET" not in client.get("/api/settings/llm").text

    # 사내 프로필: base_url + CA, 키 비우면 기존 유지
    client.put("/api/settings/llm/profiles/corp", json={"model": "m", "api_key": "corp-KEY-9999", "base_url": "https://llm.corp/v1", "ca_bundle": "/etc/ca.pem"})
    client.put("/api/settings/llm/profiles/corp", json={"model": "m2"})
    assert llm_mod().list_profiles()["profiles"][1]["model"] == "m2"
    assert llm_mod()._load()["profiles"]["corp"]["api_key"] == "corp-KEY-9999"

    assert client.post("/api/settings/llm/active", json={"name": "corp"}).json()["active"] == "corp"
    assert llm_mod().active_profile()["base_url"] == "https://llm.corp/v1"
    assert client.post("/api/settings/llm/active", json={"name": "nope"}).status_code == 404
    assert client.put("/api/settings/llm/profiles/x", json={"model": "m", "provider": "foo"}).status_code == 400

    import stat
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600

    client.delete("/api/settings/llm/profiles/corp")
    assert client.get("/api/settings/llm").json()["active"] == "external"


def llm_mod():
    from app import llm
    return llm


def test_client_construction_uses_base_url(monkeypatch):
    seen = {}

    class FakeAnthropic:
        def __init__(self, **kw):
            seen.update(kw)

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    llm_mod()._client({"provider": "anthropic", "api_key": "k", "base_url": "https://llm.corp/v1"})
    assert seen["base_url"] == "https://llm.corp/v1" and seen["api_key"] == "k"


def test_generate_review_flow(client, tmp_path, monkeypatch):
    from app import llm

    _add(client)
    calls = []

    def fake_complete(system, user, max_tokens=8000, profile=None):
        calls.append(user)
        if "다음 섹션 하나만" in user:
            return "- 새로 쓴 강점 [p.2]"
        return "# Attention\n\n## 1. TL;DR\nTransformer [p.1]\n\n## 2. 문제\n내용 [p.1]\n\n## 3. 기여\n- 기여 [p.2]\n"

    monkeypatch.setattr(llm, "complete", fake_complete)
    client.put("/api/papers/1706.03762/review", json={"content": "내가 쓴 소중한 리뷰"})

    r = client.post("/api/papers/1706.03762/generate-review", json={})
    assert r.status_code == 200 and "[p.1]" in r.json()["content"] and r.json()["meta"]["ai_draft"] is True
    # 프롬프트: 본문(페이지 태그)과 템플릿이 들어가고, 인젝션 방어 규칙이 system에 있다
    assert '<page n="1">' in calls[0] and "<template>" in calls[0]
    assert "<figures>" not in calls[0]  # 추출된 그림이 없으면 블록을 넣지 않는다
    # 덮어쓰기 전 사용자 리뷰가 history에 보관됨
    stamps = client.get("/api/papers/1706.03762/review/history").json()
    assert any("소중한" in client.get(f"/api/papers/1706.03762/review/history/{s}").json()["content"] for s in stamps)
    # 생성은 저장하지 않음 (프론트가 반영)
    assert "소중한" in client.get("/api/papers/1706.03762/review").json()["content"]

    client.put("/api/papers/1706.03762/review", json={"content": r.json()["content"]})
    r2 = client.post("/api/papers/1706.03762/generate-review", json={"section": 2})
    c = r2.json()["content"]
    assert "새로 쓴 강점" in c and "## 2. 문제" in c and "## 1. TL;DR\nTransformer" in c and "## 3. 기여" in c
    assert client.post("/api/papers/1706.03762/generate-review", json={"section": 99}).status_code == 400

    assert client.patch("/api/papers/1706.03762", json={"ai_draft": False}).json()["ai_draft"] is False


def test_generate_without_profile(client):
    _add(client)
    r = client.post("/api/papers/1706.03762/generate-review", json={})
    assert r.status_code == 502 and "프로필" in r.json()["detail"]


def test_local_search(client):
    _add(client)
    client.put("/api/papers/1706.03762/notes", json={"content": "multi-head 구조가 인상적"})
    r = client.get("/api/local-search", params={"q": "BLEU wmt14"}).json()
    assert r and r[0]["arxiv_id"] == "1706.03762" and "fulltext" in r[0]["matched_in"] and "BLEU" in r[0]["snippet"]
    assert client.get("/api/local-search", params={"q": "multi-head"}).json()[0]["matched_in"] == ["notes"]
    assert client.get("/api/local-search", params={"q": "nonexistentterm"}).json() == []
    assert client.get("/api/local-search", params={"q": "bleu nonexistentterm"}).json() == []  # AND


def test_complete_against_mock_messages_api(tmp_path, monkeypatch):
    """실제 anthropic SDK 경로(스트리밍, base_url, 헤더)를 로컬 mock 서버로 검증."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    got = {}

    def sse(ev, data):
        return f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"] = self.path
            got["key"] = self.headers.get("x-api-key") or (self.headers.get("authorization") or "").removeprefix("Bearer ")
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            msg = {"id": "m1", "type": "message", "role": "assistant", "model": "x", "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            self.wfile.write(sse("message_start", {"type": "message_start", "message": msg}))
            self.wfile.write(sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
            for t in ["안녕 ", "[p.1]"]:
                self.wfile.write(sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}))
            self.wfile.write(sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            self.wfile.write(sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}))
            self.wfile.write(sse("message_stop", {"type": "message_stop"}))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    llm = llm_mod()
    llm.upsert_profile("corp", {"model": "claude-test", "api_key": "corp-key", "base_url": f"http://127.0.0.1:{srv.server_port}"})
    out = llm.complete("SYS", "USER", max_tokens=50)
    srv.shutdown()
    assert out == "안녕 [p.1]"
    assert got["path"].endswith("/v1/messages") and got["key"] == "corp-key"
    assert got["body"]["model"] == "claude-test" and got["body"]["system"] == "SYS" and got["body"]["stream"] is True


def test_ca_bundle_client_is_accepted_by_sdk(tmp_path):
    """ca_bundle 지정 시 SDK가 받아들이는 http 클라이언트가 만들어지는지 (httpx vs httpx2 회귀 방지)."""
    import subprocess

    pem = tmp_path / "ca.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(tmp_path / "k.pem"), "-out", str(pem), "-subj", "/CN=corp-ca", "-days", "1"], check=True, capture_output=True)
    c = llm_mod()._client({"provider": "anthropic", "api_key": "k", "base_url": "https://llm.corp/v1", "ca_bundle": str(pem)})
    assert str(c.base_url).startswith("https://llm.corp")


def test_search_puts_exact_title_phrase_first_and_dedups():
    import asyncio

    import httpx

    def entry(i, t):
        return f'<entry><id>http://arxiv.org/abs/{i}v1</id><title>{t}</title><summary>s</summary><author><name>A</name></author></entry>'

    def feed(*e):
        return f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>9</opensearch:totalResults>{"".join(e)}</feed>'

    seen = []

    def handler(req):
        q = req.url.params["search_query"]
        seen.append(q)
        if q.startswith("ti:"):
            return httpx.Response(200, text=feed(entry("1706.03762", "Attention Is All You Need")))
        return httpx.Response(200, text=feed(entry("2101.00001", "Other"), entry("1706.03762", "Attention Is All You Need")))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            a = await arxiv.search("attention is all you need", client=c)
            b = await arxiv.search("ti:foo", client=c)
            return a, b

    config.ARXIV_MIN_INTERVAL = 0
    a, b = asyncio.run(run())
    assert [i["arxiv_id"] for i in a["items"]] == ["1706.03762", "2101.00001"]  # 제목 일치 먼저, 중복 제거
    assert seen[:2] == ['ti:"attention is all you need"', "all:attention is all you need"]
    assert seen[2] == "ti:foo"  # 필드 문법은 그대로 전달
