import httpx
import pytest
from fastapi.testclient import TestClient

from app import arxiv, codefinder, config, storage
from tests.test_app import FEED  # noqa: F401  (fixture 재사용을 위해 모듈 로드)

META = {
    "arxiv_id": "1706.03762", "title": "Attention Is All You Need", "abstract": "We propose the Transformer.",
    "authors": ["Ashish Vaswani", "Noam Shazeer"], "comment": "", "journal_ref": "",
}

FULLTEXT = """<page n="1">
Attention Is All You Need
1 Introduction. Our code is available at https://github.com/tensorflow/
tensor2tensor, and models are released too.
</page>

<page n="5">
We compare with the baseline from https://github.com/pytorch/fairseq.
</page>

<page n="6">
See also https://github.com/nonexistent/ghost-repo. and (github.com/features)
</page>"""


def repo(full, stars=10, fork=False, archived=False, owner=None):
    return {"full_name": full, "html_url": f"https://github.com/{full}", "owner": {"login": owner or full.split("/")[0]},
            "description": "d", "stargazers_count": stars, "pushed_at": "2025-06-01T00:00:00Z", "license": {"spdx_id": "Apache-2.0"},
            "archived": archived, "fork": fork, "default_branch": "master"}


READMES = {
    "tensorflow/tensor2tensor": "T2T. Paper: https://arxiv.org/abs/1706.03762. Pretrained checkpoints below.",
    "pytorch/fairseq": "Fairseq toolkit.",
    "harvardnlp/annotated-transformer": "The Annotated Transformer: Attention Is All You Need",
    "someone/t2t-fork": "fork of arXiv:1706.03762 code",
}
REPOS = {
    "tensorflow/tensor2tensor": repo("tensorflow/tensor2tensor", 15000),
    "pytorch/fairseq": repo("pytorch/fairseq", 20000),
    "harvardnlp/annotated-transformer": repo("harvardnlp/annotated-transformer", 3000),
    "someone/t2t-fork": repo("someone/t2t-fork", 2, fork=True),
}


def handler(request: httpx.Request) -> httpx.Response:
    u = request.url
    path = u.path
    if u.host == "huggingface.co":
        if path.endswith("/papers/1706.03762"):
            return httpx.Response(200, json={"githubRepo": "https://github.com/tensorflow/tensor2tensor", "githubStars": 15000})
        if path.endswith("/models"):
            return httpx.Response(200, json=[{"id": "google/some-model", "downloads": 99}])
        return httpx.Response(404)
    if path == "/search/repositories":
        q = u.params["q"]
        names = ["someone/t2t-fork", "tensorflow/tensor2tensor"] if "1706.03762" in q else ["harvardnlp/annotated-transformer"]
        return httpx.Response(200, json={"items": [{"full_name": n} for n in names]})
    parts = path.strip("/").split("/")  # repos/{o}/{r}[/readme|/contents/]
    if parts[0] == "repos" and len(parts) >= 3:
        name = f"{parts[1]}/{parts[2]}"
        if name not in REPOS:
            return httpx.Response(404)
        if len(parts) == 3:
            return httpx.Response(200, json=REPOS[name])
        if parts[3] == "readme":
            return httpx.Response(200, text=READMES.get(name, ""))
        if parts[3] == "contents":
            return httpx.Response(200, json=[{"name": "setup.py"}, {"name": "README.md"}] if name.startswith("tensorflow") else [{"name": "README.md"}])
    return httpx.Response(404)


