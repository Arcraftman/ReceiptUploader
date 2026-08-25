"""Split multi-receipt bank PDFs using explicit per-period bank configuration."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import fitz


class BankReceiptSplitError(RuntimeError):
    pass


_FAST_OCR_ENGINE: Any | None = None
_NUMBER_LABELS = (
    "发票号码", "发票号", "银行回单号", "回单编号", "回单号", "交易流水号",
    "流水号", "凭证号码", "凭证号", "业务编号", "业务流水号", "参考号", "交易序号",
)


def _get_fast_ocr_engine() -> Any:
    global _FAST_OCR_ENGINE
    if _FAST_OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _FAST_OCR_ENGINE = RapidOCR()
    return _FAST_OCR_ENGINE


def _extract_receipt_number(text: str) -> str:
    normalized = str(text or "").replace("\r", "\n")
    for label in _NUMBER_LABELS:
        pattern = rf"{re.escape(label)}\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9-]{{5,49}})"
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().lower()
    invoice_number = re.search(r"(?<!\d)(\d{20})(?!\d)", normalized)
    return invoice_number.group(1) if invoice_number else ""


def _recognize_receipt_number(page: fitz.Page) -> tuple[str, str]:
    native_text = page.get_text("text") or ""
    native_number = _extract_receipt_number(native_text)
    if native_number:
        return native_number, "pdf-text-fast"
    pixmap = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
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
    return _extract_receipt_number(ocr_text), "rapidocr-150dpi-fast"


def _load_rules(config_path: Path) -> dict[str, int]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BankReceiptSplitError(f"无法读取银行裁剪配置：{config_path}：{exc}") from exc
    raw_rules = payload.get("banks") if isinstance(payload, Mapping) and "banks" in payload else payload
    if not isinstance(raw_rules, Mapping) or not raw_rules:
        raise BankReceiptSplitError(f"银行裁剪配置必须是非空key/value对象：{config_path}")
    rules: dict[str, int] = {}
    for raw_key, raw_parts in raw_rules.items():
        key = str(raw_key).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", key):
            raise BankReceiptSplitError(f"银行key只能使用英文小写、数字、下划线或连字符：{raw_key}")
        if isinstance(raw_parts, bool):
            raise BankReceiptSplitError(f"每页回单数量必须是整数：{key}={raw_parts}")
        try:
            parts = int(raw_parts)
        except (TypeError, ValueError) as exc:
            raise BankReceiptSplitError(f"每页回单数量必须是整数：{key}={raw_parts}") from exc
        if parts < 1 or parts > 10:
            raise BankReceiptSplitError(f"每页回单数量必须在1到10之间：{key}={parts}")
        rules[key] = parts
    return rules


def _fingerprint(source_pdf: Path, parts_per_page: int) -> dict[str, Any]:
    stat = source_pdf.stat()
    return {
        "source": str(source_pdf.resolve()),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "partsPerPage": parts_per_page,
        "namingVersion": 2,
    }


def _split_one_bank(source_pdf: Path, output_dir: Path, bank_key: str, parts_per_page: int) -> dict[str, Any]:
    manifest_path = output_dir / "split.manifest.json"
    fingerprint = _fingerprint(source_pdf, parts_per_page)
    previous_outputs: list[str] = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        outputs = manifest.get("outputs", []) if isinstance(manifest, Mapping) else []
        previous_outputs = [str(name) for name in outputs]
        if manifest.get("fingerprint") == fingerprint and outputs and all((output_dir / str(name)).is_file() for name in outputs):
            return {"bankKey": bank_key, "status": "reused", "partsPerPage": parts_per_page, "pageCount": manifest.get("pageCount", 0), "outputCount": len(outputs), "outputDirectory": str(output_dir.resolve())}

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_names: list[str] = []
    generated_records: list[dict[str, Any]] = []
    unresolved: list[str] = []
    seen_numbers: set[str] = set()
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
                        receipt_number, ocr_engine = _recognize_receipt_number(target_page)
                        if receipt_number and receipt_number not in seen_numbers:
                            relative_name = f"{receipt_number}.pdf"
                            seen_numbers.add(receipt_number)
                        else:
                            unresolved_dir = output_dir / "unrecognized"
                            unresolved_dir.mkdir(parents=True, exist_ok=True)
                            relative_name = f"unrecognized/{bank_key}_page_{page_index + 1:04d}_receipt_{part_index + 1:02d}.pdf"
                            unresolved.append(relative_name)
                        output_path = output_dir / relative_name
                        temporary = output_path.with_suffix(".tmp.pdf")
                        target.save(temporary)
                    temporary.replace(output_path)
                    generated_names.append(relative_name)
                    generated_records.append({
                        "page": page_index + 1,
                        "part": part_index + 1,
                        "receiptNumber": receipt_number,
                        "ocrEngine": ocr_engine,
                        "file": relative_name,
                    })
            page_count = source.page_count
    except Exception as exc:
        raise BankReceiptSplitError(f"银行PDF裁剪失败：{source_pdf}：{exc}") from exc

    generated_set = set(generated_names)
    for previous_name in previous_outputs:
        stale = output_dir / previous_name
        if previous_name not in generated_set and stale.is_file():
            stale.unlink()
    manifest = {
        "version": 1,
        "bankKey": bank_key,
        "fingerprint": fingerprint,
        "pageCount": page_count,
        "outputCount": len(generated_names),
        "outputs": generated_names,
        "records": generated_records,
        "unresolved": unresolved,
    }
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    if unresolved:
        raise BankReceiptSplitError(
            f"{bank_key} 有 {len(unresolved)} 张回单未唯一识别号码，已放入 {output_dir / 'unrecognized'}"
        )
    return {"bankKey": bank_key, "status": "generated", "partsPerPage": parts_per_page, "pageCount": page_count, "outputCount": len(generated_names), "outputDirectory": str(output_dir.resolve())}


def split_configured_bank_pdfs(config_path: Path, input_dir: Path, output_root: Path, report_path: Path) -> dict[str, Any]:
    rules = _load_rules(config_path)
    results: list[dict[str, Any]] = []
    for bank_key, parts_per_page in sorted(rules.items()):
        source_pdf = input_dir / f"{bank_key}.pdf"
        if not source_pdf.is_file():
            raise BankReceiptSplitError(f"配置中的银行PDF不存在：{source_pdf}")
        results.append(_split_one_bank(source_pdf, output_root / bank_key, bank_key, parts_per_page))
    report = {
        "version": 1,
        "config": str(config_path.resolve()),
        "inputDirectory": str(input_dir.resolve()),
        "outputRoot": str(output_root.resolve()),
        "banks": results,
        "summary": {
            "bankCount": len(results),
            "generatedBankCount": sum(1 for item in results if item["status"] == "generated"),
            "reusedBankCount": sum(1 for item in results if item["status"] == "reused"),
            "receiptCount": sum(int(item["outputCount"]) for item in results),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_report.replace(report_path)
    return report
