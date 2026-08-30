from pathlib import Path

from kdzwy_receipt_uploader.pipeline_runner import _cleanup_obsolete_source_maps


def test_sales_map_cleanup_removes_only_cross_source_duplicates(tmp_path: Path) -> None:
    map_directory = tmp_path / "generated" / "maps" / "sales"
    map_directory.mkdir(parents=True)
    keep = map_directory / "sales_map.json"
    keep.write_text("{}", encoding="utf-8")
    review = map_directory / "preupload_review.report.json"
    review.write_text("{}", encoding="utf-8")
    duplicates = [
        map_directory / "purchase_map.json",
        map_directory / "purchase_map.report.json",
        map_directory / "xlsx_pdf_map.json",
        map_directory / "xlsx_pdf_map.report.json",
    ]
    for duplicate in duplicates:
        duplicate.write_text("{}", encoding="utf-8")

    removed = _cleanup_obsolete_source_maps(map_directory, "sales")

    assert set(removed) == set(duplicates)
    assert keep.is_file()
    assert review.is_file()
    assert all(not duplicate.exists() for duplicate in duplicates)


def test_empty_bank_map_directory_is_removed(tmp_path: Path) -> None:
    map_directory = tmp_path / "generated" / "maps" / "bank"
    map_directory.mkdir(parents=True)

    assert _cleanup_obsolete_source_maps(map_directory, "bank") == []
    assert not map_directory.exists()
