"""Reusable XLSX invoice-number to PDF-path matching functions."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import re

from .source_profile import source_from_folder_name
from .xlsx_cache import load_read_only_workbook, load_value_workbook

NUMERIC_TEXT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def normalize_invoice_number(value: Any) -> str | None:
    """Normalize numeric Excel values or numeric text for invoice matching."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or not NUMERIC_TEXT_RE.fullmatch(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def read_xlsx_column_a(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read all worksheets' column A without modifying the workbook."""
    values: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    workbook = load_read_only_workbook(path)
    try:
        for worksheet in workbook.worksheets:
            for row_number, row in enumerate(
                worksheet.iter_rows(min_col=1, max_col=1, values_only=True),
                start=1,
            ):
                raw = row[0]
                if raw is None or str(raw).strip() == "":
                    continue
                key = normalize_invoice_number(raw)
                if key is None:
                    invalid.append({
                        "xlsx": str(path.resolve()),
                        "sheet": worksheet.title,
                        "row": row_number,
                        "value": str(raw),
                    })
                else:
                    values.append({
                        "key": key,
                        "xlsx": str(path.resolve()),
                        "sheet": worksheet.title,
                        "row": row_number,
                        "originalValue": raw,
                    })
    finally:
        pass
    return values, invalid


def pdf_invoice_number(path: Path) -> str | None:
    """Return the number between the first and second underscore in a PDF name."""
    parts = path.stem.split("_")
    if len(parts) < 3:
        return None
    return normalize_invoice_number(parts[1])


