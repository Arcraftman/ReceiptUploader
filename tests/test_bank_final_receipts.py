from __future__ import annotations

import json
from pathlib import Path

from src.kdzwy_receipt_uploader.bank_final_receipts import (
    BankFinalReceiptError,
    generate_bank_final_receipts,
    source_values,
)
import pytest


def test_statement_counterparty_is_not_overwritten_by_ocr(tmp_path: Path) -> None:
    ocr_text = tmp_path / "ocr.txt"
    ocr_text.write_text("对方户名：OCR识别出的其他名称", encoding="utf-8")
    values = source_values(
        {
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
            "ourCreditAmount": "2632.00",
            "invoiceNumbers": [],
        }
    )
    assert values["companyHousingFund"] == "1316.00"
    assert values["employeeHousingFund"] == "1316.00"


def test_prepare_existing_generates_final_drafts_and_preserves_edits(tmp_path: Path) -> None:
    pdf = tmp_path / "V001.pdf"
    pdf.write_bytes(b"%PDF-1.4 bank")
    matched = {
        "bank_a__V001": {
            "key": "bank_a__V001",
            "bankKey": "bank_a",
            "index": "V001",
            "flowDirection": "outflow",
            "bankAccountNumber": "100201",
            "invoiceNumbers": [],
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
    matched_path = output / "receipt_bank_a__V001" / "receipt.json"
    matched_receipt = json.loads(matched_path.read_text(encoding="utf-8"))
    assert matched_receipt["draft"] is True
    assert matched_receipt["voucher"]["date"] == "2026-07-31"
    assert matched_receipt["voucher"]["attachments"] == 1
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


def test_prepare_existing_rejects_wrong_bank_account_number(tmp_path: Path) -> None:
    matched = {
        "bank_a__V001": {
            "key": "bank_a__V001",
            "bankKey": "bank_a",
            "index": "V001",
            "bankAccountNumber": "100204",
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
    with pytest.raises(BankFinalReceiptError, match="配置=100204，分析=100201"):
        generate_bank_final_receipts(
            matched,
            analysis,
            tmp_path / "receipts",
            "company_1",
            "2026-07",
            {},
        )
