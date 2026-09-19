"""Offline HTTP and tenant boundaries for the production accounting API."""
import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.models import ApiError, AttachmentFile


@pytest.fixture
def api(tmp_path):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'target_url': 'https://vip4-kj.kdzwy.com/accounting/index.html',
        'cookies': [{'name': 'session', 'value': 'dummy', 'domain': 'vip4-kj.kdzwy.com', 'path': '/'}],
        'company_name': '测试公司', 'company_id': '1', 'dbid': '123', 'access_token': 'dummy-token'}))
    KdzwyApi._shared_read_cache.clear()
    return KdzwyApi(AppConfig(path, expected_company='测试公司'))


def response(monkeypatch, payload):
    res = Mock(status=200)
    res.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    manager = Mock()
    manager.__enter__ = Mock(return_value=res)
    manager.__exit__ = Mock(return_value=False)
    call = Mock(return_value=manager)
    monkeypatch.setattr('kdzwy_receipt_uploader.api.urlopen', call)
    return call


@pytest.mark.parametrize('payload', [b'<html>login</html>', [], {'code': 500, 'data': {}}, {'errcode': 1}, {'success': False}, {'ok': False}, {'code': 0, 'data': []}, {'code': 0, 'data': {'id': 0}}])
def test_save_rejects_bad_response_without_retry(api, monkeypatch, payload):
    call = response(monkeypatch, payload)
    with pytest.raises(ApiError):
        api.save_voucher_v1({'vch': {'id': '0'}})
    assert call.call_count == 1


@pytest.mark.parametrize('error', [URLError('offline'), HTTPError('https://example.test', 502, 'bad gateway', {}, io.BytesIO(b'failed')), TimeoutError('timeout')])
def test_save_network_failure_is_never_retried(api, monkeypatch, error):
    call = Mock(side_effect=error)
    monkeypatch.setattr('kdzwy_receipt_uploader.api.urlopen', call)
    with pytest.raises((ApiError, TimeoutError)):
        api.save_voucher_v1({'vch': {'id': '0'}})
    call.assert_called_once()


def test_save_uses_locked_tenant_and_string_id(api, monkeypatch):
    call = response(monkeypatch, {'code': 0, 'data': {'id': 9}})
    assert api.save_voucher_v1({'vch': {'id': '0'}}) == '9'
    request = call.call_args.args[0]
    assert '/jdy-fi/123/gl/v1/voucher/save' in request.full_url
    assert request.get_method() == 'POST'
    assert json.loads(request.data)['vch']['id'] == '0'
    assert request.get_header('App-token') == 'dummy-token'


@pytest.mark.parametrize('remote', [{'DBID': '999', 'companyId': '1'}, {'DBID': '123', 'companyId': '2'}])
def test_remote_tenant_mismatch_rejected(api, monkeypatch, remote):
    response(monkeypatch, {'code': 0, 'data': remote})
    with pytest.raises(ApiError, match='串账'):
        api.get_dynamic_system_params()


def test_missing_session_identity_and_wrong_company_rejected(api, monkeypatch):
    with pytest.raises(ApiError, match='公司不匹配'):
        KdzwyApi(AppConfig(api.config.cookie_file, expected_company='其他公司'))
    api.session_company_id = None
    response(monkeypatch, {'code': 0, 'data': {'DBID': '123'}})
    with pytest.raises(ApiError, match='缺少锁定'):
        api.get_dynamic_system_params()


def test_cache_isolated_between_sessions_and_returns_copy(api, monkeypatch):
    call = response(monkeypatch, {'code': 0, 'data': {'rows': [{'id': 'a'}]}})
    tree = api.get_subject_tree()
    tree['rows'].clear()
    assert api.get_subject_tree()['rows'] == [{'id': 'a'}]
    assert call.call_count == 1
    payload = json.loads(api.config.cookie_file.read_text())
    payload.update(dbid='456', company_id='2')
    api.config.cookie_file.write_text(json.dumps(payload))
    other = KdzwyApi(api.config)
    other.get_subject_tree()
    assert call.call_count == 2
    assert '/456/' in call.call_args.args[0].full_url


def test_upload_and_bind_http_contract(api, monkeypatch, tmp_path):
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'%PDF-1.4 test')
    call = response(monkeypatch, {'errcode': 0, 'data': {'fileId': 'file-1'}})
    api.upload_invoice_pdf_v1(AttachmentFile(pdf, 'source.pdf'))
    request = call.call_args.args[0]
    assert '/123/v1/invoice/discern' in request.full_url
    assert b'%PDF-1.4 test' in request.data
    first_name = request.data.split(b'filename=')[1].split(b'\r\n')[0]
    api.upload_invoice_pdf_v1(AttachmentFile(pdf, 'source.pdf'))
    assert first_name != call.call_args.args[0].data.split(b'filename=')[1].split(b'\r\n')[0]
    api.bind_voucher_files_v1('v1', ['f1'])
    assert json.loads(call.call_args.args[0].data) == {'ids': ['f1'], 'vchIds': ['v1'], 'vchId': 'v1', 'optType': '1', 'notIncrement': True}
    api.get_voucher_v1('v1')
    assert call.call_args.args[0].full_url.endswith('/voucher/v1')


@pytest.mark.parametrize('payload', [None, {}, {'target_url': 'http://example.test', 'cookies': []}, {'target_url': 'https://example.test', 'cookies': []}])
def test_invalid_session_rejected(tmp_path, payload):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(ApiError):
        KdzwyApi(AppConfig(path))
