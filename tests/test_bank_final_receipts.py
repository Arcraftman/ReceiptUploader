from __future__ import annotations

import json
from pathlib import Path

from kdzwy_receipt_uploader.bank_final_receipts import (
    BankFinalReceiptError,
    generate_bank_final_receipts,
    source_values,
    validate_bank_analysis_rules,
)
import pytest


def verified_record(amount="12.30", **extra):
    return {"transactionAmount": amount, "statementAmount": amount,
            "amountSource": "bank_statement.ourCreditAmount", "amountValidated": True, **extra}


def stamp_analysis(record, analysis):
    from kdzwy_receipt_uploader.receipts_ocr import bank_amount_snapshot
    analysis["bankSourceAmounts"] = bank_amount_snapshot(source_values(record))
    analysis.setdefault("extractedFields", {}).update(verified_record(record["transactionAmount"]))


def test_statement_counterparty_is_not_overwritten_by_ocr(tmp_path: Path) -> None:
    ocr_text = tmp_path / "ocr.txt"
    ocr_text.write_text("对方户名：OCR识别出的其他名称", encoding="utf-8")
    values = source_values(
        {
            **verified_record(),
            "counterpartyName": "Excel指定列供应商",
            "counterpartyType": "supplier",
            "supplierName": "Excel指定列供应商",
            "bankAccountNumber": "100204",
            "receipt": {"ocrText": str(ocr_text)},
        }
    )
    assert values["counterpartyName"] == "Excel指定列供应商"
    assert values["supplierName"] == "Excel指定列供应商"
    assert values["bankAccountNumber"] == "100204"


def test_bank_source_values_split_housing_fund_from_verified_history() -> None:
    values = source_values(
        {
            **verified_record("2632.00", configCompany="上海微誉信息技术有限公司"),
            "ourCreditAmount": "2632.00",
            "invoiceNumbers": [],
        }
    )
    assert values["companyHousingFund"] == "1316.00"
    assert values["employeeHousingFund"] == "1316.00"


