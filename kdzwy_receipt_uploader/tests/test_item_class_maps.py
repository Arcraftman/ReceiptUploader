from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.item_class_maps import ItemClassMapStore, format_item_number


def main() -> None:
    assert [format_item_number(value) for value in (1, 2, 9, 10, 11, 99, 100, 999, 1000, 1001)] == [
        "001", "002", "009", "010", "011", "099", "100", "999", "1000", "1001"
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "item_class_maps.json"
        store = ItemClassMapStore.load(path)
        remote_customers = [
            {"id": f"customer-{index}", "number": f"{index:03d}", "name": f"客户{index}"}
            for index in range(1, 26)
        ]
        remote_suppliers = [
            {"id": f"supplier-{index}", "number": f"{index:03d}", "name": f"供应商{index}"}
            for index in range(1, 47)
        ]
        store.seed_remote(1, remote_customers, "客户")
        store.seed_remote(5, remote_suppliers, "供应商")
        hit = store.resolve_name(1, "客户25", "客户")
        assert hit["number"] == "025" and hit["created"] is False
        new_customer = store.resolve_name(1, "新客户", "客户")
        assert new_customer["number"] == "026" and new_customer["created"] is True
        new_supplier = store.resolve_name(5, "新供应商", "供应商")
        assert new_supplier["number"] == "047" and new_supplier["created"] is True
        store.save()
        reloaded = ItemClassMapStore.load(path)
        assert reloaded.resolve_name(1, "新客户", "客户")["number"] == "026"
        assert reloaded.resolve_name(5, "新供应商", "供应商")["number"] == "047"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["maps"]["1"]["items"]["001"] == "客户1"
        assert payload["maps"]["1"]["items"]["026"] == "新客户"
        assert payload["maps"]["5"]["items"]["047"] == "新供应商"
    print("ItemClass 独立 map、重复复用和递增编码测试通过")


if __name__ == "__main__":
    main()
