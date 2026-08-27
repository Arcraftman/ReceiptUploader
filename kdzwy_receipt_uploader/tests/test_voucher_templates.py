from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.voucher_templates import TemplateContext, TemplateError, VoucherTemplateEngine  # noqa: E402


def main() -> None:
    config = {
        "voucher_templates": [
            {
                "name": "supplier-logistics",
                "when": {"itemClass": "供应商", "customNameContains": "物流"},
                "summary": {"header": "采购", "body": "物流服务", "separator": "-"},
                "entries": [{"dc": 1}, {"dc": -1}, {"dc": 1}],
            },
            {
                "name": "fallback-supplier",
                "when": {"itemClass": "供应商"},
                "summary": {"header": "其他", "body": ""},
                "entries": [{"dc": 1}, {"dc": -1}],
            },
        ]
    }
    engine = VoucherTemplateEngine.from_config(config)
    context = TemplateContext(
        invoice_code="1001",
        sales_map={"1001": {"totalAmount": 100}},
        purchase_map={"1001": {"totalAmount": 113, "amount": 100, "supplierName": "甲方物流有限公司"}},
        accountbook={},
        source={"itemClass": "供应商", "customName": "甲方物流有限公司"},
    )
    result = engine.render_for(context)
    assert result["templateName"] == "supplier-logistics"
    assert result["summary_header"] == "采购"
    assert result["summary_body"] == "物流服务"
    assert result["summary"] == "采购-物流服务"
    assert len(result["entries"]) == 3
    assert [entry["lineNo"] for entry in result["entries"]] == [1, 2, 3]

    source_test = VoucherTemplateEngine.from_config({"voucher_templates": [{
        "name": "j-map-source",
        "when": {},
        "summary": {"header": "", "body": "{purchase_map.date}", "separator": ""},
        "entries": [{"dc": 1, "amountFrom": "purchase_map.totalAmount", "amountForFrom": "purchase_map.amount"}],
    }]}).render_for(context)
    assert source_test["summary_body"] == ""
    assert source_test["entries"][0]["amount"] == 113
    assert source_test["entries"][0]["amountFor"] == 100

    try:
        VoucherTemplateEngine.from_config({"voucher_templates": []}).render_for(context)
    except TemplateError:
        pass
    else:
        raise AssertionError("没有匹配模板时必须报错")

    print("凭证模板框架测试通过")


if __name__ == "__main__":
    main()
