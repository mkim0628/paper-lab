"""그림/표 추출: 합성 PDF 로 레이아웃 케이스를 검증 (실제 논문 PDF 는 scripts/eval_figures.py 로 평가)."""
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, figures, storage

LOREM = ("We study attention mechanisms for sequence transduction and show that a model based solely on attention can be trained "
         "faster and generalizes better across many tasks in practice, as the following experiments will demonstrate in detail. ") * 3


def para(page, y, x0=72, x1=540, text=LOREM, h=48):
    page.insert_textbox((x0, y, x1, y + h), text, fontsize=10, align=3)


def fig_graphics(page, x0, y0, x1, y1):
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), fill=(0.8, 0.9, 1))
    page.draw_line((x0, y0), (x1, y1), color=(1, 0, 0))
    page.insert_text((x0 + 10, y0 + 20), "encoder", fontsize=7)


def save(doc, tmp_path, name="p.pdf"):
    p = tmp_path / name
    doc.save(p)
    return p


def run(pdf, tmp_path):
    return figures.extract(pdf, tmp_path / "out")


def by_id(res):
    return {i["id"]: i for i in res["items"]}


def test_figure_with_caption_below_excludes_text(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 100, h=40)
    fig_graphics(pg, 150, 170, 450, 320)
    pg.insert_text((72, 340), "Figure 1: A synthetic figure about the encoder.", fontsize=10)
    para(pg, 370, h=40)
    para(pg, 400, h=40)
    res = run(save(doc, tmp_path), tmp_path)
    f = by_id(res)["figure-1"]
    x0, y0, x1, y1 = f["bbox"]
    assert f["kind"] == "figure" and f["side"] == "above"
    assert x0 <= 150 and x1 >= 450 and y0 <= 170 and y1 >= 320  # 그래픽 전체 포함
    assert y0 > 135 and y1 < 328  # 위 본문 문단과 캡션은 제외
    assert (tmp_path / "out" / "figure-1.png").stat().st_size > 1000
    assert "synthetic figure" in f["caption"]


def test_table_caption_above_and_below_conventions(tmp_path):
    for variant in ("above", "below"):
        doc = pymupdf.open()
        pg = doc.new_page()
        para(pg, 72, h=40)
        para(pg, 100, h=40)
        top = 190 if variant == "above" else 150  # 표 본체 시작 y
        cap_y = 160 if variant == "above" else 300
        # 표: 가로줄 + 셀 텍스트
        for y in (top, top + 20, top + 100):
            pg.draw_line((150, y), (450, y))
        for r, row in enumerate([("Model", "BLEU"), ("Base", "27.3"), ("Big", "28.4"), ("Ours", "29.1")]):
            for c, cell in enumerate(row):
                pg.insert_text((160 + c * 150, top + 15 + r * 25), cell, fontsize=10)
        pg.insert_text((72, cap_y), "Table 1: Results of the experiment.", fontsize=10)
        para(pg, 330 if variant == "above" else 330, h=40)
        para(pg, 360, h=40)
        res = run(save(doc, tmp_path, f"{variant}.pdf"), tmp_path / variant)
        t = by_id(res)["table-1"]
        assert t["bbox"] is not None, variant
        x0, y0, x1, y1 = t["bbox"]
        assert y0 <= top + 1 and y1 >= top + 100 and x0 <= 150 and x1 >= 450, (variant, t["bbox"])
        # 문서 내 표가 하나뿐이어도 한쪽에만 내용이 있으므로 올바른 쪽을 고른다
        assert t["side"] == ("below" if variant == "above" else "above")