@pytest.fixture
def gh(monkeypatch):
    monkeypatch.setattr(codefinder, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_extract_paper_links_handles_linebreaks_and_noise():
    links = codefinder.extract_paper_links(META, FULLTEXT)
    assert links["tensorflow/tensor2tensor"]["ctx_early"] is True  # 줄바꿈으로 끊긴 URL 복구 + 'Our code is available at'
    assert links["pytorch/fairseq"]["ctx"] is False and links["pytorch/fairseq"]["pages"] == [5]
    assert "features/..." not in links and not any(k.startswith("features/") for k in links)


def test_extract_baseline_phrase_is_not_ctx():
    t = '<page n="1">We use the official implementation of BERT at https://github.com/google-research/bert.</page>'
    assert codefinder.extract_paper_links(META, t)["google-research/bert"]["ctx_early"] is False


def test_arxiv_comment_link_counts_as_code_context():
    m = {**META, "comment": "Code: https://github.com/a/b"}
    assert codefinder.extract_paper_links(m, "")["a/b"]["ctx_early"] is True


@pytest.mark.parametrize(
    "case,info_kw,link,hf,readme,expected",
    [
        ("ctx link early", {}, {"pages": [1], "ctx": True, "ctx_early": True}, False, "", "official"),
        ("hf + readme arxiv", {}, None, True, "see arXiv:1706.03762", "official"),
        ("hf only", {}, None, True, "", "likely-official"),
        ("paper link + readme arxiv", {}, {"pages": [5], "ctx": False, "ctx_early": False}, False, "1706.03762", "likely-official"),
        ("baseline link only", {}, {"pages": [5], "ctx": False, "ctx_early": False}, False, "", "weak"),
        ("readme title only", {}, None, False, "attention is all you need implementation", "community"),
        ("fork cannot be official", {"fork": True}, {"pages": [1], "ctx": True, "ctx_early": True}, False, "", "community"),
        ("nothing", {}, None, False, "", "weak"),
    ],
)
def test_classification_rules(case, info_kw, link, hf, readme, expected):
    info = {"full_name": "x/y", "owner": "x", "stars": 5, "pushed_at": "2025-01-01T00:00:00Z", "archived": False, "fork": False, "env_files": [], "readme": readme, **info_kw}
    assert codefinder.evaluate(info, META, link, hf)["classification"] == expected, case


def test_author_match():
    assert codefinder._author_match("vaswani-lab", ["Ashish Vaswani"]) is True
    assert codefinder._author_match("tensorflow", ["Ashish Vaswani"]) is False


@pytest.fixture
def client(tmp_path, monkeypatch, gh):
    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed():
    storage.create({**META, "version": "1706.03762v7", "categories": ["cs.CL"], "abs_url": "x", "published": "", "updated": "", "primary_category": "cs.CL", "doi": ""})
    storage.write_text("1706.03762", "fulltext.md", FULLTEXT)


def test_find_end_to_end(client):
    _seed()
    r = client.post("/api/papers/1706.03762/code/search")
    assert r.status_code == 200, r.text
    d = r.json()
    by = {c["full_name"]: c for c in d["candidates"]}
    assert "nonexistent/ghost-repo" not in by  # 404 repo 는 제외
    t2t = by["tensorflow/tensor2tensor"]
    assert t2t["classification"] == "official" and t2t["found_via"] == ["hf", "paper", "search"]
    assert t2t["env_files"] == ["setup.py"] and t2t["checkpoint_hint"] is True
    assert any("README" in e for e in t2t["evidence"])
    assert by["pytorch/fairseq"]["classification"] == "weak"
    assert by["someone/t2t-fork"]["classification"] == "community"  # fork 은 official 불가
    assert by["harvardnlp/annotated-transformer"]["classification"] == "community"
    assert d["candidates"][0]["full_name"] == "tensorflow/tensor2tensor"  # 점수순 정렬
    assert d["hf_models"] == [{"id": "google/some-model", "downloads": 99}]
    assert d["errors"] == []
    # meta 요약 + 파일 저장
    m = client.get("/api/papers/1706.03762").json()
    assert m["code_status"] == "done" and m["code"]["best"] == "tensorflow/tensor2tensor" and m["code"]["best_class"] == "official"
    assert (config.PAPERS_DIR / "1706.03762" / "code_candidates.json").exists()


def test_select_manual_and_research_keeps_choices(client):
    _seed()
    client.post("/api/papers/1706.03762/code/search")
    assert client.put("/api/papers/1706.03762/code/select", json={"full_name": "nobody/nothing"}).status_code == 400
    r = client.put("/api/papers/1706.03762/code/select", json={"full_name": "tensorflow/tensor2tensor"})
    assert r.json()["selected"] == "tensorflow/tensor2tensor"
    assert client.get("/api/papers/1706.03762").json()["code"]["selected"] == "tensorflow/tensor2tensor"

    # 수동 추가: URL 형식 검증, 존재 확인
    assert client.post("/api/papers/1706.03762/code/add", json={"url": "not a repo"}).status_code == 400
    assert client.post("/api/papers/1706.03762/code/add", json={"url": "https://github.com/ghost/none"}).status_code == 404
    REPOS["manual/mine"] = repo("manual/mine", 1)
    try:
        c = client.post("/api/papers/1706.03762/code/add", json={"url": "https://github.com/manual/mine.git"}).json()
        assert c["full_name"] == "manual/mine" and c["found_via"] == ["manual"]
        client.post("/api/papers/1706.03762/code/search")  # 재탐색
        d = client.get("/api/papers/1706.03762/code").json()
        assert "manual/mine" in [x["full_name"] for x in d["candidates"]]  # 수동 후보 유지
        assert d["selected"] == "tensorflow/tensor2tensor"  # 선택 유지
    finally:
        REPOS.pop("manual/mine")

    # '공개 코드 없음' → 직접 구현 경로
    r = client.put("/api/papers/1706.03762/code/select", json={"full_name": None, "no_official": True}).json()
    assert r["selected"] is None and r["no_official"] is True
    assert client.get("/api/papers/1706.03762").json()["code"]["no_official"] is True
    assert client.put("/api/papers/1706.03762/code/select", json={"full_name": "tensorflow/tensor2tensor", "no_official": True}).status_code == 400


def test_source_failures_are_reported_not_fatal(client, monkeypatch):
    _seed()

    def broken(request):
        if request.url.host == "huggingface.co":
            raise httpx.ConnectError("blocked")
        if request.url.path == "/search/repositories":
            return httpx.Response(403)
        return handler(request)

    monkeypatch.setattr(codefinder, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    d = client.post("/api/papers/1706.03762/code/search").json()
    assert len(d["errors"]) == 3 and any("Hugging Face" in e for e in d["errors"]) and any("rate limit" in e for e in d["errors"])
    assert d["candidates"][0]["full_name"] == "tensorflow/tensor2tensor"  # 논문 링크 기반 결과는 그대로


def test_stale_states_reset_on_startup(tmp_path, monkeypatch, gh):
    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _seed()
    storage.update_meta("1706.03762", code_status="searching", pdf_status="pending")
    from app.main import app

    with TestClient(app) as c:
        m = c.get("/api/papers/1706.03762").json()
    assert m["code_status"] == "failed" and m["pdf_status"] == "failed"
