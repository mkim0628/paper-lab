"""PDF에서 Figure / Table 을 캡션 앵커 방식으로 추출한다.

ML 논문의 그림은 대부분 벡터(matplotlib/TikZ)라 '임베디드 이미지 추출'만으로는 부족하다.
그래서 (1) `Figure N` / `Table N` 캡션 블록을 찾고, (2) 캡션 위/아래에서 그래픽(이미지·벡터 드로잉)과
그림 내부 텍스트를 묶어 영역을 정하고, (3) 그 영역을 고해상도로 렌더링한다.

핵심 휴리스틱
- '본문 문단'은 줄이 블록 너비를 가득 채운다(양쪽 정렬). 표의 행/그림 라벨은 그렇지 않다 → 영역의 경계로 사용
- 캡션이 그림/표의 위인지 아래인지는 논문마다 다르다 → 문서 전체에서 모호하지 않은 캡션들로 투표해 결정
- 열(column) 모델 대신 '캡션의 x-범위와 겹치는 요소'를 모아 범위를 확장 (1단/2단/나란히 놓인 그림 모두 처리)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

DPI = 200
PAD = 3.0

_CAP_RE = re.compile(r"^\s*(Figure|Fig\.?|Table)\s*([A-Z]?\.?\d+(?:\.\d+)?)\s*(?P<delim>[:.|]|—|–|-(?=\s))?", re.I)
_HEADING_RE = re.compile(r"^(\d+(\.\d+)*\.?|[A-Z]\.\d*(\.\d+)*|Appendix\b.*)\s+[A-Z]")  # '3.1 X', 'A.2 X', 'A. X' (단독 대문자+단어는 축 라벨일 수 있어 제외)
_NUM_ONLY = re.compile(r"^\s*\d{1,3}\s*$")


@dataclass
class Blk:
    rect: pymupdf.Rect
    lines: int
    size: float
    text: str
    kind: str  # body | caption | heading | other
    rotated: bool = False
    fill: float = 0.0


def _union(rects) -> pymupdf.Rect:
    """좌표 min/max 합집합. (pymupdf.Rect 의 `|` 는 높이/너비 0인 '빈 rect'(표의 가로줄 등)를 무시한다)"""
    rs = list(rects)
    return pymupdf.Rect(min(r.x0 for r in rs), min(r.y0 for r in rs), max(r.x1 for r in rs), max(r.y1 for r in rs))


def _alpha_ratio(t: str) -> float:
    chars = re.sub(r"\s+", "", t)
    return sum(c.isalpha() for c in chars) / max(1, len(chars))


def _overlap(a_x0: float, a_x1: float, b_x0: float, b_x1: float) -> float:
    return max(0.0, min(a_x1, b_x1) - max(a_x0, b_x0))


def _x_overlaps(r: pymupdf.Rect, x0: float, x1: float, frac: float = 0.25) -> bool:
    ov = _overlap(r.x0, r.x1, x0, x1)
    return ov > 0 and ov >= frac * min(r.width, x1 - x0 if x1 > x0 else 1)


def body_font_size(doc: pymupdf.Document) -> float:
    """문서 전체에서 글자 수 기준 최빈 폰트 크기."""
    c: Counter = Counter()
    for pno in range(min(doc.page_count, 8)):
        for b in doc[pno].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    c[round(s["size"], 1)] += len(s["text"].strip())
    return c.most_common(1)[0][0] if c else 10.0


def running_margins(doc: pymupdf.Document) -> tuple[float, float]:
    """여러 페이지의 같은 위치에 반복되는 머리글/꼬리글 텍스트를 찾아 (본문 시작 y, 본문 끝 y)를 반환.
    예: 'Published as a conference paper at ICLR 2021' + 밑줄."""
    n = min(doc.page_count, 12)
    top: Counter = Counter()
    bot: Counter = Counter()
    ys_top: dict = {}
    ys_bot: dict = {}
    for pno in range(n):
        page = doc[pno]
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0 or not b["lines"]:
                continue
            t = re.sub(r"\d+", "#", " ".join(s_["text"] for l in b["lines"] for s_ in l["spans"]).strip().lower())
            if not t or t == "#":
                continue
            if b["bbox"][3] < 0.12 * h:
                top[t] += 1
                ys_top[t] = max(ys_top.get(t, 0), b["bbox"][3])
            elif b["bbox"][1] > 0.9 * h:
                bot[t] += 1
                ys_bot[t] = min(ys_bot.get(t, 1e9), b["bbox"][1])
    need = max(3, n // 2)
    t_lim = max([ys_top[t] for t, c in top.items() if c >= need] + [0.0])
    b_lim = min([ys_bot[t] for t, c in bot.items() if c >= need] + [1e9])
    return t_lim, b_lim


def running_decor(doc: pymupdf.Document) -> set[tuple]:
    """여러 페이지의 같은 위치에 반복되는 드로잉(머리글/꼬리글 가로줄, 페이지 테두리 장식)의 좌표 키 집합."""
    n = min(doc.page_count, 12)
    cnt: Counter = Counter()
    for pno in range(n):
        keys = {tuple(round(v) for v in d["rect"]) for d in doc[pno].get_drawings()}
        cnt.update(keys)
    need = max(3, n // 2)
    return {k for k, c in cnt.items() if c >= need}


def _classify_page(page: pymupdf.Page, body_size: float, margins: tuple[float, float] = (0.0, 1e9), decor: frozenset | set = frozenset()) -> tuple[list[Blk], list[pymupdf.Rect], list[pymupdf.Rect]]:
    """(텍스트 블록들, 그래픽(이미지) rect들, 그래픽(드로잉) rect들)"""
    ph, pw = page.rect.height, page.rect.width
    blocks: list[Blk] = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0 or not b["lines"]:
            continue
        r = pymupdf.Rect(b["bbox"])
        lines = b["lines"]
        text = " ".join(" ".join(s["text"] for s in l["spans"]) for l in lines).strip()
        if not text:
            continue
        size = sum(s["size"] * len(s["text"]) for l in lines for s in l["spans"]) / max(1, sum(len(s["text"]) for l in lines for s in l["spans"]))
        rotated = any(abs(l["dir"][0]) < 0.9 for l in lines)
        # 머리글/꼬리글/페이지 번호는 무시
        if (_NUM_ONLY.match(text) and (r.y0 > ph - 70 or r.y1 < 60)) or r.y1 < 28 or r.y0 > ph - 28:
            continue
        if r.y1 <= margins[0] + 1 or r.y0 >= margins[1] - 1:  # 반복되는 러닝 헤더/푸터
            continue
        widths = [l["bbox"][2] - l["bbox"][0] for l in lines]
        bw = max(1.0, r.width)
        fill = (sum(widths[:-1]) / (len(widths) - 1) / bw) if len(widths) > 1 else widths[0] / bw
        first = " ".join(s["text"] for s in lines[0]["spans"]).strip()
        kind = "other"
        m = _CAP_RE.match(first)
        bold0 = "bold" in lines[0]["spans"][0]["font"].lower() or bool(lines[0]["spans"][0]["flags"] & 16)
        if m and (m.group("delim") or (bold0 and len(first) > m.end() + 3)):
            kind = "caption"
        elif not rotated and len(lines) <= 2 and r.width < 0.85 * pw and len(text) < 90 and _HEADING_RE.match(text) and _alpha_ratio(text) >= 0.75 and (
            bold0 or size > body_size + 0.4 or body_size - 2.0 <= size < body_size - 0.3  # 굵은 제목 / 큰 제목 / small-caps 제목
        ):
            kind = "heading"
        elif not rotated and abs(size - body_size) <= 1.6 and bw >= 150 and (
            (len(lines) >= 2 and fill >= 0.8) or (len(lines) >= 3 and fill >= 0.7) or (len(lines) == 1 and bw > 0.6 * pw and len(text) > 80)
            # 인라인 수식 때문에 줄 폭이 들쭉날쭉한 문단: 줄당 단어 수가 많고 글자 위주
            or (len(lines) >= 2 and len(text.split()) / len(lines) >= 8 and _alpha_ratio(text) >= 0.75)
        ):
            kind = "body"
        blocks.append(Blk(r, len(lines), size, text, kind, rotated, fill))

    big = 0.75 * page.rect.get_area()
    imgs = []
    for i in page.get_image_info():
        r = pymupdf.Rect(i["bbox"]) & page.rect
        if r.get_area() >= 16 and r.get_area() < big * 1.3:
            imgs.append(r)
    draws = []
    for d in page.get_drawings():
        if tuple(round(v) for v in d["rect"]) in decor:  # 모든 페이지에 반복되는 장식 → 그림/표의 일부가 아님
            continue
        r = pymupdf.Rect(d["rect"]) & page.rect
        if r.y1 <= margins[0] + 6 and r.height < 3:  # 헤더 밑줄
            continue
        if r.y0 >= margins[1] - 6 and r.height < 3:
            continue
        if r.is_empty and r.width == 0 and r.height == 0:
            continue
        if r.get_area() > big:  # 배경/페이지 테두리
            continue
        draws.append(r)
    return blocks, imgs, draws


def _region(cap: Blk, side: str, kind: str, blocks: list[Blk], imgs: list, draws: list, page: pymupdf.Page, margins: tuple[float, float] = (0.0, 1e9)) -> pymupdf.Rect | None:
    ph = page.rect.height
    x0, x1 = cap.rect.x0, cap.rect.x1
    bounds = [b for b in blocks if b.kind in ("body", "heading", "caption") and b is not cap]
    others = [b for b in blocks if b.kind == "other" and not b.rotated or b.kind == "other" and b.rotated]
    sel: list[pymupdf.Rect] = []
    for _ in range(4):
        if side == "above":
            top = max([b.rect.y1 for b in bounds if b.rect.y1 <= cap.rect.y0 + 1 and _x_overlaps(b.rect, x0, x1)] + [max(30.0, margins[0] + 2)])
            lo, hi = top + 0.5, cap.rect.y0 - 0.5
        else:
            bottom = min([b.rect.y0 for b in bounds if b.rect.y0 >= cap.rect.y1 - 1 and _x_overlaps(b.rect, x0, x1)] + [min(ph - 30.0, margins[1] - 2)])
            lo, hi = cap.rect.y1 + 0.5, bottom - 0.5
        if hi - lo < 8:
            return None

        def inband(r: pymupdf.Rect) -> bool:
            if r.height < 0.5:  # 높이 0인 가로줄(표의 rule): 겹침 길이가 0이라 별도로 판정
                return lo - 0.5 <= r.y0 <= hi + 0.5
            ov = min(r.y1, hi) - max(r.y0, lo)
            return ov > 0 and ov >= 0.7 * r.height

        g = [r for r in imgs + draws if inband(r) and _x_overlaps(r, x0, x1, 0.05)]
        t = [b.rect for b in others if inband(b.rect) and _x_overlaps(b.rect, x0, x1, 0.05)]
        sel = g + t
        if not sel:
            return None
        u = _union(sel)
        nx0, nx1 = min(x0, u.x0), max(x1, u.x1)
        if abs(nx0 - x0) < 1 and abs(nx1 - x1) < 1:
            break
        x0, x1 = nx0, nx1
    u = _union(sel)
    if kind == "figure":
        gareas = sum(r.get_area() for r in imgs + draws if inband(r) and _x_overlaps(r, x0, x1, 0.05))
        if not any(inband(r) and _x_overlaps(r, x0, x1, 0.05) for r in imgs + draws) or (gareas < 30 and u.get_area() < 400):
            return None
    else:  # table: 글자 줄이 충분히 있어야 함
        nlines = sum(b.lines for b in others if inband(b.rect) and _x_overlaps(b.rect, x0, x1, 0.05))
        if nlines < 3:
            return None
    # 캡션/경계 블록을 침범하지 않도록 세로 범위를 band 로 제한
    u = pymupdf.Rect(u.x0, max(u.y0, lo), u.x1, min(u.y1, hi))
    u = pymupdf.Rect(u.x0 - PAD, max(u.y0 - PAD, lo), u.x1 + PAD, min(u.y1 + PAD, hi)) & page.rect
    return u if u.width > 20 and u.height > 12 else None


def _region_by_rules(cap: Blk, side: str, blocks: list[Blk], draws: list, page: pymupdf.Page, margins: tuple[float, float]) -> pymupdf.Rect | None:
    """긴 문장이 들어간 텍스트 위주 표: 셀 텍스트가 '본문 문단'처럼 보여 일반 경로가 실패한다.
    이때는 캡션에 인접한 표의 가로줄/테두리(드로잉) 군집을 표의 범위로 보고, 그 안의 텍스트를 포함한다."""
    ph = page.rect.height
    x0, x1 = cap.rect.x0, cap.rect.x1
    stop = [b for b in blocks if b.kind in ("heading", "caption") and b is not cap]
    if side == "above":
        lo = max([b.rect.y1 for b in stop if b.rect.y1 <= cap.rect.y0 + 1 and _x_overlaps(b.rect, x0, x1)] + [max(30.0, margins[0] + 2)])
        hi = cap.rect.y0 - 0.5
    else:
        lo = cap.rect.y1 + 0.5
        hi = min([b.rect.y0 for b in stop if b.rect.y0 >= cap.rect.y1 - 1 and _x_overlaps(b.rect, x0, x1)] + [min(ph - 30.0, margins[1] - 2)])
    rules = [r for r in draws if r.y0 >= lo - 1 and r.y1 <= hi + 1 and _x_overlaps(r, x0, x1, 0.3) and r.width >= 0.3 * (x1 - x0)]
    if len(rules) < 2:
        return None
    key = (lambda r: -r.y1) if side == "above" else (lambda r: r.y0)
    rules.sort(key=key)
    edge = cap.rect.y0 if side == "above" else cap.rect.y1
    if abs((rules[0].y1 if side == "above" else rules[0].y0) - edge) > 40:
        return None  # 캡션에 붙어 있지 않으면 이 캡션의 표가 아님
    group = [rules[0]]
    u = _union(group)
    for r in rules[1:]:
        gap = (u.y0 - r.y1) if side == "above" else (r.y0 - u.y1)
        if gap > 150:
            break
        group.append(r)
        u = _union(group)
    inside = [b.rect for b in blocks if b.kind != "caption" and b.rect.y0 >= u.y0 - 1 and b.rect.y1 <= u.y1 + 1 and _x_overlaps(b.rect, u.x0, u.x1, 0.05)]
    u = _union(group + inside)
    u = pymupdf.Rect(u.x0 - PAD, max(u.y0 - PAD, lo), u.x1 + PAD, min(u.y1 + PAD, hi)) & page.rect
    return u if u.height > 20 else None


def _label_of(first_line: str) -> tuple[str, str, str]:
    m = _CAP_RE.match(first_line)
    word = m.group(1).lower()
    kind = "table" if word.startswith("tab") else "figure"
    num = m.group(2).replace(".", "") if re.match(r"[A-Z]\.\d", m.group(2)) else m.group(2)
    return kind, num, f"{'Table' if kind == 'table' else 'Figure'} {m.group(2)}"


def _full_caption(cap: Blk, blocks: list[Blk]) -> str:
    """캡션이 한 줄(예: 'Table 2:')로 끊긴 경우 바로 아래 블록을 이어 붙인다."""
    text = cap.text
    if cap.lines == 1 and len(text) < 40:
        nxt = [b for b in blocks if b.kind == "other" and 0 <= b.rect.y0 - cap.rect.y1 < 4 and abs(b.rect.x0 - cap.rect.x0) < 6]
        if nxt:
            text += " " + nxt[0].text
    return re.sub(r"\s+", " ", text).strip()


def extract(pdf: Path, out_dir: Path, dpi: int = DPI) -> dict:
    """figures.json 구조를 반환하고 PNG를 out_dir에 저장한다."""
    doc = pymupdf.open(pdf)
    body = body_font_size(doc)
    margins = running_margins(doc)
    decor = running_decor(doc)
    page_data = []
    found: list[dict] = []
    # 1) 페이지별 분석 + 캡션별로 양쪽 후보 영역 계산
    for pno in range(doc.page_count):
        page = doc[pno]
        blocks, imgs, draws = _classify_page(page, body, margins, decor)
        page_data.append((blocks, imgs, draws))
        for cap in [b for b in blocks if b.kind == "caption"]:
            kind, num, label = _label_of(cap.text)
            above = _region(cap, "above", kind, blocks, imgs, draws, page, margins)
            below = _region(cap, "below", kind, blocks, imgs, draws, page, margins)
            if kind == "table" and above is None and below is None:
                above = _region_by_rules(cap, "above", blocks, draws, page, margins)
                below = _region_by_rules(cap, "below", blocks, draws, page, margins) if above is None else None
            found.append({"page": pno + 1, "cap": cap, "kind": kind, "num": num, "label": label, "above": above, "below": below})

    # 2) 종류별 캡션 위치 관례를 모호하지 않은 캡션들로 투표 (기본: 그림=캡션 아래, 표=캡션 위)
    convention = {}
    for kind, default in (("figure", "above"), ("table", "below")):
        votes = Counter()
        for f in found:
            if f["kind"] == kind and (f["above"] is None) != (f["below"] is None):
                votes["above" if f["above"] is not None else "below"] += 1
        convention[kind] = votes.most_common(1)[0][0] if votes else default

    # 3) 영역 선택, 중복 라벨 제거, 렌더
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    seen: set[str] = set()
    for f in found:
        fid = f"{f['kind']}-{f['num']}"
        if fid in seen:
            continue
        a, b = f["above"], f["below"]
        side = None
        if a is not None and b is None:
            side = "above"
        elif b is not None and a is None:
            side = "below"
        elif a is not None and b is not None:
            side = convention[f["kind"]]
        seen.add(fid)
        rect = (a if side == "above" else b) if side else None
        item = {
            "id": fid, "label": f["label"], "kind": f["kind"], "page": f["page"],
            "caption": _full_caption(f["cap"], page_data[f["page"] - 1][0]),
            "caption_bbox": [round(x, 1) for x in f["cap"].rect],
            "bbox": [round(x, 1) for x in rect] if rect else None,
            "side": side, "source": "pdf-crop", "auto": True, "hidden": False,
            "file": None, "mentions": [],
        }
        if rect:
            item["file"] = f"{fid}.png"
            render_clip(doc, f["page"], rect, out_dir / item["file"], dpi)
        items.append(item)

    _attach_mentions(doc, items)
    result = {
        "page_sizes": [[round(p.rect.width, 1), round(p.rect.height, 1)] for p in doc],
        "convention": convention,
        "stats": {"captions": len(items), "with_region": sum(1 for i in items if i["bbox"])},
        "items": items,
    }
    doc.close()
    return result


def render_clip(doc: pymupdf.Document, page_no: int, rect: pymupdf.Rect, out: Path, dpi: int = DPI) -> None:
    page = doc[page_no - 1]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), clip=pymupdf.Rect(rect) & page.rect, alpha=False)
    tmp = out.with_suffix(".tmp.png")
    pix.save(tmp)
    tmp.replace(out)


def render_page(pdf: Path, page_no: int, dpi: int = 100) -> bytes:
    with pymupdf.open(pdf) as doc:
        if not 1 <= page_no <= doc.page_count:
            raise IndexError(page_no)
        return doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False).tobytes("png")


def _attach_mentions(doc: pymupdf.Document, items: list[dict], limit: int = 5) -> None:
    """본문에서 'Figure N' / 'Table N' 을 언급한 문장 조각 (설명 생성/링크용)."""
    texts = [re.sub(r"\s+", " ", p.get_text("text")) for p in doc]
    for it in items:
        n = re.escape(it["label"].split(" ", 1)[1])
        pat = re.compile(rf"\b(?:Figure|Fig\.|Table)s?\s*{n}\b(?![.\d]*\d)", re.I if it["kind"] == "figure" else 0)
        if it["kind"] == "table":
            pat = re.compile(rf"\bTables?\s*{n}\b(?!\d)")
        ms = []
        for pno, t in enumerate(texts, start=1):
            for m in pat.finditer(t):
                start = max(0, m.start() - 90)
                snip = t[start : m.end() + 140]
                if snip.lstrip().startswith(it["caption"][:25]) or it["caption"][:40] in snip:
                    continue  # 캡션 자신
                ms.append({"page": pno, "snippet": ("…" if start else "") + snip.strip() + "…"})
                if len(ms) >= limit:
                    break
            if len(ms) >= limit:
                break
        it["mentions"] = ms
