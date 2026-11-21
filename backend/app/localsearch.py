"""라이브러리 내 전문 검색 (제목/초록/리뷰/메모/본문). 개인용 규모라 파일 스캔으로 충분."""
from __future__ import annotations

import re

from . import storage


def _snippet(text: str, terms: list[str], width: int = 80) -> str:
    low = text.lower()
    pos = min((i for i in (low.find(t) for t in terms) if i >= 0), default=-1)
    if pos < 0:
        return ""
    s = max(0, pos - width)
    return ("…" if s else "") + re.sub(r"\s+", " ", text[s : pos + width * 2]).strip() + "…"


def search(q: str, limit: int = 30) -> list[dict]:
    terms = [t for t in re.split(r"\s+", q.lower().strip()) if t]
    if not terms:
        return []
    fields = [("title", 10), ("abstract", 3), ("review", 5), ("notes", 5), ("fulltext", 1)]
    results = []
    for meta in storage.list_papers():
        aid = meta["arxiv_id"]
        texts = {
            "title": meta["title"],
            "abstract": meta["abstract"],
            "review": storage.read_text(aid, "review.md"),
            "notes": storage.read_text(aid, "notes.md"),
            "fulltext": storage.read_text(aid, "fulltext.md"),
        }
        low = {k: v.lower() for k, v in texts.items()}
        # 모든 검색어가 (어느 필드든) 한 번은 나와야 매칭 (AND)
        if not all(any(t in low[f] for f, _ in fields) for t in terms):
            continue
        score, where, snip = 0, [], ""
        for f, w in fields:
            hits = sum(low[f].count(t) for t in terms)
            if hits:
                score += w * min(hits, 20)
                where.append(f)
                if not snip and f != "title":
                    snip = _snippet(texts[f], terms)
        results.append({"arxiv_id": aid, "title": meta["title"], "status": meta["status"], "score": score, "matched_in": where, "snippet": snip})
    return sorted(results, key=lambda r: -r["score"])[:limit]
