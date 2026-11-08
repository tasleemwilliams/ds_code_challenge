#!/usr/bin/env bash
# Creates a virtual environment, installs dependencies and runs the whole pipeline.
# No human interaction is needed. Set PYTHON to choose a specific interpreter.
set -euo pipefail

cd "$(dirname "$0")"

find_python() {
  local candidate
  for candidate in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
      echo "$candidate"
      return 0
    fi
  done
  echo "Python 3.9 or newer is required." >&2
  return 1
}

if [ ! -x .venv/bin/python ]; then
  "$(find_python)" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt

exec .venv/bin/python -m cct_pipeline "$@"
