from __future__ import annotations

from pathlib import Path

from kdzwy_receipt_uploader.final_template_sample import (
    build_final_template_context,
    load_final_template_sample,
    validate_filled_entries,
)


def test_final_template_sample_and_runtime_catalog() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sample = load_final_template_sample(project_root / "templates" / "weiyu" / "final_template_sample.json")
    context = build_final_template_context(
        sample,
        account_catalog=[{"id": "a1", "number": "1001", "fullName": "库存现金"}],
        item_catalog={"供应商": {"itemClassId": 5, "items": [{"id": "s1", "number": "001", "name": "供应商A"}]}},
        source="purchase",
        map_values={"amount": 10, "taxAmount": 1, "totalAmount": 11},
    )
    decision = {"filledEntries": [
        {"entryId": 1, "dc": 1, "accountNumber": "1001", "amount": 11, "auxiliary": {}},
        {"entryId": 2, "dc": -1, "accountNumber": "1001", "amount": 11, "auxiliary": {"itemClassId": 5, "id": "s1"}},
    ]}
    assert validate_filled_entries(decision, context) == []
