from __future__ import annotations

import json
from pathlib import Path

from src.kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts


def _receipt(*, receipt_id: str, draft: bool, valid: bool) -> dict:
    voucher = {
        "date": "2026-07-31" if valid else "",
        "groupId": "group-1" if valid else "",
        "groupName": "记",
        "summary": "银行手续费" if valid else "",
        "attachments": 0,
        "attachmentFiles": [],
        "invoiceCodes": [],
        "userName": "reviewer" if valid else "",
        "entries": [
            {
                "lineNo": 1,
                "accountId": "account-1" if valid else "",
                "accountNumber": "6603" if valid else "",
                "accountName": "财务费用" if valid else "",
                "dc": 1,
                "amount": "2.70",
                "amountFor": "2.70",
            },
            {
                "lineNo": 2,
                "accountId": "account-2" if valid else "",
                "accountNumber": "1002" if valid else "",
                "accountName": "银行存款" if valid else "",
                "dc": -1,
                "amount": "2.70",
                "amountFor": "2.70",
            },
        ],
    }
    return {
        "schemaVersion": "1.0",
        "draft": draft,
        "receiptId": receipt_id,
        "voucher": voucher,
    }


def _write_receipt(root: Path, name: str, value: dict) -> Path:
    path = root / name / "receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_verify_lists_only_final_drafts_after_prepare(tmp_path: Path) -> None:
    receipt_root = tmp_path / "bank"
    _write_receipt(
        receipt_root,
        "receipt_V001",
        _receipt(receipt_id="bank-company-2026-07-V001", draft=True, valid=False),
    )
    _write_receipt(
        receipt_root,
        "receipt_V002",
        _receipt(receipt_id="bank-company-2026-07-V002", draft=False, valid=True),
    )
    report = verify_bank_receipts(receipt_root)
    assert report["status"] == "drafts_pending"
    assert report["summary"] == {
        "receiptCount": 2,
        "draftCount": 1,
        "readyCount": 1,
        "invalidCount": 0,
    }
    assert report["drafts"][0]["receiptId"] == "bank-company-2026-07-V001"


def test_verify_rejects_draft_false_with_incomplete_final_fields(tmp_path: Path) -> None:
    receipt_root = tmp_path / "bank"
    _write_receipt(
        receipt_root,
        "receipt_V001",
        _receipt(receipt_id="bank-company-2026-07-V001", draft=False, valid=False),
    )
    report = verify_bank_receipts(receipt_root)
    assert report["status"] == "invalid"
    assert report["summary"]["invalidCount"] == 1
    assert "voucher.date" in report["invalid"][0]["error"]


def test_verify_blocks_receipt_removed_from_current_bank_map(tmp_path: Path) -> None:
    receipt_root = tmp_path / "bank"
    _write_receipt(
        receipt_root,
        "receipt_alpha__A12345",
        _receipt(
            receipt_id="bank-company-2026-07-alpha__A12345",
            draft=False,
            valid=True,
        ),
    )
    report = verify_bank_receipts(
        receipt_root, allowed_record_keys={"alpha__A99999"}
    )

    assert report["status"] == "invalid"
    assert report["summary"]["orphanCount"] == 1
    assert report["summary"]["readyCount"] == 0
    assert "不属于当前普通 bank_map" in report["invalid"][0]["error"]
