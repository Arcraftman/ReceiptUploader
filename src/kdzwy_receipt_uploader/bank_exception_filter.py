"""Physically separate configured bank exception names before ordinary OCR."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import filecmp
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

import pymupdf

from .bank_receipt_ocr import _extract_text as _extract_bank_receipt_text
from .bank_statement_matcher import read_bank_statement_rows


class BankExceptionFilterError(RuntimeError):
    pass


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_bank_exception_pdf_keywords(path: Path) -> dict[str, list[str]]:
    """Load technical PDF keyword rules kept outside the month name list."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BankExceptionFilterError(f"无法读取银行 exception 默认规则：{path}：{exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 2
        or set(payload) != {"version", "exceptions", "pdf_keywords"}
    ):
        raise BankExceptionFilterError(
            f"银行 exception 默认规则必须是 version 2：{path}"
        )
    raw_rules = payload.get("pdf_keywords")
    if not isinstance(raw_rules, dict):
        raise BankExceptionFilterError(f"pdf_keywords 必须是对象：{path}")
    normalized: dict[str, list[str]] = {}
    for raw_name, raw_keywords in raw_rules.items():
        name = str(raw_name).strip()
        if (
            not name
            or not isinstance(raw_keywords, list)
            or not raw_keywords
            or any(
                not isinstance(keyword, str) or not keyword.strip()
                for keyword in raw_keywords
            )
        ):
            raise BankExceptionFilterError(
                f"pdf_keywords.{raw_name} 必须是非空文本数组"
            )
        normalized[name] = [keyword.strip() for keyword in raw_keywords]
    return normalized


def _money(value: object) -> Decimal | None:
    text = str(value or "").replace(",", "").replace("￥", "").strip()
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _statement_date(index: object) -> str:
    text = str(index or "").strip()
    match = re.match(r"^[A-Za-z]0(\d{6})", text)
    if match:
        return "20" + match.group(1)
    match = re.search(r"(20\d{6})", text)
    return match.group(1) if match else ""


def _pdf_text(pdf_path: Path) -> str:
    try:
        with pymupdf.open(pdf_path) as document:
            native_text = "\n".join(page.get_text("text", sort=True) for page in document)
        if native_text.strip():
            return native_text
        ocr_text, _ = _extract_bank_receipt_text(pdf_path)
        return ocr_text
    except Exception as exc:
        raise BankExceptionFilterError(f"无法读取切割后的 PDF：{pdf_path}：{exc}") from exc


def _pdf_signature(text: str) -> tuple[str, Decimal | None]:
    date_match = re.search(r"记账日期\s*[:：]?\s*(20\d{6})", text)
    amount_match = re.search(
        r"小写\s*[（(]?合计[）)]?\s*金额\s*[:：]?\s*[￥¥]?\s*([\d,]+(?:\.\d{1,2})?)",
        text,
    )
    return (
        date_match.group(1) if date_match else "",
        _money(amount_match.group(1)) if amount_match else None,
    )


