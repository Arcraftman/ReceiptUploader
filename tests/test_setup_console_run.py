from unittest.mock import Mock
import subprocess
import pytest
from kdzwy_receipt_uploader.commands import login_companies as login


@pytest.mark.parametrize('selector', ['company_17867515', '17867515'])
def test_console_run_uses_dataset_config_and_returns_to_prompt(monkeypatch, selector, capsys):
    rows = [{'key': 'company_17867515', 'company_id': '17867515', 'name': '上海微誉信息技术有限公司'}]
    commands = iter([f'run {selector} 2026-08', 'help', 'quit'])
    monkeypatch.setattr('builtins.input', lambda _: next(commands))
    launch = Mock()
    monkeypatch.setattr(login.subprocess, 'run', launch)
    login.console(rows)
    args = launch.call_args.args[0]
    assert args[2:] == ['kdzwy_receipt_uploader.command_dispatch', 'run_company',
                        'company_17867515_上海微誉信息技术有限公司', '2026-08']
    assert launch.call_args.kwargs['check'] is True
    assert 'run DATASET YYYY-MM' in capsys.readouterr().out


def test_console_run_failure_keeps_console_available(monkeypatch, capsys):
    commands = iter(['run company_1 2026-08', 'list', 'quit'])
    monkeypatch.setattr('builtins.input', lambda _: next(commands))
    monkeypatch.setattr(login.subprocess, 'run', Mock(side_effect=subprocess.CalledProcessError(2, 'run')))
    login.console([{'key': 'company_1', 'company_id': '1', 'name': '测试公司'}])
    assert 'company_1 测试公司' in capsys.readouterr().out


def test_run_dispatch_authorizes_configured_stages_without_overriding_them(tmp_path, monkeypatch):
    from kdzwy_receipt_uploader import command_dispatch as dispatch
    config = tmp_path / 'config/companies/company_1_测试公司.json'
    config.parent.mkdir(parents=True)
    config.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(dispatch, 'project_root', lambda: tmp_path)
    launch = Mock()
    monkeypatch.setattr(dispatch, 'run', launch)
    assert dispatch.dispatch(['run', 'company_1_测试公司', '2026-08']) == 0
    calls = launch.call_args_list
    assert [c.args[0] for c in calls] == ['prepare_company_workspace.py', 'login_companies.py', 'run_companies.py']
    assert '--allow-confirm' in calls[-1].args
    assert '--stage' not in calls[-1].args
    assert '--source' not in calls[-1].args
