#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$APP_ROOT/xhs-ops-demo-skill"

if [[ ! -d "$SKILL_ROOT" ]]; then
  echo "Missing xhs-ops-demo-skill directory under $APP_ROOT" >&2
  exit 1
fi

cd "$SKILL_ROOT"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8765}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -x "$APP_ROOT/.venv/bin/python" ]]; then
  python_bin="$APP_ROOT/.venv/bin/python"
elif [[ -x "$SKILL_ROOT/.venv/bin/python" ]]; then
  python_bin="$SKILL_ROOT/.venv/bin/python"
else
  python_bin="$(command -v python3 || true)"
fi

if [[ -z "$python_bin" ]]; then
  echo "Python 3 not found. Set PYTHON_BIN or create .venv." >&2
  exit 1
fi

args=(scripts/launch_local.py --host "$HOST" --port "$PORT")
if [[ "${NO_OPEN:-}" == "1" ]]; then
  args+=(--no-open)
fi
if [[ "${WITH_PLAYWRIGHT:-}" == "1" ]]; then
  args+=(--with-playwright)
fi

echo "Starting local open lead workbench on http://${HOST}:${PORT}/index.html"
exec "$python_bin" "${args[@]}"
