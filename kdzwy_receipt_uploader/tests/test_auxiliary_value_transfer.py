from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.models import Receipt
from kdzwy_receipt_uploader.workflow import ApiError, build_voucher, validate_auxiliary_readback


def make_receipt(item_class: str, prefix: str, item_id: str, name: str) -> Receipt:
    return Receipt(
        receipt_id="aux-test",
        voucher={
            "date": "2026-07-31",
            "groupId": "group-1",
            "summary": "辅助核算测试",
            "attachments": 0,
            "userName": "tester",
            "debitTotal": 1,
            "creditTotal": 1,
            "entries": [
                {
                    "lineNo": 1,
                    "entryId": 1,
                    "accountId": "account-1",
                    "accountNumber": "1122",
                    "accountName": name,
                    "subjectAccountName": "应收账款",
                    "dc": 1,
                    "amount": 1,
                    "amountFor": 1,
                    f"{prefix}Id": item_id,
                    f"{prefix}Number": "001",
                    f"{prefix}Name": name,
                    "auxiliaryExpected": {"itemClass": item_class, "id": item_id, "number": "001", "name": name, "accountNumber": "1122", "subjectAccountName": "应收账款"},
                },
                {
                    "lineNo": 2,
                    "entryId": 2,
                    "accountId": "account-2",
                    "accountNumber": "5001",
                    "accountName": "销售收入",
                    "dc": -1,
                    "amount": 1,
                    "amountFor": 1,
                },
            ],
        },
    )


def main() -> None:
    for item_class, prefix, item_id, name in (
        ("客户", "custom", "customer-live-id", "动态客户"),
        ("供应商", "supplier", "supplier-live-id", "动态供应商"),
    ):
        receipt = make_receipt(item_class, prefix, item_id, name)
        voucher = build_voucher(receipt, {"vchNum": 1, "year": "2026", "period": "7"}, "db-1")
        entry = voucher["entries"][0]
        assert entry[f"{prefix}Id"] == item_id
        assert entry["accountName"] == name
        assert "subjectAccountName" not in entry
        assert f"{prefix}Number" not in entry
        assert f"{prefix}Name" not in entry
        assert "auxiliaryExpected" not in entry
        detail = {"entries": [{f"{prefix}Id": item_id, "auxiliaryName": name, "accountName": "应收账款", "accountNumber": "1122"}, {}]}
        result = validate_auxiliary_readback(receipt.voucher["entries"], detail)
        assert result[0]["name"] == name
        try:
            validate_auxiliary_readback(receipt.voucher["entries"], {"entries": [{f"{prefix}Id": "wrong", "auxiliaryName": "应收账款", "accountName": "应收账款", "accountNumber": "1122"}, {}]})
        except ApiError as exc:
            assert "辅助核算回读不一致" in str(exc)
        else:
            raise AssertionError("回读不一致时必须失败")
    print("客户/供应商动态 ID 传值与回读校验通过")


if __name__ == "__main__":
    main()
