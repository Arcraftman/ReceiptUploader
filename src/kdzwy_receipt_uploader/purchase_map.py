"""Build the supplier-side invoice business map from 用途确认信息.xlsx.

This intentionally mirrors :mod:`sales_map` while using the supplier workbook
layout requested by the business flow:

- invoice number: column E
- date: column H
- supplier name: column J
- amount: column K
- tax amount: column L
- total amount: calculated per row as K + L

The map is grouped by invoice number. Multiple detail rows for one invoice are
summed, a single date/name is retained when unambiguous, and conflicts/errors
are written to the companion report.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .matching import normalize_invoice_number
from .xlsx_cache import load_value_workbook


class JMapError(ValueError):
    """Raised when the supplier-side source workbook is invalid."""


def _number(value: Any, path: Path, column: str, row: int, errors: list[dict[str, Any]]) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        errors.append({"file": str(path), "row": row, "column": column, "value": value, "reason": "金额无法解析"})
        return Decimal("0")


def _invoice(value: Any) -> str:
    normalized = normalize_invoice_number(value)
    return normalized or ""


def _date(value: Any, path: Path, row: int, errors: list[dict[str, Any]]) -> str:
    if value in (None, ""):
        return ""
    parsed: date | None = None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
    if parsed is None:
        errors.append({"file": str(path), "row": row, "column": "H", "value": value, "reason": "日期无法解析"})
        return ""
    return parsed.isoformat()


def build_purchase_map(path: Path, output_path: Path | None = None, report_path: Path | None = None) -> dict[str, Any]:
    """Read the supplier-side columns and optionally persist map/report JSON."""
    path = path.resolve()
    if not path.is_file() or path.name.startswith("~$"):
        raise JMapError(f"用途确认信息不存在或为临时文件：{path}")

    errors: list[dict[str, Any]] = []
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    workbook = load_value_workbook(path)
    try:
        source_sheets = [sheet for sheet in workbook.worksheets if sheet.title == "发票"]
        if not source_sheets:
            raise JMapError("用途确认信息中缺少发票工作表：发票")
        for sheet in source_sheets:
            # E:L gives indices E=0, H=3, J=5, K=6, L=7.
            for row_number, values in enumerate(sheet.iter_rows(min_col=5, max_col=12, values_only=True), start=1):
                invoice_code = _invoice(values[0])
                if not invoice_code:
                    continue
                amount = _number(values[6], path, "K", row_number, errors)
                tax_amount = _number(values[7], path, "L", row_number, errors)
                rows[invoice_code].append({
                    "amount": amount,
                    "taxAmount": tax_amount,
                    "totalAmount": amount + tax_amount,
                    "date": _date(values[3], path, row_number, errors),
                    "itemClass": "供应商",
                    "supplierName": str(values[5] or "").strip(),
                })
    finally:
        workbook.close()

    result: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for invoice_code, items in sorted(rows.items()):
        dates = sorted({item["date"] for item in items if item["date"]})
        if len(dates) > 1:
            conflicts.append({"invoiceCode": invoice_code, "dates": dates, "reason": "同一发票号存在多个日期"})
        supplier_names = sorted({str(item.get("supplierName", "")).strip() for item in items if str(item.get("supplierName", "")).strip()})
        result[invoice_code] = {
            "amount": float(sum(item["amount"] for item in items)),
            "taxAmount": float(sum(item["taxAmount"] for item in items)),
            "totalAmount": float(sum(item["totalAmount"] for item in items)),
            "date": dates[0] if len(dates) == 1 else "",
            "itemClass": "供应商",
            "supplierName": supplier_names[0] if len(supplier_names) == 1 else "",
            "supplierNameCandidates": supplier_names,
            "rowCount": len(items),
        }

    report = {
        "source": str(path),
        "sourceSheet": "发票",
        "columns": {
            "invoiceCode": "E",
            "supplierName": "J",
            "amount": "K",
            "taxAmount": "L",
            "totalAmount": "K + L (逐行计算)",
            "date": "H",
        },
        "summary": {
            "invoiceCount": len(result),
            "sourceRowCount": sum(len(items) for items in rows.values()),
            "dateConflictCount": len(conflicts),
            "supplierConflictCount": sum(1 for values in result.values() if len(values["supplierNameCandidates"]) > 1),
            "errorCount": len(errors),
        },
        "dateConflicts": conflicts,
        "supplierConflicts": [
            {"invoiceCode": code, "supplierNames": values["supplierNameCandidates"], "reason": "同一发票号存在多个供应商"}
            for code, values in result.items()
            if len(values["supplierNameCandidates"]) > 1
        ],
        "errors": errors,
    }
    if output_path:
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"map": result, "report": report}
