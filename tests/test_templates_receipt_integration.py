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
        month = root / "data" / "inbox" / "company_17867515_上海微誉信息技术有限公司" / "2026-08"
        input_root = month / "input"
        receipt_root = root / "workspaces" / "account_1" / "company_17867515" / "2026-08" / "generated" / "receipts" / "sales"
        (input_root / "sales").mkdir(parents=True)
        (input_root / "sales" / "dzfp_1001_demo.pdf").write_bytes(b"%PDF")
        cfg = MonthConfig.from_mapping("demo", "2026-08", {"income_cost_filename": "收入成本表.xlsx", "usage_filename": "用途确认信息.xlsx", "usage_column": "E"})
        report = generate_receipts(
            input_root, cfg, receipt_root, overwrite=True, folder_patterns=["sales"],
            voucher_defaults={"group_id": "g", "group_name": "记", "user_name": "u", "itemClass": "供应商", "businessType": "采购商品", "settlementMethod": "往来结算"},
            entry_defaults=[],
            sales_map_values={"1001": {"amount": 100, "taxAmount": 13, "totalAmount": 113, "date": "2026-08-01", "itemClass": "供应商", "customName": "甲方供应商", "businessType": "采购商品", "settlementMethod": "往来结算", "auxiliaryItem": {"itemClass": "供应商", "itemClassId": 5, "id": "s1", "number": "1543", "name": "甲方供应商"}}},
            template_config={"voucher_templates": [{"id": "t1", "name": "采购商品模板", "version": "2.0", "source": "templates/purchase-goods.json", "when": {"businessType": "采购商品", "settlementMethod": "往来结算"}, "summary": {"header": "采购商品", "body": "{invoiceCode}", "separator": " "}, "entries": [{"dc": 1, "accountSelector": {"number": "1405"}, "amountFrom": "sales_map.totalAmount"}]}]},
        )
        assert report["summary"]["generatedCount"] == 1
        payload = json.loads((receipt_root / "receipt_1001" / "receipt.json").read_text(encoding="utf-8"))
        assert "templates" not in payload
        assert payload["voucher"]["summary"] == "采购商品 1001"
        assert len(payload["voucher"]["entries"]) == 1
        assert payload["voucher"]["entries"][0]["amount"] == 113
    print("模板与 receipt 生成器集成测试通过")


def test_templates_receipt_integration() -> None:
    main()


if __name__ == "__main__":
    main()
