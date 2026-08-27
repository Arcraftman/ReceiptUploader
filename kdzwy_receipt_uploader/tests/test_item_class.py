from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.item_class import auxiliary_field, build_auxiliary_expectation, resolve_item_class_id


def main() -> None:
    assert resolve_item_class_id("客户") == 1
    assert resolve_item_class_id(item_class_id="5") == 5
    assert auxiliary_field("客户") == "customId"
    assert auxiliary_field(item_class_id=5) == "supplierId"
    row = build_auxiliary_expectation({"id": 123, "number": "023", "name": "客户甲"}, "客户")
    assert row == {"itemClass": "客户", "itemClassId": 1, "field": "customId", "id": "123", "number": "023", "name": "客户甲"}
    assert build_auxiliary_expectation({"id": 456, "number": "001", "name": "供应商甲"}, item_class_id=5)["field"] == "supplierId"
    print("ItemClassId 映射测试通过")


if __name__ == "__main__":
    main()
