"""Exercise entry points in a subprocess; no network or user workspace writes."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('script', ['run_pipeline.py', 'initialize_company_month.py', 'verify_bank_receipts.py'])
def test_python_command_help(script, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/commands' / script), '--help'],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8',
                            env={**os.environ, 'PYTHONUTF8': '1'}, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()


def test_native_month_entry_rejects_missing_arguments(tmp_path):
    command = ([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(ROOT / 'commands/initialize_month.bat')]
               if os.name == 'nt' else ['bash', str(ROOT / 'commands/initialize_month.sh')])
    result = subprocess.run(command, cwd=tmp_path, capture_output=True,
                            env={**os.environ, 'PYTHON_EXE': sys.executable}, timeout=20)
    assert result.returncode == 2
