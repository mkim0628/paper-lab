#!/usr/bin/env bash
# Linux / macOS: Python 3.10+ 만 있으면 됩니다. 나머지(가상환경, 패키지, 설정)는 런처가 처리합니다.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)" || { echo "[Paper Lab] Python 3.10 이상이 필요합니다."; exit 1; }
exec "$PY" scripts/launcher.py "$@"