def _safe_component(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return safe or "unnamed"


def _discover_split_pdfs(split_report: Mapping[str, Any]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for bank in split_report.get("banks", []) or []:
        if not isinstance(bank, Mapping):
            continue
        bank_key = str(bank.get("bankKey") or "")
        output_directory = Path(str(bank.get("outputDirectory") or ""))
        manifest_path = output_directory / "split.manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BankExceptionFilterError(
                f"无法读取银行裁剪清单：{manifest_path}：{exc}"
            ) from exc
        for raw_relative in manifest.get("outputs", []) or []:
            pdf_path = output_directory / Path(str(raw_relative))
            if pdf_path.is_file() and pdf_path.suffix.lower() == ".pdf":
                result.append((bank_key, pdf_path.resolve()))
    return result


def _copy_special_pdf(
    source: Path,
    output_root: Path,
    party_name: str,
    key: str,
) -> Path:
    destination = output_root / _safe_component(party_name) / f"{_safe_component(key)}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or not filecmp.cmp(source, destination, shallow=False):
        shutil.copy2(source, destination)
    return destination.resolve()


def _remove_stale_copies(
    output_root: Path,
    manifest_path: Path,
    desired: set[str],
) -> None:
    if not manifest_path.is_file():
        return
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    root = output_root.resolve()
    for item in (previous.get("entries") or {}).values():
        if not isinstance(item, Mapping):
            continue
        raw_path = str(item.get("copiedPdf") or "")
        if not raw_path or raw_path in desired:
            continue
        candidate = Path(raw_path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            candidate.unlink()
            parent = candidate.parent
            while parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def filter_bank_exception_pdfs(
    bank_configs: Mapping[str, Mapping[str, Any]],
    exceptions: Sequence[str],
    input_directory: Path,
    split_report: Mapping[str, Any],
    output_root: Path,
    manifest_path: Path,
    config_company: str = "",
    pdf_keyword_rules: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Copy exact-name exception PDFs aside and return ordinary-pipeline exclusions."""
    exception_names = [str(name).strip() for name in exceptions if str(name).strip()]
    exception_name_set = set(exception_names)
    statement_rows = read_bank_statement_rows(
        bank_configs, input_directory, config_company
    )
    split_pdfs = _discover_split_pdfs(split_report)
    split_exception_pdfs = [
        (bank_key, pdf_path)
        for bank_key, pdf_path in split_pdfs
        if pdf_path.parent.name == "bank_exception"
    ]
    pdf_by_index = {
        (bank_key, pdf_path.stem): pdf_path
        for bank_key, pdf_path in split_pdfs
        if pdf_path.parent.name != "bank_exception"
    }

    configured_records: dict[str, dict[str, Any]] = {}
    for bank_key, rows in statement_rows.items():
        for row in rows:
            party_name = str(row.get("counterpartyName") or "").strip()
            if party_name not in exception_name_set:
                continue
            index = str(row.get("index") or "")
            key = f"{bank_key}__{index}"
            configured_records[key] = {
                "key": key,
                "bankKey": bank_key,
                "index": index,
                "counterpartyName": party_name,
                "record": dict(row),
                "sourcePdf": None,
                "matchMethod": "missing",
            }

    keyword_rules = {
        name: [str(keyword) for keyword in keywords if str(keyword)]
        for name, keywords in (pdf_keyword_rules or {}).items()
        if name in exception_name_set
    }
    orphan_records: dict[str, dict[str, Any]] = {}
    used_pdfs: set[str] = set()

    # Step 1: associate split-time bank exceptions using system-maintained keywords.
    for bank_key, pdf_path in split_pdfs:
        if not keyword_rules:
            break
        text = _pdf_text(pdf_path)
        matches = [
            name
            for name, keywords in keyword_rules.items()
            if any(keyword in text for keyword in keywords)
        ]
        if not matches:
            continue
        if len(matches) != 1:
            raise BankExceptionFilterError(
                f"特殊 PDF 同时命中多个 exception 名称：{pdf_path}"
            )
        party_name = matches[0]
        receipt_date, receipt_amount = _pdf_signature(text)
        candidates = [
            item
            for item in configured_records.values()
            if item["bankKey"] == bank_key
            and item["counterpartyName"] == party_name
            and item["sourcePdf"] is None
            and _money(item["record"].get("transactionAmount"))
            == receipt_amount
            and (
                not receipt_date
                or _statement_date(item["index"]) == receipt_date
            )
        ]
        if len(candidates) == 1:
            candidates[0]["sourcePdf"] = pdf_path
            candidates[0]["matchMethod"] = "pdf_keyword_date_amount"
        else:
            key = f"{bank_key}__pdf__{pdf_path.stem}"
            orphan_records[key] = {
                "key": key,
                "bankKey": bank_key,
                "index": "",
                "counterpartyName": party_name,
                "record": {},
                "sourcePdf": pdf_path,
                "matchMethod": "pdf_keyword_only",
            }
        used_pdfs.add(str(pdf_path.resolve()))

    # Every split without a valid naming index is a bank exception, even when it
    # cannot be associated with a configured counterparty name.
    for bank_key, pdf_path in split_exception_pdfs:
        resolved = str(pdf_path.resolve())
        if resolved in used_pdfs:
            continue
        key = f"{bank_key}__split_exception__{pdf_path.stem}"
        orphan_records[key] = {
            "key": key,
            "bankKey": bank_key,
            "index": "",
            "counterpartyName": "_无命名索引",
            "record": {},
            "sourcePdf": pdf_path,
            "matchMethod": "split_bank_exception",
        }
        used_pdfs.add(resolved)

    # Step 2: isolate every remaining configured name by its statement index.
    for item in configured_records.values():
        if item["sourcePdf"] is not None:
            continue
        direct_pdf = pdf_by_index.get((item["bankKey"], item["index"]))
        if direct_pdf is not None:
            item["sourcePdf"] = direct_pdf
            item["matchMethod"] = "statement_index"
            used_pdfs.add(str(direct_pdf.resolve()))

    entries: dict[str, dict[str, Any]] = {}
    desired_copies: set[str] = set()
    for key, item in sorted({**configured_records, **orphan_records}.items()):
        record = item["record"]
        source_pdf = item["sourcePdf"]
        copied_pdf = None
        if isinstance(source_pdf, Path):
            copied_pdf = _copy_special_pdf(
                source_pdf,
                output_root,
                item["counterpartyName"],
                key,
            )
            desired_copies.add(str(copied_pdf))
        entries[key] = {
            "key": key,
            "bankKey": item["bankKey"],
            "index": item["index"],
            "counterpartyName": item["counterpartyName"],
            "matchMethod": item["matchMethod"],
            "flowDirection": str(record.get("flowDirection") or ""),
            "amount": record.get("transactionAmount"),
            "statement": record.get("statement"),
            "sourcePdf": str(source_pdf.resolve()) if isinstance(source_pdf, Path) else None,
            "copiedPdf": str(copied_pdf) if copied_pdf else None,
            "pdfStatus": "separated" if copied_pdf else "missing",
            "downstreamEligible": False,
        }

    _remove_stale_copies(output_root, manifest_path, desired_copies)
    excluded_statement_indices: dict[str, list[str]] = {}
    for item in configured_records.values():
        excluded_statement_indices.setdefault(item["bankKey"], []).append(item["index"])
    result = {
        "version": 3,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "configSource": "project.json.sources.bank.exceptions",
        "specialPdfDirectory": str(output_root.resolve()),
        "entries": entries,
        "excludedStatementIndices": {
            bank_key: sorted(indexes)
            for bank_key, indexes in sorted(excluded_statement_indices.items())
        },
        "excludedPdfPaths": {
            path: "configured_exception" for path in sorted(used_pdfs)
        },
        "summary": {
            "exceptionNameCount": len(exception_names),
            "exceptionStatementCount": len(configured_records),
            "splitExceptionPdfCount": len(split_exception_pdfs),
            "exceptionPdfCount": len(used_pdfs),
            "copiedPdfCount": len(desired_copies),
            "missingPdfCount": sum(
                item["pdfStatus"] == "missing" for item in entries.values()
            ),
        },
    }
    _write_json(manifest_path, result)
    return result


def quarantine_bank_runtime_exceptions(
    exception_report: Mapping[str, Any],
    output_root: Path,
    manifest_path: Path,
    ocr_report: Mapping[str, Any],
    match_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Append all post-split OCR/matching abnormalities to bank exceptions."""
    result = dict(exception_report)
    entries = dict(result.get("entries") or {})
    runtime_keys: set[str] = set()

    def add_entry(
        bank_key: str,
        reason: str,
        ordinal: int,
        record: Mapping[str, Any],
        source_pdf: object = None,
    ) -> None:
        index = str(record.get("index") or "").strip()
        key = f"{bank_key}__runtime__{reason}__{index or ordinal}"
        suffix = 2
        while key in entries:
            key = f"{bank_key}__runtime__{reason}__{index or ordinal}_{suffix}"
            suffix += 1
        source_path = Path(str(source_pdf)).resolve() if source_pdf else None
        if source_path is not None and not source_path.is_file():
            source_path = None
        copied_path = None
        if source_path is not None:
            copied_path = _copy_special_pdf(
                source_path,
                output_root,
                f"_运行异常_{reason}",
                key,
            )
        statement = record.get("statement")
        entries[key] = {
            "key": key,
            "bankKey": bank_key,
            "index": index,
            "counterpartyName": str(record.get("counterpartyName") or ""),
            "matchMethod": f"runtime_{reason}",
            "flowDirection": str(record.get("flowDirection") or ""),
            "amount": record.get("transactionAmount"),
            "statement": statement,
            "sourcePdf": str(source_path) if source_path else None,
            "copiedPdf": str(copied_path) if copied_path else None,
            "pdfStatus": "separated" if copied_path else "missing",
            "reason": reason,
            "details": dict(record),
            "downstreamEligible": False,
        }
        runtime_keys.add(key)

    for ordinal, error in enumerate(ocr_report.get("errors") or [], start=1):
        if not isinstance(error, Mapping):
            continue
        add_entry(
            str(error.get("bankKey") or "unknown_bank"),
            "ocr_error",
            ordinal,
            error,
            error.get("sourcePdf") or error.get("pdf"),
        )

    banks = match_report.get("banks") or {}
    if isinstance(banks, Mapping):
        for bank_key, bank_report in banks.items():
            if not isinstance(bank_report, Mapping):
                continue
            categories = (
                ("unmatched_statement", "unmatchedStatements"),
                ("unmatched_receipt", "unmatchedReceipts"),
                ("person_name", "skippedPersonNameStatements"),
                ("direction_error", "directionErrors"),
            )
            for reason, field in categories:
                for ordinal, record in enumerate(bank_report.get(field) or [], start=1):
                    if not isinstance(record, Mapping):
                        continue
                    receipt = record.get("receipt")
                    source_pdf = receipt.get("pdf") if isinstance(receipt, Mapping) else None
                    add_entry(str(bank_key), reason, ordinal, record, source_pdf)
            for ordinal, duplicate in enumerate(bank_report.get("duplicateIndexes") or [], start=1):
                if not isinstance(duplicate, Mapping):
                    continue
                receipts = duplicate.get("receipts") or []
                if not receipts:
                    add_entry(str(bank_key), "duplicate_index", ordinal, duplicate)
                    continue
                for receipt_ordinal, receipt in enumerate(receipts, start=1):
                    receipt_record = receipt if isinstance(receipt, Mapping) else {}
                    add_entry(
                        str(bank_key),
                        "duplicate_index",
                        ordinal * 1000 + receipt_ordinal,
                        duplicate,
                        receipt_record.get("pdf"),
                    )

    result["entries"] = entries
    summary = dict(result.get("summary") or {})
    summary["runtimeExceptionCount"] = len(runtime_keys)
    summary["totalExceptionCount"] = len(entries)
    summary["exceptionPdfCount"] = sum(
        bool(item.get("sourcePdf")) for item in entries.values()
    )
    summary["copiedPdfCount"] = sum(
        bool(item.get("copiedPdf")) for item in entries.values()
    )
    summary["missingPdfCount"] = sum(
        item.get("pdfStatus") == "missing" for item in entries.values()
    )
    result["summary"] = summary
    _write_json(manifest_path, result)
    return result
