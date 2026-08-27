from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.month_config import MonthConfig
from kdzwy_receipt_uploader.receipt_generation import generate_receipts


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        month = root / "weiyu" / "7月"
        (month / "sales1").mkdir(parents=True)
        (month / "sales1" / "dzfp_1001_customer.pdf").write_bytes(b"%PDF")
        (month / "month.conf").write_text("[processing]\ncompany=weiyu\nmonth=7月\n", encoding="utf-8")
        cfg = MonthConfig.load(month / "month.conf")
        generate_receipts(
            month,
            cfg,
            month / "receipts",
            overwrite=True,
            folder_patterns=["sales*"],
            voucher_defaults={
                "group_id": "g1",
                "group_name": "记",
                "user_name": "用户",
                "itemClass": "客户",
                "customName": "创界科技有限公司",
                "customer_item": {"id": "c1", "number": "001", "name": "创界科技有限公司"},
            },
            entry_defaults=[
                {"line_no": 1, "account_id": "a1", "account_number": "1122", "account_name": "应收账款", "dc": 1},
                {"line_no": 2, "account_id": "a2", "account_number": "5001", "account_name": "销售收入", "dc": 1},
                {"line_no": 3, "account_id": "a3", "account_number": "22210106", "account_name": "其他应付款", "dc": -1},
            ],
            sales_map_values={"1001": {"amount": 100.0, "taxAmount": 13.0, "totalAmount": 113.0, "date": "2026-07-31"}},
            template_config={
                "voucher_templates": [{
                    "name": "收客户款",
                    "when": {"itemClass": "客户"},
                    "summary": {"header": "销售收入", "body": "{invoiceCode}", "separator": " "},
                    "entries": [
                        {"dc": 1, "accountSelector": {"number": "1122"}, "amountFrom": "sales_map.totalAmount", "amountForFrom": "sales_map.totalAmount", "auxiliary": {"field": "customId", "name": "创界科技有限公司", "itemClassId": 1}},
                        {"dc": 1, "accountSelector": {"number": "5001"}, "amountFrom": "sales_map.taxAmount", "amountForFrom": "sales_map.taxAmount"},
                        {"dc": -1, "accountSelector": {"number": "22210106"}, "amountFrom": "sales_map.amount", "amountForFrom": "sales_map.amount"},
                    ],
                }]
            },
        )
        payload = json.loads((month / "receipts" / "receipt_1001" / "receipt.json").read_text(encoding="utf-8"))
        voucher = payload["voucher"]
        assert voucher["summary"] == "销售收入 1001"
        assert [entry["amount"] for entry in voucher["entries"]] == [113.0, 13.0, 100.0]
        assert voucher["entries"][0]["customId"] == "c1"
        assert voucher["entries"][0]["accountName"] == "创界科技有限公司"
        assert voucher["entries"][0]["subjectAccountName"] == "应收账款"
        assert "customNumber" not in voucher["entries"][0]
        assert "customName" not in voucher["entries"][0]
        assert voucher["entries"][0]["auxiliaryExpected"]["name"] == "创界科技有限公司"
        assert [entry["accountNumber"] for entry in voucher["entries"]] == ["1122", "5001", "22210106"]
        print("收客户款模板测试通过")


if __name__ == "__main__":
    main()
