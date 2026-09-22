from unittest.mock import Mock
import pytest
from kdzwy_receipt_uploader.finance import service_lifecycle as lifecycle
from kdzwy_receipt_uploader.finance.serve import FinanceHTTPServer
from http.server import BaseHTTPRequestHandler


def setup(monkeypatch, tmp_path, child):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(lifecycle.time, 'sleep', lambda _: None)
    retired = Mock()
    monkeypatch.setattr(lifecycle, 'retire_services', retired)
    launch = Mock(return_value=child)
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', launch)
    return retired, launch


def test_healthy_service_is_reused(tmp_path, monkeypatch):
    retired, launch = setup(monkeypatch, tmp_path, Mock())
    lifecycle.ensure_service(tmp_path, lambda _: True)
    retired.assert_not_called()
    launch.assert_not_called()


def test_replacement_starts_and_keeps_healthy_child(tmp_path, monkeypatch):
    child = Mock()
    child.poll.return_value = None
    retired, launch = setup(monkeypatch, tmp_path, child)
    lifecycle.ensure_service(tmp_path, Mock(side_effect=[False, False, True]))
    retired.assert_called_once()
    assert launch.call_args.kwargs['stderr'] is launch.call_args.kwargs['stdout']
    child.terminate.assert_not_called()


def test_failed_child_exits_without_wait_or_orphan(tmp_path, monkeypatch):
    child = Mock(returncode=1)
    child.poll.return_value = 1
    setup(monkeypatch, tmp_path, child)
    with pytest.raises(RuntimeError, match='启动日志'):
        lifecycle.ensure_service(tmp_path, lambda _: False)
    child.terminate.assert_not_called()


def test_timeout_terminates_only_launched_child(tmp_path, monkeypatch):
    child = Mock()
    child.poll.return_value = None
    setup(monkeypatch, tmp_path, child)
    with pytest.raises(RuntimeError, match='超时'):
        lifecycle.ensure_service(tmp_path, lambda _: False)
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=5)


def test_port_cannot_be_bound_twice():
    first = FinanceHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
    try:
        with pytest.raises(OSError):
            FinanceHTTPServer(first.server_address, BaseHTTPRequestHandler)
    finally:
        first.server_close()