def test_two_column_figure_stays_in_its_column(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    for y in (72, 120, 168, 216, 264, 312):  # 왼쪽 단 본문
        para(pg, y, 72, 290, h=44)
    fig_graphics(pg, 330, 100, 520, 220)
    pg.insert_text((320, 240), "Figure 2: Right column figure.", fontsize=10)
    para(pg, 270, 310, 540, h=44)
    para(pg, 318, 310, 540, h=44)
    f = by_id(run(save(doc, tmp_path), tmp_path))["figure-2"]
    assert f["bbox"][0] >= 300 and f["bbox"][2] <= 545  # 왼쪽 단 본문을 포함하지 않는다
    assert f["bbox"][1] <= 100 and f["bbox"][3] >= 220


def test_running_header_is_excluded(tmp_path):
    doc = pymupdf.open()
    for i in range(4):
        pg = doc.new_page()
        pg.insert_text((72, 40), "Published as a conference paper at ICLR 2021", fontsize=9)
        pg.draw_line((72, 46), (540, 46))
        if i == 2:
            fig_graphics(pg, 150, 70, 450, 200)  # 헤더 바로 아래에서 시작하는 그림
            pg.insert_text((72, 220), "Figure 1: Figure right below the header.", fontsize=10)
            para(pg, 250, h=40)
            para(pg, 290, h=40)
        else:
            para(pg, 72, h=40)
            para(pg, 120, h=40)
    f = by_id(run(save(doc, tmp_path), tmp_path))["figure-1"]
    assert f["bbox"][1] >= 46  # 헤더 텍스트/밑줄(y<=46)은 제외


def test_repeated_decorative_rule_is_excluded(tmp_path):
    """텍스트 없이 모든 페이지에 반복되는 머리글 가로줄이 표/그림 영역을 위로 끌어올리면 안 된다 (실제 LoRA 에서 발생)"""
    doc = pymupdf.open()
    for i in range(4):
        pg = doc.new_page()
        pg.draw_line((72, 36), (540, 36), width=2)
        if i == 1:
            fig_graphics(pg, 150, 80, 450, 200)
            pg.insert_text((72, 220), "Figure 1: Figure on a page with a decorative header rule.", fontsize=10)
            para(pg, 250, h=40)
            para(pg, 290, h=40)
        else:
            para(pg, 72, h=40)
            para(pg, 120, h=40)
    f = by_id(run(save(doc, tmp_path), tmp_path))["figure-1"]
    assert f["bbox"][1] > 60  # 헤더 가로줄(y=36)을 포함하지 않는다


def test_text_heavy_table_uses_rule_fallback(tmp_path):
    """셀이 긴 문장이라 '본문 문단'으로 분류되는 표 → 가로줄 군집으로 영역 결정"""
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 110, h=40)
    ys = [170, 240, 310]
    for y in ys:
        pg.draw_line((72, y), (540, y))
    for y in ys[:2]:
        para(pg, y + 6, 160, 530, h=56)
        pg.insert_text((80, y + 20), "Prompt:", fontsize=10)
    pg.insert_text((72, 335), "Table 3: Example prompts and long responses.", fontsize=10)
    para(pg, 360, h=40)
    para(pg, 400, h=40)
    t = by_id(run(save(doc, tmp_path), tmp_path))["table-3"]
    assert t["bbox"] is not None
    assert t["bbox"][1] <= 171 and t["bbox"][3] >= 309 and t["bbox"][3] < 336


def test_zero_height_rules_count_in_union(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 110, h=40)
    pg.draw_line((100, 190), (500, 190))  # 표보다 넓은 가로줄(높이 0)
    pg.insert_text((250, 210), "A  1  2  3", fontsize=10)
    pg.insert_text((250, 230), "B  4  5  6", fontsize=10)
    pg.draw_line((100, 245), (500, 245))
    pg.insert_text((72, 270), "Table 1: Narrow content with wide rules.", fontsize=10)
    para(pg, 300, h=40)
    para(pg, 340, h=40)
    t = by_id(run(save(doc, tmp_path), tmp_path))["table-1"]
    assert t["bbox"][0] <= 100 and t["bbox"][2] >= 500


def test_caption_without_graphics_has_no_region(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 110, h=40)
    pg.insert_text((72, 200), "Figure 9: A caption with nothing above or below it.", fontsize=10)
    para(pg, 230, h=40)
    para(pg, 270, h=40)
    res = run(save(doc, tmp_path), tmp_path)
    f = by_id(res)["figure-9"]
    assert f["bbox"] is None and f["file"] is None
    assert res["stats"] == {"captions": 1, "with_region": 0}


def test_axis_label_is_not_mistaken_for_heading(tmp_path):
    """'N Samples' 같은 축 라벨이 섹션 제목으로 오인되어 영역을 자르면 안 된다 (실제 Llama 2 에서 발생)"""
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 110, h=40)
    fig_graphics(pg, 150, 170, 450, 300)
    pg.insert_text((290, 312), "N Samples", fontsize=6.5)
    pg.insert_text((72, 340), "Figure 7: Plot with an axis label just above the caption.", fontsize=10)
    para(pg, 350, h=40)
    para(pg, 390, h=40)
    f = by_id(run(save(doc, tmp_path), tmp_path))["figure-7"]
    assert f["bbox"] is not None and f["bbox"][3] >= 312


def test_mentions_and_dedup(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, text="As Figure 1 shows, the encoder is a stack of layers. " * 6, h=48)
    fig_graphics(pg, 150, 140, 450, 260)
    pg.insert_text((72, 280), "Figure 1: Overview of the encoder stack.", fontsize=10)
    para(pg, 310, h=40)
    pg2 = doc.new_page()
    pg2.insert_text((72, 100), "Figure 1: Overview of the encoder stack. (duplicate label)", fontsize=10)
    res = run(save(doc, tmp_path), tmp_path)
    assert len([i for i in res["items"] if i["id"] == "figure-1"]) == 1  # 중복 라벨은 첫 번째만
    f = by_id(res)["figure-1"]
    assert f["mentions"] and "Figure 1 shows" in f["mentions"][0]["snippet"] and f["mentions"][0]["page"] == 1


