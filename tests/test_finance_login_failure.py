import importlib.util
from pathlib import Path
from unittest.mock import Mock

from kdzwy_receipt_uploader.upload_journal import exclusive_lock
import pytest
from kdzwy_receipt_uploader.commands.login_companies import login_account


def test_failed_login_releases_lock_and_exits_without_enter_prompt(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('finance_entry', Path(__file__).parents[1] / 'scripts/finance/frozen_entry.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    inputs = Mock(return_value='test-user')
    monkeypatch.setattr('builtins.input', inputs)
    monkeypatch.setattr(entry.getpass, 'getpass', lambda _: 'test-password')
    monkeypatch.setattr(entry.time, 'sleep', lambda _: None)
    monkeypatch.setattr(entry, 'login_account', Mock(side_effect=RuntimeError('账号或密码错误')))
    launch = Mock()
    monkeypatch.setattr(entry.subprocess, 'Popen', launch)
    for _ in range(2):
        assert entry.login(tmp_path) == 1
        with exclusive_lock(tmp_path / 'runtime/locks/company_login.lock'):
            pass
    assert inputs.call_count == 2
    launch.assert_not_called()


def test_credential_failure_closes_owned_browser(monkeypatch, tmp_path):
    import playwright.sync_api
    import kdzwy_receipt_uploader.commands.login_companies as login_module
    from unittest.mock import MagicMock
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', lambda: manager)
    monkeypatch.setattr(login_module, 'project_root', lambda: tmp_path)
    monkeypatch.setattr(login_module, 'wait_authenticated_context', Mock(side_effect=RuntimeError('账号或密码错误')))
    with pytest.raises(RuntimeError, match='账号或密码错误'):
        login_account({'key':'test', 'username':'test', 'password':'test'}, headed=True, reuse=False)
    browser.close.assert_called_once()
    assert not (tmp_path / 'http_sessions/accounts/test/browser.session.json').exists()
