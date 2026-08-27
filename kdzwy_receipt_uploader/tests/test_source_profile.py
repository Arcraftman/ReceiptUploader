from __future__ import annotations

from kdzwy_receipt_uploader.source_profile import normalize_source_key, source_from_folder_name


def test_normalize_source_key_aliases() -> None:
    assert normalize_source_key("sales") == "sales"
    assert normalize_source_key("销售") == "sales"
    assert normalize_source_key("purchase") == "purchase"
    assert normalize_source_key("进项发票") == "purchase"
    assert normalize_source_key("bank") == "bank"
    assert normalize_source_key("杂项") == "misc"


def test_source_from_folder_name_aliases() -> None:
    assert source_from_folder_name("销售发票-2026") == "sales"
    assert source_from_folder_name("采购附件") == "purchase"
    assert source_from_folder_name("招商银行") == "bank"
    assert source_from_folder_name("银行流水-建行") == "bank"
    assert source_from_folder_name("杂项") == "misc"
