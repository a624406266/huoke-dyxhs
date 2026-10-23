#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PORT="${PORT:-8765}"

if [[ -z "${PYTHON_BIN:-}" && -x "$SCRIPT_DIR/.runtime/.venv/bin/python" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.runtime/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3 not found. Set PYTHON_BIN to a Python 3 executable." >&2
  exit 1
fi

args=(scripts/launch_local.py --port "$PORT")
if [[ "${NO_OPEN:-}" == "1" ]]; then
  args+=(--no-open)
fi

echo "Starting local open lead workbench on http://127.0.0.1:${PORT}/index.html"
"$PYTHON_BIN" "${args[@]}"
