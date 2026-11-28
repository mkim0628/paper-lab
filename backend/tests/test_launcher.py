import importlib.util
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("launcher", ROOT / "scripts" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_parse_env_file(tmp_path):
    f = tmp_path / "paperlab.env"
    f.write_text('\ufeff# 주석\nPAPERLAB_S2_API_KEY=abc\nPAPERLAB_OPENALEX_API_KEY=\nQUOTED="x y"\n  SPACED = v=1 \nnoequals\n#COMMENTED=1\nPAPERLAB_DATA=D:\\PaperLabData\n', encoding="utf-8")
    env = launcher.parse_env_file(f)
    assert env == {"PAPERLAB_S2_API_KEY": "abc", "QUOTED": "x y", "SPACED": "v=1", "PAPERLAB_DATA": "D:\\PaperLabData"}  # BOM 허용, 빈 값/주석 무시
    assert launcher.parse_env_file(tmp_path / "missing.env") == {}


def test_env_file_created_from_example_once(tmp_path, monkeypatch):
    ex, envf = tmp_path / "paperlab.env.example", tmp_path / "paperlab.env"
    ex.write_text("PAPERLAB_PORT=9000\n")
    monkeypatch.setattr(launcher, "ENV_EXAMPLE", ex)
    monkeypatch.setattr(launcher, "ENV_FILE", envf)
    launcher.ensure_env_file()
    assert envf.read_text() == "PAPERLAB_PORT=9000\n"
    envf.write_text("PAPERLAB_PORT=1234\n")  # 사용자가 편집한 설정은 덮어쓰지 않는다
    launcher.ensure_env_file()
    assert envf.read_text() == "PAPERLAB_PORT=1234\n"


def test_example_env_file_in_repo_is_valid_and_has_no_active_secrets():
    env = launcher.parse_env_file(ROOT / "paperlab.env.example")
    assert env == {}  # 기본은 모두 비어 있어 설정으로 취급되지 않는다


def test_deps_marker_logic(tmp_path, monkeypatch):
    req, marker = tmp_path / "requirements.txt", tmp_path / "marker"
    req.write_text("a==1\n")
    monkeypatch.setattr(launcher, "REQ", req)
    monkeypatch.setattr(launcher, "MARKER", marker)
    assert launcher.deps_up_to_date() is False  # 설치 기록 없음
    marker.write_text("a==1\n")
    assert launcher.deps_up_to_date() is True
    req.write_text("a==2\n")  # requirements 가 바뀌면 재설치
    assert launcher.deps_up_to_date() is False


def test_ensure_deps_installs_then_skips_and_cleans_marker_on_failure(tmp_path, monkeypatch):
    req, marker = tmp_path / "requirements.txt", tmp_path / "marker"
    req.write_text("a==1\n")
    monkeypatch.setattr(launcher, "REQ", req)
    monkeypatch.setattr(launcher, "MARKER", marker)
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R(0))
    launcher.ensure_deps()
    assert len(calls) == 1 and "-r" in calls[0] and marker.exists()
    launcher.ensure_deps()
    assert len(calls) == 1  # 이미 최신이면 pip 를 다시 돌리지 않는다
    launcher.ensure_deps(force=True)
    assert len(calls) == 2
    marker.unlink()
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **k: R(1))
    with pytest.raises(SystemExit):
        launcher.ensure_deps()
    assert not marker.exists()  # 실패하면 '설치됨' 기록을 남기지 않는다


def test_port_in_use():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    assert launcher.port_in_use(port) is True
    s.close()
    assert launcher.port_in_use(port) is False


def test_python_version_gate(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    with pytest.raises(SystemExit):
        launcher.check_python()


def test_batch_files_have_crlf_and_launcher_call():
    for name in ("start.bat", "update.bat"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # 모든 줄이 CRLF
    bat = (ROOT / "start.bat").read_text(encoding="utf-8")
    assert "scripts\\launcher.py" in bat and "winget install" in bat and "Python.Python.3.12" in bat
    assert "%PY%" in bat and "chcp 65001" in bat
