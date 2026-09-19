"""Invoice field parsing and folder-based party rules."""
from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..source_profile import source_from_folder_name


def _detect_invoice_document_kind(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    if "航空运输电子客票" in compact or ("航班号" in compact and "民航发展基金" in compact):
        return "air_ticket"
    if "铁路电子客票" in compact or ("电子客票号" in compact and "车次" in compact):
        return "railway_ticket"
    return "vat_invoice"


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
