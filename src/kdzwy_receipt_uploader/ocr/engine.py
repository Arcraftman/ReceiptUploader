"""PDF OCR execution, cache validation and artifact loading."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..matching import pdf_invoice_number
from ..receipt_generation import discover_source_pdfs
from ..source_profile import source_from_folder_name
from .fields import apply_folder_party_rule, extract_invoice_fields
from .models import OcrArtifact, OcrPipelineError

_OCR_ENGINE: Any | None = None
_OCR_CACHE_VERSION = 11
_OCR_RENDER_DPIS = (300, 400, 500)


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _source_fingerprint(pdf_path: Path) -> dict[str, Any]:
    stat = pdf_path.stat()
    try:
        engine_version = importlib_metadata.version("rapidocr-onnxruntime")
    except importlib_metadata.PackageNotFoundError:
        engine_version = "unavailable"
    return {
        "size": stat.st_size,
        "modifiedNs": stat.st_mtime_ns,
        "ocrEngine": "rapidocr-onnxruntime",
        "ocrEngineVersion": engine_version,
        "nativePdfTextIncluded": True,
        "renderDpis": list(_OCR_RENDER_DPIS),
        "selection": "critical-field-completeness",
        "minimumScore": 0.35,
    }


def discover_pdf_files(month_directory: Path, folder_patterns: Iterable[str] = ("sales", "purchase", "bank", "misc"), allowed_invoice_codes: set[str] | None = None) -> list[Path]:
    indexed, _ = discover_source_pdfs(month_directory, list(folder_patterns))
    pdfs = sorted({pdf.resolve() for paths in indexed.values() for pdf in paths})
    if allowed_invoice_codes is None:
        return pdfs
    return [pdf for pdf in pdfs if pdf_invoice_number(pdf) in allowed_invoice_codes]


def _merge_ocr_candidates(candidates: list[tuple[str, str]]) -> str:
    """Merge unique lines from native text and OCR passes without hiding provenance."""
    merged: list[str] = []
    seen: set[str] = set()
    for text, _ in candidates:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            key = re.sub(r"\s+", "", line).lower()
            if line and key and key not in seen:
                seen.add(key)
                merged.append(line)
    return "\n".join(merged).strip()


def _ocr_candidate_quality(text: str, expected_invoice_code: str) -> tuple[int, int, int, float, int]:
    fields = extract_invoice_fields(text)
    invoice_number = re.sub(r"\D", "", str(fields.get("invoiceNumber") or ""))
    exact_invoice = int(bool(expected_invoice_code) and invoice_number == expected_invoice_code)
    document_kind = str(fields.get("documentKind") or "vat_invoice")
    if document_kind == "railway_ticket":
        required = (
            "invoiceNumber",
            "issueDate",
            "buyer",
            "totalAmountWithTax",
            "travelDate",
            "trainNumber",
            "seatClass",
        )
    elif document_kind == "air_ticket":
        required = (
            "invoiceNumber",
            "issueDate",
            "buyer",
            "seller",
            "totalAmountWithTax",
            "travelDate",
            "flightNumber",
            "passengerName",
        )
    else:
        required = ("invoiceNumber", "issueDate", "buyer", "seller", "totalAmountWithTax", "taxRate")
    present = sum(1 for name in required if str(fields.get(name) or "").strip())
    confidence = fields.get("fieldConfidence")
    confidence_total = (
        sum(float(confidence.get(name, 0) or 0) for name in required)
        if isinstance(confidence, Mapping)
        else 0.0
    )
    return exact_invoice, int(bool(fields.get("criticalFieldsReady"))), present, confidence_total, min(len(text), 20000)


def _ocr_candidate_complete(text: str, expected_invoice_code: str) -> bool:
    fields = extract_invoice_fields(text)
    invoice_number = re.sub(r"\D", "", str(fields.get("invoiceNumber") or ""))
    return invoice_number == expected_invoice_code and bool(fields.get("criticalFieldsReady"))


def _default_ocr(pdf_path: Path) -> tuple[str, str]:
    """Use native PDF text plus adaptive multi-DPI OCR in accuracy-first mode."""
    try:
        import pymupdf  # type: ignore
        with pymupdf.open(str(pdf_path)) as document:
            engine = _get_ocr_engine()
            expected_invoice_code = pdf_invoice_number(pdf_path)
            candidates: list[tuple[str, str]] = []

            native_parts = [page.get_text("text", sort=True).strip() for page in document]
            native_text = "\n".join(part for part in native_parts if part).strip()
            if native_text:
                candidates.append((native_text, "pymupdf-native-text"))

            for dpi in _OCR_RENDER_DPIS:
                lines: list[tuple[int, float, float, str]] = []
                for page_index, page in enumerate(document):
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False
                    )
                    result, _ = engine(pixmap.tobytes("png"))
                    for row in result or []:
                        box, text, score = row
                        if float(score) >= 0.35 and text:
                            x = min(float(point[0]) for point in box)
                            y = min(float(point[1]) for point in box)
                            lines.append((page_index, y, x, str(text).strip()))
                ocr_text = "\n".join(item[3] for item in sorted(lines)).strip()
                if ocr_text:
                    candidates.append((ocr_text, f"rapidocr-onnxruntime-{dpi}dpi"))

                merged_text = _merge_ocr_candidates(candidates)
                if merged_text:
                    merged_label = "+".join(label for _, label in candidates)
                    scored = candidates + [(merged_text, f"merged:{merged_label}")]
                    best_text, best_engine = max(
                        scored,
                        key=lambda item: _ocr_candidate_quality(item[0], expected_invoice_code),
                    )
                    if _ocr_candidate_complete(best_text, expected_invoice_code):
                        return best_text, best_engine

            if candidates:
                merged_text = _merge_ocr_candidates(candidates)
                merged_label = "+".join(label for _, label in candidates)
                scored = candidates + [(merged_text, f"merged:{merged_label}")]
                return max(
                    scored,
                    key=lambda item: _ocr_candidate_quality(item[0], expected_invoice_code),
                )
    except Exception as exc:
        return "", f"ocr_error:{type(exc).__name__}"
    return "", "ocr_unavailable"


def run_pdf_ocr(pdf_path: Path, output_dir: Path, ocr_runner: Callable[[Path], tuple[str, str]] | None = None, company: str = "", source_month_directory: Path | None = None) -> OcrArtifact:
    invoice_code = pdf_invoice_number(pdf_path)
    if not invoice_code:
        raise OcrPipelineError(f"PDF 文件名无法提取发票号：{pdf_path}")
    source_root = (source_month_directory or pdf_path.parent).resolve()
    try:
        relative = pdf_path.resolve().relative_to(source_root)
        source_folder = next((part for part in relative.parts if source_from_folder_name(part)), pdf_path.parent.name)
    except ValueError:
        source_folder = pdf_path.parent.name
    source_key = source_from_folder_name(source_folder)
    source_side = "sales" if source_key == "sales" else "purchase" if source_key == "purchase" else "bank" if source_key == "bank" else "misc" if source_key == "misc" else "unknown"
    # output_dir is already source-specific (for example generated/ocr/sales).
    # Do not append source_folder again or copy the source PDF into OCR output.
    target = output_dir / invoice_code
    target.mkdir(parents=True, exist_ok=True)
    text_path = target / "ocr.txt"
    metadata_path = target / "ocr.json"
    source_fingerprint = _source_fingerprint(pdf_path)
    if ocr_runner is None and text_path.is_file() and metadata_path.is_file():
        try:
            cached_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            same_source = Path(str(cached_metadata.get("sourcePdf", ""))).resolve() == pdf_path.resolve()
            same_company = str(cached_metadata.get("configCompany", "")) == str(company or "")
            same_fingerprint = cached_metadata.get("sourceFingerprint") == source_fingerprint
            same_cache_version = cached_metadata.get("ocrCacheVersion") == _OCR_CACHE_VERSION
            if same_source and same_company and same_fingerprint and same_cache_version and cached_metadata.get("status") == "success":
                cached_text = text_path.read_text(encoding="utf-8")
                return OcrArtifact(
                    invoice_code,
                    pdf_path,
                    source_folder,
                    source_side,
                    target,
                    text_path,
                    metadata_path,
                    cached_text,
                    str(cached_metadata.get("engine", "cached")),
                    "success",
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    text, engine = (ocr_runner or _default_ocr)(pdf_path)
    text_path.write_text(text, encoding="utf-8")
    metadata = {
        "invoiceCode": invoice_code,
        "sourceFolder": source_folder,
        "sourceSide": source_side,
        "configCompany": company,
        "partyRule": {
            "salesFolderCompanyRole": "seller",
            "purchaseFolderCompanyRole": "buyer",
            "counterpartySource": "OCR",
            "sameCompanyCounterpartyAllowed": True,
            "authoritative": "sourceFolder",
        },
        "fields": apply_folder_party_rule(extract_invoice_fields(text), source_folder, company),
        "sourcePdf": str(pdf_path.resolve()),
        "sourceFingerprint": source_fingerprint,
        "ocrCacheVersion": _OCR_CACHE_VERSION,
        "ocrText": str(text_path.resolve()),
        "engine": engine,
        "status": "success" if text else engine,
        "textLength": len(text),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return OcrArtifact(invoice_code, pdf_path, source_folder, source_side, target, text_path, metadata_path, text, engine, metadata["status"])


def run_ocr_stage(
    month_directory: Path,
    output_directory: Path,
    folder_patterns: Iterable[str] = ("sales", "purchase", "bank", "misc"),
    ocr_runner: Callable[[Path], tuple[str, str]] | None = None,
    company: str = "",
    allowed_invoice_codes: set[str] | None = None,
    return_artifacts: bool = False,
    workers: int | None = None,
) -> dict[str, Any] | tuple[dict[str, Any], list[OcrArtifact]]:
    logger = logging.getLogger("run_pipeline")
    logger.setLevel(logging.INFO)
    artifacts: list[OcrArtifact] = []
    errors: list[dict[str, str]] = []
    all_pdfs = discover_pdf_files(month_directory, folder_patterns, allowed_invoice_codes=allowed_invoice_codes)
    max_files = os.environ.get("OCR_MAX_FILES")
    try:
        max_count = int(max_files) if max_files else None
    except ValueError:
        max_count = None
    if max_count is not None and max_count > 0:
        all_pdfs = all_pdfs[:max_count]
        logger.warning("OCR_MAX_FILES 生效：仅处理前 %s 张 PDF", max_count)
    total = len(all_pdfs)
    configured_workers = workers if workers is not None else os.environ.get("OCR_WORKERS", "2")
    try:
        worker_count = max(1, int(configured_workers))
    except (TypeError, ValueError):
        worker_count = 2
    worker_count = min(worker_count, max(1, total))
    # Custom runners may be closures and are not guaranteed to be process-safe.
    if ocr_runner is not None:
        worker_count = 1
    progress_every = int(os.environ.get("OCR_PROGRESS_EVERY", "10"))
    if progress_every <= 0:
        progress_every = 10
    start = time.time()
    if worker_count > 1:
        logger.info("OCR 有限并行已启用：%s 个工作进程", worker_count)
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            pending = [
                (pdf, executor.submit(run_pdf_ocr, pdf, output_directory, None, company, month_directory))
                for pdf in all_pdfs
            ]
            for index, (pdf, future) in enumerate(pending, start=1):
                if index == 1 or index % progress_every == 0 or index == total:
                    logger.info("OCR 处理中 %s/%s：%s", index, total, pdf.name)
                try:
                    artifacts.append(future.result())
                except Exception as exc:
                    errors.append({"pdf": str(pdf), "error": str(exc)})
    else:
        for index, pdf in enumerate(all_pdfs, start=1):
            if index == 1 or index % progress_every == 0 or index == total:
                logger.info("OCR 处理中 %s/%s：%s", index, total, pdf.name)
            try:
                artifacts.append(run_pdf_ocr(pdf, output_directory, ocr_runner, company=company, source_month_directory=month_directory))
            except Exception as exc:
                errors.append({"pdf": str(pdf), "error": str(exc)})
    elapsed = time.time() - start
    text_success_count = sum(bool(item.text) for item in artifacts)
    empty_text_count = len(artifacts) - text_success_count
    logger.info(
        "OCR 阶段完成：%s 有文本、%s 无文本、%s 异常，耗时 %.1f 秒",
        text_success_count,
        empty_text_count,
        len(errors),
        elapsed,
    )
    report = {
        "sourceDirectory": str(month_directory.resolve()),
        "outputDirectory": str(output_directory.resolve()),
        "allowedInvoiceCodes": sorted(allowed_invoice_codes) if allowed_invoice_codes is not None else None,
        "filterRule": "仅处理 allowedInvoiceCodes；purchase 的 allowedInvoiceCodes 必须来自用途确认信息.xlsx 匹配后且存在PDF的发票号",
        "artifacts": [str(item.metadata_path.resolve()) for item in artifacts],
        "errors": errors,
        "summary": {
            "pdfCount": len(artifacts),
            "workerCount": worker_count,
            "allowedInvoiceCodeCount": len(allowed_invoice_codes) if allowed_invoice_codes is not None else None,
            "errorCount": len(errors),
            "successTextCount": text_success_count,
            "emptyTextCount": empty_text_count,
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "ocr_stage.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if return_artifacts:
        return report, artifacts
    return report


def load_ocr_artifacts(output_directory: Path, allowed_invoice_codes: set[str] | None = None) -> list[OcrArtifact]:
    """Load completed OCR artifacts without opening or OCR-rendering source PDFs."""
    report_path = output_directory / "ocr_stage.report.json"
    if not report_path.is_file():
        raise OcrPipelineError(f"Qwen阶段缺少OCR报告，请先运行 --stage ocr：{report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reported_output = Path(str(report.get("outputDirectory") or output_directory))

    def resolve_current_artifact_path(value: object, fallback: Path | None = None) -> Path:
        configured = Path(str(value or fallback or ""))
        candidates: list[Path] = []
        if configured.is_absolute():
            try:
                candidates.append(output_directory / configured.relative_to(reported_output))
            except ValueError:
                pass
        elif str(configured):
            candidates.append(output_directory / configured)
        if fallback is not None:
            candidates.append(fallback)
        candidates.append(configured)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    artifacts: list[OcrArtifact] = []
    for raw_path in report.get("artifacts", []):
        metadata_value = raw_path.get("metadata") if isinstance(raw_path, dict) else raw_path
        metadata_path = resolve_current_artifact_path(metadata_value)
        if not metadata_path.is_file():
            raise OcrPipelineError(f"OCR元数据不存在：{metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        invoice_code = str(metadata.get("invoiceCode") or "")
        if allowed_invoice_codes is not None and invoice_code not in allowed_invoice_codes:
            continue
        text_path = resolve_current_artifact_path(
            metadata.get("ocrText"),
            metadata_path.with_name("ocr.txt"),
        )
        source_pdf = Path(str(metadata.get("sourcePdf") or ""))
        if not invoice_code or not text_path.is_file():
            raise OcrPipelineError(f"OCR产物不完整：{metadata_path}")
        artifacts.append(OcrArtifact(
            invoice_code=invoice_code,
            source_pdf=source_pdf,
            source_folder=str(metadata.get("sourceFolder") or metadata_path.parent.parent.name),
            source_side=str(metadata.get("sourceSide") or "unknown"),
            output_dir=metadata_path.parent,
            text_path=text_path,
            metadata_path=metadata_path,
            text=text_path.read_text(encoding="utf-8"),
            engine=str(metadata.get("engine") or "saved-ocr"),
            status=str(metadata.get("status") or "unknown"),
        ))
    if not artifacts:
        raise OcrPipelineError("没有可供Qwen分析的OCR产物，请先运行 --stage ocr")
    return artifacts
