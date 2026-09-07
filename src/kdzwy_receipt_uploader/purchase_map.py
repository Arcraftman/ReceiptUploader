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
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .matching import normalize_invoice_number
from .xlsx_cache import load_value_workbook


class JMapError(ValueError):
    """Raised when the supplier-side source workbook is invalid."""


def _pdf_invoice_code(path: Path) -> str:
    match = re.search(r"(?<!\d)(\d{20})(?!\d)", path.stem)
    return match.group(1) if match else ""


def _filename_supplier(path: Path, invoice_code: str) -> str:
    marker = path.stem.find(invoice_code)
    if marker < 0:
        return ""
    suffix = path.stem[marker + len(invoice_code):].lstrip("_")
    return re.sub(r"_\d{14}$", "", suffix).strip("_").strip()


def build_purchase_map_from_pdfs(
    purchase_input_dir: Path,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Use actual purchase PDFs when no usage-confirmation workbook is required."""
    purchase_input_dir = purchase_input_dir.resolve()
    rows: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    duplicate_codes: dict[str, list[str]] = {}
    raw_pdf_count = 0
    if purchase_input_dir.is_dir():
        for pdf_path in sorted(purchase_input_dir.rglob("*.pdf")):
            raw_pdf_count += 1
            resolved_pdf = pdf_path.resolve()
            invoice_code = _pdf_invoice_code(pdf_path)
            if not invoice_code:
                invalid.append({"pdf": str(resolved_pdf), "reason": "PDF文件名中没有唯一的20位发票号码"})
                continue
            supplier = _filename_supplier(pdf_path, invoice_code)
            if invoice_code in rows:
                paths = duplicate_codes.setdefault(invoice_code, list(rows[invoice_code]["sourcePdfs"]))
                paths.append(str(resolved_pdf))
                rows[invoice_code]["sourcePdfs"] = paths
                continue
            rows[invoice_code] = {
                "amount": "",
                "taxAmount": "",
                "totalAmount": "",
                "invoiceTaxAmount": "",
                "date": "",
                "itemClass": "供应商",
                "supplierName": supplier,
                "supplierNameCandidates": [supplier] if supplier else [],
                "rowCount": 0,
                "dataSource": "pdf_ocr_pending",
                "sourcePdf": str(resolved_pdf),
                "sourcePdfs": [str(resolved_pdf)],
                "taxpayerType": "small_scale",
                "usageConfirmationEnabled": False,
                "inputTaxDeductible": False,
            }
    report = {
        "source": str(purchase_input_dir),
        "scope": "小规模纳税人：采购范围只由input/purchase下实际存在的PDF决定，不需要用途确认信息.xlsx",
        "usageConfirmationEnabled": False,
        "summary": {
            "rawPdfCount": raw_pdf_count,
            "invoiceCount": len(rows),
            "invalidPdfCount": len(invalid),
            "duplicateInvoiceCodeCount": len(duplicate_codes),
            "dateConflictCount": 0,
            "supplierConflictCount": 0,
        },
        "invalidPdfs": invalid,
        "duplicateInvoiceCodes": [
            {"invoiceCode": code, "pdfs": paths}
            for code, paths in sorted(duplicate_codes.items())
        ],
    }
    output_path = output_path.resolve()
    report_path = report_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"map": rows, "report": report}


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


def finalize_purchase_pdf_map(
    purchase_map_report: dict[str, Any],
    ocr_directory: Path,
    configured_company: str,
    expected_month: str,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Validate PDF OCR and book gross purchase cost for a small-scale taxpayer."""

    def clean_name(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).replace("(", "（").replace(")", "）")

    def money(value: Any) -> Decimal | None:
        text = str(value or "").replace(",", "").replace("￥", "").replace("¥", "").replace("－", "-").replace("−", "-").strip()
        try:
            return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None

    def iso_date(value: Any) -> str:
        match = re.search(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", str(value or "").strip())
        if not match:
            return ""
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    def totals(fields: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
        gross = money(fields.get("totalAmountWithTax"))
        text = str(fields.get("_normalizedText") or "")
        if gross is None:
            match = re.search(r"价税合计[\s\S]{0,80}?[（(]小写[）)]\s*[￥¥]\s*([-－−]?[0-9][0-9,]*\.\d{2})", text)
            if match:
                gross = money(match.group(1))
        if gross is None:
            return None, None, None, ""
        amounts = [money(value) for value in re.findall(r"[￥¥]\s*([-－−]?[0-9][0-9,]*\.\d{2})", text)]
        amounts = [value for value in amounts if value is not None]
        for index, left in enumerate(amounts):
            for right in amounts[index + 1:]:
                if (left + right).quantize(Decimal("0.01")) == gross:
                    net, tax = (left, right) if abs(left) >= abs(right) else (right, left)
                    return net, tax, gross, "ocr_invoice_totals"
        rates = {Decimal(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(fields.get("taxRate") or ""))}
        if len(rates) == 1:
            rate = next(iter(rates))
            net = (gross * Decimal("100") / (Decimal("100") + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return net, gross - net, gross, "gross_and_single_tax_rate"
        return None, None, gross, ""

    purchase_map = purchase_map_report.setdefault("map", {})
    ready: list[str] = []
    blocked: list[dict[str, Any]] = []
    for invoice_code, values in purchase_map.items():
        ocr_path = ocr_directory / invoice_code / "ocr.json"
        errors: list[str] = []
        fields: dict[str, Any] = {}
        if not ocr_path.is_file():
            errors.append("缺少OCR结果")
        else:
            try:
                fields = dict(json.loads(ocr_path.read_text(encoding="utf-8-sig")).get("fields") or {})
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                errors.append(f"OCR结果无法读取：{exc}")
        ocr_invoice = str(fields.get("invoiceNumber") or "").strip()
        buyer = str(fields.get("buyer") or "").strip()
        seller = str(fields.get("seller") or "").strip()
        invoice_date = iso_date(fields.get("issueDate"))
        net, invoice_tax, gross, amount_method = totals(fields)
        filename_supplier = str(values.get("supplierName") or "").strip()
        normalized_text = clean_name(fields.get("_normalizedText"))
        if filename_supplier and clean_name(filename_supplier) in normalized_text:
            seller = filename_supplier
        if ocr_invoice != invoice_code:
            errors.append("OCR发票号与PDF文件名不一致")
        if not buyer or clean_name(buyer) != clean_name(configured_company):
            errors.append("OCR购买方与资料公司不一致")
        if not seller:
            errors.append("OCR未识别销售方")
        elif filename_supplier and clean_name(seller) != clean_name(filename_supplier):
            errors.append("OCR销售方与PDF文件名供应商不一致")
        if not invoice_date or (expected_month and not invoice_date.startswith(expected_month)):
            errors.append("OCR开票日期缺失或不属于当前月份")
        if net is None or invoice_tax is None or gross is None:
            errors.append("OCR无法唯一取得金额、税额和价税合计")
        elif (net + invoice_tax).quantize(Decimal("0.01")) != gross:
            errors.append("OCR金额加税额不等于价税合计")
        if errors:
            values.update({"dataSource": "pdf_ocr_blocked", "ocrStatus": "blocked", "ocrErrors": errors})
            blocked.append({"documentId": invoice_code, "errorType": "purchase_pdf_ocr_invalid", "message": "；".join(errors), "sourcePdf": str(values.get("sourcePdf") or "")})
            continue
        values.update({
            "amount": f"{gross:.2f}",
            "taxAmount": "0.00",
            "totalAmount": f"{gross:.2f}",
            "invoiceNetAmount": f"{net:.2f}",
            "invoiceTaxAmount": f"{invoice_tax:.2f}",
            "date": invoice_date,
            "supplierName": seller,
            "supplierNameCandidates": [seller],
            "dataSource": "pdf_ocr",
            "ocrStatus": "ready",
            "ocrAmountMethod": amount_method,
            "taxpayerType": "small_scale",
            "usageConfirmationEnabled": False,
            "inputTaxDeductible": False,
            "ocrEvidence": {"invoiceNumber": ocr_invoice, "buyer": buyer, "seller": seller, "issueDate": str(fields.get("issueDate") or "")},
        })
        values.pop("ocrErrors", None)
        ready.append(invoice_code)
    report = purchase_map_report.setdefault("report", {})
    summary = report.setdefault("summary", {})
    summary["ocrReadyCount"] = len(ready)
    summary["ocrBlockedCount"] = len(blocked)
    report["ocrReady"] = ready
    report["ocrBlocked"] = blocked
    output_path.write_text(json.dumps(purchase_map, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ready": ready, "blocked": blocked}
