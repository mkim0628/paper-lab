#!/usr/bin/env python3
"""프런트엔드를 빌드하고 소스 해시를 frontend/dist/.src-hash 에 기록한다.
frontend/dist 는 사용자가 Node.js 없이 실행할 수 있도록 저장소에 포함되므로, 프런트엔드를 수정한 뒤에는 반드시 이 스크립트를 실행해 커밋한다.
(backend/tests/test_dist_fresh.py 가 소스와 dist 가 어긋나면 실패한다)
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

FE = Path(__file__).resolve().parents[1] / "frontend"
INPUTS = ["src", "index.html", "package.json", "package-lock.json", "vite.config.js"]


def source_hash() -> str:
    h = hashlib.sha256()
    for name in INPUTS:
        p = FE / name
        files = sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
        for f in files:
            h.update(f.relative_to(FE).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))  # 줄바꿈 차이(Windows)로 해시가 달라지지 않게
    return h.hexdigest()


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("npm 이 필요합니다 (개발자 전용 단계)")
        return 1
    if not (FE / "node_modules").exists():
        subprocess.run([npm, "ci"], cwd=FE, check=True)
    subprocess.run([npm, "run", "build"], cwd=FE, check=True)
    (FE / "dist" / ".src-hash").write_text(source_hash() + "\n")
    print("OK: dist 갱신 + 해시 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main())