@pytest.mark.parametrize("with_pdf", [True, False])
def test_prepare_existing_generates_final_drafts_and_preserves_edits(tmp_path: Path, with_pdf) -> None:
    pdf = tmp_path / "V001.pdf"
    if with_pdf:
        pdf.write_bytes(b"%PDF-1.4 bank")
    matched = {
        "bank_a__V001": {
            "key": "bank_a__V001",
            "bankKey": "bank_a",
            "index": "V001",
            "flowDirection": "outflow",
            "bankAccountNumber": "100201",
            "invoiceNumbers": [],
            **verified_record(),
            "ourCreditAmount": "12.30",
            "receipt": {"pdf": str(pdf)},
        },
        "bank_a__V002": {
            "key": "bank_a__V002",
            "bankKey": "bank_a",
            "index": "V002",
            "flowDirection": "outflow",
            "bankAccountNumber": "100201",
            "invoiceNumbers": [],
            **verified_record("9.90"),
            "ourCreditAmount": "9.90",
            "receipt": {"pdf": str(pdf)},
        },
    }
    analysis = {
        "bank_a__V001": {
            "analysisStatus": "ready_for_review",
            "explanation": "支付银行手续费",
            "explanation_body": "支付银行手续费",
            "bankTransactionDate": "2026-07-31",
            "extractedFields": {"transactionDate": "2026-07-31"},
            "filledEntries": [
                {"dc": 1, "accountId": "1", "accountNumber": "6603", "accountName": "财务费用", "amount": "12.30", "amountFor": "12.30", "explanation": "支付银行手续费"},
                {"dc": -1, "accountId": "2", "accountNumber": "100201", "accountName": "银行存款_上海银行", "amount": "12.30", "amountFor": "12.30", "explanation": "支付银行手续费 2026-07-31"},
            ],
        }
    }
    stamp_analysis(matched["bank_a__V001"], analysis["bank_a__V001"])
    output = tmp_path / "receipts" / "bank"
    report = generate_bank_final_receipts(
        matched,
        analysis,
        output,
        "company_1",
        "2026-07",
        {"group_id": "g", "group_name": "记", "user_name": "tester"},
    )
    assert report["summary"] == {
        "matchedRecordCount": 2,
        "receiptCount": 1,
        "generatedCount": 1,
        "reusedCount": 0,
        "blockedAnalysisCount": 1,
    }
    assert report["blocked"][0]["key"] == "bank_a__V002"
    matched_path = output / ("automatic" if with_pdf else "manual") / "receipt_bank_a__V001" / "receipt.json"
    matched_receipt = json.loads(matched_path.read_text(encoding="utf-8"))
    assert matched_receipt["draft"] is True
    assert matched_receipt["voucher"]["date"] == "2026-07-31"
    assert matched_receipt["voucher"]["attachments"] == int(with_pdf)
    assert not (output / "receipt_bank_a__V002").exists()

    matched_receipt["voucher"]["summary"] = "人工已修改"
    matched_path.write_text(json.dumps(matched_receipt, ensure_ascii=False), encoding="utf-8")
    rerun = generate_bank_final_receipts(
        matched,
        analysis,
        output,
        "company_1",
        "2026-07",
        {"group_id": "g", "group_name": "记", "user_name": "tester"},
    )
    assert rerun["summary"]["generatedCount"] == 0
    assert rerun["summary"]["reusedCount"] == 1
    assert json.loads(matched_path.read_text(encoding="utf-8"))["voucher"]["summary"] == "人工已修改"
    manual_pdf = matched_path.parent / "manual.pdf"
    manual_pdf.write_bytes(b"%PDF-1.4")
    matched_receipt["voucher"]["attachmentFiles"] = [{"path": "manual.pdf"}]
    matched_path.write_text(json.dumps(matched_receipt), encoding="utf-8")
    generate_bank_final_receipts(matched, analysis, output, "company_1", "2026-07",
                                {"group_id": "g", "group_name": "记", "user_name": "tester"}, overwrite=True)
    regenerated = json.loads(matched_path.read_text(encoding="utf-8"))
    assert regenerated["voucher"]["attachmentFiles"] == [{"path": "manual.pdf"}]
    assert regenerated["voucher"]["attachments"] == 1
    # Remark-driven manual classification wins over an existing PDF/category.
    matched["bank_a__V001"]["remark"] = "扣款（增值税缴税）"
    generate_bank_final_receipts(matched, analysis, output, "company_1", "2026-07",
                                {"group_id": "g", "group_name": "记", "user_name": "tester"})
    manual_path = output / "manual/receipt_bank_a__V001/receipt.json"
    assert manual_path.is_file()
    assert json.loads(manual_path.read_text(encoding="utf-8")) == regenerated




def test_prepare_existing_rejects_wrong_bank_account_number(tmp_path: Path) -> None:
    matched = {
        "bank_a__V001": {
            "key": "bank_a__V001",
            "bankKey": "bank_a",
            "index": "V001",
            "bankAccountNumber": "100204",
            **verified_record(),
            "ourCreditAmount": "12.30",
            "receipt": {},
        }
    }
    analysis = {
        "bank_a__V001": {
            "analysisStatus": "ready_for_review",
            "filledEntries": [
                {
                    "dc": -1,
                    "accountNumber": "100201",
                    "accountName": "银行存款_上海银行",
                    "amount": "12.30",
                }
            ],
        }
    }
    analysis["bank_a__V001"]["filledEntries"].insert(0, {
        "dc": 1, "accountNumber": "560303", "accountName": "财务费用", "amount": "12.30",
    })
    stamp_analysis(matched["bank_a__V001"], analysis["bank_a__V001"])
    with pytest.raises(BankFinalReceiptError, match="配置=100204，分析=100201"):
        generate_bank_final_receipts(
            matched,
            analysis,
            tmp_path / "receipts",
            "company_1",
            "2026-07",
            {},
        )


def test_prepare_rejects_legacy_remark_forced_analysis() -> None:
    record = {
        "bankKey": "alpha",
        "index": "A12345",
        "transactionAmount": "12.30",
        "statementAmount": "12.30",
        "amountSource": "bank_statement.ourCreditAmount",
        "amountValidated": True,
        "bankAccountNumber": "100201",
        "remark": "运费",
        "forcedTemplatePath": "bank/freight_template.json",
    }
    with pytest.raises(BankFinalReceiptError, match="旧备注强制模板分析已失效"):
        validate_bank_analysis_rules(
            record,
            {"templatePath": "bank/other_template.json", "selectionMode": "statement_remark_exact"},
        )
