from __future__ import annotations

import json
from pathlib import Path

from src.kdzwy_receipt_uploader.paths import ProjectPaths
from src.kdzwy_receipt_uploader.workflow import build_voucher, find_receipts, load_snapshot


def test_receipt_entry_ids_and_pdf(tmp_path: Path) -> None:
    root = tmp_path / "project"
    paths = ProjectPaths.from_root(root)
    paths.ensure()
    item_dir = paths.inbox / "one"
    (item_dir / "attachment").mkdir(parents=True)
    (item_dir / "attachment" / "one.pdf").write_bytes(b"%PDF-1.4 local")
    payload = {
        "schemaVersion": "1.0",
        "receiptId": "local-test-001",
        "voucher": {
            "date": "2026-07-31",
            "groupId": "g",
            "summary": "local",
            "attachments": 1,
            "attachmentFiles": [{"path": "attachment/one.pdf"}],
            "userName": "tester",
            "entries": [
                {"lineNo": 1, "accountId": "1", "accountNumber": "1001", "accountName": "现金", "dc": 1, "amount": "1.00", "amountFor": "1.00", "cur": "RMB", "rate": "1"},
                {"lineNo": 2, "accountId": "2", "accountNumber": "1002", "accountName": "银行", "dc": -1, "amount": "1.00", "amountFor": "1.00", "cur": "RMB", "rate": "1"}
            ]
        }
    }
    receipt_path = item_dir / "receipt.json"
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    valid, invalid = find_receipts(paths.inbox, {})
    assert not invalid
    assert len(valid) == 1
    receipt = valid[0][1]
    voucher = build_voucher(receipt, {"vchNum": 1, "year": "2026", "period": "7", "yearPeriod": "202607"}, None)
    assert [entry["entryId"] for entry in voucher["entries"]] == [1, 2]
    assert "attachmentFiles" not in voucher
