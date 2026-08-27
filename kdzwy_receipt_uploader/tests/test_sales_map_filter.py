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
        for code in ("1001", "1002", "1003"):
            (month / "sales1" / f"dzfp_{code}_customer.pdf").write_bytes(b"%PDF")
        conf = month / "month.conf"
        conf.write_text("[processing]\ncompany=weiyu\nmonth=7月\n", encoding="utf-8")
        cfg = MonthConfig.load(conf)
        sales_map = {
            "1001": {"amount": 100.0, "taxAmount": 13.0, "totalAmount": 113.0, "date": "2026-07-31"},
            "1002": {"amount": 200.0, "taxAmount": 26.0, "totalAmount": 226.0, "date": "2026-07-31"},
        }
        report = generate_receipts(
            month,
            cfg,
            month / "receipts_sales_map",
            overwrite=True,
            folder_patterns=["sales*"],
            voucher_defaults={"group_id": "g1", "group_name": "记", "user_name": "用户", "itemClass": "客户", "customName": "创界科技有限公司", "customer_item": {"id": "c1", "number": "001", "name": "创界科技有限公司"}},
            entry_defaults=[
                {"line_no": 1, "dc": 1, "account_id": "a1", "account_number": "1122", "account_name": "应收账款"},
                {"line_no": 2, "dc": -1, "account_id": "a2", "account_number": "5001", "account_name": "销售收入"},
                {"line_no": 3, "dc": -1, "account_id": "a3", "account_number": "22210106", "account_name": "销项税额"},
            ],
            sales_map_values=sales_map,
            template_config={"voucher_templates": [{"name": "客户", "when": {"itemClass": "客户"}, "summary": {"header": "销售收入", "body": "{invoiceCode}", "separator": " "}, "entries": [{"dc": 1, "accountSelector": {"number": "1122"}, "amountFrom": "sales_map.totalAmount"}, {"dc": -1, "accountSelector": {"number": "5001"}, "amountFrom": "sales_map.amount"}, {"dc": -1, "accountSelector": {"number": "22210106"}, "amountFrom": "sales_map.taxAmount"}]}]},
            only_mapped_invoices=True,
        )
        assert report["summary"]["generatedCount"] == 2
        assert report["summary"]["filteredOutCount"] == 1
        assert (month / "receipts_sales_map" / "receipt_1001" / "receipt.json").is_file()
        assert not (month / "receipts_sales_map" / "receipt_1003").exists()
        payload = json.loads((month / "receipts_sales_map" / "receipt_1001" / "receipt.json").read_text(encoding="utf-8"))
        assert payload["voucher"]["date"] == "2026-07-31"
        assert [entry["amount"] for entry in payload["voucher"]["entries"]] == [113.0, 100.0, 13.0]
        print("sales_map 命中过滤测试通过")


if __name__ == "__main__":
    main()
