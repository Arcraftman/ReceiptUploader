"""Reject malformed accounting inputs before any submission."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from kdzwy_receipt_uploader.models import ReceiptError, ApiError
from kdzwy_receipt_uploader.workflow import load_receipt, money, validate_auxiliary_readback


@pytest.fixture
def document(tmp_path):
    (tmp_path / 'a.pdf').write_bytes(b'%PDF-1.4')
    payload = {'schemaVersion': '1.0', 'receiptId': 'test-001', 'voucher': {
        'date': '2026-09-01', 'groupId': 'g', 'summary': 'test', 'userName': 'tester', 'attachments': 1,
        'attachmentFiles': [{'path': 'a.pdf'}],
        'entries': [dict(accountId=str(i), accountNumber=str(i), accountName='科目', dc=dc, amount='1.00')
                    for i, dc in [(1, 1), (2, -1)]]}}
    return tmp_path / 'r.json', payload


@pytest.mark.parametrize('key,value', [('schemaVersion','2'), ('draft',True), ('receiptId','!'), ('voucher',[])])
def test_invalid_root(document, key, value):
    path, payload = document
    payload[key] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ReceiptError):
        load_receipt(path, {})


@pytest.mark.parametrize('key,value', [('date',None), ('date','2026-02-30'), ('groupId',''), ('summary',''), ('userName',''),
    ('attachments',True), ('attachments',-1), ('attachments',2), ('entries',[]), ('entries',[None, {}]),
    ('attachmentFiles','oops'), ('attachmentFiles',[None]), ('attachmentFiles',[{'path':'../outside.pdf'}]),
    ('attachmentFiles',[{'path':'missing.pdf'}]), ('attachmentFiles',[{'path':'a.pdf'}, {'path':'a.pdf'}]),
    ('invoiceCodes',['missing-map'])])
def test_invalid_voucher(document, key, value):
    path, payload = document
    payload['voucher'][key] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ReceiptError):
        load_receipt(path, {})


@pytest.mark.parametrize('key,value', [('lineNo',True), ('lineNo',0), ('accountId',''), ('accountNumber',''),
    ('accountName',''), ('dc',0), ('amount','NaN'), ('amount','Infinity'), ('amount','oops'), ('amount','2.00')])
def test_invalid_entry(document, key, value):
    path, payload = document
    payload['voucher']['entries'][0][key] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ReceiptError):
        load_receipt(path, {})


def test_snapshot_subject_mismatch(document):
    path, payload = document
    path.write_text(json.dumps(payload))
    for snapshot in [{('missing','missing'): {}}, {('1','1'): {'fullName':'different'}}]:
        with pytest.raises(ReceiptError):
            load_receipt(path, snapshot)


def test_empty_file_and_duplicate_line(document):
    path, payload = document
    (path.parent / 'a.pdf').write_bytes(b'')
    path.write_text(json.dumps(payload))
    with pytest.raises(ReceiptError, match='附件为空'):
        load_receipt(path,{})
    payload['voucher']['entries'][1]['lineNo'] = 1
    path.write_text(json.dumps(payload))
    with pytest.raises(ReceiptError, match='重复'):
        load_receipt(path,{})


def test_red_amounts_still_allowed():
    assert money('-12.30', 'amount') == Decimal('-12.30')


def test_auxiliary_missing_rows_and_unknown_class():
    source = [{'auxiliaryExpected': {'itemClass':'未知','id':'1','name':'张三'}}]
    with pytest.raises(ApiError, match='缺少分录'):
        validate_auxiliary_readback(source,{})
    with pytest.raises(ApiError, match='回读不一致'):
        validate_auxiliary_readback(source,{'entries':[{}]})
