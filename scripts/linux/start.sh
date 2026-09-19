#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${PYTHON_EXE:-}" ]]; then
    python_exe="$PYTHON_EXE"
elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    python_exe="$PROJECT_ROOT/.venv/bin/python"
elif [[ -x "$PROJECT_ROOT/.auto/bin/python" ]]; then
    python_exe="$PROJECT_ROOT/.auto/bin/python"
else
    python_exe=python3
fi
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
exec "$python_exe" "$PROJECT_ROOT/scripts/start.py" "$@"
