import json
from kdzwy_receipt_uploader.cli import _write_upload_checkpoint
from kdzwy_receipt_uploader.bank_upload_report import report_unuploaded_bank_pdfs


def test_only_verified_attachment_hash_is_excluded(tmp_path):
    split = tmp_path/'split'; split.mkdir()
    sent=split/'sent.pdf';sent.write_bytes(b'sent')
    pending=split/'pending.pdf';pending.write_bytes(b'pending')
    root=tmp_path/'receipts';r=root/'receipt_1'/'receipt.json';r.parent.mkdir(parents=True)
    r.write_text(json.dumps({'voucher':{'attachmentFiles':[{'path':str(sent)}]}}))
    _write_upload_checkpoint(r, {'attachmentStatus':'uploaded_linked_and_verified','attachmentFileIds':['1']})
    report=report_unuploaded_bank_pdfs(split,root,tmp_path/'exception')
    assert [x['pdf'] for x in report['entries']] == [str(pending.resolve())]
    sent.write_bytes(b'changed')
    assert report_unuploaded_bank_pdfs(split,root,tmp_path/'exception')['unuploadedPdfCount'] == 2
