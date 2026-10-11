#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null; then
  echo '请先安装 Python 3.11 或更新版本。'
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements-desktop.txt
exec .venv/bin/python photosieve.py "$@"
