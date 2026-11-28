"""저장소에 포함된 frontend/dist 가 프런트엔드 소스와 일치하는지 (사용자는 Node 없이 dist 만 사용한다)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_committed_dist_matches_frontend_sources():
    marker = ROOT / "frontend" / "dist" / ".src-hash"
    if not marker.exists():
        pytest.skip("frontend/dist 가 없음 (개발 환경)")
    build = _load("build_frontend", ROOT / "scripts" / "build_frontend.py")
    assert marker.read_text().strip() == build.source_hash(), (
        "frontend 소스가 바뀌었는데 dist 가 갱신되지 않았습니다. `python scripts/build_frontend.py` 를 실행하고 frontend/dist 를 커밋하세요."
    )
