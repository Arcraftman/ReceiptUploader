"""One portable offline quality command. Run after `uv sync --locked --extra dev`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    commands = [
        ['-m', 'ruff', 'check', 'src', 'scripts'],
        ['-m', 'mypy'],
        ['-m', 'coverage', 'run', '-m', 'pytest', '-q'],
        ['-m', 'coverage', 'json'],
        ['-m', 'coverage', 'html', '--directory', 'outputs/quality/coverage'],
        ['scripts/maintenance/check_coverage.py'],
        ['-m', 'build', '--no-isolation', '--outdir', 'outputs/quality/dist'],
    ]
    for arguments in commands:
        print('Running: python ' + ' '.join(arguments), flush=True)
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