# ---------------- API ----------------
META = {"arxiv_id": "1706.03762", "version": "1706.03762v7", "title": "T", "abstract": "a", "authors": ["A"], "categories": ["cs.CL"],
        "abs_url": "x", "published": "", "updated": "", "primary_category": "cs.CL", "doi": "", "comment": "", "journal_ref": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import httpx

    from app import codefinder

    monkeypatch.setattr(config, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(codefinder, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    from app.main import app

    with TestClient(app) as c:
        yield c


def seed(client, tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    para(pg, 72, h=40)
    para(pg, 110, h=40)
    fig_graphics(pg, 150, 170, 450, 300)
    pg.insert_text((72, 325), "Figure 1: Synthetic figure.", fontsize=10)
    para(pg, 350, h=40)
    para(pg, 390, h=40)
    storage.create(META)
    doc.save(storage.paper_dir("1706.03762") / "paper.pdf")


def test_figures_api_flow(client, tmp_path):
    seed(client, tmp_path)
    r = client.post("/api/papers/1706.03762/figures/extract")
    assert r.status_code == 200 and r.json()["stats"] == {"captions": 1, "with_region": 1}
    assert client.get("/api/papers/1706.03762").json()["figure_count"] == 1
    png = client.get("/api/papers/1706.03762/figures/figure-1.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    page = client.get("/api/papers/1706.03762/pages/1.png", params={"dpi": 72})
    assert page.status_code == 200 and page.content[:4] == b"\x89PNG"
    assert client.get("/api/papers/1706.03762/pages/9.png").status_code == 404

    # 영역 수정 → 재렌더, edited 표시
    old_size = len(png.content)
    r = client.put("/api/papers/1706.03762/figures/figure-1", json={"bbox": [140, 160, 460, 310]})
    assert r.status_code == 200 and r.json()["edited"] is True and r.json()["bbox"] == [140.0, 160.0, 460.0, 310.0]
    assert len(client.get("/api/papers/1706.03762/figures/figure-1.png").content) != old_size
    assert client.put("/api/papers/1706.03762/figures/figure-1", json={"bbox": [1, 1, 3, 3]}).status_code == 400  # 너무 작음
    assert client.put("/api/papers/1706.03762/figures/figure-1", json={"bbox": [0, 0, 9999, 9999]}).json()["bbox"] == [0.0, 0.0, 595.0, 842.0]  # 페이지로 클램프

    # 수동 추가
    r = client.post("/api/papers/1706.03762/figures", json={"page": 1, "bbox": [72, 72, 540, 150], "kind": "table", "caption": "my table"})
    assert r.status_code == 201 and r.json()["id"] == "table-m1" and r.json()["source"] == "manual"
    assert client.post("/api/papers/1706.03762/figures", json={"page": 5, "bbox": [72, 72, 540, 150]}).status_code == 400

    # 재추출해도 수정/수동 항목은 보존
    client.put("/api/papers/1706.03762/figures/figure-1", json={"bbox": [140, 160, 460, 310]})
    r = client.post("/api/papers/1706.03762/figures/extract").json()
    ids = {i["id"]: i for i in r["items"]}
    assert set(ids) == {"figure-1", "table-m1"} and ids["figure-1"]["bbox"] == [140.0, 160.0, 460.0, 310.0]

    # 삭제: 수동은 제거, 자동은 숨김 (재추출해도 숨김 유지)
    assert client.delete("/api/papers/1706.03762/figures/table-m1").status_code == 204
    assert client.delete("/api/papers/1706.03762/figures/figure-1").status_code == 204
    r = client.post("/api/papers/1706.03762/figures/extract").json()
    assert [(i["id"], i["hidden"]) for i in r["items"]] == [("figure-1", True)]
    assert client.get("/api/papers/1706.03762").json()["figure_count"] == 0


def test_figures_api_validation(client, tmp_path):
    seed(client, tmp_path)
    client.post("/api/papers/1706.03762/figures/extract")
    assert client.get("/api/papers/1706.03762/figures/..%2F..%2Fmeta.json.png").status_code in (400, 404)
    assert client.put("/api/papers/1706.03762/figures/not_valid!", json={"label": "x"}).status_code == 400
    assert client.put("/api/papers/1706.03762/figures/figure-99", json={"label": "x"}).status_code == 404
    assert client.get("/api/papers/1706.03762/figures/figure-99.png").status_code == 404
    assert client.get("/api/papers/1706.03762/pages/1.png", params={"dpi": 9999}).status_code == 422


def test_generate_prompt_lists_figures(client, tmp_path, monkeypatch):
    from app import llm

    seed(client, tmp_path)
    storage.write_text("1706.03762", "fulltext.md", '<page n="1">hello</page>')
    client.post("/api/papers/1706.03762/figures/extract")
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=8000, profile=None: seen.append(user) or "# x\n\n## 1. a\nbody")
    assert client.post("/api/papers/1706.03762/generate-review", json={}).status_code == 200
    assert "<figures>" in seen[0] and "id=figure-1 | Figure 1" in seen[0] and "![[fig:<id>]]" in seen[0]
    # 숨긴 그림은 프롬프트에서 제외
    client.delete("/api/papers/1706.03762/figures/figure-1")
    client.post("/api/papers/1706.03762/generate-review", json={})
    assert "<figures>" not in seen[1]
