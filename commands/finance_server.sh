#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${PYTHON_EXE:-python3}" "$PROJECT_ROOT/scripts/finance/serve.py" "$@"
