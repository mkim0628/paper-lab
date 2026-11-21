"""PDF → 페이지 태그가 붙은 텍스트. 리뷰 생성의 근거 인용([p.N])에 쓰인다."""
from __future__ import annotations

from pathlib import Path


def extract(pdf: Path) -> tuple[str, int]:
    import pymupdf

    parts = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            parts.append(f'<page n="{i}">\n{page.get_text("text").strip()}\n</page>')
        n = len(doc)
    return "\n\n".join(parts), n
