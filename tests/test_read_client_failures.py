import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.integrations.read_client import CheckFailure, ReadClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(KdzwyApi, '_load_session', lambda _: ('https://vip4-kj.kdzwy.com', 'dummy', '123', 'token', '公司', '1'))
    return ReadClient(tmp_path / 'session', '1', '公司')


@pytest.mark.parametrize('raw', [b'invalid', b'[]', b'x' * (10 * 1024 * 1024 + 1), b'{"code":500,"data":{}}'])
def test_bad_response(client, raw):
    response = Mock(status=200)
    response.read.return_value = raw
    client.opener = Mock()
    client.opener.open.return_value.__enter__ = Mock(return_value=response)
    client.opener.open.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(CheckFailure):
        client.request('GET', '/jdy-fi/123/gl/v1/voucher/list')


@pytest.mark.parametrize('failure', [URLError('offline'), TimeoutError(), OSError(), HTTPError('url', 302, 'redirect', {}, None)])
def test_network_failure(client, failure):
    client.opener = Mock()
    client.opener.open.side_effect = failure
    with pytest.raises(CheckFailure):
        client.request('GET', '/jdy-fi/123/gl/v1/voucher/list')
    client.opener.open.assert_called_once()


def test_success_keeps_only_safe_response_metadata(client):
    response = Mock(status=200)
    response.read.return_value = json.dumps({'code': '0', 'data': {'rows': []}}).encode()
    client.opener = Mock()
    client.opener.open.return_value.__enter__ = Mock(return_value=response)
    client.opener.open.return_value.__exit__ = Mock(return_value=False)
    assert client.request('POST', '/jdy-fi-rpt/123/v1/gl/general/query-total', body={}) == {'rows': []}
    assert client.last_response == {'http_status': 200, 'business_status': {'code': '0'}}


@pytest.mark.parametrize('origin,name,cid', [('https://other.test','公司','1'), ('https://vip4-kj.kdzwy.com','其他','1'), ('https://vip4-kj.kdzwy.com','公司','2')])
def test_bad_session_identity(monkeypatch, tmp_path, origin, name, cid):
    monkeypatch.setattr(KdzwyApi, '_load_session', lambda _: (origin, 'dummy', '123', 'token', name, cid))
    with pytest.raises(CheckFailure):
        ReadClient(tmp_path/'s', '1', '公司')