def read_xlsx_column(path: Path, column: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read a configured Excel column without modifying the workbook."""
    values: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    column_text = str(column).strip().upper()
    if not column_text or not column_text.isalpha():
        return [], [{"xlsx": str(path.resolve()), "column": column, "reason": "Excel列名无效"}]
    column_index = 0
    for character in column_text:
        column_index = column_index * 26 + ord(character) - ord("A") + 1
    workbook = load_value_workbook(path)
    try:
        for worksheet in workbook.worksheets:
            for row_number, row in enumerate(
                worksheet.iter_rows(min_col=column_index, max_col=column_index, values_only=True),
                start=1,
            ):
                raw = row[0]
                if raw is None or str(raw).strip() == "":
                    continue
                key = normalize_invoice_number(raw)
                if key is None:
                    invalid.append({"xlsx": str(path.resolve()), "sheet": worksheet.title, "row": row_number, "column": column_text, "value": str(raw)})
                else:
                    values.append({"key": key, "xlsx": str(path.resolve()), "sheet": worksheet.title, "row": row_number, "column": column_text, "originalValue": raw})
    finally:
        workbook.close()
    return values, invalid


def scan_pdf_names(directory: Path) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Scan PDF names only and return local-directory invoice-number candidates."""
    candidates: dict[str, list[str]] = defaultdict(list)
    invalid_names: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        key = pdf_invoice_number(path)
        if key is None:
            invalid_names.append({
                "pdf": str(path.resolve()),
                "reason": "文件名缺少两个下划线或中间部分不是数字",
            })
            continue
        absolute = str(path.resolve())
        if absolute not in candidates[key]:
            candidates[key].append(absolute)
    return dict(candidates), invalid_names


def match_directory(directory: Path) -> dict[str, Any]:
    """Match one extracted directory; never use PDFs from another directory.

    Returns a dictionary containing:
    - map: invoice number -> one absolute PDF path, or "" when absent
    - unmatched: XLSX invoice numbers without a local PDF
    - duplicates: local numbers with more than one PDF
    - invalidXlsxValues / invalidPdfNames: diagnostic entries
    """
    directory = directory.resolve()
    xlsx_files = sorted(
        path for path in directory.glob("*.xlsx")
        if not path.name.startswith("~$")
    )
    pdf_candidates, invalid_pdf_names = scan_pdf_names(directory)
    xlsx_values: list[dict[str, Any]] = []
    invalid_xlsx_values: list[dict[str, Any]] = []
    for xlsx in xlsx_files:
        values, invalid = read_xlsx_column_a(xlsx)
        xlsx_values.extend(values)
        invalid_xlsx_values.extend(invalid)
    keys = sorted({item["key"] for item in xlsx_values}, key=lambda value: (Decimal(value), value))
    result_map = {key: (paths[0] if paths else "") for key in keys for paths in [pdf_candidates.get(key, [])]}
    unmatched = [key for key in keys if result_map[key] == ""]
    duplicates = [
        {"key": key, "pdfPaths": pdf_candidates[key], "count": len(pdf_candidates[key])}
        for key in keys
        if len(pdf_candidates.get(key, [])) > 1
    ]
    return {
        "directory": str(directory),
        "xlsxFiles": [str(path.resolve()) for path in xlsx_files],
        "map": result_map,
        "unmatched": unmatched,
        "duplicates": duplicates,
        "invalidXlsxValues": invalid_xlsx_values,
        "invalidPdfNames": invalid_pdf_names,
        "summary": {
            "xlsxNumberCount": len(keys),
            "pdfCount": sum(len(paths) for paths in pdf_candidates.values()),
            "matchedCount": sum(1 for value in result_map.values() if value),
            "unmatchedCount": len(unmatched),
            "duplicateCount": len(duplicates),
        },
    }


def read_purchase_folder_xlsx_a(month_directory: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read A-column invoice numbers and same-folder PDF paths from purchase folders."""
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for folder in sorted(
        path for path in month_directory.iterdir() if path.is_dir() and source_from_folder_name(path.name) == "purchase"
    ):
        pdf_candidates, invalid_pdf = scan_pdf_names(folder)
        invalid.extend(invalid_pdf)
        for xlsx in sorted(path for path in folder.glob("*.xlsx") if not path.name.startswith("~$")):
            values, errors = read_xlsx_column_a(xlsx)
            invalid.extend(errors)
            for item in values:
                key = item["key"]
                xlsx_path = str(xlsx.resolve())
                pdf_paths = pdf_candidates.get(key, [])
                candidate = {"folder": str(folder.resolve()), "xlsx": xlsx_path, "pdfPaths": pdf_paths, "sheet": item["sheet"], "row": item["row"]}
                candidates[key].append(candidate)
                sources.append({"key": key, **candidate})
    return dict(candidates), invalid, sources


def match_month_directory(month_directory: Path, config: Any, output_directory: Path) -> dict[str, Any]:
    """Use usage-confirmation E as the only receipt-code candidate set."""
    month_directory = month_directory.resolve()
    usage_path = month_directory / config.usage_filename
    configured_income = month_directory / config.income_cost_filename
    usage_values, invalid_usage = read_xlsx_column(usage_path, config.usage_column) if usage_path.is_file() else ([], [{"xlsx": str(usage_path), "reason": "用途确认信息不存在"}])
    usage_keys = sorted({item["key"] for item in usage_values}, key=lambda value: (Decimal(value), value))
    purchase_candidates, invalid_purchase_xlsx, purchase_sources = read_purchase_folder_xlsx_a(month_directory)
    result_map: dict[str, str] = {}
    for key in usage_keys:
        candidates = purchase_candidates.get(key, [])
        pdf_paths = [pdf for candidate in candidates for pdf in candidate.get("pdfPaths", [])]
        result_map[key] = pdf_paths[0] if pdf_paths else ""
    invalid_xlsx = invalid_purchase_xlsx
    keys = usage_keys
    report = {
        "company": config.company,
        "month": config.month,
        "monthDirectory": str(month_directory),
        "incomeCostFile": str(configured_income),
        "incomeCostExcludedFromCandidates": True,
        "usageConfirmFile": str(usage_path),
        "usageConfirmColumn": config.usage_column,
        "usageConfirmKeys": keys,
        "purchaseFolderXlsxSources": purchase_sources,
        "purchaseFolderXlsxKeys": sorted(purchase_candidates, key=lambda value: (Decimal(value), value)),
        "map": result_map,
        "missingFromPurchaseFolder": [key for key in keys if not purchase_candidates.get(key)],
        "missingPdfInPurchaseFolder": [key for key in keys if purchase_candidates.get(key) and not any(candidate.get("pdfPaths") for candidate in purchase_candidates[key])],
        "duplicates": [{"key": key, "sources": sources} for key, sources in purchase_candidates.items() if len(sources) > 1],
        "invalidXlsxValues": invalid_xlsx,
        "invalidUsageValues": invalid_usage,
        "invalidPdfNames": [],
        "summary": {
            "usageConfirmNumberCount": len(keys),
            "purchaseFolderNumberCount": len(purchase_candidates),
            "matchedCount": sum(1 for value in result_map.values() if value),
            "emptyCount": sum(1 for value in result_map.values() if value == ""),
            "missingFromPurchaseFolderCount": sum(1 for key in keys if not purchase_candidates.get(key)),
            "missingPdfInPurchaseFolderCount": sum(1 for key in keys if purchase_candidates.get(key) and not any(candidate.get("pdfPaths") for candidate in purchase_candidates[key])),
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    import json
    (output_directory / "xlsx_pdf_map.json").write_text(json.dumps(result_map, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_directory / "xlsx_pdf_map.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def match_company_directory(company_directory: Path, output_directory: Path) -> dict[str, Any]:
    """Match every extracted child directory and write map/report JSON files."""
    company_directory = company_directory.resolve()
    output_directory = output_directory.resolve()
    directory_reports = [
        match_directory(directory)
        for directory in sorted(path for path in company_directory.iterdir() if path.is_dir())
    ]
    combined_map: dict[str, str] = {}
    cross_directory_duplicates: list[dict[str, Any]] = []
    for report in directory_reports:
        for key, path in report["map"].items():
            if key in combined_map:
                cross_directory_duplicates.append({"key": key, "directory": report["directory"], "path": path})
                continue
            combined_map[key] = path
    combined_map = dict(sorted(combined_map.items(), key=lambda item: (Decimal(item[0]), item[0])))
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "companyPlaceholder": company_directory.name,
        "companyDirectory": str(company_directory),
        "scope": "每个解压目录独立匹配；不跨目录补匹配",
        "mapValue": "唯一匹配为 PDF 绝对路径；无匹配为字符串空值",
        "summary": {
            "directoryCount": len(directory_reports),
            "xlsxNumberCount": sum(item["summary"]["xlsxNumberCount"] for item in directory_reports),
            "matchedCount": sum(item["summary"]["matchedCount"] for item in directory_reports),
            "unmatchedCount": sum(item["summary"]["unmatchedCount"] for item in directory_reports),
            "duplicateCount": sum(item["summary"]["duplicateCount"] for item in directory_reports),
            "crossDirectoryDuplicateCount": len(cross_directory_duplicates),
        },
        "directories": directory_reports,
        "crossDirectoryDuplicates": cross_directory_duplicates,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "xlsx_pdf_map.json").write_text(
        __import__("json").dumps(combined_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_directory / "xlsx_pdf_map.report.json").write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report
