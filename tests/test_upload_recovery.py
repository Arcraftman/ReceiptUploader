"""Fault injection at remote writes and local durability boundaries, without a live API."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from kdzwy_receipt_uploader import cli
from kdzwy_receipt_uploader.models import ApiError
from kdzwy_receipt_uploader.paths import ProjectPaths
from kdzwy_receipt_uploader.upload_journal import UploadJournal, UploadRecoveryRequired, exclusive_lock
from kdzwy_receipt_uploader.workflow import load_receipt, process_one


@pytest.fixture
def upload(tmp_path, monkeypatch):
    monkeypatch.setattr('kdzwy_receipt_uploader.workflow.time.sleep', lambda _: None)
    pdf = tmp_path / 'invoice.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    payload = {'schemaVersion': '1.0', 'receiptId': 'receipt-001', 'source': 'bank', 'voucher': {
        'date': '2026-09-01', 'groupId': 'g1', 'summary': '测试', 'userName': 'tester',
        'attachments': 1, 'attachmentFiles': [{'path': 'invoice.pdf'}],
        'entries': [dict(accountId=str(i), accountNumber=number, accountName=name, dc=dc, amount='100.00')
                    for i, number, name, dc in [(1, '560106', '差旅费', 1), (2, '100203', '银行', -1)]]}}
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    receipt = load_receipt(path, {})
    api = Mock(dbid='123', session_company_id='1', accounting_origin='https://example.test')
    api.get_dynamic_system_params.return_value = {'DBID': '123'}
    api.get_current_user_context.return_value = {'userName': 'tester'}
    api.upload_invoice_pdf_v1.return_value = {'data': [{'fileId': 'file-1', 'uploadStatus': True}]}
    api.get_voucher_number.return_value = {'vchNum': 12, 'year': '2026', 'period': '9'}
    api.save_voucher_v1.return_value = 'voucher-12'
    api.get_voucher_v1.return_value = {'id': 'voucher-12', 'entries': [], 'attachments': 1, 'usedAttachments': 1}
    journal = UploadJournal(tmp_path / 'journal', 'tenant-1', receipt.receipt_id)
    return path, receipt, api, journal


def reopen(journal, receipt):
    new = UploadJournal(journal.path.parent, 'tenant-1', receipt.receipt_id)
    return new


def test_verified_receipt_is_not_submitted_again(upload):
    _, receipt, api, journal = upload
    first = process_one(receipt, api, journal)
    second = process_one(receipt, api, reopen(journal, receipt))
    assert first == second and first['voucherId'] == 'voucher-12'
    api.upload_invoice_pdf_v1.assert_called_once()
    api.save_voucher_v1.assert_called_once()
    api.bind_voucher_files_v1.assert_called_once()


@pytest.mark.parametrize('failure', [TimeoutError('timeout'), KeyboardInterrupt(), SystemExit(7)])
def test_uncertain_save_is_never_repeated(upload, failure):
    _, receipt, api, journal = upload
    api.save_voucher_v1.side_effect = failure
    with pytest.raises(type(failure)):
        process_one(receipt, api, journal)
    with pytest.raises(UploadRecoveryRequired, match='保存结果不明'):
        process_one(receipt, api, reopen(journal, receipt))
    api.save_voucher_v1.assert_called_once()
    assert json.loads(journal.path.read_text())['phase'] == 'saving'


def test_saved_voucher_readback_timeout_resumes_without_save_or_upload(upload):
    _, receipt, api, journal = upload
    good = api.get_voucher_v1.return_value
    api.get_voucher_v1.side_effect = [TimeoutError(), good, good]
    with pytest.raises(TimeoutError):
        process_one(receipt, api, journal)
    result = process_one(receipt, api, reopen(journal, receipt))
    assert result['status'] == 'submitted_and_verified'
    api.save_voucher_v1.assert_called_once()
    api.upload_invoice_pdf_v1.assert_called_once()


@pytest.mark.parametrize('used', [0, 1])
def test_uncertain_bind_is_only_read_back_never_repeated(upload, used):
    _, receipt, api, journal = upload
    api.bind_voucher_files_v1.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        process_one(receipt, api, journal)
    api.get_voucher_v1.return_value['usedAttachments'] = used
    if used:
        assert process_one(receipt, api, reopen(journal, receipt))['attachmentStatus'] == 'uploaded_linked_and_verified'
    else:
        with pytest.raises(UploadRecoveryRequired, match='绑定结果不明'):
            process_one(receipt, api, reopen(journal, receipt))
    api.save_voucher_v1.assert_called_once()
    api.bind_voucher_files_v1.assert_called_once()


def test_attachment_is_reused_when_number_request_fails(upload):
    _, receipt, api, journal = upload
    number = api.get_voucher_number.return_value
    api.get_voucher_number.side_effect = [ApiError('offline'), number]
    with pytest.raises(ApiError):
        process_one(receipt, api, journal)
    process_one(receipt, api, reopen(journal, receipt))
    api.upload_invoice_pdf_v1.assert_called_once()
    api.save_voucher_v1.assert_called_once()


@pytest.mark.parametrize('change', ['voucher', 'pdf'])
def test_changed_content_cannot_reuse_prior_submission(upload, change):
    path, receipt, api, journal = upload
    process_one(receipt, api, journal)
    if change == 'pdf':
        (path.parent / 'invoice.pdf').write_bytes(b'%PDF-1.4 changed')
    else:
        receipt.voucher['summary'] = 'changed'
    with pytest.raises(UploadRecoveryRequired, match='内容已变化'):
        process_one(receipt, api, reopen(journal, receipt))
    api.save_voucher_v1.assert_called_once()


@pytest.mark.parametrize('when', ['saving', 'saved', 'verified'])
def test_disk_failure_prevents_unsafe_save_retry(upload, monkeypatch, when):
    _, receipt, api, journal = upload
    original = journal.write

    def fail():
        if journal.state.phase == when:
            raise OSError('disk full')
        original()

    monkeypatch.setattr(journal, 'write', fail)
    with pytest.raises(OSError):
        process_one(receipt, api, journal)
    if when == 'saving':
        api.save_voucher_v1.assert_not_called()
        process_one(receipt, api, reopen(journal, receipt))
    elif when == 'saved':
        with pytest.raises(UploadRecoveryRequired):
            process_one(receipt, api, reopen(journal, receipt))
    else:
        process_one(receipt, api, reopen(journal, receipt))
    assert api.save_voucher_v1.call_count == 1


def test_atomic_replace_failure_keeps_previous_intent(upload, monkeypatch):
    _, receipt, api, journal = upload
    journal.load(receipt)
    old = journal.path.read_bytes()
    monkeypatch.setattr('kdzwy_receipt_uploader.upload_journal.os.replace', Mock(side_effect=PermissionError('locked')))
    with pytest.raises(PermissionError):
        process_one(receipt, api, journal)
    assert journal.path.read_bytes() == old
    assert not list(journal.path.parent.glob('*.tmp'))
    api.save_voucher_v1.assert_not_called()


@pytest.mark.parametrize('bad', ['{', '{}', '{"version": 99}'])
def test_corrupted_journal_blocks_network_writes(upload, bad):
    _, receipt, api, journal = upload
    journal.path.parent.mkdir()
    journal.path.write_text(bad)
    with pytest.raises(UploadRecoveryRequired):
        process_one(receipt, api, journal)
    api.save_voucher_v1.assert_not_called()
    api.upload_invoice_pdf_v1.assert_not_called()


def test_os_lock_released_after_forced_process_exit(tmp_path):
    lock = tmp_path / 'process.lock'
    code = "from pathlib import Path; from kdzwy_receipt_uploader.upload_journal import exclusive_lock; import os;\nwith exclusive_lock(Path(%r)):\n os._exit(23)" % str(lock)
    child = subprocess.run([sys.executable, '-c', code], timeout=20)
    assert child.returncode == 23
    with exclusive_lock(lock):
        with pytest.raises(UploadRecoveryRequired, match='另一个进程'):
            with exclusive_lock(lock):
                pytest.fail('concurrent lock acquired')


def test_checkpoint_failure_does_not_duplicate_on_next_batch(upload, monkeypatch):
    path, receipt, api, _ = upload
    paths = ProjectPaths.from_root(path.parent)
    paths.ensure()
    ledger = path.parent / 'exceptions.json'
    monkeypatch.setattr(cli, '_write_upload_checkpoint', Mock(side_effect=OSError('locked')))
    for _ in range(2):
        assert cli.run_confirm_sequential([(path, receipt)], api, paths, ledger, 'bank') == (False, 1)
    api.save_voucher_v1.assert_called_once()


def test_batch_readback_failure_stops_next_and_resumes_first(upload):
    path, receipt, api, _ = upload
    paths = ProjectPaths.from_root(path.parent)
    paths.ensure()
    next_receipt = copy.deepcopy(receipt)
    next_receipt.receipt_id = 'receipt-002'
    api.get_voucher_v1.side_effect = TimeoutError('offline')
    ledger = path.parent / 'exceptions.json'
    assert cli.run_confirm_sequential([(path, receipt), (path.parent / 'next.json', next_receipt)], api, paths, ledger, 'bank') == (True, 0)
    assert path.exists()
    api.get_voucher_v1.side_effect = None
    assert cli.run_confirm_sequential([(path, receipt)], api, paths, ledger, 'bank') == (False, 1)
    api.save_voucher_v1.assert_called_once()


@pytest.mark.parametrize('failure, attempts', [
    (ApiError('返回 HTTP 502'), 4), (ApiError('permission denied'), 1),
    ({'data': [{'uploadStatus': False, 'fileId': 'not-valid'}]}, 4),
])
def test_upload_failure_stays_before_save(upload, failure, attempts):
    _, receipt, api, journal = upload
    if isinstance(failure, Exception):
        api.upload_invoice_pdf_v1.side_effect = failure
    else:
        api.upload_invoice_pdf_v1.return_value = failure
    with pytest.raises(ApiError, match='尚未保存凭证'):
        process_one(receipt, api, journal)
    assert api.upload_invoice_pdf_v1.call_count == attempts
    api.save_voucher_v1.assert_not_called()
    assert json.loads(journal.path.read_text())['phase'] == 'prepared'


def test_transient_upload_failure_then_success(upload):
    _, receipt, api, journal = upload
    good = api.upload_invoice_pdf_v1.return_value
    api.upload_invoice_pdf_v1.side_effect = [ApiError('返回 HTTP 502'), {'data': []}, good]
    process_one(receipt, api, journal)
    assert api.upload_invoice_pdf_v1.call_count == 3
    api.save_voucher_v1.assert_called_once()


def test_missing_number_does_not_save(upload):
    _, receipt, api, journal = upload
    api.get_voucher_number.return_value = {}
    with pytest.raises(ApiError, match='未调用保存接口'):
        process_one(receipt, api, journal)
    api.save_voucher_v1.assert_not_called()


def test_no_attachments_never_calls_file_service(upload):
    _, receipt, api, journal = upload
    receipt.attachment_files = []
    process_one(receipt, api, journal)
    api.upload_invoice_pdf_v1.assert_not_called()
    api.bind_voucher_files_v1.assert_not_called()


def test_tenant_journals_are_separate(upload):
    _, receipt, _, journal = upload
    other = UploadJournal(journal.path.parent, 'tenant-2', receipt.receipt_id)
    assert other.path != journal.path


@pytest.mark.parametrize('change', [
    {'phase': 'unknown'}, {'fingerprint': 1}, {'file_ids': 'f1'}, {'file_ids': [1]},
    {'result': []}, {'phase': 'saved', 'voucher_id': ''},
    {'phase': 'verified', 'voucher_id': 'v1', 'result': {}},
])
def test_invalid_state_contract_fails_closed(upload, change):
    _, receipt, api, journal = upload
    journal.load(receipt)
    state = json.loads(journal.path.read_text())
    state.update(change)
    journal.path.write_text(json.dumps(state))
    with pytest.raises(UploadRecoveryRequired):
        process_one(receipt, api, journal)
    api.save_voucher_v1.assert_not_called()


@pytest.mark.parametrize('change', [{'phase':'prepared', 'voucher_id':'unexpected'}, {'file_ids':['f1','f2']}])
def test_inconsistent_journal_blocks_retry(upload, change):
    _, receipt, api, journal = upload
    journal.load(receipt)
    payload = json.loads(journal.path.read_text())
    payload.update(change)
    journal.path.write_text(json.dumps(payload))
    with pytest.raises(UploadRecoveryRequired):
        process_one(receipt, api, journal)
    api.save_voucher_v1.assert_not_called()


def test_concurrent_batch_leaves_active_receipt_and_ledger_untouched(upload):
    path, receipt, api, _ = upload
    paths = ProjectPaths.from_root(path.parent)
    paths.ensure()
    ledger = path.parent / 'exceptions.json'
    tenant = f'{api.accounting_origin}/{api.session_company_id}/{api.dbid}'
    journal = UploadJournal(paths.processing / 'uploads', tenant, receipt.receipt_id)
    with exclusive_lock(journal.lock_path):
        assert cli.run_confirm_sequential([(path, receipt)], api, paths, ledger, 'bank') == (True, 0)
    assert path.exists()
    assert not ledger.exists()
    api.save_voucher_v1.assert_not_called()


def test_copied_journal_from_other_tenant_is_rejected(upload):
    _, receipt, api, journal = upload
    journal.load(receipt)
    other = UploadJournal(journal.path.parent, 'tenant-2', receipt.receipt_id)
    other.path.write_bytes(journal.path.read_bytes())
    with pytest.raises(UploadRecoveryRequired, match='身份不一致'):
        process_one(receipt, api, other)
    api.save_voucher_v1.assert_not_called()


def test_integer_trailing_zeros_survive_voucher_serialization(upload):
    from kdzwy_receipt_uploader.workflow import build_voucher
    _, receipt, _, _ = upload
    entry = receipt.voucher['entries'][0]
    entry.update(amount=100, amountFor=100, rate=10, qty=20, price=50)
    payload = build_voucher(receipt, {'vchNum': 1, 'year': '2026', 'period': '9'}, '123')
    actual = payload['entries'][0]
    assert [actual[k] for k in ('amount','amountFor','rate','qty','price')] == ['100','100','10','20','50']
