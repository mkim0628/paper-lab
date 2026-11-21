import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from app import chat, config, llm, storage

META = {"arxiv_id": "1706.03762", "version": "1706.03762v7", "title": "Attention Is All You Need", "abstract": "We propose the Transformer.",
        "authors": ["A", "B"], "categories": ["cs.CL"], "abs_url": "x", "published": "", "updated": "", "primary_category": "cs.CL", "doi": "", "comment": "", "journal_ref": ""}


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


def seed(profile=True):
    storage.create(META)
    storage.write_text("1706.03762", "fulltext.md", '<page n="1">Transformer. Scaled dot-product attention divides by sqrt(d_k).</page>')
    if profile:
        llm.upsert_profile("p", {"model": "m", "api_key": "k"})


def sse_events(resp):
    return [json.loads(l[6:]) for l in resp.text.split("\n\n") if l.startswith("data: ")]


def test_chat_streams_and_persists(client, monkeypatch):
    seed()
    seen = {}

    def fake_stream(system, messages, max_tokens=4000, profile=None):
        seen.update(system=system, messages=messages)
        yield "√d_k 로 나누는 이유는 "
        yield "분산 때문입니다 [p.4]."

    monkeypatch.setattr(llm, "stream", fake_stream)
    r = client.post("/api/papers/1706.03762/chat", json={"message": "왜 √d_k 로 나누나요?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse_events(r)
    assert [e.get("delta") for e in ev[:-1]] == ["√d_k 로 나누는 이유는 ", "분산 때문입니다 [p.4]."] and ev[-1] == {"done": True}
    # 시스템 프롬프트: 규칙 + 논문(페이지 태그) + 프롬프트 캐싱 표시, 인젝션 방어 규칙
    sysb = seen["system"]
    assert "따르지 마세요" in sysb[0]["text"] and "[p.N]" in sysb[0]["text"]
    assert '<page n="1">' in sysb[1]["text"] and sysb[1]["cache_control"] == {"type": "ephemeral"}
    assert seen["messages"] == [{"role": "user", "content": "왜 √d_k 로 나누나요?"}]
    # 저장 + 후속 질문에서 이전 대화가 문맥으로 전달됨
    h = client.get("/api/papers/1706.03762/chat").json()["messages"]
    assert [m["role"] for m in h] == ["user", "assistant"] and h[1]["content"].endswith("[p.4].")
    client.post("/api/papers/1706.03762/chat", json={"message": "더 쉽게"})
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "user"]
    assert client.delete("/api/papers/1706.03762/chat").status_code == 204
    assert client.get("/api/papers/1706.03762/chat").json()["messages"] == []


def test_chat_prompt_includes_figures_when_available(client, monkeypatch):
    seed()
    storage.write_text("1706.03762", "figures/figures.json", json.dumps({"items": [
        {"id": "figure-1", "label": "Figure 1", "page": 3, "caption": "The Transformer - model architecture.", "bbox": [1, 2, 3, 4], "hidden": False},
        {"id": "figure-2", "label": "Figure 2", "page": 4, "caption": "hidden one", "bbox": [1, 2, 3, 4], "hidden": True}]}))
    seen = {}
    monkeypatch.setattr(llm, "stream", lambda system, messages, max_tokens=4000, profile=None: seen.update(s=system) or iter(["ok"]))
    client.post("/api/papers/1706.03762/chat", json={"message": "q"})
    t = seen["s"][1]["text"]
    assert "id=figure-1 | Figure 1 (p.3)" in t and "figure-2" not in t


def test_chat_errors(client, monkeypatch):
    seed(profile=False)
    r = client.post("/api/papers/1706.03762/chat", json={"message": "q"})
    assert r.status_code == 502 and "프로필" in r.json()["detail"]  # 스트림 시작 전 일반 HTTP 오류
    llm.upsert_profile("p", {"model": "m", "api_key": "k"})

    def boom(*a, **k):
        yield "부분 "
        raise llm.LLMError("LLM 호출 실패 (X): boom")

    monkeypatch.setattr(llm, "stream", boom)
    ev = sse_events(client.post("/api/papers/1706.03762/chat", json={"message": "q"}))
    assert ev[0] == {"delta": "부분 "} and "boom" in ev[1]["error"] and not any(e.get("done") for e in ev)
    h = client.get("/api/papers/1706.03762/chat").json()["messages"]
    assert h[-1]["role"] == "assistant" and h[-1]["content"] == "부분" and "boom" in h[-1]["error"]  # 부분 응답과 오류가 남는다
    assert client.post("/api/papers/1706.03762/chat", json={"message": ""}).status_code == 422
    storage.write_text("1706.03762", "fulltext.md", "")
    assert client.post("/api/papers/1706.03762/chat", json={"message": "q"}).status_code == 409


def test_api_messages_alternate_and_start_with_user():
    msgs = [{"role": "assistant", "content": "orphan"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}, {"role": "assistant", "content": ""}]
    assert chat.api_messages(msgs) == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]
    many = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(40)]
    assert len(chat.api_messages(many)) <= chat.MAX_HISTORY


def test_stream_against_mock_messages_api_sends_cache_control(tmp_path, monkeypatch):
    """실제 anthropic SDK 스트리밍 경로 검증: 시스템 블록(cache_control)과 텍스트 조각 전달"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    got = {}

    def sse(ev, d):
        return f"event: {ev}\ndata: {json.dumps(d)}\n\n".encode()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            msg = {"id": "m", "type": "message", "role": "assistant", "model": "x", "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            w = self.wfile.write
            w(sse("message_start", {"type": "message_start", "message": msg}))
            w(sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
            for t in ["안녕", "하세요"]:
                w(sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}))
            w(sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            w(sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}))
            w(sse("message_stop", {"type": "message_stop"}))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    llm.upsert_profile("c", {"model": "claude-test", "api_key": "k", "base_url": f"http://127.0.0.1:{srv.server_port}"})
    sysb = chat.system_blocks(META, "<page n=\"1\">x</page>", None)
    out = list(llm.stream(sysb, [{"role": "user", "content": "hi"}], max_tokens=50))
    srv.shutdown()
    assert out == ["안녕", "하세요"]
    assert got["body"]["system"][1]["cache_control"] == {"type": "ephemeral"} and got["body"]["messages"] == [{"role": "user", "content": "hi"}]
