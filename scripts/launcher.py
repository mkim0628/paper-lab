#!/usr/bin/env python3
"""Paper Lab 런처: 가상환경 생성 → 의존성 설치 → 설정 로드 → 서버 실행 → 브라우저 열기.

표준 라이브러리만 사용하므로 시스템 파이썬(3.10+) 하나만 있으면 된다. Windows(start.bat)와 Linux/macOS(start.sh) 공용.
  python scripts/launcher.py              # 설치(필요 시) 후 실행
  python scripts/launcher.py --no-browser # 브라우저를 열지 않음
  python scripts/launcher.py --check      # 설치/구성만 검증하고 종료 (서버를 켜지 않음)
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
REQ = ROOT / "backend" / "requirements.txt"
MARKER = VENV / ".requirements.installed"
ENV_FILE = ROOT / "paperlab.env"
ENV_EXAMPLE = ROOT / "paperlab.env.example"
DIST = ROOT / "frontend" / "dist" / "index.html"
MIN_PY = (3, 10)


def say(msg: str) -> None:
    print(f"[Paper Lab] {msg}", flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"\n[Paper Lab] ❌ {msg}\n", flush=True)
    sys.exit(code)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check_python() -> None:
    if sys.version_info < MIN_PY:
        die(f"Python {MIN_PY[0]}.{MIN_PY[1]} 이상이 필요합니다 (현재 {sys.version.split()[0]}). https://www.python.org/downloads/ 에서 설치하세요.")


def ensure_venv() -> None:
    if venv_python().exists():
        return
    say("가상환경(.venv)을 만드는 중…")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0 or not venv_python().exists():
        shutil.rmtree(VENV, ignore_errors=True)
        die("가상환경 생성에 실패했습니다. (Linux 에서는 `sudo apt install python3-venv` 가 필요할 수 있습니다)")


def deps_up_to_date() -> bool:
    return MARKER.exists() and MARKER.read_text(encoding="utf-8") == REQ.read_text(encoding="utf-8")


def ensure_deps(force: bool = False) -> None:
    if not force and deps_up_to_date():
        return
    say("필요한 패키지를 설치하는 중입니다. 처음 한 번만 1~3분 걸립니다…")
    base = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check"]
    r = subprocess.run(base + ["-r", str(REQ)])
    if r.returncode != 0:
        MARKER.unlink(missing_ok=True)
        die("패키지 설치에 실패했습니다. 위 오류의 마지막 10줄을 알려 주세요.\n"
            "  - 회사/학교망이면 프록시 설정이 필요할 수 있습니다: paperlab.env 에 HTTPS_PROXY=http://주소:포트 를 추가하세요.")
    MARKER.write_text(REQ.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_frontend() -> None:
    if DIST.exists():
        return
    # 배포본에는 빌드 결과(frontend/dist)가 포함되어 있다. 없으면(개발 중 clone 등) Node 로 빌드를 시도한다.
    npm = shutil.which("npm")
    if not npm:
        die("frontend/dist 가 없고 Node.js(npm)도 없어 화면을 만들 수 없습니다.\n  최신 저장소를 다시 받거나 https://nodejs.org 에서 Node.js LTS 를 설치한 뒤 다시 실행하세요.")
    say("프런트엔드를 빌드하는 중… (Node.js)")
    fe = ROOT / "frontend"
    for cmd in ([npm, "ci"], [npm, "run", "build"]):
        if subprocess.run(cmd, cwd=fe, shell=(os.name == "nt")).returncode != 0:
            die("프런트엔드 빌드에 실패했습니다.")


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 형식. '#' 주석, 빈 값(=미설정), 따옴표 허용."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return out


def ensure_env_file() -> None:
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        say(f"설정 파일을 만들었습니다: {ENV_FILE.name}  (API 키 등은 선택 사항이며, 필요할 때 메모장으로 편집하세요)")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_ready(port: int, timeout: float = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/papers", timeout=2).read()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true", help="설치/구성만 검증하고 종료")
    ap.add_argument("--reinstall", action="store_true", help="패키지를 다시 설치")
    a = ap.parse_args(argv)

    check_python()
    ensure_env_file()
    ensure_venv()
    ensure_deps(force=a.reinstall)
    ensure_frontend()

    env = {**os.environ, "PYTHONUTF8": "1", **parse_env_file(ENV_FILE)}
    port = int(env.get("PAPERLAB_PORT", "8000"))
    py = str(venv_python())

    # 설치 검증: 앱이 import 되는지
    r = subprocess.run([py, "-c", "import app.main"], cwd=ROOT / "backend", env=env, capture_output=True, text=True)
    if r.returncode != 0:
        die("앱을 불러오지 못했습니다:\n" + r.stderr[-1500:] + "\n(패키지가 깨졌다면 `start.bat --reinstall` 로 다시 설치해 보세요)")
    if a.check:
        say("✅ 설치와 구성이 정상입니다.")
        return 0

    url = f"http://localhost:{port}"
    if port_in_use(port):
        say(f"포트 {port} 가 이미 사용 중입니다. 이미 실행 중이면 브라우저만 엽니다: {url}")
        if not a.no_browser:
            webbrowser.open(url)
        return 0

    keys = [k for k in ("PAPERLAB_S2_API_KEY", "PAPERLAB_OPENALEX_API_KEY", "PAPERLAB_GITHUB_TOKEN") if env.get(k)]
    say(f"데이터 폴더: {env.get('PAPERLAB_DATA', ROOT / 'data')}")
    say("외부 API 키: " + (", ".join(keys) if keys else "없음 (선택 사항 — 관련 연구/코드 탐색이 느리거나 일부 제한될 수 있음)"))
    say(f"서버를 시작합니다: {url}   (종료: 이 창에서 Ctrl+C)")
    proc = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT / "backend", env=env)
    try:
        if wait_ready(port):
            say(f"✅ 준비되었습니다. 브라우저에서 {url} 을 여세요.")
            if not a.no_browser:
                webbrowser.open(url)
        else:
            say("서버가 40초 안에 응답하지 않았습니다. 위 로그를 확인하세요.")
        return proc.wait()
    except KeyboardInterrupt:
        say("종료합니다…")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    sys.exit(main())
