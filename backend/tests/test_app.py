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
    items, _ = arxiv.parse_feed(FEED)

    async def fake_meta(aid, client=None):
        if arxiv.base_id(aid) != "1706.03762":
            raise arxiv.ArxivError("not found")
        return items[0]

    async def fake_search(q, start=0, max_results=20, client=None):
        return {"total": 1, "start": start, "items": [dict(items[0])]}

    async def fake_pdf(aid, dest, client=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake")

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
