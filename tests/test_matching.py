from __future__ import annotations

from pathlib import Path

from src.kdzwy_receipt_uploader.matching import match_directory, normalize_invoice_number, pdf_invoice_number


def test_normalize_text_number() -> None:
    assert normalize_invoice_number("001234.0") == "1234"
    assert normalize_invoice_number(1234) == "1234"
    assert normalize_invoice_number("发票号码") is None


def test_directory_match_returns_empty_for_missing_pdf(tmp_path: Path) -> None:
    directory = tmp_path / "batch"
    directory.mkdir()
    assert pdf_invoice_number(Path("dzfp_12345678901234567890_company.pdf")) == "12345678901234567890"
    report = match_directory(directory)
    assert report["map"] == {}
