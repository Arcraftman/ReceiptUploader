"""OCR every single-page bank receipt after the complete split stage."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Mapping

import pymupdf


class BankReceiptOcrError(RuntimeError):
    pass


_OCR_ENGINE: Any | None = None
_OCR_CACHE_VERSION = 1


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
        "ocrCacheVersion": _OCR_CACHE_VERSION,
        "nativePdfTextFirst": True,
        "ocrEngine": "rapidocr-onnxruntime",
        "ocrEngineVersion": engine_version,
        "renderDpi": 300,
        "minimumScore": 0.35,
    }


def _extract_text(pdf_path: Path) -> tuple[str, str]:
    with pymupdf.open(pdf_path) as document:
        native_text = "\n".join((page.get_text("text") or "").strip() for page in document).strip()
        if native_text:
            return native_text, "pymupdf-native-text"
        engine = _get_ocr_engine()
        lines: list[tuple[int, float, float, str]] = []
        for page_index, page in enumerate(document):
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False
            )
            result, _ = engine(pixmap.tobytes("png"))
            for row in result or []:
                box, text, score = row
                if float(score) < 0.35 or not text:
                    continue
                x = min(float(point[0]) for point in box)
                y = min(float(point[1]) for point in box)
                lines.append((page_index, y, x, str(text).strip()))
        return "\n".join(item[3] for item in sorted(lines)).strip(), "rapidocr-onnxruntime"


def _safe_resolve_inside(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise BankReceiptOcrError(f"{label} 越过银行生成目录：{resolved}") from exc
    return resolved


def _discover_split_receipts(split_report: Mapping[str, Any]) -> list[tuple[str, Path, str]]:
    receipts: list[tuple[str, Path, str]] = []
    banks = split_report.get("banks")
    if not isinstance(banks, list) or not banks:
        raise BankReceiptOcrError("银行裁剪报告没有银行结果")
    for raw_bank in banks:
        if not isinstance(raw_bank, Mapping):
            raise BankReceiptOcrError("银行裁剪报告中的银行结果格式错误")
        bank_key = str(raw_bank.get("bankKey") or "").strip()
        output_directory = Path(str(raw_bank.get("outputDirectory") or ""))
        manifest_path = output_directory / "split.manifest.json"
        if not bank_key or not manifest_path.is_file():
            raise BankReceiptOcrError(f"银行裁剪 manifest 不存在：{manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        outputs = manifest.get("outputs") if isinstance(manifest, Mapping) else None
        if not isinstance(outputs, list) or not outputs:
            raise BankReceiptOcrError(f"银行裁剪 manifest 没有输出：{manifest_path}")
        for raw_relative in outputs:
            relative = Path(str(raw_relative))
            pdf_path = _safe_resolve_inside(output_directory / relative, output_directory, "裁剪回单")
            if not pdf_path.is_file():
                raise BankReceiptOcrError(f"裁剪回单不存在：{pdf_path}")
            artifact_relative = (Path(bank_key) / relative).with_suffix("").as_posix()
            receipts.append((bank_key, pdf_path, artifact_relative))
    return receipts


def _write_one_artifact(
    bank_key: str,
    pdf_path: Path,
    artifact_relative: str,
    output_root: Path,
    company: str = "",
    ocr_runner: Callable[[Path], tuple[str, str]] | None = None,
) -> dict[str, Any]:
    artifact_directory = _safe_resolve_inside(
        output_root / Path(artifact_relative), output_root, "银行 OCR 产物"
    )
    artifact_directory.mkdir(parents=True, exist_ok=True)
    text_path = artifact_directory / "ocr.txt"
    metadata_path = artifact_directory / "ocr.json"
    fingerprint = _source_fingerprint(pdf_path)
    if ocr_runner is None and text_path.is_file() and metadata_path.is_file():
        try:
            cached = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            if (
                cached.get("sourceFingerprint") == fingerprint
                and Path(str(cached.get("sourcePdf") or "")).resolve() == pdf_path.resolve()
                and str(cached.get("configCompany") or "") == company
                and cached.get("status") == "success"
            ):
                cached_text = text_path.read_text(encoding="utf-8")
                return {
                    "bankKey": bank_key,
                    "sourcePdf": str(pdf_path.resolve()),
                    "artifactDirectory": artifact_relative,
                    "metadata": str(metadata_path.resolve()),
                    "status": "success",
                    "engine": str(cached.get("engine") or "cached"),
                    "textLength": len(cached_text),
                    "cacheStatus": "reused",
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    text, engine = (ocr_runner or _extract_text)(pdf_path)
    text_path.write_text(text, encoding="utf-8")
    status = "success" if text else "empty_text"
    metadata = {
        "version": 1,
        "bankKey": bank_key,
        "sourceFolder": "bank",
        "sourceSide": "bank",
        "configCompany": company,
        "partyRule": {
            "configuredCompanyRole": "account_owner",
            "counterpartyRole": "transaction_counterparty",
            "mapSource": "source",
            "allowedTemplateBlocks": ["银行", "费用"],
        },
        "filenameIndex": pdf_path.stem,
        "sourcePdf": str(pdf_path.resolve()),
        "sourceFingerprint": fingerprint,
        "ocrText": str(text_path.resolve()),
        "engine": engine,
        "status": status,
        "textLength": len(text),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "bankKey": bank_key,
        "sourcePdf": str(pdf_path.resolve()),
        "artifactDirectory": artifact_relative,
        "metadata": str(metadata_path.resolve()),
        "status": status,
        "engine": engine,
        "textLength": len(text),
        "cacheStatus": "generated",
    }


def _remove_stale_artifacts(output_root: Path, previous_report: Mapping[str, Any], keep: set[str]) -> None:
    for raw_relative in previous_report.get("artifactDirectories", []):
        relative = str(raw_relative)
        if not relative or relative in keep:
            continue
        candidate = _safe_resolve_inside(output_root / Path(relative), output_root, "旧银行 OCR 产物")
        if candidate.is_dir():
            shutil.rmtree(candidate)
    directories = sorted(
        (path for path in output_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def run_bank_receipt_ocr(
    split_report: Mapping[str, Any],
    output_root: Path,
    *,
    workers: int = 2,
    company: str = "",
    excluded_indices: Mapping[str, set[str]] | None = None,
    excluded_pdf_paths: Mapping[str, str] | None = None,
    ocr_runner: Callable[[Path], tuple[str, str]] | None = None,
) -> dict[str, Any]:
    discovered_receipts = _discover_split_receipts(split_report)
    normalized_exclusions = {
        str(bank_key): {str(index) for index in indexes}
        for bank_key, indexes in (excluded_indices or {}).items()
    }
    normalized_pdf_exclusions = {
        str(Path(path).resolve()): str(reason or "configured_exception")
        for path, reason in (excluded_pdf_paths or {}).items()
    }
    receipts: list[tuple[str, Path, str]] = []
    excluded_before_ocr: list[dict[str, str]] = []
    for bank_key, pdf_path, artifact_relative in discovered_receipts:
        resolved_pdf = str(pdf_path.resolve())
        if "bank_exception" in Path(artifact_relative).parts:
            excluded_before_ocr.append(
                {
                    "bankKey": bank_key,
                    "index": "",
                    "pdf": resolved_pdf,
                    "reason": "split_bank_exception",
                }
            )
            continue
        if resolved_pdf in normalized_pdf_exclusions:
            excluded_before_ocr.append(
                {
                    "bankKey": bank_key,
                    "index": pdf_path.stem,
                    "pdf": resolved_pdf,
                    "reason": normalized_pdf_exclusions[resolved_pdf],
                }
            )
            continue
        if pdf_path.stem in normalized_exclusions.get(bank_key, set()):
            excluded_before_ocr.append(
                {
                    "bankKey": bank_key,
                    "index": pdf_path.stem,
                    "pdf": str(pdf_path.resolve()),
                    "reason": "person_name",
                }
            )
            continue
        receipts.append((bank_key, pdf_path, artifact_relative))
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "ocr_stage.report.json"
    previous_report: Mapping[str, Any] = {}
    if report_path.is_file():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8-sig"))
            previous_report = loaded if isinstance(loaded, Mapping) else {}
        except (OSError, json.JSONDecodeError):
            previous_report = {}
    keep = {artifact_relative for _, _, artifact_relative in receipts}
    _remove_stale_artifacts(output_root, previous_report, keep)

    try:
        worker_count = max(1, int(workers))
    except (TypeError, ValueError):
        worker_count = 2
    worker_count = min(worker_count, max(1, len(receipts)))
    if ocr_runner is not None:
        worker_count = 1
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    started = time.time()
    if worker_count > 1:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            pending = [
                (
                    pdf_path,
                    executor.submit(
                        _write_one_artifact,
                        bank_key,
                        pdf_path,
                        artifact_relative,
                        output_root,
                        company,
                        None,
                    ),
                )
                for bank_key, pdf_path, artifact_relative in receipts
            ]
            for pdf_path, future in pending:
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append({"pdf": str(pdf_path), "error": str(exc)})
    else:
        for bank_key, pdf_path, artifact_relative in receipts:
            try:
                results.append(
                    _write_one_artifact(
                        bank_key,
                        pdf_path,
                        artifact_relative,
                        output_root,
                        company,
                        ocr_runner,
                    )
                )
            except Exception as exc:
                errors.append({"pdf": str(pdf_path), "error": str(exc)})

    generated_count = sum(item["cacheStatus"] == "generated" for item in results)
    reused_count = sum(item["cacheStatus"] == "reused" for item in results)
    success_count = sum(item["status"] == "success" for item in results)
    report = {
        "version": 1,
        "source": "bank_receipt_outputs",
        "configCompany": company,
        "outputDirectory": str(output_root.resolve()),
        "artifactDirectories": sorted(keep),
        "artifacts": results,
        "excludedBeforeOcr": excluded_before_ocr,
        "errors": errors,
        "summary": {
            "receiptCount": len(discovered_receipts),
            "eligibleReceiptCount": len(receipts),
            "excludedBeforeOcrCount": len(excluded_before_ocr),
            "processedCount": len(results),
            "generatedCount": generated_count,
            "reusedCount": reused_count,
            "successTextCount": success_count,
            "emptyTextCount": len(results) - success_count,
            "errorCount": len(errors),
            "workerCount": worker_count,
            "elapsedSeconds": round(time.time() - started, 3),
        },
    }
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(report_path)
    return report
