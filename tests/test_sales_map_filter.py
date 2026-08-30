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
        month = root / "data" / "inbox" / "company_17867515_上海微誉信息技术有限公司" / "2026-08"
        input_root = month / "input"
        receipt_root = root / "workspaces" / "account_1" / "company_17867515" / "2026-08" / "generated" / "receipts" / "sales"
        (input_root / "sales").mkdir(parents=True)
        for code in ("1001", "1002", "1003"):
            (input_root / "sales" / f"dzfp_{code}_customer.pdf").write_bytes(b"%PDF")
        cfg = MonthConfig.from_mapping("weiyu", "2026-08", {"income_cost_filename": "收入成本表.xlsx", "usage_filename": "用途确认信息.xlsx", "usage_column": "E"})
        sales_map = {
            "1001": {"amount": 100.0, "taxAmount": 13.0, "totalAmount": 113.0, "date": "2026-08-31"},
            "1002": {"amount": 200.0, "taxAmount": 26.0, "totalAmount": 226.0, "date": "2026-08-31"},
        }
        report = generate_receipts(
            input_root,
            cfg,
            receipt_root,
            overwrite=True,
            folder_patterns=["sales"],
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
        assert (receipt_root / "receipt_1001" / "receipt.json").is_file()
        assert not (receipt_root / "receipt_1003").exists()
        payload = json.loads((receipt_root / "receipt_1001" / "receipt.json").read_text(encoding="utf-8"))
        assert payload["voucher"]["date"] == "2026-08-31"
        assert [entry["amount"] for entry in payload["voucher"]["entries"]] == [113.0, 100.0, 13.0]
        print("sales_map 命中过滤测试通过")


def test_sales_map_filter() -> None:
    main()


if __name__ == "__main__":
    main()
