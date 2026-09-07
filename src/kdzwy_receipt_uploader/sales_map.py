"""Build the sales business map directly from the actual PDF inventory."""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


class SalesMapError(ValueError):
    pass


def _invoice_code(path: Path) -> str:
    match = re.search(r"(?<!\d)(\d{20})(?!\d)", path.stem)
    return match.group(1) if match else ""


def _filename_customer(path: Path, invoice_code: str) -> str:
    marker = path.stem.find(invoice_code)
    if marker < 0:
        return ""
    suffix = path.stem[marker + len(invoice_code):].lstrip("_")
    return re.sub(r"_\d{14}$", "", suffix).strip("_").strip()


def build_sales_map_from_pdfs(
    sales_input_dir: Path,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Use every real sales PDF with a valid invoice number as the sales scope."""
    sales_input_dir = sales_input_dir.resolve()
    rows: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    duplicate_codes: dict[str, list[str]] = {}
    raw_pdf_count = 0

    if sales_input_dir.is_dir():
        for pdf_path in sorted(sales_input_dir.rglob("*.pdf")):
            raw_pdf_count += 1
            resolved_pdf = pdf_path.resolve()
            invoice_code = _invoice_code(pdf_path)
            if not invoice_code:
                invalid.append({
                    "pdf": str(resolved_pdf),
                    "reason": "PDF文件名中没有唯一的20位发票号码",
                })
                continue
            customer = _filename_customer(pdf_path, invoice_code)
            if invoice_code in rows:
                paths = duplicate_codes.setdefault(
                    invoice_code,
                    list(rows[invoice_code].get("sourcePdfs") or []),
                )
                paths.append(str(resolved_pdf))
                rows[invoice_code]["sourcePdfs"] = paths
                continue
            rows[invoice_code] = {
                "amount": "",
                "taxAmount": "",
                "totalAmount": "",
                "date": "",
                "itemClass": "客户",
                "customName": customer,
                "customNameCandidates": [customer] if customer else [],
                "rowCount": 0,
                "dataSource": "pdf_ocr_pending",
                "sourcePdf": str(resolved_pdf),
                "sourcePdfs": [str(resolved_pdf)],
            }

    report = {
        "source": str(sales_input_dir),
        "scope": "销售范围只由input/sales下实际存在的PDF决定，不读取收入成本表",
        "summary": {
            "rawPdfCount": raw_pdf_count,
            "invoiceCount": len(rows),
            "invalidPdfCount": len(invalid),
            "duplicateInvoiceCodeCount": len(duplicate_codes),
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


def finalize_sales_pdf_map(
    sales_map_report: dict[str, Any],
    ocr_directory: Path,
    configured_company: str,
    expected_month: str,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Fill every PDF-backed sales record only after strict OCR validation."""

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
            values["dataSource"] = "pdf_ocr_blocked"
            values["ocrStatus"] = "blocked"
            values["ocrErrors"] = errors
            blocked.append({
                "documentId": invoice_code,
                "errorType": "sales_pdf_ocr_invalid",
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
            "dataSource": "pdf_ocr",
            "ocrStatus": "ready",
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
        values.pop("ocrErrors", None)
        ready.append(invoice_code)

    report = sales_map_report.setdefault("report", {})
    summary = report.setdefault("summary", {})
    summary["ocrReadyCount"] = len(ready)
    summary["ocrBlockedCount"] = len(blocked)
    report["ocrReady"] = ready
    report["ocrBlocked"] = blocked
    output_path.write_text(json.dumps(sales_map, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ready": ready, "blocked": blocked}
