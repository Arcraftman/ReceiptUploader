"""Build the invoice business map from 收入成本表.xlsx."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .xlsx_cache import load_read_only_workbook


class SalesMapError(ValueError):
    pass


def _number(value: Any, path: Path, column: str, row: int, errors: list[dict[str, Any]]) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        errors.append({"file": str(path), "row": row, "column": column, "value": value, "reason": "金额无法解析"})
        return Decimal("0")


def _invoice(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


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
        errors.append({"file": str(path), "row": row, "column": "I", "value": value, "reason": "日期无法解析"})
        return ""
    return parsed.isoformat()


def build_sales_map(path: Path, output_path: Path | None = None, report_path: Path | None = None) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.name.startswith("~$"):
        raise SalesMapError(f"收入成本表不存在或为临时文件：{path}")
    errors: list[dict[str, Any]] = []
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    workbook = load_read_only_workbook(path)
    try:
        source_sheets = [sheet for sheet in workbook.worksheets if sheet.title == "信息汇总表"]
        if not source_sheets:
            raise SalesMapError("收入成本表中缺少金额明细工作表：信息汇总表")
        for sheet in source_sheets:
            for row_number, values in enumerate(sheet.iter_rows(min_col=4, max_col=20, values_only=True), start=1):
                invoice_code = _invoice(values[0])
                if not invoice_code or invoice_code in {"发票号码", "发票号", "数电发票号码"}:
                    continue
                rows[invoice_code].append({
                    "amount": _number(values[13], path, "Q", row_number, errors),
                    "taxAmount": _number(values[15], path, "S", row_number, errors),
                    "totalAmount": _number(values[16], path, "T", row_number, errors),
                    "date": _date(values[5], path, row_number, errors),
                    "itemClass": "客户",
                    "customName": str(values[4] or "").strip(),
                })
    finally:
        pass

    result: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for invoice_code, items in sorted(rows.items()):
        dates = sorted({item["date"] for item in items if item["date"]})
        if len(dates) > 1:
            conflicts.append({"invoiceCode": invoice_code, "dates": dates, "reason": "同一发票号存在多个日期"})
        custom_names = sorted({str(item.get("customName", "")).strip() for item in items if str(item.get("customName", "")).strip()})
        result[invoice_code] = {
            "amount": float(sum(item["amount"] for item in items)),
            "taxAmount": float(sum(item["taxAmount"] for item in items)),
            "totalAmount": float(sum(item["totalAmount"] for item in items)),
            "date": dates[0] if len(dates) == 1 else "",
            "itemClass": "客户",
            "customName": custom_names[0] if len(custom_names) == 1 else "",
            "customNameCandidates": custom_names,
            "rowCount": len(items),
        }
    report = {
        "source": str(path),
        "sourceSheet": "信息汇总表",
        "columns": {"invoiceCode": "D", "itemClass": "H", "customName": "H", "amount": "Q", "taxAmount": "S", "totalAmount": "T", "date": "I"},
        "summary": {"invoiceCount": len(result), "sourceRowCount": sum(len(items) for items in rows.values()), "dateConflictCount": len(conflicts), "errorCount": len(errors)},
        "dateConflicts": conflicts,
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
