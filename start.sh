#!/usr/bin/env sh
# PUBG Tracker launcher for Linux and macOS.
# Usage: ./start.sh [--host 0.0.0.0] [--port 8000] [--data-dir /path/to/storage] [--no-browser]
set -eu
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3.10+ is required (https://www.python.org/downloads/)" >&2
    exit 1
fi

# First run without dependencies: install them into a local virtual environment
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
    echo "Installing dependencies into .venv ..."
    "$PY" -m venv .venv
    PY=.venv/bin/python
    "$PY" -m pip install --quiet -r requirements.txt
fi

exec "$PY" app.py "$@"
