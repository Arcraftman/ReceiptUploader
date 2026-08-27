from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.month_config import MonthConfig
from kdzwy_receipt_uploader.receipt_generation import generate_receipts


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        month = root / "demo" / "8月"
        (month / "sales1").mkdir(parents=True)
        (month / "sales1" / "dzfp_1001_demo.pdf").write_bytes(b"%PDF")
        (month / "month.conf").write_text("[processing]\ncompany=demo\nmonth=8月\n", encoding="utf-8")
        cfg = MonthConfig.load(month / "month.conf")
        report = generate_receipts(
            month, cfg, month / "receipts", overwrite=True, folder_patterns=["sales*"],
            voucher_defaults={"group_id": "g", "group_name": "记", "user_name": "u", "itemClass": "供应商", "businessType": "采购商品", "settlementMethod": "往来结算"},
            entry_defaults=[],
            sales_map_values={"1001": {"amount": 100, "taxAmount": 13, "totalAmount": 113, "date": "2026-08-01", "itemClass": "供应商", "customName": "甲方供应商", "businessType": "采购商品", "settlementMethod": "往来结算", "auxiliaryItem": {"itemClass": "供应商", "itemClassId": 5, "id": "s1", "number": "1543", "name": "甲方供应商"}}},
            template_config={"templates": [{"id": "t1", "name": "采购商品模板", "version": "2.0", "source": "templates/purchase-goods.json", "when": {"businessType": "采购商品", "settlementMethod": "往来结算"}, "summary": {"header": "采购商品", "body": "{invoiceCode}", "separator": " "}, "entries": [{"dc": 1, "accountSelector": {"number": "1405"}, "amountFrom": "sales_map.totalAmount"}]}]},
        )
        # This test intentionally exercises the engine's legacy key and dynamic
        # fields separately; the receipt generator remains backward-compatible.
        assert report["summary"]["generatedCount"] == 1
        payload = json.loads((month / "receipts" / "receipt_1001" / "receipt.json").read_text(encoding="utf-8"))
        assert "templates" in payload
        assert payload["templates"]["name"] == "采购商品模板"
        assert len(payload["voucher"]["entries"]) == 1
    print("模板与 receipt 生成器兼容测试通过")


if __name__ == "__main__":
    main()
