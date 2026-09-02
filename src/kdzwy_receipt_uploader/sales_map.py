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
        workbook.close()

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


def add_sales_pdf_fallback_candidates(
    sales_map_report: dict[str, Any],
    sales_input_dir: Path,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Add real sales PDFs missing from the income/cost workbook as OCR candidates."""
    import re

    sales_map = sales_map_report.setdefault("map", {})
    added: list[str] = []
    if sales_input_dir.is_dir():
        for pdf_path in sorted(sales_input_dir.rglob("*.pdf")):
            match = re.search(r"(?<!\d)(\d{20})(?!\d)", pdf_path.stem)
            if not match:
                continue
            invoice_code = match.group(1)
            if invoice_code in sales_map:
                continue
            suffix = pdf_path.stem[match.end():].lstrip("_")
            suffix = re.sub(r"_\d{14}$", "", suffix).strip("_").strip()
            sales_map[invoice_code] = {
                "amount": "",
                "taxAmount": "",
                "totalAmount": "",
                "date": "",
                "itemClass": "客户",
                "customName": suffix,
                "customNameCandidates": [suffix] if suffix else [],
                "rowCount": 0,
                "dataSource": "ocr_fallback_pending",
                "missingFromIncomeCost": True,
                "sourcePdf": str(pdf_path.resolve()),
            }
            added.append(invoice_code)

    report = sales_map_report.setdefault("report", {})
    summary = report.setdefault("summary", {})
    summary["incomeCostInvoiceCount"] = len(sales_map) - len(added)
    summary["ocrFallbackCandidateCount"] = len(added)
    summary["invoiceCount"] = len(sales_map)
    if added:
        report["ocrFallbackCandidates"] = added
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sales_map, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return sales_map_report


def finalize_sales_ocr_fallbacks(
    sales_map_report: dict[str, Any],
    ocr_directory: Path,
    configured_company: str,
    expected_month: str,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Promote workbook-missing sales PDFs only after strict OCR field validation."""
    import re
    from decimal import ROUND_HALF_UP

    def clean_name(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).replace("(", "（").replace(")", "）")

    def money(value: Any) -> Decimal | None:
        text = (
            str(value or "")
            .replace(",", "")
            .replace("￥", "")
            .replace("¥", "")
            .replace("－", "-")
            .replace("−", "-")
            .strip()
        )
        try:
            return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None

    def iso_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.search(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", text)
        if not match:
            return ""
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    def totals(fields: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
        gross = money(fields.get("totalAmountWithTax"))
        text = str(fields.get("_normalizedText") or "")
        if gross is None:
            gross_match = re.search(
                r"价税合计[\s\S]{0,80}?[（(]小写[）)]\s*[￥¥]\s*([-－−]?[0-9][0-9,]*\.\d{2})",
                text,
            )
            if gross_match:
                gross = money(gross_match.group(1))
        if gross is None:
            return None, None, None, ""
        amounts = [
            money(value)
            for value in re.findall(r"[￥¥]\s*([-－−]?[0-9][0-9,]*\.\d{2})", text)
        ]
        amounts = [value for value in amounts if value is not None]
        for left_index, left in enumerate(amounts):
            for right in amounts[left_index + 1:]:
                if (left + right).quantize(Decimal("0.01")) == gross:
                    if abs(left) >= abs(right):
                        return left, right, gross, "ocr_invoice_totals"
                    return right, left, gross, "ocr_invoice_totals"
        rate_values = {
            Decimal(value)
            for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(fields.get("taxRate") or ""))
        }
        if len(rate_values) == 1:
            rate = next(iter(rate_values))
            net = (gross * Decimal("100") / (Decimal("100") + rate)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            return net, gross - net, gross, "gross_and_single_tax_rate"
        return None, None, gross, ""

    sales_map = sales_map_report.setdefault("map", {})
    ready: list[str] = []
    blocked: list[dict[str, Any]] = []
    for invoice_code, values in sales_map.items():
        if values.get("dataSource") not in {"ocr_fallback_pending", "ocr_fallback", "ocr_fallback_blocked"}:
            continue
        ocr_path = ocr_directory / invoice_code / "ocr.json"
        errors: list[str] = []
        fields: dict[str, Any] = {}
        if not ocr_path.is_file():
            errors.append("缺少OCR结果")
        else:
            try:
                payload = json.loads(ocr_path.read_text(encoding="utf-8-sig"))
                fields = dict(payload.get("fields") or {})
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                errors.append(f"OCR结果无法读取：{exc}")

        ocr_invoice = str(fields.get("invoiceNumber") or "").strip()
        buyer = str(fields.get("buyer") or "").strip()
        seller = str(fields.get("seller") or "").strip()
        invoice_date = iso_date(fields.get("issueDate"))
        net, tax, gross, amount_method = totals(fields)
        filename_customer = str(values.get("customName") or "").strip()
        normalized_ocr_text = clean_name(fields.get("_normalizedText"))
        if filename_customer and clean_name(filename_customer) in normalized_ocr_text:
            buyer = filename_customer
        if ocr_invoice != invoice_code:
            errors.append("OCR发票号与PDF文件名不一致")
        if not seller or clean_name(seller) != clean_name(configured_company):
            errors.append("OCR销售方与资料公司不一致")
        if not buyer:
            errors.append("OCR未识别购买方")
        elif filename_customer and clean_name(buyer) != clean_name(filename_customer):
            errors.append("OCR购买方与PDF文件名客户不一致")
        if not invoice_date or (expected_month and not invoice_date.startswith(expected_month)):
            errors.append("OCR开票日期缺失或不属于当前月份")
        if net is None or tax is None or gross is None:
            errors.append("OCR无法唯一取得金额、税额和价税合计")
        elif (net + tax).quantize(Decimal("0.01")) != gross:
            errors.append("OCR金额加税额不等于价税合计")
        if errors:
            values["dataSource"] = "ocr_fallback_blocked"
            values["ocrFallbackStatus"] = "blocked"
            values["ocrFallbackErrors"] = errors
            blocked.append({
                "documentId": invoice_code,
                "errorType": "sales_missing_from_income_cost_ocr_invalid",
                "message": "；".join(errors),
                "sourcePdf": str(values.get("sourcePdf") or ""),
            })
            continue

        values.update({
            "amount": f"{net:.2f}",
            "taxAmount": f"{tax:.2f}",
            "totalAmount": f"{gross:.2f}",
            "date": invoice_date,
            "customName": buyer,
            "customNameCandidates": [buyer],
            "dataSource": "ocr_fallback",
            "missingFromIncomeCost": True,
            "ocrFallbackStatus": "ready",
            "ocrAmountMethod": amount_method,
            "ocrEvidence": {
                "invoiceNumber": ocr_invoice,
                "buyer": buyer,
                "seller": seller,
                "issueDate": str(fields.get("issueDate") or ""),
                "totalAmountEvidence": str(fields.get("totalAmountEvidence") or ""),
                "taxRateEvidence": str(fields.get("taxRateEvidence") or ""),
            },
        })
        values.pop("ocrFallbackErrors", None)
        ready.append(invoice_code)

    report = sales_map_report.setdefault("report", {})
    summary = report.setdefault("summary", {})
    summary["ocrFallbackReadyCount"] = len(ready)
    summary["ocrFallbackBlockedCount"] = len(blocked)
    report["ocrFallbackReady"] = ready
    report["ocrFallbackBlocked"] = blocked
    output_path.write_text(json.dumps(sales_map, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ready": ready, "blocked": blocked}
