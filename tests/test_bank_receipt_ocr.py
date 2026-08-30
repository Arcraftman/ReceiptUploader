from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from kdzwy_receipt_uploader import bank_receipt_ocr


def _write_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page(width=100, height=100)
        page.insert_text((10, 20), "bank receipt")
        document.save(path)


def _split_report(tmp_path: Path) -> dict[str, object]:
    banks = []
    outputs_by_bank = {
        "banka": ["A1234567.pdf", "bank_exception/banka_page_0001_receipt_02.pdf"],
        "bankb": ["B1234567.pdf"],
    }
    for bank_key, outputs in outputs_by_bank.items():
        output_directory = tmp_path / "bank_receipts" / bank_key
        for relative in outputs:
            _write_pdf(output_directory / relative)
        (output_directory / "split.manifest.json").write_text(
            json.dumps({"outputs": outputs}), encoding="utf-8"
        )
        banks.append({
            "bankKey": bank_key,
            "outputDirectory": str(output_directory),
        })
    return {"banks": banks}


def test_bank_ocr_runs_only_after_all_manifest_outputs_are_discovered(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_ocr(path: Path) -> tuple[str, str]:
        calls.append(path.name)
        return f"OCR:{path.stem}", "fake-ocr"

    output = tmp_path / "ocr" / "bank"
    report = bank_receipt_ocr.run_bank_receipt_ocr(
        _split_report(tmp_path), output, workers=4, ocr_runner=fake_ocr
    )

    assert sorted(calls) == ["A1234567.pdf", "B1234567.pdf"]
    assert report["summary"] == {
        "receiptCount": 3,
        "eligibleReceiptCount": 2,
        "excludedBeforeOcrCount": 1,
        "processedCount": 2,
        "generatedCount": 2,
        "reusedCount": 0,
        "successTextCount": 2,
        "emptyTextCount": 0,
        "errorCount": 0,
        "workerCount": 1,
        "elapsedSeconds": report["summary"]["elapsedSeconds"],
    }
    assert (output / "banka" / "A1234567" / "ocr.txt").read_text(encoding="utf-8") == "OCR:A1234567"
    assert not (output / "banka" / "bank_exception").exists()
    assert report["excludedBeforeOcr"][0]["reason"] == "split_bank_exception"
    assert (output / "ocr_stage.report.json").is_file()


def test_bank_ocr_reuses_unchanged_successful_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    split_report = _split_report(tmp_path)
    output = tmp_path / "ocr" / "bank"
    calls: list[str] = []

    def fake_extract(path: Path) -> tuple[str, str]:
        calls.append(path.name)
        return "recognized text", "fake-native"

    monkeypatch.setattr(bank_receipt_ocr, "_extract_text", fake_extract)
    first = bank_receipt_ocr.run_bank_receipt_ocr(split_report, output, workers=1)
    second = bank_receipt_ocr.run_bank_receipt_ocr(split_report, output, workers=1)

    assert first["summary"]["generatedCount"] == 2
    assert second["summary"]["generatedCount"] == 0
    assert second["summary"]["reusedCount"] == 2
    assert len(calls) == 2


def test_bank_ocr_excludes_person_name_indexes_before_processing(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_ocr(path: Path) -> tuple[str, str]:
        calls.append(path.name)
        return "text", "fake"

    output = tmp_path / "ocr" / "bank"
    report = bank_receipt_ocr.run_bank_receipt_ocr(
        _split_report(tmp_path),
        output,
        excluded_indices={"banka": {"A1234567"}},
        ocr_runner=fake_ocr,
    )

    assert sorted(calls) == ["B1234567.pdf"]
    assert report["summary"]["receiptCount"] == 3
    assert report["summary"]["eligibleReceiptCount"] == 1
    assert report["summary"]["excludedBeforeOcrCount"] == 2
    assert any(item["index"] == "A1234567" for item in report["excludedBeforeOcr"])
    assert not (output / "banka" / "A1234567").exists()


def test_bank_ocr_excludes_exact_special_pdf_path_before_processing(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_ocr(path: Path) -> tuple[str, str]:
        calls.append(path.name)
        return "text", "fake"

    split_report = _split_report(tmp_path)
    special_pdf = tmp_path / "bank_receipts" / "banka" / "A1234567.pdf"
    report = bank_receipt_ocr.run_bank_receipt_ocr(
        split_report,
        tmp_path / "ocr" / "bank",
        excluded_pdf_paths={str(special_pdf): "configured_exception"},
        ocr_runner=fake_ocr,
    )

    assert sorted(calls) == ["B1234567.pdf"]
    assert report["summary"]["eligibleReceiptCount"] == 1
    assert report["summary"]["excludedBeforeOcrCount"] == 2
    assert any(
        item["reason"] == "configured_exception"
        for item in report["excludedBeforeOcr"]
    )
