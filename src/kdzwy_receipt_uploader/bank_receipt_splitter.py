"""Split multi-receipt bank PDFs using explicit per-period bank configuration."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import pymupdf as fitz


class BankReceiptSplitError(RuntimeError):
    pass


_FAST_OCR_ENGINE: Any | None = None
_FILENAME_OCR_DPIS = (150, 300, 400, 500)
_BLANK_SLICE_MAX_INK_RATIO = 0.001
_FILENAME_INDEX_LABELS = (
    ("交易流水号", "transaction_serial"),
    ("交易流水", "transaction_serial"),
    ("核心流水号", "transaction_serial"),
    ("回单编号", "receipt_number"),
    ("银行回单号", "receipt_number"),
    ("回单号", "receipt_number"),
)


def _get_fast_ocr_engine() -> Any:
    global _FAST_OCR_ENGINE
    if _FAST_OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _FAST_OCR_ENGINE = RapidOCR()
    return _FAST_OCR_ENGINE


def _matches_filename_rule(value: str, configured_length: int, configured_prefix: str) -> bool:
    candidate = str(value or "").strip()
    return (
        len(candidate) == configured_length
        and candidate.startswith(configured_prefix)
        and re.fullmatch(r"[A-Za-z0-9]+", candidate) is not None
    )


def _extract_filename_index(
    text: str, configured_length: int, configured_prefix: str
) -> tuple[str, str]:
    normalized = str(text or "").replace("\r", "\n")
    for label, source in _FILENAME_INDEX_LABELS:
        pattern = rf"{re.escape(label)}\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9-]{{5,49}})"
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            candidate = match.group(1).strip()
            if _matches_filename_rule(candidate, configured_length, configured_prefix):
                return candidate, source
    escaped_prefix = re.escape(configured_prefix)
    suffix_length = configured_length - len(configured_prefix)
    configured = re.search(
        rf"(?<![A-Za-z0-9])({escaped_prefix}[A-Za-z0-9]{{{suffix_length}}})(?![A-Za-z0-9])",
        normalized,
    )
    if configured:
        return configured.group(1), (
            f"configured_{configured_prefix}_{configured_length}"
        )
    return "", "bank_exception"


def _visible_ink_ratio(pixmap: fitz.Pixmap) -> float:
    """Estimate dark-pixel density without allocating another full image."""
    pixel_count = max(1, int(pixmap.width) * int(pixmap.height))
    channels = max(1, int(pixmap.n))
    sample_step = max(1, pixel_count // 100_000)
    samples = memoryview(pixmap.samples)
    inspected = 0
    dark = 0
    for pixel_index in range(0, pixel_count, sample_step):
        offset = pixel_index * channels
        if offset + min(3, channels) > len(samples):
            break
        inspected += 1
        color_channels = min(3, channels)
        if min(samples[offset : offset + color_channels]) < 220:
            dark += 1
    return dark / max(1, inspected)


def _recognize_filename_index(
    page: fitz.Page, configured_length: int, configured_prefix: str
) -> tuple[str, str, str, bool]:
    native_text = page.get_text("text") or ""
    native_index, native_source = _extract_filename_index(
        native_text, configured_length, configured_prefix
    )
    if native_index:
        return native_index, "pdf-text-fast", native_source, False
    combined_ocr_texts: list[str] = []
    first_pixmap: fitz.Pixmap | None = None
    for dpi in _FILENAME_OCR_DPIS:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        if first_pixmap is None:
            first_pixmap = pixmap
        result, _ = _get_fast_ocr_engine()(pixmap.tobytes("png"))
        lines: list[tuple[float, float, str]] = []
        for row in result or []:
            box, text, score = row
            if float(score) < 0.35 or not text:
                continue
            x = min(float(point[0]) for point in box)
            y = min(float(point[1]) for point in box)
            lines.append((y, x, str(text).strip()))
        ocr_text = "\n".join(item[2] for item in sorted(lines))
        if ocr_text.strip():
            combined_ocr_texts.append(ocr_text)
        combined_text = "\n".join(combined_ocr_texts)
        filename_index, index_source = _extract_filename_index(
            combined_text, configured_length, configured_prefix
        )
        if filename_index:
            return filename_index, f"rapidocr-{dpi}dpi", index_source, False
    ocr_text = "\n".join(combined_ocr_texts)
    blank_slice = (
        not native_text.strip()
        and not ocr_text.strip()
        and first_pixmap is not None
        and _visible_ink_ratio(first_pixmap) <= _BLANK_SLICE_MAX_INK_RATIO
    )
    return "", "rapidocr-multipass-no-index", "bank_exception", blank_slice


def _fingerprint(
    source_pdf: Path,
    parts_per_page: int,
    filename_index_length: int,
    filename_index_prefix: str,
) -> dict[str, Any]:
    stat = source_pdf.stat()
    return {
        "source": str(source_pdf.resolve()),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "partsPerPage": parts_per_page,
        "filenameIndexLength": filename_index_length,
        "filenameIndexPrefix": filename_index_prefix,
        "namingVersion": 8,
    }


def _remove_orphan_pdfs(output_dir: Path, keep_names: list[str]) -> None:
    keep = {Path(name).as_posix() for name in keep_names}
    if not output_dir.is_dir():
        return
    for candidate in output_dir.rglob("*.pdf"):
        relative_name = candidate.relative_to(output_dir).as_posix()
        if relative_name not in keep:
            candidate.unlink()
    directories = sorted(
        (path for path in output_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def _remove_case_variant(output_path: Path) -> None:
    """Remove an existing Windows filename whose casing differs from the new name."""
    parent = output_path.parent
    if not parent.is_dir():
        return
    for candidate in parent.iterdir():
        if (
            candidate.is_file()
            and candidate.name.casefold() == output_path.name.casefold()
            and candidate.name != output_path.name
        ):
            candidate.unlink()


def _is_file_with_exact_name(path: Path) -> bool:
    if not path.parent.is_dir():
        return False
    return any(
        candidate.is_file() and candidate.name == path.name
        for candidate in path.parent.iterdir()
    )


def _split_one_bank(
    source_pdf: Path,
    output_dir: Path,
    bank_key: str,
    parts_per_page: int,
    filename_index_length: int,
    filename_index_prefix: str,
) -> dict[str, Any]:
    manifest_path = output_dir / "split.manifest.json"
    fingerprint = _fingerprint(
        source_pdf, parts_per_page, filename_index_length, filename_index_prefix
    )
    previous_outputs: list[str] = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        outputs = manifest.get("outputs", []) if isinstance(manifest, Mapping) else []
        previous_outputs = [str(name) for name in outputs]
        if (
            manifest.get("fingerprint") == fingerprint
            and outputs
            and all(_is_file_with_exact_name(output_dir / str(name)) for name in outputs)
        ):
            bank_exceptions = manifest.get("bankExceptions", [])
            bank_exception_count = (
                len(bank_exceptions) if isinstance(bank_exceptions, list) else 0
            )
            blank_slices = manifest.get("blankSlices", [])
            blank_slice_count = (
                len(blank_slices) if isinstance(blank_slices, list) else 0
            )
            _remove_orphan_pdfs(output_dir, previous_outputs)
            return {
                "bankKey": bank_key,
                "status": "reused",
                "partsPerPage": parts_per_page,
                "filenameIndexLength": filename_index_length,
                "filenameIndexPrefix": filename_index_prefix,
                "pageCount": manifest.get("pageCount", 0),
                "outputCount": len(outputs),
                "recognizedCount": len(outputs) - bank_exception_count,
                "bankExceptionCount": bank_exception_count,
                "blankSliceCount": blank_slice_count,
                "outputDirectory": str(output_dir.resolve()),
            }

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_names: list[str] = []
    generated_records: list[dict[str, Any]] = []
    bank_exceptions: list[str] = []
    blank_slices: list[dict[str, Any]] = []
    seen_filename_keys: set[str] = set()
    try:
        with fitz.open(source_pdf) as source:
            for page_index in range(source.page_count):
                page = source[page_index]
                width = page.rect.width
                height = page.rect.height
                part_height = height / parts_per_page
                for part_index in range(parts_per_page):
                    top = part_index * part_height
                    bottom = height if part_index == parts_per_page - 1 else (part_index + 1) * part_height
                    clip = fitz.Rect(0, top, width, bottom)
                    with fitz.open() as target:
                        target_page = target.new_page(width=width, height=bottom - top)
                        target_page.show_pdf_page(target_page.rect, source, page_index, clip=clip)
                        filename_index, ocr_engine, index_source, blank_slice = _recognize_filename_index(
                            target_page, filename_index_length, filename_index_prefix
                        )
                        if blank_slice:
                            blank_slices.append({
                                "page": page_index + 1,
                                "part": part_index + 1,
                                "reason": "no_pdf_text_no_ocr_text_low_ink",
                            })
                            generated_records.append({
                                "page": page_index + 1,
                                "part": part_index + 1,
                                "filenameIndex": "",
                                "indexSource": "blank_slice",
                                "ocrEngine": ocr_engine,
                                "file": "",
                                "blank": True,
                            })
                            continue
                        filename_key = filename_index.casefold()
                        if filename_index and filename_key not in seen_filename_keys:
                            relative_name = f"{filename_index}.pdf"
                            seen_filename_keys.add(filename_key)
                        else:
                            exception_dir = output_dir / "bank_exception"
                            exception_dir.mkdir(parents=True, exist_ok=True)
                            relative_name = f"bank_exception/{bank_key}_page_{page_index + 1:04d}_receipt_{part_index + 1:02d}.pdf"
                            bank_exceptions.append(relative_name)
                        output_path = output_dir / relative_name
                        temporary = output_path.with_suffix(".tmp.pdf")
                        target.save(temporary)
                    _remove_case_variant(output_path)
                    temporary.replace(output_path)
                    generated_names.append(relative_name)
                    generated_records.append({
                        "page": page_index + 1,
                        "part": part_index + 1,
                        "filenameIndex": filename_index,
                        "indexSource": index_source,
                        "ocrEngine": ocr_engine,
                        "file": relative_name,
                    })
            page_count = source.page_count
    except Exception as exc:
        raise BankReceiptSplitError(f"银行PDF裁剪失败：{source_pdf}：{exc}") from exc

    _remove_orphan_pdfs(output_dir, generated_names)
    manifest = {
        "version": 1,
        "bankKey": bank_key,
        "fingerprint": fingerprint,
        "pageCount": page_count,
        "outputCount": len(generated_names),
        "outputs": generated_names,
        "records": generated_records,
        "bankExceptions": bank_exceptions,
        "blankSlices": blank_slices,
    }
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return {
        "bankKey": bank_key,
        "status": "generated",
        "partsPerPage": parts_per_page,
        "filenameIndexLength": filename_index_length,
        "filenameIndexPrefix": filename_index_prefix,
        "pageCount": page_count,
        "outputCount": len(generated_names),
        "recognizedCount": len(generated_names) - len(bank_exceptions),
        "bankExceptionCount": len(bank_exceptions),
        "blankSliceCount": len(blank_slices),
        "outputDirectory": str(output_dir.resolve()),
    }


def split_configured_bank_pdfs(
    bank_configs: Mapping[str, Mapping[str, Any]],
    input_dir: Path,
    output_root: Path,
    report_path: Path,
) -> dict[str, Any]:
    if not isinstance(bank_configs, Mapping) or not bank_configs:
        raise BankReceiptSplitError(
            "project.json sources.bank.banks 必须至少配置一家银行"
        )
    results: list[dict[str, Any]] = []
    for bank_key, bank_config in sorted(bank_configs.items()):
        if not isinstance(bank_config, Mapping) or not isinstance(bank_config.get("split"), Mapping):
            raise BankReceiptSplitError(
                f"project.json sources.bank.banks.{bank_key}.split 必须是对象"
            )
        rule = bank_config["split"]
        parts_per_page = int(rule["parts_per_page"])
        filename_index_length = int(rule["filename_index_length"])
        filename_index_prefix = str(rule["filename_index_prefix"])
        source_pdf = input_dir / f"{bank_key}.pdf"
        if not source_pdf.is_file():
            raise BankReceiptSplitError(f"配置中的银行PDF不存在：{source_pdf}")
        results.append(
            _split_one_bank(
                source_pdf,
                output_root / bank_key,
                bank_key,
                parts_per_page,
                filename_index_length,
                filename_index_prefix,
            )
        )
    report = {
        "version": 1,
        "configSource": "project.json.sources.bank.banks",
        "inputDirectory": str(input_dir.resolve()),
        "outputRoot": str(output_root.resolve()),
        "banks": results,
        "summary": {
            "bankCount": len(results),
            "generatedBankCount": sum(1 for item in results if item["status"] == "generated"),
            "reusedBankCount": sum(1 for item in results if item["status"] == "reused"),
            "receiptCount": sum(int(item["outputCount"]) for item in results),
            "recognizedReceiptCount": sum(int(item["recognizedCount"]) for item in results),
            "bankExceptionReceiptCount": sum(
                int(item["bankExceptionCount"]) for item in results
            ),
            "blankSliceCount": sum(
                int(item.get("blankSliceCount", 0)) for item in results
            ),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_report.replace(report_path)
    return report
