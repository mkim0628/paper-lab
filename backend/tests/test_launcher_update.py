"""시작 시 업데이트 확인: 실제 로컬 git 저장소(원격/개발자/사용자)로 검증."""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("launcher", ROOT / "scripts" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def g(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def repos(tmp_path):
    remote, dev, user = tmp_path / "remote.git", tmp_path / "dev", tmp_path / "user"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(dev)], check=True, capture_output=True)
    (dev / "app.txt").write_text("v1\n")
    g(dev, "add", "."), g(dev, "commit", "-qm", "첫 커밋"), g(dev, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(user)], check=True, capture_output=True)

    def publish(name="app.txt", text="v2\n", msg="새 기능 추가"):
        (dev / name).write_text(text)
        g(dev, "add", "."), g(dev, "commit", "-qm", msg), g(dev, "push", "-q", "origin", "main")

    return types.SimpleNamespace(remote=remote, dev=dev, user=user, publish=publish)


class Ask:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, title, message):
        self.calls.append((title, message))
        return self.answer


def test_no_update_means_no_prompt(repos):
    ask = Ask(True)
    assert launcher.check_for_update(repos.user)["behind"] == 0
    assert launcher.maybe_update("ask", repos.user, ask) == "none" and ask.calls == []


def test_update_available_prompts_with_changes_and_applies_on_yes(repos):
    repos.publish(msg="관련 연구 탭 개선")
    info = launcher.check_for_update(repos.user)
    assert info["behind"] == 1 and info["ahead"] == 0 and info["dirty"] is False
    assert info["commits"] == ["관련 연구 탭 개선"] and info["upstream"] == "origin/main"
    ask = Ask(True)
    assert launcher.maybe_update("ask", repos.user, ask) == "updated"
    title, msg = ask.calls[0]
    assert title == "Paper Lab 업데이트" and "새 업데이트가 1개" in msg and "관련 연구 탭 개선" in msg and "지금 자동으로 업데이트" in msg
    assert (repos.user / "app.txt").read_text() == "v2\n"  # 실제로 반영됨
    assert launcher.check_for_update(repos.user)["behind"] == 0


def test_declining_keeps_current_version(repos):
    repos.publish()
    assert launcher.maybe_update("ask", repos.user, Ask(False)) == "declined"
    assert (repos.user / "app.txt").read_text() == "v1\n"


def test_modes_off_and_auto(repos):
    repos.publish()
    ask = Ask(False)
    assert launcher.maybe_update("off", repos.user, ask) == "skipped" and ask.calls == []
    assert (repos.user / "app.txt").read_text() == "v1\n"
    assert launcher.maybe_update("auto", repos.user, ask) == "updated" and ask.calls == []  # 묻지 않고 업데이트
    assert (repos.user / "app.txt").read_text() == "v2\n"


def test_cannot_ask_skips_safely(repos):
    repos.publish()
    assert launcher.maybe_update("ask", repos.user, Ask(None)) == "skipped"
    assert (repos.user / "app.txt").read_text() == "v1\n"


def test_not_a_git_repo_or_no_upstream_or_offline_is_silent(repos, tmp_path):
    assert launcher.check_for_update(tmp_path / "plain") is None  # .git 없음 (ZIP 으로 받은 경우)
    (tmp_path / "plain").mkdir()
    assert launcher.maybe_update("ask", tmp_path / "plain", Ask(True)) == "skipped"
    g(repos.user, "checkout", "-q", "-b", "local-only")  # 업스트림 없는 브랜치
    assert launcher.check_for_update(repos.user) is None
    g(repos.user, "checkout", "-q", "main")
    repos.publish()
    repos.remote.rename(repos.remote.parent / "gone.git")  # 네트워크/원격 접근 불가
    ask = Ask(True)
    assert launcher.maybe_update("ask", repos.user, ask) == "skipped" and ask.calls == []


