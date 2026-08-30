from __future__ import annotations

from pathlib import Path

from kdzwy_receipt_uploader.cli import select_random_receipts
from kdzwy_receipt_uploader.models import Receipt


def make(path: str, receipt_id: str):
    return Path(path), Receipt(receipt_id, {"entries": []})


def test_source_random_selection_is_reproducible(tmp_path: Path) -> None:
    valid = [
        make(str(tmp_path / "sales" / "receipt_1" / "receipt.json"), "sales-1"),
        make(str(tmp_path / "sales" / "receipt_2" / "receipt.json"), "sales-2"),
        make(str(tmp_path / "purchase" / "receipt_1" / "receipt.json"), "purchase-1"),
        make(str(tmp_path / "bank" / "receipt_1" / "receipt.json"), "bank-1"),
    ]
    first = [r.receipt_id for _, r in select_random_receipts(valid, "sales", 1, 7)]
    second = [r.receipt_id for _, r in select_random_receipts(valid, "sales", 1, 7)]
    assert first == second
    assert first[0] in {"sales-1", "sales-2"}


def test_standard_receipt_path_uses_receipt_source(tmp_path: Path) -> None:
    receipt = Receipt("sales-receipt", {"entries": []}, source="sales")
    path = tmp_path / "workspaces" / "account_1" / "company_17867515" / "2026-08" / "generated" / "receipts" / "sales" / "receipt_sales" / "receipt.json"
    assert [r.receipt_id for _, r in select_random_receipts([(path, receipt)], "sales", 1, 1)] == ["sales-receipt"]


def test_all_candidates_and_count(tmp_path: Path) -> None:
    valid = [make(str(tmp_path / "sales" / f"receipt_{i}" / "receipt.json"), f"sales-{i}") for i in range(3)]
    assert len(select_random_receipts(valid, "all", 2, 1)) == 2
