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

# A venv puts its interpreter under bin/ on Linux and macOS and under Scripts/ on
# Windows (Git Bash). Use whichever exists after creating it.
venv_python() {
  if [ -x .venv/bin/python ]; then
    echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then
    echo .venv/Scripts/python.exe
  else
    return 1
  fi
}

if ! venv_python >/dev/null; then
  "$(find_python)" -m venv .venv
fi
PY="$(venv_python)"
"$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

exec "$PY" -m cct_pipeline "$@"