def test_local_commits_block_auto_update(repos):
    (repos.user / "mine.txt").write_text("x")
    g(repos.user, "add", "."), g(repos.user, "commit", "-qm", "내 변경")
    repos.publish()
    ask = Ask(True)
    assert launcher.maybe_update("ask", repos.user, ask) == "skipped" and ask.calls == []  # fast-forward 불가 → 묻지도 않음
    assert (repos.user / "app.txt").read_text() == "v1\n"


def test_dirty_tree_conflict_fails_gracefully_but_nonconflicting_dirty_is_ok(repos):
    (repos.user / "app.txt").write_text("내가 수정함\n")
    repos.publish(name="app.txt", text="v2\n")  # 같은 파일을 서버도 수정 → 충돌
    ask = Ask(True)
    assert launcher.maybe_update("ask", repos.user, ask) == "failed"
    assert "수정한 파일이 있습니다" in ask.calls[0][1]  # 경고에 표시됨
    assert (repos.user / "app.txt").read_text() == "내가 수정함\n"  # 사용자의 변경은 보존
    g(repos.user, "checkout", "--", "app.txt")
    (repos.user / "app.txt").write_text("로컬 수정\n")
    g(repos.user, "stash", "-q")
    (repos.user / "other.txt").write_text("o")  # 추적되지 않는 파일은 dirty 로 보지 않음
    assert launcher.check_for_update(repos.user)["dirty"] is False


def test_message_truncates_long_change_lists(repos):
    for i in range(10):
        repos.publish(text=f"v{i}\n", msg=f"변경 {i}")
    msg = launcher.update_message(launcher.check_for_update(repos.user))
    assert "새 업데이트가 10개" in msg and "외 2개" in msg and msg.count("•") == 8


def test_main_restarts_with_new_code_after_update_and_does_not_loop(monkeypatch):
    runs = []
    monkeypatch.setattr(launcher, "maybe_update", lambda mode: "updated")
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **k: runs.append((cmd, k)) or types.SimpleNamespace(returncode=7))
    monkeypatch.delenv("PAPERLAB_UPDATED", raising=False)
    assert launcher.main(["--no-browser"]) == 7
    cmd, kw = runs[0]
    assert cmd[1].endswith("launcher.py") and "--no-browser" in cmd and kw["env"]["PAPERLAB_UPDATED"] == "1"  # 재시작 + 반복 방지 플래그

    # 재시작된 프로세스(PAPERLAB_UPDATED=1)와 --check / --no-update-check 는 확인 자체를 건너뛴다
    def must_not_run(mode):
        raise AssertionError("업데이트 확인이 실행되면 안 됨")

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop

    monkeypatch.setattr(launcher, "maybe_update", must_not_run)
    monkeypatch.setattr(launcher, "ensure_venv", stop)
    monkeypatch.setenv("PAPERLAB_UPDATED", "1")
    with pytest.raises(Stop):
        launcher.main([])
    monkeypatch.delenv("PAPERLAB_UPDATED")
    for flag in (["--check"], ["--no-update-check"]):
        with pytest.raises(Stop):
            launcher.main(flag)


def test_confirm_dialog_windows_uses_native_warning_box(monkeypatch):
    seen = {}

    def box(hwnd, msg, title, flags):
        seen.update(msg=msg, title=title, flags=flags)
        return seen.get("ret", 6)

    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(MessageBoxW=box)))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(launcher.os, "name", "nt")
    assert launcher.confirm_dialog("제목", "본문") is True
    assert seen["flags"] == 0x4 | 0x30 | 0x40000 and seen["title"] == "제목"  # 예/아니요 + 경고 아이콘 + 항상 위
    seen["ret"] = 7
    assert launcher.confirm_dialog("제목", "본문") is False  # '아니요'


def test_confirm_dialog_without_gui_or_terminal_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinter", None)  # 창을 띄울 수 없는 환경 (개발 PC 에서 실제 창이 뜨지 않게 차단)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert launcher.confirm_dialog("t", "m") is None
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert launcher.confirm_dialog("t", "m") is False
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert launcher.confirm_dialog("t", "m") is True  # 엔터 = 예
