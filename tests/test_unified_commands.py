"""Cross-platform launch contracts, using isolated fake checkouts and no network."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

from kdzwy_receipt_uploader import command_dispatch as dispatch

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('args', [
    ['--help'], ['start', '--help'], ['login', '--help'], ['discover', '--help'],
    ['month', '--help'], ['bank', '--help'], ['verify', '--help'],
    ['confirm-one', '--help'], ['confirm-all', '--help'],
    ['finance', 'serve', '--help'], ['create-company', '--help'],
])
def test_help_is_english_and_works_outside_checkout(args, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/start.py'), *args],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
    assert not any('\u4e00' <= c <= '\u9fff' for c in result.stdout)
    assert not list(tmp_path.iterdir())


def test_invalid_command_cannot_start_login(monkeypatch):
    run = Mock()
    monkeypatch.setattr(dispatch, 'run', run)
    with pytest.raises(SystemExit) as error:
        dispatch.main(['not-a-command'])
    assert error.value.code == 2
    run.assert_not_called()


@pytest.mark.parametrize(('args', 'expected'), [
    (['login', '--headed'], ('login_companies.py', '--mode', 'login_companies', '--headed')),
    (['discover'], ('login_companies.py', '--mode', 'discover_companies')),
    (['month', 'company_1_测试 公司', '2026-09', 'company_2'],
     ('initialize_company_month.py', 'company_1_测试 公司', '2026-09', 'company_2')),
    (['finance', 'serve', '--port', '19000'], ('finance/serve.py', '--port', '19000')),
    (['reset-upload-state', 'company_1', '2026-09', 'bank'],
     ('reset_upload_state.py', 'company_1', '2026-09', '--source', 'bank')),
])
def test_command_dispatch_preserves_arguments(monkeypatch, args, expected):
    run = Mock()
    monkeypatch.setattr(dispatch, 'run', run)
    monkeypatch.setattr(dispatch, 'project_root', lambda: ROOT)
    assert dispatch.main(args) == 0
    run.assert_called_once_with(*expected)


def test_existing_receipt_option_calls_remain_supported(monkeypatch):
    from kdzwy_receipt_uploader import cli
    handler = Mock(return_value=7)
    monkeypatch.setattr(cli, 'main', handler)
    assert dispatch.main(['--input-dir', 'test receipts']) == 7
    handler.assert_called_once_with(['--input-dir', 'test receipts'])


def test_explicit_invalid_project_root_is_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv('KDZWY_PROJECT_ROOT', str(tmp_path))
    with pytest.raises(ValueError, match='Project workspace not found'):
        dispatch.project_root()


@pytest.mark.parametrize('entry', ['linux', 'bat', 'powershell'])
def test_native_launcher_preserves_arguments_and_exit_status(entry, tmp_path):
    if entry == 'linux' and os.name != 'posix':
        pytest.skip('Requires bash')
    if entry in ('bat', 'powershell') and os.name != 'nt':
        pytest.skip('Requires Windows')
    checkout = tmp_path / 'checkout 测试 with spaces'
    for name in ['scripts/start.py', 'scripts/linux/start.sh', 'scripts/win/start.bat',
                 'scripts/win/start.ps1']:
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    package = checkout / 'src/kdzwy_receipt_uploader'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'command_dispatch.py').write_text(
        'import json, sys\ndef main():\n    print(json.dumps(sys.argv[1:], ensure_ascii=False))\n    return 17\n', encoding='utf-8')
    if entry == 'linux':
        command = ['bash', str(checkout / 'scripts/linux/start.sh')]
    elif entry == 'powershell':
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(checkout / 'scripts/win/start.ps1')]
    elif entry == 'bat':
        command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(checkout / 'scripts/win/start.bat')]
    arguments = ['company_1_测试 公司', '2026-09', 'company_2']
    invocation = command + arguments
    if os.name == 'nt' and entry == 'bat':
        # CMD needs an outer quoted command in addition to the quoted BAT path.
        prefix = subprocess.list2cmdline([command[0], '/d', '/s', '/c'])
        invocation = prefix + ' "' + subprocess.list2cmdline([command[-1], *arguments]) + '"'
    result = subprocess.run(invocation, cwd=tmp_path, capture_output=True,
                            env={**os.environ, 'PYTHON_EXE': sys.executable}, timeout=15)
    assert result.returncode == 17, result.stderr
    expected = arguments
    assert json.loads(result.stdout.decode('utf-8')) == expected


def test_login_lock_is_released_before_console(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('login_lock_test', ROOT / 'src/kdzwy_receipt_uploader/commands/login_companies.py')
    login = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(login)
    from kdzwy_receipt_uploader.upload_journal import exclusive_lock, UploadInProgress
    monkeypatch.setattr(login, 'project_root', lambda: tmp_path)
    monkeypatch.setattr(sys, 'argv', ['login_companies.py'])
    path = tmp_path / 'runtime/locks/company_login.lock'

    def main(release, argv=None):
        with pytest.raises(UploadInProgress):
            with exclusive_lock(path):
                pass
        release()
        with exclusive_lock(path):
            pass
        return 0

    monkeypatch.setattr(login, 'main', main)
    assert login.entrypoint() == 0
    with exclusive_lock(path):
        assert login.entrypoint() == 1


def test_platform_launcher_sources_are_ascii():
    for folder in ['scripts/linux', 'scripts/win']:
        for path in (ROOT / folder).iterdir():
            if path.suffix in {'.sh', '.bat', '.ps1'}:
                path.read_bytes().decode('ascii')


def test_obsolete_launchers_are_removed():
    assert not (ROOT / 'commands').exists()
    assert not (ROOT / 'scripts/commands').exists()
