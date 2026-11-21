#!/usr/bin/env python3
"""골든 PDF에 대해 그림/표 추출 결과를 요약하고 컨택트 시트(PNG)를 만든다.
  pip install pillow && python scripts/eval_figures.py <out_dir> <pdf> [<pdf> ...]
사람이 컨택트 시트를 눈으로 보고 판정한다 (recall/precision 은 README 의 기준 참고).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from PIL import Image, ImageDraw  # noqa: E402

from app import figures  # noqa: E402


def sheet(items, d: Path, out: Path):
    thumbs = []
    for it in items:
        if it["file"]:
            im = Image.open(d / it["file"]).convert("RGB")
            im.thumbnail((420, 300))
        else:
            im = Image.new("RGB", (420, 60), (255, 220, 220))
        canvas = Image.new("RGB", (430, im.height + 22), "white")
        canvas.paste(im, (5, 20))
        ImageDraw.Draw(canvas).text((5, 4), f"{it['id']} p{it['page']} {it['side'] or 'NO REGION'}", fill="black")
        thumbs.append(canvas)
    cols = 4
    rows = [thumbs[i : i + cols] for i in range(0, len(thumbs), cols)]
    H = sum(max(t.height for t in r) for r in rows) + 10
    sh = Image.new("RGB", (430 * cols, H), (235, 235, 235))
    y = 0
    for r in rows:
        for i, t in enumerate(r):
            sh.paste(t, (430 * i, y))
        y += max(t.height for t in r)
    sh.save(out)


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for pdf in map(Path, sys.argv[2:]):
        d = out / pdf.stem
        r = figures.extract(pdf, d)
        (d / "figures.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        print(f"== {pdf.stem}: captions={r['stats']['captions']} with_region={r['stats']['with_region']} convention={r['convention']}")
        for it in r["items"]:
            print(f"   {it['id']:12} p{it['page']:<3} {str(it['side']):6} {it['bbox']}  {it['caption'][:60]!r}")
        sheet(r["items"], d, out / f"{pdf.stem}_sheet.png")
