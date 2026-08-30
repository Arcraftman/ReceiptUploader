from __future__ import annotations

from kdzwy_receipt_uploader.preload_items import preload_bank_counterparties


class FakeApi:
    def __init__(self) -> None:
        self.created: list[tuple[int, str, str]] = []
        self.next_number = 100

    def get_items_v1(self, class_id: int, page_size: int = 500):
        assert page_size == 500
        if class_id == 1:
            return {
                "rows": [
                    {"id": "customer-1", "number": "C001", "name": "已有客户"}
                ]
            }
        return {"rows": []}

    def get_next_item_number_v1(self, class_id: int) -> str:
        self.next_number += 1
        return f"N{class_id}-{self.next_number}"

    def create_item_v1(self, class_id: int, number: str, name: str):
        self.created.append((class_id, number, name))
        return {"id": f"created-{class_id}-{name}", "number": number, "name": name}


def test_bank_preload_uses_statement_direction_and_skips_self_transfer() -> None:
    api = FakeApi()
    result = preload_bank_counterparties(
        api,
        {
            "inflow": {
                "flowDirection": "inflow",
                "counterpartyName": "已有客户",
                "configCompany": "资料公司",
            },
            "outflow": {
                "flowDirection": "outflow",
                "counterpartyName": "新增供应商",
                "configCompany": "资料公司",
            },
            "self": {
                "flowDirection": "inflow",
                "counterpartyName": "资料公司",
                "configCompany": "资料公司",
            },
        },
    )
    assert result.source_columns == {"1": ["已有客户"], "5": ["新增供应商"]}
    assert result.resolve(1, "已有客户")["id"] == "customer-1"
    assert result.resolve(5, "新增供应商")["name"] == "新增供应商"
    assert api.created == [(5, "N5-101", "新增供应商")]
