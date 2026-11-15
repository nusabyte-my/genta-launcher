#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ ! -x .venv/bin/python ]]; then
  /usr/bin/python -m venv --system-site-packages .venv
fi
.venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python install.py
