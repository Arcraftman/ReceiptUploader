from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.template_catalog import TemplateCatalog
from kdzwy_receipt_uploader.voucher_templates import TemplateContext


def main() -> None:
    catalog = TemplateCatalog.load(PROJECT / "templates")
    assert len(catalog.records) >= 4
    assert any(record["path"] == "销售_增值税发票_往来结算_销售商品收入_人民币_template.json" for record in catalog.records)
    context = TemplateContext(
        invoice_code="1001",
        sales_map={"1001": {"businessType": "采购原材料", "settlementMethod": "往来结算", "itemClass": "供应商"}},
        accountbook={},
        source={"businessType": "采购原材料", "settlementMethod": "往来结算", "itemClass": "供应商"},
    )
    rendered = catalog.render_for(context)
    assert rendered["templateBlock"] == "采购"
    assert rendered["templateSettlementMethod"] == "往来结算"
    assert rendered["templateBusinessType"] == "采购原材料"
    assert rendered["templatePath"] == "采购_增值税发票_往来结算_采购原材料_人民币_template.json"
    assert len(rendered["entries"]) == 2
    print("单级详细命名模板目录选择测试通过")


if __name__ == "__main__":
    main()
