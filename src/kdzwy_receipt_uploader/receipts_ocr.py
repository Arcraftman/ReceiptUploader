"""OCR preparation and Qwen-assisted template selection.

This module is deliberately independent from voucher upload. It reads source
PDFs in place and creates an inspectable ``ocr/<source>/<invoice>`` artifact
containing only OCR text and metadata, then optionally asks Qwen to choose
a concrete four-level template.
"""
from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Sequence

import copy
import json
import inspect
import os
import re
import logging
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .matching import pdf_invoice_number
from .receipt_generation import discover_source_pdfs
from .source_profile import source_from_folder_name
from .voucher_templates import TemplateContext, VoucherTemplateEngine


class OcrPipelineError(ValueError):
    pass


_OCR_ENGINE: Any | None = None
_OCR_CACHE_VERSION = 11
_OCR_RENDER_DPIS = (300, 400, 500)
_ANALYSIS_MEMORY_LOCK = threading.Lock()


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


@dataclass(frozen=True)
class OcrArtifact:
    invoice_code: str
    source_pdf: Path
    source_folder: str
    source_side: str
    output_dir: Path
    text_path: Path
    metadata_path: Path
    text: str
    engine: str
    status: str


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


def _detect_invoice_document_kind(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    if "航空运输电子客票" in compact or ("航班号" in compact and "民航发展基金" in compact):
        return "air_ticket"
    if "铁路电子客票" in compact or ("电子客票号" in compact and "车次" in compact):
        return "railway_ticket"
    return "vat_invoice"


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


def _enrich_transport_ticket_fields(fields: dict[str, Any], text: str) -> dict[str, Any]:
    document_kind = _detect_invoice_document_kind(text)
    fields["documentKind"] = document_kind
    fields["criticalFieldProfile"] = document_kind
    if document_kind == "vat_invoice":
        return fields

    normalized = unicodedata.normalize("NFKC", text or "")
    lines = [re.sub(r"\s+", "", line) for line in normalized.splitlines() if line.strip()]
    compact = "\n".join(lines)
    confidence = dict(fields.get("fieldConfidence") or {})

    def set_field(name: str, value: str, score: float = 0.98) -> None:
        value = str(value or "").strip()
        if not value:
            return
        fields[name] = value
        confidence[name] = max(float(confidence.get(name, 0) or 0), score)

    def first_group(patterns: Sequence[str], source: str = compact) -> str:
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                return str(match.group(1)).strip()
        return ""

    passenger_name = ""
    for line in lines:
        match = re.match(r"^([\u4e00-\u9fff·]{2,8})(?:\d|\*|＊){6,}", line)
        if match:
            passenger_name = match.group(1)
            break

    if document_kind == "railway_ticket":
        travel_date = first_group([
            r"(20\d{2}年\d{1,2}月\d{1,2}日)(?:\d{1,2}:\d{2})?开",
            r"乘车日期[:：]?(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)",
        ])
        train_number = first_group([r"(?<![A-Z0-9])([GDCZTKSY]\d{1,4})(?!\d)"])
        seat_class = first_group([r"(商务座|特等座|一等座|二等座|软卧|硬卧|软座|硬座|无座)"])
        ticket_amount = first_group([r"票价[:：]?[¥￥]?([0-9][0-9,]*(?:\.\d{1,2})?)"])
        route = re.search(
            r"([\u4e00-\u9fff]{1,12}站)(?:\s*)([GDCZTKSY]\d{1,4})(?:\s*)([\u4e00-\u9fff]{1,12}站)",
            re.sub(r"\s+", "", normalized),
            flags=re.IGNORECASE,
        )
        if route:
            set_field("origin", route.group(1))
            set_field("destination", route.group(3))
            train_number = train_number or route.group(2)
        set_field("travelDate", travel_date)
        set_field("trainNumber", train_number)
        set_field("seatClass", seat_class)
        set_field("passengerName", passenger_name)
        set_field("ticketAmount", ticket_amount)
        if ticket_amount and not str(fields.get("totalAmountWithTax") or "").strip():
            set_field("totalAmountWithTax", ticket_amount)
            fields["totalAmountWithTaxMethod"] = "railway_ticket_fare"

        required = (
            "invoiceNumber",
            "issueDate",
            "buyer",
            "totalAmountWithTax",
            "travelDate",
            "trainNumber",
            "seatClass",
        )
    else:
        issue_date = first_group([r"填开日期[:：]?(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)"])
        if issue_date:
            set_field("issueDate", issue_date)

        seller = first_group([
            r"填开单位[:：]?(.+?)(?:填开日期|销售网点|$)",
            r"承运人[:：]?([\u4e00-\u9fff（）()]{4,40})",
        ])
        set_field("seller", seller)

        flight_number = first_group([r"(?<![A-Z0-9])([A-Z0-9]{2}\d{3,4})(?!\d)"])
        date_values = re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日", compact)
        normalized_issue_date = str(fields.get("issueDate") or "").replace("-", "年", 1).replace("-", "月", 1)
        if normalized_issue_date and not normalized_issue_date.endswith("日"):
            normalized_issue_date += "日"
        travel_date = next((value for value in date_values if value != normalized_issue_date), "")

        cny_amounts = []
        for value in re.findall(r"CNY\s*([0-9][0-9,]*(?:\.\d{1,2})?)", compact, flags=re.IGNORECASE):
            try:
                cny_amounts.append((Decimal(value.replace(",", "")), value.replace(",", "")))
            except InvalidOperation:
                continue
        ticket_amount = max(cny_amounts, default=(Decimal("0"), ""))[1]
        tax_rate = first_group([r"(?<![\d.])(13|9|6|5|3|1)[%％](?!\d)"])
        if tax_rate:
            set_field("taxRate", f"{tax_rate}%")
            fields["taxRateMethod"] = "air_ticket_tax_rate"

        origin = first_group([r"(?:^|\n)自[:：]?([A-Z]{3}|[\u4e00-\u9fff]{2,12})(?:\n|$)"])
        destination = first_group([r"(?:^|\n)至[:：]?([A-Z]{3}|[\u4e00-\u9fff]{2,12})(?:\n|$)"])
        set_field("travelDate", travel_date)
        set_field("flightNumber", flight_number)
        set_field("passengerName", passenger_name)
        set_field("origin", origin)
        set_field("destination", destination)
        set_field("ticketAmount", ticket_amount)
        if ticket_amount:
            set_field("totalAmountWithTax", ticket_amount)
            fields["totalAmountWithTaxMethod"] = "air_ticket_cny_total"

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

    fields["fieldConfidence"] = confidence
    fields["criticalFieldsReady"] = all(
        str(fields.get(name) or "").strip() and float(confidence.get(name, 0) or 0) >= 0.85
        for name in required
    )
    fields["criticalFieldsRequired"] = list(required)
    fields["criticalFieldsMissing"] = [name for name in required if not str(fields.get(name) or "").strip()]
    return fields


def extract_invoice_fields(text: str) -> dict[str, Any]:
    """Extract auditable invoice fields from OCR text without guessing.

    The parser keeps the raw matched value and a confidence per field. The
    buyer/seller labels are handled independently because Chinese invoices
    place both "名称" lines under different section headers.
    """
    normalized = text.replace("\uFF0F", "/").replace("\uFF1A", ":")
    lines = [re.sub(r"\s+", "", line).strip() for line in normalized.splitlines() if line.strip()]
    fields: dict[str, Any] = {}

    def first_match(patterns: list[str]) -> str:
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        return ""

    invoice_number = first_match([r"发票号码[:：]?([0-9]{8,24})", r"发票号[:：]?([0-9]{8,24})"])
    issue_date = first_match([r"开票日期[:：]?([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", r"开票日期[:：]?([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})"])
    total_amount = first_match([
        r"[（(]小写[）)][:：]?[¥￥]?([-－−]?[0-9,]+(?:[.．][0-9]{1,2})?)",
        r"价税合计[（(]?小写[）)]?[:：]?[¥￥]?([-－−]?[0-9,]+(?:[.．][0-9]{1,2})?)",
        r"价税合计[:：]?[¥￥]?([-－−]?[0-9,]+(?:[.．][0-9]+)?)",
    ])
    total_amount = total_amount.replace("．", ".").replace("－", "-").replace("−", "-") if total_amount else ""
    adjacent_total_evidence = ""
    if not total_amount:
        # A common OCR layout emits the gross amount first, then emits
        # `价税合计（大写）` and `（小写）` as independent lines.  Associate
        # only an explicitly currency-prefixed amount close to those labels;
        # never fall back to the largest number in the document.
        label_indexes = [index for index, line in enumerate(lines) if "小写" in line]
        label_indexes.extend(
            index for index, line in enumerate(lines)
            if "价税合计" in line and index not in label_indexes
        )
        for label_index in label_indexes:
            nearby_indexes = [
                index
                for distance in range(1, 5)
                for index in (label_index - distance, label_index + distance)
                if 0 <= index < len(lines)
            ]
            for nearby_index in nearby_indexes:
                match = re.search(r"[¥￥]\s*([-－−]?[0-9,]+(?:[.．][0-9]{1,2})?)", lines[nearby_index])
                if not match:
                    continue
                total_amount = (
                    match.group(1)
                    .replace(",", "")
                    .replace("．", ".")
                    .replace("－", "-")
                    .replace("−", "-")
                )
                adjacent_total_evidence = f"{lines[nearby_index]} | {lines[label_index]}"
                break
            if total_amount:
                break
    tax_rate = first_match([
        r"税率/征收率[:：]?([0-9]+(?:[.．][0-9]+)?[%％])",
        r"税率[:：]?([0-9]+(?:[.．][0-9]+)?[%％])",
        r"([0-9]+(?:[.．][0-9]+)?[%％])",
    ])
    tax_rate = tax_rate.replace("．", ".").replace("％", "%") if tax_rate else ""
    if tax_rate:
        try:
            if Decimal(tax_rate.rstrip("%")) > Decimal("100"):
                tax_rate = ""
        except InvalidOperation:
            tax_rate = ""
    if not tax_rate:
        tax_rate = next(
            (
                match.group(1).replace("％", "%")
                for line in lines
                for match in [re.fullmatch(r"\s*([0-9]{1,2}(?:[.．][0-9]+)?[%％])\s*", line)]
                if match
            ),
            "",
        )
    if not tax_rate:
        tax_rate = next((label for label in ("免税", "不征税", "零税率") if label in "\n".join(lines)), "")
    tax_rate_method = "explicit_ocr_text" if tax_rate else ""
    derived_tax_evidence = ""
    if not tax_rate and total_amount:
        # When OCR misses the small tax-rate cell, derive a rate only from an
        # independently verifiable money equation.  Every candidate must use
        # currency-prefixed OCR values, satisfy net + tax = gross, and round
        # back to the tax amount at one standard VAT rate.
        try:
            gross = Decimal(total_amount.replace(",", ""))
        except InvalidOperation:
            gross = Decimal("-1")
        money_values: set[Decimal] = set()
        for line in lines:
            for raw_value in re.findall(r"[¥￥]\s*([0-9][0-9,]*(?:[.．][0-9]{1,2})?)", line):
                try:
                    money_values.add(Decimal(raw_value.replace(",", "").replace("．", ".")))
                except InvalidOperation:
                    continue
        standard_rates = {
            Decimal("0.01"): "1%",
            Decimal("0.03"): "3%",
            Decimal("0.05"): "5%",
            Decimal("0.06"): "6%",
            Decimal("0.09"): "9%",
            Decimal("0.13"): "13%",
        }
        tolerance = Decimal("0.02")
        derived_candidates: list[tuple[Decimal, Decimal, Decimal, str]] = []
        for net_amount in money_values:
            if net_amount <= 0 or net_amount == gross:
                continue
            for tax_amount in money_values:
                if tax_amount <= 0 or tax_amount in {gross, net_amount}:
                    continue
                if abs(net_amount + tax_amount - gross) > tolerance:
                    continue
                for rate, label in standard_rates.items():
                    expected_tax = (net_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if abs(expected_tax - tax_amount) <= tolerance:
                        derived_candidates.append((rate, net_amount, tax_amount, label))
        candidate_rates = {candidate[0] for candidate in derived_candidates}
        if len(candidate_rates) == 1:
            _, net_amount, tax_amount, tax_rate = max(
                derived_candidates,
                key=lambda candidate: candidate[1],
            )
            tax_rate_method = "amount_equation"
            derived_tax_evidence = (
                f"gross={gross}; net={net_amount}; tax={tax_amount}; "
                f"tax=round(net*{tax_rate},2)"
            )
    buyer = ""
    seller = ""
    for index, line in enumerate(lines):
        if line in {"购买方信息", "购买方"}:
            window = lines[index + 1:index + 8]
            buyer = next((item.split("名称:", 1)[1] for item in window if item.startswith("名称:") and len(item.split("名称:", 1)) == 2), "")
        if line in {"销售方信息", "销售方"}:
            window = lines[index + 1:index + 8]
            seller = next((item.split("名称:", 1)[1] for item in window if item.startswith("名称:") and len(item.split("名称:", 1)) == 2), "")
    # Some PDF layouts place both section labels first and the two names after
    # them. In that layout the first name is seller and the second is buyer.
    if not buyer and not seller:
        name_lines = [item.split("名称:", 1)[1] for item in lines if item.startswith("名称:") and len(item.split("名称:", 1)) == 2]
        if len(name_lines) >= 2:
            seller, buyer = name_lines[0], name_lines[1]
    elif buyer == seller:
        name_lines = [item.split("名称:", 1)[1] for item in lines if item.startswith("名称:") and len(item.split("名称:", 1)) == 2]
        if len(name_lines) >= 2:
            seller, buyer = name_lines[0], name_lines[1]
    if not buyer:
        buyer = first_match([r"购买方名称[:：]?(.+)"])
    if not seller:
        seller = first_match([r"销售方名称[:：]?(.+)"])
    normalized_text = "\n".join(lines)
    total_amount_evidence = adjacent_total_evidence
    total_amount_method = "adjacent_small_amount_label" if adjacent_total_evidence else ""
    if total_amount:
        if not total_amount_method:
            for line in lines:
                if "小写" in line and total_amount.replace(",", "") in line.replace(",", "").replace("．", "."):
                    total_amount_evidence = line
                    total_amount_method = "explicit_small_amount_label"
                    break
        if not total_amount_method:
            total_amount_method = "explicit_total_amount_label"
    tax_rate_evidence = derived_tax_evidence or next(
        (line for line in lines if tax_rate and (tax_rate in line.replace("％", "%").replace("．", ".") or tax_rate in {"免税", "不征税", "零税率"} and tax_rate in line)),
        "",
    )
    fields["_normalizedText"] = normalized_text
    fields["invoiceNumber"] = invoice_number
    fields["issueDate"] = issue_date
    fields["buyer"] = buyer
    fields["seller"] = seller
    fields["totalAmountWithTax"] = total_amount.replace(",", "") if total_amount else ""
    fields["taxRate"] = tax_rate
    fields["totalAmountEvidence"] = total_amount_evidence
    fields["totalAmountMethod"] = total_amount_method
    fields["taxRateEvidence"] = tax_rate_evidence
    fields["taxRateMethod"] = tax_rate_method
    fields["fieldConfidence"] = {
        "invoiceNumber": 1.0 if invoice_number else 0.0,
        "issueDate": 0.95 if issue_date else 0.0,
        "buyer": 0.95 if buyer else 0.0,
        "seller": 0.95 if seller else 0.0,
        "totalAmountWithTax": 0.9 if total_amount else 0.0,
        "taxRate": 0.95 if tax_rate_method == "amount_equation" else (0.85 if tax_rate else 0.0),
    }
    fields["criticalFieldsReady"] = all(fields["fieldConfidence"][key] >= 0.85 for key in ("invoiceNumber", "buyer", "seller", "totalAmountWithTax"))
    return _enrich_transport_ticket_fields(fields, text)


def apply_folder_party_rule(fields: Mapping[str, Any], source_folder: str, config_company: str) -> dict[str, Any]:
    """Attach the authoritative sales/purchase party direction to OCR fields.

    The OCR labels are retained as raw observations. Accounting direction must
    never be inferred from a possibly misordered OCR layout: sales is always the
    configured company as seller (sales-side map), while purchase is always the
    configured company as buyer (purchase-side map).
    """
    folder = str(source_folder or "")
    folder_key = source_from_folder_name(folder) or ""
    corrected = dict(fields)
    if folder_key == "sales":
        rule = {
            "configuredCompanyRole": "seller",
            "counterpartyRole": "buyer",
            "mapSource": "sales_map",
            "allowedTemplateBlocks": ["销售"],
        }
    elif folder_key == "purchase":
        rule = {
            "configuredCompanyRole": "buyer",
            "counterpartyRole": "seller",
            "mapSource": "purchase_map",
            "allowedTemplateBlocks": ["采购", "费用"],
        }
    elif folder_key == "bank":
        rule = {
            "configuredCompanyRole": "account_owner",
            "counterpartyRole": "transaction_counterparty",
            "mapSource": "source",
            "allowedTemplateBlocks": ["银行", "费用"],
        }
    elif folder_key == "misc":
        rule = {
            "configuredCompanyRole": "document_owner",
            "counterpartyRole": "document_counterparty",
            "mapSource": "source",
            "allowedTemplateBlocks": ["杂项", "费用"],
        }
    else:
        rule = {
            "configuredCompanyRole": "unknown",
            "counterpartyRole": "unknown",
            "mapSource": "",
            "allowedTemplateBlocks": [],
        }
    corrected["ocrRawBuyer"] = str(fields.get("buyer", ""))
    corrected["ocrRawSeller"] = str(fields.get("seller", ""))
    names = []
    normalized_text = str(fields.pop("_normalizedText", "") or "")
    for line in normalized_text.splitlines():
        match = re.search(r"名称[:：](.+)", line)
        if match and match.group(1).strip() and match.group(1).strip() not in names:
            names.append(match.group(1).strip())
    # The OCR parser may swap or duplicate the two 名称 lines depending on PDF
    # layout. The folder rule is authoritative for the configured company;
    # choose the first distinct OCR name as its counterparty.
    others = [name for name in names if name != str(config_company or "")]
    if folder_key == "purchase":
        corrected["buyer"] = str(config_company or "")
        corrected["seller"] = others[0] if others else str(fields.get("seller", ""))
    elif folder_key == "sales":
        corrected["seller"] = str(config_company or "")
        corrected["buyer"] = others[0] if others else str(fields.get("buyer", ""))
    corrected["configuredCompany"] = str(config_company or "")
    corrected["configuredCompanyRole"] = rule["configuredCompanyRole"]
    corrected["counterpartyRole"] = rule["counterpartyRole"]
    corrected["mapSource"] = rule["mapSource"]
    corrected["allowedTemplateBlocks"] = rule["allowedTemplateBlocks"]
    corrected["folderRuleAuthoritative"] = True
    return corrected


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
    artifacts: list[OcrArtifact] = []
    for raw_path in report.get("artifacts", []):
        metadata_path = Path(str(raw_path))
        if not metadata_path.is_file():
            raise OcrPipelineError(f"OCR元数据不存在：{metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        invoice_code = str(metadata.get("invoiceCode") or "")
        if allowed_invoice_codes is not None and invoice_code not in allowed_invoice_codes:
            continue
        text_path = Path(str(metadata.get("ocrText") or metadata_path.with_name("ocr.txt")))
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


class OpenAICompatibleTemplateSelector:
    """Call a configured OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        api_key: str | None,
        endpoint: str,
        model: str = "qwen3.7-flash",
        timeout: int = 60,
        *,
        api_key_env: str = "DASHSCOPE_API_KEY",
        provider_name: str = "Qwen",
        enable_thinking: bool = False,
    ) -> None:
        self.api_key = api_key or ""
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.provider_name = provider_name
        self.enable_thinking = enable_thinking

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "OpenAICompatibleTemplateSelector":
        raw = settings.get("llm", {})
        if not isinstance(raw, Mapping):
            raise OcrPipelineError("pipeline.llm 必须是 JSON 对象")
        provider_name = str(raw.get("provider_name", "Qwen")).strip() or "Qwen"
        model = str(raw.get("model", "qwen3.7-flash")).strip()
        endpoint = str(
            raw.get(
                "endpoint",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )
        ).strip()
        api_key_env = str(raw.get("api_key_env", "DASHSCOPE_API_KEY")).strip()
        timeout = raw.get("timeout_seconds", 60)
        enable_thinking = raw.get("enable_thinking", False)
        if not model:
            raise OcrPipelineError("pipeline.llm.model 不能为空")
        if not endpoint.startswith("https://"):
            raise OcrPipelineError("pipeline.llm.endpoint 必须使用 https://")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", api_key_env):
            raise OcrPipelineError("pipeline.llm.api_key_env 必须是大写环境变量名")
        if not isinstance(enable_thinking, bool):
            raise OcrPipelineError("pipeline.llm.enable_thinking 必须是 JSON 布尔值")
        try:
            timeout_value = int(timeout)
        except (TypeError, ValueError) as exc:
            raise OcrPipelineError("pipeline.llm.timeout_seconds 必须是正整数") from exc
        if timeout_value <= 0:
            raise OcrPipelineError("pipeline.llm.timeout_seconds 必须是正整数")
        return cls(
            os.environ.get(api_key_env),
            endpoint,
            model,
            timeout_value,
            api_key_env=api_key_env,
            provider_name=provider_name,
            enable_thinking=enable_thinking,
        )

    def choose(self, ocr_text: str, templates: list[Mapping[str, Any]], invoice_code: str = "", final_template_context: Mapping[str, Any] | None = None, business_rules: str = "", verified_memory: list[Mapping[str, Any]] | None = None, prompt_path: Path | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"status": f"待提供{self.provider_name} API", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": f"未配置 {self.api_key_env}", "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_not_called", "llmAttempted": False, "llmProvider": self.provider_name, "llmModel": self.model}
        catalog = [{key: value for key, value in item.items() if key in {"id", "name", "path", "decisionCode", "decisionName", "documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "summary", "entries", "matchRules", "amountSource", "bankAccountNumber"}} for item in templates]
        # Dynamic account/item catalogs are used by local validation and entry
        # rendering, not by the classifier.  Sending them to Qwen made the
        # request unnecessarily large and caused opaque HTTP 400 responses.
        sample = {
            key: copy.deepcopy(value)
            for key, value in dict(final_template_context or {}).items()
            if key
            not in {
                "dynamicAccountCatalog",
                "dynamicItemClassCatalog",
                "runtimeAccountMeta",
            }
        }
        if prompt_path is None or not prompt_path.is_file():
            return {
                "status": "error",
                "invoiceCode": invoice_code,
                "templatePath": "",
                "confidence": 0,
                "reason": "缺少当前公司的模板分类提示词",
                "raw": None,
                "textLength": len(ocr_text),
            }
        prompt = prompt_path.read_text(encoding="utf-8")
        replacements = {
            "<<BUSINESS_RULES>>": business_rules,
            "<<VERIFIED_MEMORY>>": json.dumps(list(verified_memory or [])[-25:], ensure_ascii=False),
            "<<INVOICE_CODE>>": invoice_code,
            "<<OCR_TEXT>>": ocr_text[:30000],
            "<<TEMPLATE_CATALOG>>": json.dumps(catalog, ensure_ascii=False),
            "<<FINAL_CONTEXT>>": json.dumps(sample, ensure_ascii=False),
        }
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        body = {
            "model": self.model,
            "temperature": 0,
            "enable_thinking": self.enable_thinking,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你只负责严格分类和字段提取，只返回 JSON 对象，不生成会计分录。"},
                {"role": "user", "content": prompt},
            ],
        }
        request_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        retryable_http_codes = {408, 409, 429, 500, 502, 503, 504}
        for attempt in range(3):
            request = urllib.request.Request(
                self.endpoint,
                data=request_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                content = payload["choices"][0]["message"]["content"]
                parsed = _parse_json_object(content)
                parsed["status"] = "success"
                parsed["invoiceCode"] = invoice_code
                parsed["raw"] = payload
                parsed["textLength"] = len(ocr_text)
                parsed["selectionMode"] = "llm_api"
                parsed["llmAttempted"] = True
                parsed["llmProvider"] = self.provider_name
                parsed["llmModel"] = self.model
                parsed["llmRequestId"] = str(payload.get("id") or payload.get("request_id") or "")
                return parsed
            except urllib.error.HTTPError as exc:
                try:
                    error_body = exc.read().decode("utf-8", errors="replace").strip()
                except OSError:
                    error_body = ""
                if exc.code in retryable_http_codes and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                detail = error_body or str(exc.reason or exc)
                return {
                    "status": "error",
                    "invoiceCode": invoice_code,
                    "templatePath": "",
                    "confidence": 0,
                    "reason": f"HTTP {exc.code}: {detail}",
                    "raw": {"httpStatus": exc.code, "errorBody": error_body},
                    "textLength": len(ocr_text),
                    "selectionMode": "llm_api_error",
                    "llmAttempted": True,
                    "llmProvider": self.provider_name,
                    "llmModel": self.model,
                }
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return {"status": "error", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": str(exc), "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_api_error", "llmAttempted": True, "llmProvider": self.provider_name, "llmModel": self.model}
            except (KeyError, IndexError, json.JSONDecodeError, TypeError, ValueError) as exc:
                return {"status": "error", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": str(exc), "raw": None, "textLength": len(ocr_text), "selectionMode": "llm_api_error", "llmAttempted": True, "llmProvider": self.provider_name, "llmModel": self.model}
        raise AssertionError("Qwen retry loop exited unexpectedly")


def extract_bank_transaction_date(ocr_text: str) -> str:
    """Extract the transaction/accounting date from bank OCR text."""
    labelled_patterns = (
        r"(?:记账日期|交易日期|入账日期|出账日期|业务日期|交易时间|交易日期时间)\s*[:：]?\s*(20\d{2})[-/.年]?(\d{2})[-/.月]?(\d{2})日?",
        r"(?:记账日期|交易日期|入账日期|出账日期|业务日期|交易时间|交易日期时间)\s*[:：]?\s*(20\d{2})(\d{2})(\d{2})",
    )
    fallback_patterns = (
        r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
    )
    for pattern in (*labelled_patterns, *fallback_patterns):
        for match in re.finditer(pattern, ocr_text):
            candidate = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
            except ValueError:
                continue
            return candidate
    return ""


def enforce_template_explanation(
    decision: dict[str, Any],
    artifact: OcrArtifact,
    template_root: Path,
    final_template_context: Mapping[str, Any] | None,
) -> None:
    """Replace every model-provided explanation with the selected user template."""
    if decision.get("ruleFallbackUsed"):
        raise OcrPipelineError(
            "模板候选未通过用户规则，禁止回退选中："
            + json.dumps(decision.get("ruleRejectedCandidates") or {}, ensure_ascii=False)
        )
    relative_path = str(decision.get("templatePath") or "").strip()
    if not relative_path:
        return
    root = template_root.resolve()
    template_path = (root / relative_path).resolve()
    try:
        template_path.relative_to(root)
    except ValueError as exc:
        raise OcrPipelineError(f"Qwen模板路径越过模板目录：{relative_path}") from exc
    if not template_path.is_file():
        raise OcrPipelineError(f"Qwen选择的模板不存在：{template_path}")
    template = json.loads(template_path.read_text(encoding="utf-8-sig"))
    if not isinstance(template, dict):
        raise OcrPipelineError(f"Qwen选择的模板不是JSON对象：{template_path}")

    context_values = dict(final_template_context or {})
    map_values = context_values.get("businessMapValues")
    if not isinstance(map_values, Mapping):
        map_values = {}
    source_key = source_from_folder_name(artifact.source_folder) or artifact.source_side
    bank_account_number = ""
    bank_transaction_date = ""
    bank_invoice_numbers: list[str] = []
    if source_key == "bank":
        bank_account_number = str(map_values.get("bankAccountNumber") or "").strip()
        if not re.fullmatch(r"[0-9]+", bank_account_number):
            raise OcrPipelineError(
                f"银行业务缺少 project.json 固定银行存款科目号：invoice={artifact.invoice_code}"
            )
        bank_transaction_date = extract_bank_transaction_date(artifact.text)
        if not bank_transaction_date:
            raise OcrPipelineError(
                f"银行 OCR 原文未识别出交易日期：invoice={artifact.invoice_code}"
            )
        extracted_fields = decision.get("extractedFields")
        if not isinstance(extracted_fields, dict):
            extracted_fields = {}
            decision["extractedFields"] = extracted_fields
        transaction_amount = map_values.get("transactionAmount")
        statement_amount = map_values.get("statementAmount")
        amount_source = str(map_values.get("amountSource") or "").strip()
        if (
            transaction_amount in (None, "")
            or statement_amount in (None, "")
            or not amount_source
            or map_values.get("amountValidated") is not True
        ):
            raise OcrPipelineError(
                f"银行映射缺少已验证 transactionAmount：invoice={artifact.invoice_code}"
            )
        try:
            normalized_transaction_amount = Decimal(str(transaction_amount)).quantize(
                Decimal("0.01")
            )
            normalized_statement_amount = Decimal(str(statement_amount)).quantize(
                Decimal("0.01")
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise OcrPipelineError(
                f"银行映射 transactionAmount 无效：invoice={artifact.invoice_code}"
            ) from exc
        if normalized_transaction_amount <= 0 or normalized_transaction_amount != normalized_statement_amount:
            raise OcrPipelineError(
                f"银行映射金额未通过一致性校验：invoice={artifact.invoice_code}"
            )
        ocr_amount = extracted_fields.get("totalAmountWithTax")
        amount_validated = True
        if ocr_amount not in (None, ""):
            try:
                normalized_ocr_amount = Decimal(str(ocr_amount)).quantize(Decimal("0.01"))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise OcrPipelineError(
                    f"银行 OCR 金额无效：invoice={artifact.invoice_code}"
                ) from exc
            amount_validated = normalized_ocr_amount == normalized_transaction_amount
            if not amount_validated:
                raise OcrPipelineError(
                    f"银行 OCR 金额与流水金额不一致：invoice={artifact.invoice_code}，"
                    f"ocr={normalized_ocr_amount}，statement={normalized_transaction_amount}"
                )
        extracted_fields["transactionAmount"] = float(normalized_transaction_amount)
        extracted_fields["statementAmount"] = float(normalized_statement_amount)
        extracted_fields["ocrAmount"] = ocr_amount if ocr_amount not in (None, "") else None
        extracted_fields["amountSource"] = amount_source
        extracted_fields["amountValidated"] = amount_validated
        extracted_fields["transactionDate"] = bank_transaction_date
        if str(map_values.get("flowDirection") or "") == "inflow":
            for value in map_values.get("invoiceNumbers") or []:
                number = str(value or "").strip()
                if re.fullmatch(r"\d{8,20}", number) and number not in bank_invoice_numbers:
                    bank_invoice_numbers.append(number)
    sales_map = {artifact.invoice_code: dict(map_values)} if source_key == "sales" else {}
    purchase_map = {artifact.invoice_code: dict(map_values)} if source_key == "purchase" else {}
    rendered = VoucherTemplateEngine([template]).render(
        template,
        TemplateContext(
            invoice_code=artifact.invoice_code,
            sales_map=sales_map,
            purchase_map=purchase_map,
            accountbook={},
            source=dict(map_values),
            template_name=str(template.get("name") or ""),
        ),
    )
    explanation_header = str(rendered.get("explanation_header") or "")
    explanation_body = str(rendered.get("explanation_body") or "").rstrip()
    if bank_invoice_numbers:
        explanation_body = " ".join(bank_invoice_numbers)
    explanation_separator = str(template.get("explanation_separator", " "))
    explanation = explanation_separator.join(
        part for part in (explanation_header, explanation_body) if part
    )
    bank_entry_explanation = (
        f"{explanation.rstrip()} {bank_transaction_date}"
        if source_key == "bank"
        else explanation
    )
    template_entries = template.get("entries") if isinstance(template.get("entries"), list) else []
    account_container = context_values.get("dynamicAccountCatalog")
    account_rows = account_container.get("accounts", []) if isinstance(account_container, Mapping) else []
    accounts_by_number: dict[str, list[Mapping[str, Any]]] = {}
    for account in account_rows:
        if isinstance(account, Mapping):
            accounts_by_number.setdefault(str(account.get("number") or ""), []).append(account)

    entries: list[dict[str, Any]] = []
    bank_deposit_entry_count = 0
    for index, template_entry in enumerate(template_entries, 1):
        if not isinstance(template_entry, Mapping):
            raise OcrPipelineError(f"模板分录不是对象：{relative_path} entries[{index}]")
        selector = template_entry.get("accountSelector")
        if not isinstance(selector, Mapping):
            raise OcrPipelineError(f"模板分录缺少accountSelector：{relative_path} entries[{index}]")
        account_number = str(selector.get("number") or "").strip()
        account_number_from = str(selector.get("numberFrom") or "").strip()
        is_bank_deposit_entry = (
            source_key == "bank"
            and account_number_from == "source.bankAccountNumber"
        )
        if (
            source_key == "bank"
            and "银行存款" in str(selector.get("name") or "")
            and not is_bank_deposit_entry
        ):
            raise OcrPipelineError(
                "银行存款分录必须使用动态科目来源 "
                f"numberFrom=source.bankAccountNumber：{relative_path} entries[{index}]"
            )
        if is_bank_deposit_entry:
            bank_deposit_entry_count += 1
            account_number = bank_account_number
        account_matches = accounts_by_number.get(account_number, [])
        if len(account_matches) != 1:
            raise OcrPipelineError(
                f"模板科目无法在当前账套唯一解析：number={account_number}, matches={len(account_matches)}"
            )
        account = account_matches[0]
        amount_reference = str(template_entry.get("amountFrom") or "").strip()
        amount_field = amount_reference.rsplit(".", 1)[-1]
        amount_value = map_values.get(amount_field)
        if amount_value in (None, ""):
            raise OcrPipelineError(
                f"模板金额来源不存在：invoice={artifact.invoice_code}, source={amount_reference}"
            )
        try:
            amount = round(float(amount_value), 2)
        except (TypeError, ValueError) as exc:
            raise OcrPipelineError(
                f"模板金额不是有效数字：invoice={artifact.invoice_code}, source={amount_reference}, value={amount_value}"
            ) from exc
        entries.append({
            "dc": int(template_entry.get("dc")),
            "accountNumber": account_number,
            "accountName": str(account.get("fullName") or ""),
            "accountId": str(account.get("id") or ""),
            "amount": amount,
            "amountFor": amount,
            "explanation": (
                bank_entry_explanation if is_bank_deposit_entry else explanation
            ),
            "cur": "RMB",
            "rate": "1",
        })
    if source_key == "bank" and bank_deposit_entry_count != 1:
        raise OcrPipelineError(
            f"银行模板必须恰好包含一条银行存款分录：template={relative_path}, actual={bank_deposit_entry_count}"
        )
    decision["filledEntries"] = entries
    if source_key == "bank":
        decision["bankAccountNumber"] = bank_account_number
        decision["bankTransactionDate"] = bank_transaction_date
        decision["invoiceNumbers"] = bank_invoice_numbers
    item_catalog = context_values.get("dynamicItemClassCatalog")

    def find_auxiliary(item_class_id: int, name: str) -> tuple[dict[str, Any] | None, int]:
        matches: dict[str, dict[str, Any]] = {}

        def visit(value: Any, inherited_class_id: int | None = None) -> None:
            if isinstance(value, Mapping):
                own_class_id = value.get("itemClassId", inherited_class_id)
                try:
                    current_class_id = int(own_class_id) if own_class_id not in (None, "") else inherited_class_id
                except (TypeError, ValueError):
                    current_class_id = inherited_class_id
                item_id = value.get("id")
                if current_class_id == item_class_id and item_id not in (None, "", 0, "0") and str(value.get("name") or "").strip() == name:
                    matches[str(item_id)] = dict(value)
                for child in value.values():
                    visit(child, current_class_id)
            elif isinstance(value, list):
                for child in value:
                    visit(child, inherited_class_id)

        visit(item_catalog)
        if len(matches) != 1:
            return None, len(matches)
        return next(iter(matches.values())), 1

    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            template_entry = template_entries[index] if index < len(template_entries) and isinstance(template_entries[index], Mapping) else {}
            auxiliary_rule = template_entry.get("auxiliary") if isinstance(template_entry, Mapping) else None
            if not isinstance(auxiliary_rule, Mapping):
                entry.pop("auxiliary", None)
                continue
            item_class_id = int(auxiliary_rule.get("itemClassId"))
            counterparty_name = str(
                map_values.get("customName") if source_key == "sales"
                else map_values.get("supplierName") if source_key == "purchase"
                else map_values.get("counterpartyName") if source_key == "bank"
                else ""
            ).strip()
            if source_key == "bank" and not counterparty_name:
                extracted = decision.get("extractedFields")
                if isinstance(extracted, Mapping):
                    counterparty_name = str(
                        extracted.get("counterpartyName")
                        or extracted.get("counterparty")
                        or extracted.get("payeeName")
                        or extracted.get("payerName")
                        or ""
                    ).strip()
            if not counterparty_name:
                raise OcrPipelineError(f"业务映射缺少交易对方名称：source={source_key}, invoice={artifact.invoice_code}")
            mapped = map_values.get("auxiliaryItem")
            if not isinstance(mapped, Mapping) or str(mapped.get("name") or "").strip() != counterparty_name or mapped.get("id") in (None, "", 0, "0"):
                mapped, match_count = find_auxiliary(item_class_id, counterparty_name)
                if mapped is None:
                    reason = f"动态辅助核算目录无法唯一解析：itemClassId={item_class_id}, name={counterparty_name}, matches={match_count}"
                    decision["status"] = "blocked"
                    decision["analysisStatus"] = "blocked"
                    decision["blockReason"] = reason
                    errors = decision.get("finalTemplateValidationErrors")
                    if not isinstance(errors, list):
                        errors = []
                        decision["finalTemplateValidationErrors"] = errors
                    if reason not in errors:
                        errors.append(reason)
                    entry["auxiliary"] = {
                        "itemClassId": item_class_id,
                        "itemClass": str(map_values.get("itemClass") or ""),
                        "id": "",
                        "number": "",
                        "name": counterparty_name,
                        "field": str(auxiliary_rule.get("field") or ""),
                    }
                    continue
            resolved_item_class = str(mapped.get("itemClass") or "").strip()
            if not resolved_item_class:
                resolved_item_class = {
                    1: "客户",
                    2: "存货",
                    3: "职员",
                    4: "项目",
                    5: "供应商",
                    6: "部门",
                }.get(item_class_id, str(map_values.get("itemClass") or ""))
            entry["auxiliary"] = {
                "itemClassId": item_class_id,
                "itemClass": resolved_item_class,
                "id": str(mapped.get("id")),
                "number": str(mapped.get("number") or ""),
                "name": counterparty_name,
                "field": str(auxiliary_rule.get("field") or ""),
            }
    decision["explanation_header"] = explanation_header
    decision["explanation_body"] = explanation_body
    decision["explanation"] = bank_entry_explanation if source_key == "bank" else explanation


def enforce_dynamic_supplier_payables_exception(
    decision: dict[str, Any],
    artifact: OcrArtifact,
    template_root: Path,
    final_template_context: Mapping[str, Any] | None,
    chosen_record: Mapping[str, Any],
) -> bool:
    """Resolve a user-configured dynamic AP split, or keep it pending safely."""
    context_values = dict(final_template_context or {})
    map_values = context_values.get("businessMapValues")
    if not isinstance(map_values, Mapping):
        map_values = {}
    definition = chosen_record.get("exception")
    if not isinstance(definition, Mapping):
        raise OcrPipelineError("动态异常模板缺少 exception 定义")

    bank_account_number = str(map_values.get("bankAccountNumber") or "").strip()
    transaction_date = extract_bank_transaction_date(artifact.text)
    exception_config = map_values.get("exceptionConfig")
    allocations = (
        exception_config.get("allocations")
        if isinstance(exception_config, Mapping)
        and isinstance(exception_config.get("allocations"), list)
        else []
    )
    errors: list[str] = []
    expected_template_id = str(chosen_record.get("id") or "")
    configured_template_id = (
        str(exception_config.get("template_id") or "").strip()
        if isinstance(exception_config, Mapping)
        else ""
    )
    configured_handling = (
        str(exception_config.get("handling") or "").strip()
        if isinstance(exception_config, Mapping)
        else ""
    )
    if not isinstance(exception_config, Mapping):
        errors.append("project.json 的 sources.bank.exceptions 未包含该供应商")
    elif configured_handling != "dynamic_supplier_payables":
        errors.append(
            "exception handling 不匹配："
            f"expected=dynamic_supplier_payables, actual={configured_handling or '-'}"
        )
    elif configured_template_id != expected_template_id:
        errors.append(
            "exception template_id 不匹配："
            f"expected={expected_template_id}, actual={configured_template_id or '-'}"
        )
    if not allocations:
        errors.append("allocations 为空，等待填写实际应付账款供应商和金额")
    if not re.fullmatch(r"[0-9]+", bank_account_number):
        errors.append("缺少有效 bank_account_number")
    if not transaction_date:
        errors.append("OCR 未识别出交易日期")

    try:
        required_total = Decimal(str(map_values.get("amount"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError):
        required_total = Decimal("0")
        errors.append("银行流水金额无效")

    normalized_allocations: list[tuple[str, Decimal]] = []
    for index, allocation in enumerate(allocations, 1):
        if not isinstance(allocation, Mapping):
            errors.append(f"allocations[{index}] 不是对象")
            continue
        supplier_name = str(allocation.get("supplier_name") or "").strip()
        try:
            amount = Decimal(str(allocation.get("amount"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"allocations[{index}] 金额无效")
            continue
        if not supplier_name:
            errors.append(f"allocations[{index}] 缺少 supplier_name")
        elif amount <= 0:
            errors.append(f"allocations[{index}] amount 必须大于 0")
        else:
            normalized_allocations.append((supplier_name, amount))
    allocated_total = sum((amount for _, amount in normalized_allocations), Decimal("0"))
    if allocations and allocated_total != required_total:
        errors.append(
            "动态供应商分摊合计必须等于银行付款："
            f"allocations={format(allocated_total, 'f')}, bank={format(required_total, 'f')}"
        )

    account_rows = (
        context_values.get("dynamicAccountCatalog", {}).get("accounts", [])
        if isinstance(context_values.get("dynamicAccountCatalog"), Mapping)
        else []
    )
    accounts_by_number: dict[str, list[Mapping[str, Any]]] = {}
    for account in account_rows if isinstance(account_rows, list) else []:
        if isinstance(account, Mapping):
            accounts_by_number.setdefault(str(account.get("number") or ""), []).append(account)
    payable_number = str(definition.get("allocationAccountNumber") or "2202")
    payable_matches = accounts_by_number.get(payable_number, [])
    bank_matches = accounts_by_number.get(bank_account_number, [])
    if len(payable_matches) != 1:
        errors.append(f"目标账套应付账款科目无法唯一解析：number={payable_number}")
    if len(bank_matches) != 1:
        errors.append(f"目标账套银行存款科目无法唯一解析：number={bank_account_number}")

    item_catalog = context_values.get("dynamicItemClassCatalog")

    def find_supplier(name: str) -> tuple[dict[str, Any] | None, int]:
        matches: dict[str, dict[str, Any]] = {}

        def visit(value: Any, inherited_class_id: int | None = None) -> None:
            if isinstance(value, Mapping):
                own_class_id = value.get("itemClassId", inherited_class_id)
                try:
                    class_id = int(own_class_id) if own_class_id not in (None, "") else inherited_class_id
                except (TypeError, ValueError):
                    class_id = inherited_class_id
                item_id = value.get("id")
                if (
                    class_id == 5
                    and item_id not in (None, "", 0, "0")
                    and str(value.get("name") or "").strip() == name
                ):
                    matches[str(item_id)] = dict(value)
                for child in value.values():
                    visit(child, class_id)
            elif isinstance(value, list):
                for child in value:
                    visit(child, inherited_class_id)

        visit(item_catalog)
        if len(matches) != 1:
            return None, len(matches)
        return next(iter(matches.values())), 1

    suppliers: list[tuple[str, Decimal, dict[str, Any]]] = []
    for supplier_name, amount in normalized_allocations:
        supplier, match_count = find_supplier(supplier_name)
        if supplier is None:
            errors.append(
                "动态供应商无法唯一解析："
                f"itemClassId=5, name={supplier_name}, matches={match_count}"
            )
        else:
            suppliers.append((supplier_name, amount, supplier))

    decision["exceptionType"] = str(definition.get("type") or "")
    decision["exceptionConfig"] = copy.deepcopy(dict(exception_config or {}))
    decision["exceptionValidationErrors"] = errors
    decision["bankAccountNumber"] = bank_account_number
    decision["bankTransactionDate"] = transaction_date
    decision["invoiceNumbers"] = []
    decision["explanation_header"] = ""
    decision["explanation_body"] = "付供应商款"
    decision["explanation"] = (
        f"付供应商款 {transaction_date}" if transaction_date else "付供应商款"
    )
    extracted_fields = decision.get("extractedFields")
    if not isinstance(extracted_fields, dict):
        extracted_fields = {}
        decision["extractedFields"] = extracted_fields
    extracted_fields["transactionDate"] = transaction_date

    if errors:
        decision["status"] = "exception"
        decision["analysisStatus"] = "exception_pending"
        decision["exceptionStatus"] = "pending"
        decision["blockReason"] = "；".join(errors)
        decision["filledEntries"] = []
        return False

    payable_account = payable_matches[0]
    bank_account = bank_matches[0]
    entries: list[dict[str, Any]] = []
    for supplier_name, amount, supplier in suppliers:
        numeric_amount = float(amount)
        entries.append({
            "dc": 1,
            "accountNumber": payable_number,
            "accountName": str(payable_account.get("fullName") or ""),
            "accountId": str(payable_account.get("id") or ""),
            "amount": numeric_amount,
            "amountFor": numeric_amount,
            "explanation": "付供应商款",
            "cur": "RMB",
            "rate": "1",
            "auxiliary": {
                "itemClassId": 5,
                "itemClass": "供应商",
                "id": str(supplier.get("id") or ""),
                "number": str(supplier.get("number") or ""),
                "name": supplier_name,
                "field": "supplierId",
            },
        })
    entries.append({
        "dc": -1,
        "accountNumber": bank_account_number,
        "accountName": str(bank_account.get("fullName") or ""),
        "accountId": str(bank_account.get("id") or ""),
        "amount": float(required_total),
        "amountFor": float(required_total),
        "explanation": f"付供应商款 {transaction_date}",
        "cur": "RMB",
        "rate": "1",
    })
    decision["filledEntries"] = entries
    decision["status"] = "success"
    decision["analysisStatus"] = "ready_for_review"
    decision["exceptionStatus"] = "resolved"
    decision["selectionMode"] = "deterministic_exception"
    return True


def compact_analysis_for_storage(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Persist only fields needed by review and later receipt generation."""
    result: dict[str, Any] = {}
    for key in (
        "templatePath", "templateId", "decisionCode", "decisionName", "selectionMode", "confidence", "reason", "status", "analysisStatus",
        "llmAttempted", "llmProvider", "llmModel", "llmRequestId",
        "explanation_header", "explanation_body", "explanation", "sourceFolder", "configCompany",
        "partyRule", "bankAccountNumber", "bankTransactionDate", "invoiceNumbers", "sourcePdf", "validation",
        "exceptionStatus", "exceptionType", "exceptionConfig", "exceptionValidationErrors",
    ):
        if key in decision:
            result[key] = copy.deepcopy(decision[key])

    extracted = decision.get("extractedFields")
    if isinstance(extracted, Mapping):
        result["extractedFields"] = {key: copy.deepcopy(value) for key, value in extracted.items() if key != "invoiceCode"}

    stored_entries: list[dict[str, Any]] = []
    for raw_entry in decision.get("filledEntries", []) if isinstance(decision.get("filledEntries"), list) else []:
        if not isinstance(raw_entry, Mapping):
            continue
        entry = copy.deepcopy(dict(raw_entry))
        removable_keys = ["entryId", "amountFrom"]
        if str(decision.get("sourceFolder") or "") != "bank":
            removable_keys.append("explanation")
        for key in removable_keys:
            entry.pop(key, None)
        if entry.get("auxiliary") is None:
            entry.pop("auxiliary", None)
        stored_entries.append(entry)
    result["filledEntries"] = stored_entries

    ocr_fields = decision.get("ocrFields")
    if isinstance(ocr_fields, Mapping):
        result["ocrFields"] = {key: copy.deepcopy(value) for key, value in ocr_fields.items() if key != "_normalizedText"}

    diagnostics = {
        key: copy.deepcopy(decision[key])
        for key in ("ruleRejectedCandidates", "ruleFallbackUsed", "finalTemplateValidationErrors", "blockReason")
        if decision.get(key)
    }
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("模型返回不是JSON对象")
    return value


def _normalize_match_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _keyword_matches(normalized_text: str, keyword: Any) -> bool:
    normalized_keyword = _normalize_match_text(keyword)
    if not normalized_keyword:
        return False
    invoice_aliases = {
        "增值税发票", "电子发票", "数电发票", "发票号码", "开票日期",
        "增值税专用发票", "增值税普通发票", "全电发票",
    }
    if normalized_keyword in {_normalize_match_text(value) for value in invoice_aliases}:
        return any(_normalize_match_text(value) in normalized_text for value in invoice_aliases)
    return normalized_keyword in normalized_text


def _contains_foreign_currency(ocr_text: str) -> bool:
    text = unicodedata.normalize("NFKC", str(ocr_text or "")).casefold()
    if any(marker in text for marker in ("美元", "美金", "港币", "欧元")):
        return True
    return any(
        re.search(pattern, text, flags=re.IGNORECASE) is not None
        for pattern in (
            r"(?<![a-z])usd(?![a-z])",
            r"(?<![a-z])us\s*\$",
            r"(?<![a-z])hkd(?![a-z])",
            r"(?<![a-z])eur(?![a-z])",
        )
    )


def _enrich_bank_counterparty_roles(
    final_template_context: Mapping[str, Any] | None,
    business_values: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve customer/supplier roles from the live target-accountbook catalog."""
    values = dict(business_values)
    counterparty_name = str(values.get("counterpartyName") or "").strip()
    if not counterparty_name or not isinstance(final_template_context, Mapping):
        return values
    catalog = final_template_context.get("dynamicItemClassCatalog")
    matches: dict[str, dict[str, Any]] = {}

    def visit(value: Any, inherited_class_id: int | None = None) -> None:
        if isinstance(value, Mapping):
            own_class_id = value.get("itemClassId", inherited_class_id)
            try:
                class_id = int(own_class_id) if own_class_id not in (None, "") else inherited_class_id
            except (TypeError, ValueError):
                class_id = inherited_class_id
            if (
                class_id in {1, 5}
                and value.get("id") not in (None, "", 0, "0")
                and str(value.get("name") or "").strip() == counterparty_name
            ):
                role = "customer" if class_id == 1 else "supplier"
                matches[role] = dict(value)
            for child in value.values():
                visit(child, class_id)
        elif isinstance(value, list):
            for child in value:
                visit(child, inherited_class_id)

    visit(catalog)
    roles = sorted(matches)
    values["counterpartyRoles"] = roles
    values["counterpartyRoleSource"] = (
        "dynamic_item_catalog" if matches else "unresolved"
    )
    values.pop("auxiliaryItem", None)
    values.pop("supplierName", None)
    values.pop("customerName", None)
    values.pop("customName", None)
    if not roles:
        values.pop("counterpartyType", None)
        values.pop("itemClass", None)
    if len(roles) == 1:
        role = roles[0]
        values["counterpartyType"] = role
        values["itemClass"] = "客户" if role == "customer" else "供应商"
        if role == "customer":
            values["customerName"] = counterparty_name
            values["customName"] = counterparty_name
        else:
            values["supplierName"] = counterparty_name
        if role in matches:
            values["auxiliaryItem"] = matches[role]
    return values


def _rule_candidates(
    candidate_records: list[dict[str, Any]],
    artifact: OcrArtifact,
    business_values: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    fields = metadata.get("fields", {}) if isinstance(metadata.get("fields"), Mapping) else {}
    text = _normalize_match_text(artifact.text)
    folder = artifact.source_folder.lower()
    explicit: list[tuple[int, int, int, dict[str, Any]]] = []
    defaults: list[tuple[int, dict[str, Any]]] = []
    reasons: dict[str, str] = {}
    has_business_values = isinstance(business_values, Mapping)
    values = business_values if has_business_values else {}
    flow_direction = str(values.get("flowDirection") or "").strip().lower()
    actual_counterparty_roles = {
        str(value).strip().lower()
        for value in values.get("counterpartyRoles") or []
        if str(value).strip()
    }
    if not actual_counterparty_roles:
        legacy_role = str(values.get("counterpartyType") or "").strip().lower()
        if legacy_role:
            actual_counterparty_roles.add(legacy_role)
    invoice_numbers = [
        str(value).strip()
        for value in values.get("invoiceNumbers") or []
        if str(value).strip()
    ]
    for item in candidate_records:
        rules=item.get("matchRules", {}) if isinstance(item.get("matchRules"), Mapping) else {}
        if not rules and len(candidate_records) == 1:
            explicit.append((0, 0, 0, item))
            continue
        source_folders = [str(x).strip().lower() for x in rules.get("sourceFolders", [])]
        if source_folders and folder not in source_folders:
            reasons[str(item.get("path"))]="sourceFolder不匹配"
            continue
        if bool(rules.get("excludeCounterpartyEqualsConfigCompany")):
            counterparty_name = str(values.get("counterpartyName") or "").strip()
            config_company = str(values.get("configCompany") or "").strip()
            if counterparty_name and config_company and counterparty_name == config_company:
                reasons[str(item.get("path"))] = "交易对方是资料公司自身"
                continue
        configured_counterparties = [
            str(value).strip()
            for value in rules.get("counterpartyNames", [])
            if str(value).strip()
        ]
        actual_counterparty = str(values.get("counterpartyName") or "").strip()
        matched_counterparty = ""
        if configured_counterparties:
            normalized_actual = _normalize_match_text(actual_counterparty)
            matched_counterparty = next(
                (
                    value
                    for value in configured_counterparties
                    if _normalize_match_text(value) == normalized_actual
                ),
                "",
            )
            if not matched_counterparty:
                reasons[str(item.get("path"))] = "交易对方不匹配"
                continue
        required_exception_handling = str(
            rules.get("requiresExceptionHandling") or ""
        ).strip()
        exception_config = values.get("exceptionConfig")
        actual_exception_handling = (
            str(exception_config.get("handling") or "").strip()
            if isinstance(exception_config, Mapping)
            else ""
        )
        if (
            required_exception_handling
            and actual_exception_handling != required_exception_handling
        ):
            reasons[str(item.get("path"))] = "未在本月项目中配置对应交易对象 exception"
            continue
        allowed_directions = {
            str(value).strip().lower()
            for value in rules.get("flowDirections", [])
            if str(value).strip()
        }
        if allowed_directions and flow_direction and flow_direction not in allowed_directions:
            reasons[str(item.get("path"))] = "资金方向不匹配"
            continue
        configured_roles = {
            str(value).strip().lower()
            for value in rules.get("counterpartyRoles", [])
            if str(value).strip()
        }
        if configured_roles and not (configured_roles & actual_counterparty_roles):
            reasons[str(item.get("path"))] = "目标账套客户/供应商目录身份不匹配"
            continue
        if (
            bool(rules.get("requiresInvoiceNumbers"))
            and has_business_values
            and not invoice_numbers
        ):
            reasons[str(item.get("path"))] = "缺少流水表纯数字发票索引"
            continue
        blocks=set(str(x) for x in fields.get("allowedTemplateBlocks", []))
        if blocks and str(item.get("documentBlock", "")) not in blocks:
            reasons[str(item.get("path"))]="销售/进项目录业务板块不匹配"
            continue
        currency = str(item.get("currency") or "")
        if currency == "人民币" and _contains_foreign_currency(artifact.text):
            reasons[str(item.get("path"))]="OCR明确为外币，人民币模板不匹配"
            continue
        required=[str(x).lower() for x in rules.get("requiredKeywords", [])]
        if required and not all(_keyword_matches(text, keyword) for keyword in required):
            reasons[str(item.get("path"))]="缺少requiredKeywords"
            continue
        excluded=[str(x).lower() for x in rules.get("excludeKeywords", [])]
        if excluded and any(_keyword_matches(text, keyword) for keyword in excluded):
            reasons[str(item.get("path"))]="命中excludeKeywords"
            continue
        document_keywords = [str(x) for x in rules.get("documentKeywords", [])]
        if document_keywords and not any(_keyword_matches(text, keyword) for keyword in document_keywords):
            reasons[str(item.get("path"))]="未识别单据类型"
            continue
        keyword_groups = [group for group in rules.get("keywordGroups", []) if isinstance(group, list)]
        if keyword_groups and not all(any(_keyword_matches(text, keyword) for keyword in group) for group in keyword_groups):
            reasons[str(item.get("path"))]="未满足keywordGroups"
            continue
        any_keywords=[str(x).lower() for x in rules.get("anyKeywords", [])]
        matched_keywords = [keyword for keyword in any_keywords if _keyword_matches(text, keyword)]
        if matched_counterparty:
            matched_keywords.append(matched_counterparty)
        for group in keyword_groups:
            matched_keywords.extend(
                str(keyword).lower() for keyword in group if _keyword_matches(text, keyword)
            )
        default_allowed = bool(rules.get("defaultForSource")) and (
            not bool(rules.get("defaultRequiresBusinessValues"))
            or has_business_values
        )
        if any_keywords and not matched_keywords and not default_allowed:
            reasons[str(item.get("path"))]="未命中业务关键词"
            continue
        priority = int(rules.get("priority", 0) or 0)
        if matched_keywords or keyword_groups:
            longest = max((_normalize_match_text(keyword).__len__() for keyword in matched_keywords), default=0)
            explicit.append((longest, len(matched_keywords), priority, item))
        elif default_allowed:
            defaults.append((priority, item))
        else:
            reasons[str(item.get("path"))]="没有可审计的业务命中词"

    if explicit:
        highest_longest = max(row[0] for row in explicit)
        longest_matches = [row for row in explicit if row[0] == highest_longest]
        highest_count = max(row[1] for row in longest_matches)
        count_matches = [row for row in longest_matches if row[1] == highest_count]
        highest_priority = max(row[2] for row in count_matches)
        selected = [row for row in count_matches if row[2] == highest_priority]
        return [row[3] for row in selected], reasons
    if defaults:
        highest_priority = max(row[0] for row in defaults)
        return [item for priority, item in defaults if priority == highest_priority], reasons
    return [], reasons


def _load_analysis_memory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "processed": [], "verifiedDecisions": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"version": 1, "processed": [], "verifiedDecisions": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "processed": [], "verifiedDecisions": []}


def _save_analysis_memory(path: Path, memory: dict[str, Any], invoice_code: str, decision: Mapping[str, Any]) -> None:
    with _ANALYSIS_MEMORY_LOCK:
        # Each worker starts from a snapshot. Reload inside the lock so one
        # worker cannot overwrite decisions saved by another worker.
        current = _load_analysis_memory(path)
        processed = [item for item in current.get("processed", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
        processed.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "analysisStatus": decision.get("analysisStatus", ""), "confidence": decision.get("confidence", 0)})
        current["processed"] = processed
        verified = [item for item in current.get("verifiedDecisions", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
        if decision.get("analysisStatus") == "ready_for_review":
            fields = decision.get("extractedFields", {}) if isinstance(decision.get("extractedFields"), Mapping) else {}
            verified.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "businessType": decision.get("businessType", ""), "sellerName": fields.get("sellerName", ""), "buyerName": fields.get("buyerName", ""), "confidence": decision.get("confidence", 0)})
        current["verifiedDecisions"] = verified
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = False
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                temporary.replace(path)
                saved = True
                break
            except OSError as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(0.05 * (attempt + 1))
        if not saved:
            logging.getLogger(__name__).warning(
                "分析记忆写入失败但不阻断当前记录：%s：%s",
                path,
                last_error,
            )
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        memory.clear()
        memory.update(copy.deepcopy(current))


def analyze_ocr_and_choose_template(artifact: OcrArtifact, template_root: Path, selector: OpenAICompatibleTemplateSelector | None = None, final_template_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from .template_catalog import TemplateCatalog

    catalog = TemplateCatalog.load(template_root)
    metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    fields = metadata.get("fields", {}) if isinstance(metadata.get("fields"), Mapping) else {}
    allowed_blocks = list(fields.get("allowedTemplateBlocks", []))
    allowed_block_set = set(allowed_blocks)
    candidate_records = []
    for record in catalog.records:
        if not bool(record.get("enabled", True)):
            continue
        enriched = dict(record)
        try:
            template_payload = catalog.load_template(record)
            enriched.update({key: template_payload.get(key) for key in ("decisionCode", "decisionName", "documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "matchRules", "amountSource", "exception") if template_payload.get(key) is not None})
        except Exception:
            pass
        enriched["templateFileName"] = Path(str(record.get("path", ""))).name
        if allowed_block_set and str(enriched.get("documentBlock", "")) not in allowed_block_set:
            continue
        candidate_records.append(enriched)
    if not candidate_records:
        raise OcrPipelineError(f"{artifact.source_folder} 没有符合买卖方方向的模板候选")
    source_key = artifact.source_side if artifact.source_side in {"sales", "purchase", "bank", "misc"} else "misc"
    runtime_map_values = (
        final_template_context.get("businessMapValues")
        if isinstance(final_template_context, Mapping)
        else None
    )
    if not isinstance(runtime_map_values, Mapping):
        runtime_map_values = {}
    required_bank_account_number = ""
    if source_key == "bank":
        runtime_map_values = _enrich_bank_counterparty_roles(
            final_template_context,
            runtime_map_values,
        )
        if isinstance(final_template_context, dict):
            final_template_context["businessMapValues"] = runtime_map_values
        required_bank_account_number = str(
            runtime_map_values.get("bankAccountNumber") or ""
        ).strip()
        if not re.fullmatch(r"[0-9]+", required_bank_account_number):
            raise OcrPipelineError(
                f"银行模板选择缺少固定银行存款科目号：invoice={artifact.invoice_code}"
            )
        for item in candidate_records:
            item["bankAccountNumber"] = required_bank_account_number
    scoped_records = []
    for item in candidate_records:
        rules = item.get("matchRules") if isinstance(item.get("matchRules"), Mapping) else {}
        configured_sources = {str(value).strip().lower() for value in rules.get("sourceFolders", [])}
        path_parts = Path(str(item.get("path", ""))).parts
        physical_source = path_parts[0].lower() if path_parts else ""
        if source_key in configured_sources or (not configured_sources and physical_source == source_key):
            scoped_records.append(item)
    if not scoped_records:
        raise OcrPipelineError(f"模板范围为空：source={source_key}，目录={template_root / source_key}")
    candidate_records = scoped_records
    rule_candidates, rejected = _rule_candidates(
        candidate_records,
        artifact,
        runtime_map_values,
    )
    if not rule_candidates:
        raise OcrPipelineError(
            "OCR文字没有命中可审计的模板规则，禁止退回全部候选猜测："
            + json.dumps(rejected, ensure_ascii=False)
        )
    rule_fallback = False
    prompt_path = template_root / "prompts" / f"{source_key}.md"
    if not prompt_path.is_file():
        raise OcrPipelineError(f"缺少{source_key}固定提示词：{prompt_path}")
    from kdzwy_receipt_uploader.fixed_prompt_rules import FIXED_LLM_RULES

    business_rules = (
        FIXED_LLM_RULES.rstrip()
        + "\n\n# 公司与业务自定义规则\n"
        + prompt_path.read_text(encoding="utf-8")
        .replace("{{source_company}}", str(metadata.get("configCompany") or ""))
        .replace("{{template_directory}}", f"templates/{template_root.name}/{source_key}")
    )
    if source_key == "bank":
        business_rules += (
            "\n\n# 当前银行固定科目\n"
            f"bankAccountNumber={required_bank_account_number}。"
            "该值来自 project.json，模板选择、模板渲染和最终分录中的银行存款科目号必须完全一致；禁止模型修改或猜测。"
        )
    memory_path = artifact.output_dir.parent / "analysis_memory.json"
    memory = _load_analysis_memory(memory_path)
    active_selector = selector or OpenAICompatibleTemplateSelector.from_settings({})
    choose_parameters = inspect.signature(active_selector.choose).parameters
    if "business_rules" in choose_parameters:
        choose_kwargs = {
            "final_template_context": final_template_context,
            "business_rules": business_rules,
            "verified_memory": list(memory.get("verifiedDecisions", [])),
        }
        if "prompt_path" in choose_parameters:
            choose_kwargs["prompt_path"] = template_root / "prompts" / "invoice_classifier_prompt.txt"
        decision = active_selector.choose(
            artifact.text, rule_candidates, artifact.invoice_code,
            **choose_kwargs,
        )
    else:
        decision = active_selector.choose(artifact.text, rule_candidates, artifact.invoice_code)
    allowed_paths = {str(item.get("path", "")) for item in rule_candidates}
    decision.setdefault("status", "success" if decision.get("templatePath") else "pending")
    chosen = str(decision.get("templatePath", ""))
    if chosen and chosen not in allowed_paths:
        decision["status"] = "invalid"
        decision["reason"] = "Qwen 返回的模板路径不在 templates 根目录候选中"
        decision["templatePath"] = ""
    chosen_record = next((item for item in rule_candidates if str(item.get("path", "")) == str(decision.get("templatePath", ""))), None)
    exception_ready: bool | None = None
    if chosen_record and isinstance(chosen_record.get("exception"), Mapping):
        exception_ready = enforce_dynamic_supplier_payables_exception(
            decision,
            artifact,
            template_root,
            final_template_context,
            chosen_record,
        )
    else:
        enforce_template_explanation(decision, artifact, template_root, final_template_context)
    decision["ocrFields"] = fields
    decision["allowedTemplateBlocks"] = allowed_blocks
    decision["sourceFolder"] = artifact.source_folder
    decision["sourceSide"] = artifact.source_side
    decision["configCompany"] = metadata.get("configCompany", "")
    decision["partyRule"] = metadata.get("partyRule", {})
    decision["ocrTextPath"] = str(artifact.text_path.resolve())
    decision["ocrMetadataPath"] = str(artifact.metadata_path.resolve())
    decision["sourcePdf"] = str(artifact.source_pdf.resolve())
    decision["templateCandidates"] = len(rule_candidates)
    decision["templateCandidatesBeforeRules"] = len(candidate_records)
    decision["ruleRejectedCandidates"] = rejected
    decision["ruleFallbackUsed"] = rule_fallback
    if chosen_record:
        expected_id = str(chosen_record.get("id") or "")
        returned_id = str(decision.get("templateId") or "")
        if returned_id != expected_id:
            decision["status"] = "invalid"
            decision["reason"] = f"模型返回的templateId与templatePath不一致：expected={expected_id}, actual={returned_id}"
        decision["decisionCode"] = str(chosen_record.get("decisionCode") or "")
        decision["decisionName"] = str(chosen_record.get("decisionName") or "")
    validation = {
        "folderRule": (
            str(chosen_record.get("documentBlock", "")) == "银行"
            if source_key == "bank" and chosen_record
            else bool(set(fields.get("allowedTemplateBlocks", [])) & {str(chosen_record.get("documentBlock", ""))}) if chosen_record else False
        ),
        "sourceFolderRule": bool(chosen_record) and (
            not chosen_record.get("matchRules", {}).get("sourceFolders")
            or artifact.source_folder.lower()
            in {str(value).strip().lower() for value in chosen_record.get("matchRules", {}).get("sourceFolders", [])}
        ),
        "confidenceRule": float(decision.get("confidence", 0) or 0) >= 0.9,
        "mapSourceRule": (
            str(chosen_record.get("amountSource", "")) == "source"
            if source_key == "bank" and chosen_record
            else (str(chosen_record.get("amountSource", "")) == str(fields.get("mapSource", ""))) if chosen_record else False
        ),
    }
    if source_key == "bank":
        configured_directions = {
            str(value).strip().lower()
            for value in (
                chosen_record.get("matchRules", {}).get("flowDirections", [])
                if chosen_record
                else []
            )
        }
        actual_direction = str(runtime_map_values.get("flowDirection") or "").lower()
        validation["flowDirectionRule"] = bool(
            chosen_record
            and actual_direction
            and (
                not configured_directions
                or actual_direction in configured_directions
            )
        )
        extracted_fields = decision.get("extractedFields")
        validation["amountRule"] = bool(
            isinstance(extracted_fields, Mapping)
            and extracted_fields.get("amountValidated") is True
            and extracted_fields.get("transactionAmount") not in (None, "")
            and extracted_fields.get("amountSource")
        )
    from .final_template_sample import validate_filled_entries
    sample_errors = validate_filled_entries(decision, final_template_context or {}) if final_template_context and exception_ready is not False else list(decision.get("exceptionValidationErrors") or [])
    decision["finalTemplateValidationErrors"] = sample_errors
    validation["finalTemplateRule"] = not sample_errors if final_template_context else True
    if exception_ready is not None:
        validation["exceptionRule"] = exception_ready
    decision["validation"] = validation
    if exception_ready is False:
        decision["analysisStatus"] = "exception_pending"
    else:
        decision["analysisStatus"] = "ready_for_review" if decision.get("status") == "success" and all(validation.values()) else "blocked"
    if decision["analysisStatus"] != "ready_for_review":
        decision.setdefault("blockReason", "系统强校验未全部通过，禁止进入可提交receipt")
    decision["businessPrompt"] = str(prompt_path.resolve())
    decision["memoryFile"] = str(memory_path.resolve())
    _save_analysis_memory(memory_path, memory, artifact.invoice_code, decision)
    return decision
