import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, llm, related, storage

THIS_YEAR = 2026


def s2ref(pid, title, year, cites, *, infl=False, intents=(), contexts=(), arxiv=None, venue="V"):
    ext = {"DOI": f"10.1/{pid}"}
    if arxiv:
        ext["ArXiv"] = arxiv
    return {"isInfluential": infl, "intents": list(intents), "contexts": list(contexts),
            "citedPaper": {"paperId": pid, "title": title, "year": year, "venue": venue, "citationCount": cites, "influentialCitationCount": cites // 10, "externalIds": ext}}


REFS = [
    s2ref("bahdanau", "Neural MT by Jointly Learning to Align and Translate", 2014, 30000, infl=True, intents=["background", "methodology"], contexts=["We follow attention [1]."] * 5, arxiv="1409.0473"),
    s2ref("gnmt", "Google's NMT System", 2016, 7500, infl=True, intents=["result"], contexts=["We outperform GNMT on WMT14."] * 3),
    s2ref("adam", "Adam: A Method for Stochastic Optimization", 2014, 175000, intents=["methodology"], contexts=["We use the Adam optimizer."], arxiv="1412.6980"),
    s2ref("dropout", "Dropout", 2014, 45000, intents=[], contexts=[]),
    s2ref("obscure", "Some Workshop Paper", 2019, 12, intents=["background"], contexts=["Related work."]),
    s2ref("gpt1", "Improving Language Understanding by Generative Pre-Training", 2018, 0, infl=True, intents=["methodology"], contexts=["Following GPT [9]."] * 4),
    s2ref("seqlike", "A Method Lineage Paper", 2017, 300, intents=["methodology"], contexts=["Based on X [5].", "We extend X [5]."]),
    {"isInfluential": False, "intents": [], "contexts": [], "citedPaper": {"paperId": None, "title": None}},  # 매칭 실패 항목은 버린다
]
BY = {r["key"]: r for r in related.rank_references(REFS)}


def test_rank_tiers_and_scoring():
    assert len(BY) == 7
    assert BY["bahdanau"]["tier"] == "foundation"  # 저명 + 이 논문이 실제로 의존(influential, 5회 인용)
    assert BY["gnmt"]["tier"] == "baseline"  # influential + result + 'outperform'
    assert "baseline" in BY["gnmt"]["role_hints"]
    assert BY["adam"]["tier"] == "method"  # 압도적 인용이어도 1회 인용 + non-influential → 기반 필독이 아님
    assert BY["dropout"]["tier"] == "classic"  # 저명하지만 이 논문과 무관
    assert BY["obscure"]["tier"] == "background"
    assert BY["seqlike"]["tier"] == "method"
    order = list(BY)
    ranked = [r["key"] for r in related.rank_references(REFS)]
    assert ranked[0] == "bahdanau" and ranked.index("adam") > ranked.index("gnmt")  # 이 논문 내 중요도가 전역 인용보다 우선
    assert set(BY["bahdanau"]["score_parts"]) == {"global_pct", "velocity_pct", "local"}
    assert BY["bahdanau"]["url"] == "https://arxiv.org/abs/1409.0473" and BY["gnmt"]["url"].startswith("https://doi.org/")
    assert order  # 사용


def test_suspect_count_flag_for_unmerged_records():
    assert BY["gpt1"]["suspect_count"] is True  # 핵심 인용인데 인용 수 0 → 중복 레코드 의심
    assert BY["obscure"]["suspect_count"] is False and BY["bahdanau"]["suspect_count"] is False


def test_percentiles():
    assert related._percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert related._percentiles([5, 5, 5]) == [0.5, 0.5, 0.5]
    assert related._percentiles([7]) == [0.5]


def test_cites_per_year_floor_for_new_papers():
    from datetime import datetime, timezone

    y = datetime.now(timezone.utc).year
    assert related._per_year(100, y) == 100.0  # 올해 논문은 1년으로 보정
    assert related._per_year(100, y - 4) == 25.0 and related._per_year(5, None) is None


# ---------------- 외부 API mock ----------------
OA_WORK = {"id": "https://openalex.org/W2626778328", "cited_by_count": 151914, "publication_year": 2017}


def oa_item(wid, title, year, cites, arxiv=None, venue="Conf"):
    return {"id": f"https://openalex.org/{wid}", "title": title, "publication_year": year, "cited_by_count": cites, "doi": f"https://doi.org/10.2/{wid}",
            "primary_location": {"source": {"display_name": venue}},
            "locations": ([{"landing_page_url": f"https://arxiv.org/abs/{arxiv}v2"}] if arxiv else [])}


def make_handler(*, oa_ok=True, s2_429_first=0, s2_many_citers=False):
    state = {"s2": 0}

    def h(req: httpx.Request) -> httpx.Response:
        u = req.url
        if u.host == "api.semanticscholar.org":
            state["s2"] += 1
            if state["s2"] <= s2_429_first:
                return httpx.Response(429, json={"message": "Too Many Requests"}, headers={"retry-after": "0"})
            if u.path.endswith("/references"):
                return httpx.Response(200, json={"data": REFS})
            if "/recommendations/" in u.path:
                assert u.params["from"] == "all-cs"
                return httpx.Response(200, json={"recommendedPapers": [
                    {"paperId": "bahdanau", "title": "dup of reference", "year": 2014, "citationCount": 1, "externalIds": {}},
                    {"paperId": "sim1", "title": "Similar Work", "year": 2022, "citationCount": 50, "externalIds": {"ArXiv": "2201.00001"}}]})
            if u.path.endswith("/citations"):
                body = {"data": [{"citingPaper": {"paperId": f"c{i}", "title": f"Citer {i}", "year": 2020 + i % 6, "citationCount": i * 10, "externalIds": {}}} for i in range(1, 30)]}
                if s2_many_citers:
                    body["next"] = 1000  # 1000건 초과 → 일부만 받음
                return httpx.Response(200, json=body)
        if u.host == "api.openalex.org":
            if not oa_ok:
                return httpx.Response(429, json={"error": "Rate limit exceeded", "message": "Insufficient budget. This request has no API key"})
            if "/works/https://doi.org/10.48550/arXiv." in str(u):
                assert u.params.get("mailto") is None  # 사용자가 설정하지 않으면 연락처를 보내지 않는다
                return httpx.Response(200, json=OA_WORK)
            if u.path == "/works":
                flt = u.params["filter"]
                assert flt.startswith("cites:W2626778328") and u.params["sort"] == "cited_by_count:desc"
                if "from_publication_date" in flt:
                    return httpx.Response(200, json={"results": [oa_item("W9", "Recent Hot Paper", 2025, 900, "2501.00009"), oa_item("W1", "BERT", 2018, 120000, "1810.04805")]})
                return httpx.Response(200, json={"results": [oa_item("W1", "BERT", 2018, 120000, "1810.04805"), oa_item("W2", "GPT-3", 2020, 60000)]})
        return httpx.Response(404)

    return h


@pytest.fixture
def mock(monkeypatch):
    def install(**kw):
        monkeypatch.setattr(related, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(make_handler(**kw))))

    monkeypatch.setattr(config, "RELATED_BACKOFF", 0.0)
    install()
    return install


@pytest.fixture
def client(tmp_path, monkeypatch, mock):
    from app import codefinder

    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(codefinder, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    from app.main import app

    with TestClient(app) as c:
        yield c


META = {"arxiv_id": "1706.03762", "version": "1706.03762v7", "title": "Attention Is All You Need", "abstract": "We propose the Transformer.", "authors": ["A"],
        "categories": ["cs.CL"], "abs_url": "x", "published": "", "updated": "", "primary_category": "cs.CL", "doi": "", "comment": "", "journal_ref": ""}


def seed(arxiv_id="1706.03762"):
    storage.create({**META, "arxiv_id": arxiv_id})


def test_build_all_sources(client):
    seed()
    d = client.post("/api/papers/1706.03762/related/refresh").json()
    assert d["errors"] == [] and d["status"] == "done"
    assert [r["key"] for r in d["references"]][0] == "bahdanau"
    assert d["citing"]["total"] == 151914
    assert [p["title"] for p in d["citing"]["top"]] == ["BERT", "GPT-3"]
    assert [p["title"] for p in d["citing"]["recent"]] == ["Recent Hot Paper"]  # 전체 상위와 겹치는 BERT 는 제외
    assert d["citing"]["top"][0]["arxiv_id"] == "1810.04805"  # OpenAlex locations 에서 arXiv ID 추출
    assert [p["key"] for p in d["similar"]] == ["sim1"]  # 이미 참고문헌인 논문은 제외
    assert d["tiers"]["foundation"] == "기반 필독"
    assert client.get("/api/papers/1706.03762").json()["related_count"] == 7


def test_openalex_failure_complete_s2_list_is_sorted(client, mock):
    mock(oa_ok=False)  # 인용 논문이 29편뿐 → S2 가 전부 줌 → 정렬 가능
    seed()
    d = client.post("/api/papers/1706.03762/related/refresh").json()
    cites = [p["citations"] for p in d["citing"]["top"]]
    assert cites == sorted(cites, reverse=True) and len(cites) == 15 and d["citing"]["total"] == 29
    assert any("PAPERLAB_OPENALEX_API_KEY" in e for e in d["errors"])  # OpenAlex 실패는 알림
    assert any("전체 인용 목록" in e for e in d["errors"])
    assert d["references"]  # 다른 소스는 영향 없음


def test_openalex_failure_with_huge_citer_list_shows_nothing_misleading(client, mock):
    """실제 서비스에서 발견: 인용 15만 건 논문의 S2 첫 1000건 표본을 '상위'로 보여주면 틀린 정보가 된다."""
    mock(oa_ok=False, s2_many_citers=True)
    seed()
    d = client.post("/api/papers/1706.03762/related/refresh").json()
    assert d["citing"]["top"] == [] and d["citing"]["recent"] == []
    assert any("OpenAlex API 키" in e and "정렬을 지원하지 않" in e for e in d["errors"])


def test_s2_429_is_retried(client, mock):
    mock(s2_429_first=2)
    seed()
    d = client.post("/api/papers/1706.03762/related/refresh").json()
    assert d["references"] and not any("Semantic Scholar" in e for e in d["errors"])


def test_s2_permanent_429_reports_key_hint_without_crashing(client, mock):
    mock(s2_429_first=999)
    seed()
    r = client.post("/api/papers/1706.03762/related/refresh")
    d = r.json()
    assert d["references"] == [] and any("PAPERLAB_S2_API_KEY" in e for e in d["errors"])


def test_library_marking_and_get(client):
    seed()
    seed("1412.6980")  # Adam 논문이 라이브러리에 있음
    client.post("/api/papers/1706.03762/related/refresh")
    d = client.get("/api/papers/1706.03762/related").json()
    by = {r["key"]: r for r in d["references"]}
    assert by["adam"]["in_library"] is True and by["bahdanau"]["in_library"] is False
    empty = client.get("/api/papers/1412.6980/related").json()
    assert empty["references"] == [] and empty["fetched_at"] is None


def test_mailto_only_sent_when_user_configures(client, monkeypatch):
    assert related._oa_params({"a": 1}) == {"a": 1}
    monkeypatch.setattr(config, "OPENALEX_MAILTO", "me@example.com")
    assert related._oa_params({"a": 1}) == {"a": 1, "mailto": "me@example.com"}


def test_api_keys_sent_as_headers(monkeypatch):
    monkeypatch.setattr(config, "S2_API_KEY", "s2k")
    monkeypatch.setattr(config, "OPENALEX_API_KEY", "oak")
    assert related._s2_headers() == {"x-api-key": "s2k"} and related._oa_headers() == {"Authorization": "Bearer oak"}


# ---------------- 가이드 ----------------
GOOD = json.dumps({
    "axes": [{"name": "Attention 기원", "summary": "Bahdanau 로 시작", "papers": ["bahdanau", "HALLUCINATED"], "start_with": "bahdanau"},
             {"name": "가짜 축", "summary": "x", "papers": ["NOPE"], "start_with": "NOPE"}],
    "reading_path": [{"key": "bahdanau", "why": "attention 의 출발"}, {"key": "FAKE", "why": "x"}, {"key": "adam", "why": "최적화"}],
    "after_reading": [{"key": "W1", "why": "BERT"}], "caveats": "주의"})


def prof(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    llm.upsert_profile("p", {"model": "m", "api_key": "k"})


def test_guide_validates_keys_and_stores(client, tmp_path, monkeypatch):
    prof(tmp_path, monkeypatch)
    seed()
    client.post("/api/papers/1706.03762/related/refresh")
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=8000, profile=None: seen.append((system, user)) or "```json\n" + GOOD + "\n```")
    r = client.post("/api/papers/1706.03762/related/guide")
    assert r.status_code == 200, r.text
    g = r.json()
    assert [a["name"] for a in g["axes"]] == ["Attention 기원"]  # 유효 논문이 없는 축은 버림
    assert g["axes"][0]["papers"] == ["bahdanau"] and g["axes"][0]["start_with"] == "bahdanau"
    assert [s["key"] for s in g["reading_path"]] == ["bahdanau", "adam"] and g["after_reading"][0]["key"] == "W1"
    assert g["dropped_unknown_refs"] == 3 and g["model"] == "m"
    # 프롬프트: 인젝션 방어 규칙 + 데이터 태그, 논문 제목/수치는 key 참조
    assert "<data>" in seen[0][1] and "따르지 마세요" in seen[0][0] and "수치는 쓰지 마세요" in seen[0][0]
    # 저장되고, 재조회해도 가이드 유지
    assert client.get("/api/papers/1706.03762/related").json()["guide"]["axes"][0]["name"] == "Attention 기원"
    client.post("/api/papers/1706.03762/related/refresh")
    assert client.get("/api/papers/1706.03762/related").json()["guide"] is not None


def test_guide_errors(client, tmp_path, monkeypatch):
    prof(tmp_path, monkeypatch)
    seed()
    assert client.post("/api/papers/1706.03762/related/guide").status_code == 502  # 데이터 없음
    client.post("/api/papers/1706.03762/related/refresh")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "죄송합니다 JSON 이 아닙니다")
    assert client.post("/api/papers/1706.03762/related/guide").status_code == 502
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({"axes": [{"name": "x", "papers": ["ZZZ"]}]}))
    r = client.post("/api/papers/1706.03762/related/guide")
    assert r.status_code == 502 and "유효한 기술 축" in r.json()["detail"]


def test_guide_without_llm_profile(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "empty")
    seed()
    client.post("/api/papers/1706.03762/related/refresh")
    r = client.post("/api/papers/1706.03762/related/guide")
    assert r.status_code == 502 and "프로필" in r.json()["detail"]
