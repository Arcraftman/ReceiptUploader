import json
from pathlib import Path
from types import SimpleNamespace

from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_pdf_links
from kdzwy_receipt_uploader.application.bank_pipeline import run_bank_pipeline


def test_gate_blocks_empty_then_accepts_manual_link_without_rewriting(tmp_path):
    path = tmp_path / 'receipt_one/receipt.json'
    path.parent.mkdir()
    payload = {'receiptId': 'bank-one', 'voucher': {'attachmentFiles': []}}
    path.write_text(json.dumps(payload))
    assert verify_bank_pdf_links(tmp_path)['status'] == 'waiting_for_pdf_binding'
    pdf = path.parent / 'bound.pdf'
    pdf.write_bytes(b'%PDF-1.4')
    payload['voucher']['attachmentFiles'] = [{'path': 'bound.pdf'}]
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    assert verify_bank_pdf_links(tmp_path)['status'] == 'ready'
    assert path.read_bytes() == before
    pdf.unlink()
    assert verify_bank_pdf_links(tmp_path)['missingPdfCount'] == 1


def test_gate_checks_only_pending_receipts(tmp_path):
    path = tmp_path / 'receipt_one/receipt.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'uploaded': True, 'voucher': {'attachmentFiles': []}}))
    assert verify_bank_pdf_links(tmp_path)['pendingReceiptCount'] == 0
    assert verify_bank_pdf_links(tmp_path)['status'] == 'ready'


def test_verify_stage_does_not_run_preprocessing_or_upload(tmp_path):
    phases = []
    values = dict.fromkeys(['root', 'app_config_path', 'args', 'company', 'config',
                           'document_entity_name', 'expected_company', 'input_dir', 'logger',
                           'map_path', 'month', 'paths_config', 'pipeline_source_key', 'settings',
                           'template_root', 'workspace_root'])
    context = SimpleNamespace(**values, analysis_stage='existing', mode='verify', receipt_dir=tmp_path,
                              checkpoint=lambda phase, **kwargs: phases.append(phase))
    assert run_bank_pipeline(context) == 4
    assert phases == ['verify', 'waiting_for_pdf_binding']


def test_both_categories_participate_in_gate(tmp_path):
    for category in ("manual", "automatic"):
        folder = tmp_path / category / ("receipt_" + category)
        folder.mkdir(parents=True)
        (folder / "bound.pdf").write_bytes(b"%PDF-1.4")
        (folder / "receipt.json").write_text(json.dumps({"receiptId": "bank-" + category,
            "voucher": {"attachmentFiles": [{"path": "bound.pdf"}]}}))
    assert verify_bank_pdf_links(tmp_path)["pendingReceiptCount"] == 2
    assert verify_bank_pdf_links(tmp_path)["status"] == "ready"
    (tmp_path / "manual/receipt_manual/bound.pdf").unlink()
    assert verify_bank_pdf_links(tmp_path)["missingPdfCount"] == 1


def test_manual_business_keywords_are_fuzzy_and_not_exclusions():
    from kdzwy_receipt_uploader.bank_rules import manual_bank_remark, remark_exception_match, DEFAULT_REMARK_EXCEPTIONS
    for text, expected in [("扣款（增值税缴税）", "增值税缴税"), ("本月社保缴税扣款", "社保缴税"),
                           ("公积 金", "公积金"), ("扣款（个税缴税）", "个税缴税")]:
        assert manual_bank_remark(text) == expected
        assert not remark_exception_match(text, DEFAULT_REMARK_EXCEPTIONS)
    assert manual_bank_remark("手续费") == ""
