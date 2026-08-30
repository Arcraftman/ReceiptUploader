from __future__ import annotations

from kdzwy_receipt_uploader.source_profile import normalize_source_key, source_from_folder_name, source_patterns


def test_only_canonical_source_keys_are_accepted() -> None:
    assert normalize_source_key("sales") == "sales"
    assert normalize_source_key("purchase") == "purchase"
    assert normalize_source_key("bank") == "bank"
    assert normalize_source_key("misc") == "misc"
    assert normalize_source_key("销售") == ""


def test_only_exact_standard_folder_names_are_accepted() -> None:
    assert source_from_folder_name("sales") == "sales"
    assert source_from_folder_name("purchase") == "purchase"
    assert source_from_folder_name("bank") == "bank"
    assert source_from_folder_name("misc") == "misc"
    assert source_from_folder_name("sales1") == ""


def test_configured_folder_patterns_are_reduced_to_exact_names() -> None:
    assert source_patterns("all", ["sales*", "purchase", "销售"]) == ["purchase"]
