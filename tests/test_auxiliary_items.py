from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.auxiliary_items import (  # noqa: E402
    AUXILIARY_ITEM_CLASSES,
    auxiliary_items_endpoint,
    extract_auxiliary_items,
    fetch_all_auxiliary_items,
)


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def post_form(self, endpoint: str, form: dict[str, str]) -> dict:
        self.calls.append((endpoint, form))
        item_class_id = int(endpoint.rsplit("=", 1)[1])
        return {
            "status": 200,
            "_httpStatus": 200,
            "data": {"items": [{"id": f"id-{item_class_id}", "number": str(item_class_id), "name": "示例"}]},
        }


def main() -> None:
    assert auxiliary_items_endpoint(5) == "/bs/item?m=findItem&itemClassId=5"
    assert extract_auxiliary_items({"data": {"items": [{"id": 1}]}}) == [{"id": 1}]
    api = FakeApi()
    reports = fetch_all_auxiliary_items(api)
    assert list(reports) == list(AUXILIARY_ITEM_CLASSES)
    assert len(api.calls) == 6
    assert all(form == {} for _, form in api.calls)
    assert reports["客户"]["itemClassId"] == 1
    assert reports["供应商"]["endpoint"].endswith("itemClassId=5")
    assert reports["部门"]["items"][0]["id"] == "id-6"
    print("辅助核算接口封装测试通过")


if __name__ == "__main__":
    main()
