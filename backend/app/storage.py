"""파일 기반 저장소. data/papers/<id>/{meta.json, paper.pdf, review.md, review.history/, notes.md}"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .arxiv import dir_name

STATUSES = ["to-read", "reviewing", "reviewed", "reproducing"]

REVIEW_TEMPLATE = """# {title}

> {authors} · arXiv:{arxiv_id}

## 1. 한눈에 보기
(한 줄 요약 / 핵심 기여 / 성능 / 읽을 가치)

## 2. 문제 정의
(풀려는 문제, 기존 방법의 한계)

## 3. 핵심 아이디어
(직관 → 세 줄 이내의 핵심)

## 4. 방법 상세
(단계별 메커니즘, 수식, 그림)

## 5. 설계 근거 (왜 이렇게 했나)
(대안과의 비교, 선택의 이유)

## 6. 실험과 결과
(설정, 핵심 표/그림, 해석)

## 7. 주장-근거 검증
| # | 저자의 주장 | 근거 | 평가 |
|---|---|---|---|

## 8. 한계와 열린 문제

## 9. 읽은 뒤에
(후속/관련 연구, 구현 시 주의점)

## 10. 용어집
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def paper_dir(arxiv_id: str) -> Path:
    return config.PAPERS_DIR / dir_name(arxiv_id)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def skeleton(meta: dict) -> str:
    return REVIEW_TEMPLATE.format(
        title=meta["title"], authors=", ".join(meta["authors"][:6]) + (" et al." if len(meta["authors"]) > 6 else ""), arxiv_id=meta["arxiv_id"]
    )


def exists(arxiv_id: str) -> bool:
    return (paper_dir(arxiv_id) / "meta.json").exists()


def create(meta: dict) -> dict:
    d = paper_dir(meta["arxiv_id"])
    meta = {**meta, "added_at": _now(), "status": "to-read", "tags": [], "rating": None, "pdf_status": "pending"}
    _write_atomic(d / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    _write_atomic(d / "review.md", "")  # 새 논문의 리뷰는 비어 있다 (빈 템플릿 대신 화면에서 작성/AI 생성을 안내)
    _write_atomic(d / "notes.md", "")
    return meta


def get_meta(arxiv_id: str) -> dict | None:
    p = paper_dir(arxiv_id) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def update_meta(arxiv_id: str, **fields) -> dict:
    meta = get_meta(arxiv_id)
    if meta is None:
        raise KeyError(arxiv_id)
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    meta.update({k: v for k, v in fields.items() if v is not None or k == "rating"})
    meta["updated_local"] = _now()
    _write_atomic(paper_dir(arxiv_id) / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def list_papers() -> list[dict]:
    out = []
    if config.PAPERS_DIR.exists():
        for p in config.PAPERS_DIR.glob("*/meta.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return sorted(out, key=lambda m: m.get("added_at", ""), reverse=True)


def delete(arxiv_id: str) -> None:
    shutil.rmtree(paper_dir(arxiv_id), ignore_errors=True)


def read_text(arxiv_id: str, name: str) -> str:
    p = paper_dir(arxiv_id) / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_text(arxiv_id: str, name: str, content: str) -> bool:
    """저장. review.md는 내용이 바뀌면 이전 버전을 history에 보관. 변경 여부 반환."""
    d = paper_dir(arxiv_id)
    target = d / name
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.read_text(encoding="utf-8") if target.exists() else None
    if old == content:
        return False
    if name == "review.md" and old:
        hist = d / "review.history"
        hist.mkdir(exist_ok=True)
        # 연속 저장(자동저장)이 history를 폭증시키지 않도록 최소 간격 두기
        last = max((f.stat().st_mtime for f in hist.glob("*.md")), default=0)
        if time.time() - last > 60:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            (hist / f"{stamp}.md").write_text(old, encoding="utf-8")
    _write_atomic(target, content)
    return True


def snapshot_review(arxiv_id: str) -> None:
    """현재 review.md를 (시간 간격 무시하고) history에 보관. LLM 생성 등 덮어쓰기 직전에 사용."""
    d = paper_dir(arxiv_id)
    cur = d / "review.md"
    if not cur.exists():
        return
    hist = d / "review.history"
    hist.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (hist / f"{stamp}.md").write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")


def list_history(arxiv_id: str) -> list[str]:
    hist = paper_dir(arxiv_id) / "review.history"
    return sorted((f.stem for f in hist.glob("*.md")), reverse=True) if hist.exists() else []


def read_history(arxiv_id: str, stamp: str) -> str | None:
    if not stamp.replace("T", "").isdigit():
        return None
    p = paper_dir(arxiv_id) / "review.history" / f"{stamp}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None
