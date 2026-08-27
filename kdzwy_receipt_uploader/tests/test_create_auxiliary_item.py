from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.auxiliary_items import create_auxiliary_item


class FakeApi:
    def __init__(self) -> None:
        self.calls = []

    def get_json(self, endpoint: str):
        self.calls.append(("GET", endpoint, None))
        return {"status": 200, "data": {"num": "026"}}

    def post_form(self, endpoint: str, form):
        self.calls.append(("POST", endpoint, form))
        return {"status": 200, "data": {"id": 999, "number": form["number"], "name": form["name"], "type": -10}}


def main() -> None:
    api = FakeApi()
    result = create_auxiliary_item(api, 1, "026", "新客户")
    assert result["id"] == "999"
    assert result["number"] == "026"
    assert result["name"] == "新客户"
    first_calls = api.calls
    api = FakeApi()
    result = create_auxiliary_item(api, 5, "026", "新供应商", remote_max_number=1542)
    assert result["number"] == "1543"
    assert first_calls[0] == ("GET", "/bs/item?m=findNextNum&itemClassId=1", None)
    assert first_calls[1] == ("POST", "/bs/item?m=save&confirmed=0", {"number": "026", "name": "新客户", "itemClassId": 1, "spec": "", "unit": ""})
    assert api.calls[1][2]["number"] == "1543"
    print("远端新增 Item 流程测试通过")


if __name__ == "__main__":
    main()
