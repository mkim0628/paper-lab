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


# ---------------------------------------------------------------- 자동 업데이트 확인
TITLE = "Paper Lab 업데이트"


def git(*args: str, cwd: Path | None = None, timeout: float = 20) -> subprocess.CompletedProcess:
    """git 실행. 자격증명 프롬프트로 멈추지 않도록 터미널 입력을 막는다 (저장된 자격증명 관리자는 그대로 동작)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def check_for_update(root: Path | None = None, fetch_timeout: float = 10) -> dict | None:
    """원격에 새 커밋이 있는지 확인한다. git 저장소가 아니거나, git 이 없거나, 네트워크/인증 문제면 None (조용히 건너뜀).
    반환: {"behind": n, "ahead": m, "dirty": bool, "commits": [제목...], "upstream": "origin/브랜치"}"""
    root = root or ROOT
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    try:
        up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=root)
        if up.returncode != 0 or "/" not in up.stdout.strip():
            return None  # 업스트림이 없는 브랜치
        upstream = up.stdout.strip()
        if git("fetch", "--quiet", upstream.split("/", 1)[0], cwd=root, timeout=fetch_timeout).returncode != 0:
            return None
        behind = int(git("rev-list", "--count", "HEAD..@{u}", cwd=root).stdout.strip() or 0)
        ahead = int(git("rev-list", "--count", "@{u}..HEAD", cwd=root).stdout.strip() or 0)
        commits: list[str] = []
        if behind:
            commits = [c for c in git("log", "--pretty=format:%s", "-n", "8", "HEAD..@{u}", cwd=root).stdout.splitlines() if c.strip()]
        dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=root).stdout.strip())
        return {"behind": behind, "ahead": ahead, "dirty": dirty, "commits": commits, "upstream": upstream}
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def update_message(info: dict) -> str:
    lines = [f"새 업데이트가 {info['behind']}개 있습니다.", ""]
    lines += [f"  • {c[:90]}" for c in info["commits"]]
    if info["behind"] > len(info["commits"]):
        lines.append(f"  … 외 {info['behind'] - len(info['commits'])}개")
    lines += ["", "지금 자동으로 업데이트하고 시작할까요?", "  [예] 업데이트 후 시작   [아니요] 업데이트 없이 시작"]
    if info["dirty"]:
        lines += ["", "※ 수정한 파일이 있습니다. 충돌하면 업데이트를 건너뛰고 현재 버전으로 시작합니다."]
    return "\n".join(lines)


def confirm_dialog(title: str, message: str) -> bool | None:
    """경고 창으로 예/아니요를 묻는다. True=예, False=아니요, None=물어볼 수단이 없음(창도 터미널도 없음)."""
    if os.name == "nt":
        try:
            import ctypes

            # MB_YESNO | MB_ICONWARNING | MB_TOPMOST — 표준 라이브러리만 사용 (tkinter 설치 여부와 무관)
            return ctypes.windll.user32.MessageBoxW(0, message, title, 0x4 | 0x30 | 0x40000) == 6
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            ans = messagebox.askyesno(title, message, icon="warning", parent=root)
            root.destroy()
            return bool(ans)
        except Exception:  # noqa: BLE001  (디스플레이 없음/tkinter 없음)
            pass
    if sys.stdin and sys.stdin.isatty():
        print(f"\n[{title}]\n{message}\n", flush=True)
        return input("업데이트할까요? [Y/n] ").strip().lower() in ("", "y", "yes", "예")
    return None


def apply_update(root: Path | None = None) -> tuple[bool, str]:
    try:
        r = git("pull", "--ff-only", cwd=root or ROOT, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"{type(e).__name__}"
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]


def maybe_update(mode: str, root: Path | None = None, ask=confirm_dialog) -> str:
    """시작 시 업데이트 확인. 결과: 'updated' | 'declined' | 'failed' | 'none' | 'skipped'.
    mode: ask(기본, 경고 창으로 확인) | auto(묻지 않고 업데이트) | off(확인 안 함). 어떤 경우에도 실패하면 현재 버전으로 계속 시작한다."""
    if mode == "off":
        return "skipped"
    info = check_for_update(root)
    if info is None:
        return "skipped"
    if info["behind"] == 0:
        return "none"
    if info["ahead"] > 0:
        say(f"업데이트 {info['behind']}개가 있지만 이 폴더에 로컬 커밋이 있어 자동 업데이트를 건너뜁니다. 직접 `git pull` 하세요.")
        return "skipped"
    if mode != "auto":
        ans = ask(TITLE, update_message(info))
        if ans is None:
            say(f"업데이트 {info['behind']}개가 있습니다 (창을 띄울 수 없어 건너뜀). update.bat 으로 업데이트하세요.")
            return "skipped"
        if not ans:
            say("업데이트 없이 시작합니다.")
            return "declined"
    say("업데이트하는 중…")
    ok, msg = apply_update(root)
    if ok:
        say("✅ 업데이트했습니다. 새 버전으로 시작합니다.")
        return "updated"
    say(f"⚠️ 업데이트하지 못했습니다 (현재 버전으로 시작합니다): {msg}")
    return "failed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true", help="설치/구성만 검증하고 종료")
    ap.add_argument("--reinstall", action="store_true", help="패키지를 다시 설치")
    ap.add_argument("--no-update-check", action="store_true", help="시작 시 업데이트 확인을 하지 않음")
    a = ap.parse_args(argv)

    check_python()
    ensure_env_file()

    # 시작 시 업데이트 확인 (paperlab.env 의 PAPERLAB_UPDATE_CHECK = ask(기본) | auto | off)
    mode = parse_env_file(ENV_FILE).get("PAPERLAB_UPDATE_CHECK", "ask").lower()
    if not (a.check or a.no_update_check or os.environ.get("PAPERLAB_UPDATED")) and maybe_update(mode) == "updated":
        # 런처 자신도 바뀌었을 수 있으므로 새 코드로 다시 시작한다 (무한 반복 방지 플래그)
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])], env={**os.environ, "PAPERLAB_UPDATED": "1"})
        return r.returncode
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
