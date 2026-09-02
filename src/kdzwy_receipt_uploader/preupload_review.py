"""Generate and enforce the human review package before live upload."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class PreuploadReviewError(RuntimeError):
    pass


def _money_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 0.005
    except (TypeError, ValueError):
        return False


def _first_money_value(fields: Mapping[str, Any], *names: str) -> Any:
    """Return the first populated amount, supporting old and current analysis fields."""
    for name in names:
        value = fields.get(name)
        if value not in (None, ""):
            return value
    return None


def _analysis_is_corroborated(analysis: Mapping[str, Any], voucher: Mapping[str, Any]) -> bool:
    """Verify the compact receipt against its independent Qwen analysis."""
    extracted = analysis.get("extractedFields", {}) if isinstance(analysis.get("extractedFields"), Mapping) else {}
    if analysis.get("status") != "success" or analysis.get("analysisStatus") != "ready_for_review" or not analysis.get("templatePath"):
        return False
    if float(analysis.get("confidence", 0) or 0) < 0.9:
        return False
    entries = voucher.get("entries", []) if isinstance(voucher.get("entries"), list) else []
    debit = sum(float(item.get("amount", 0) or 0) for item in entries if isinstance(item, Mapping) and item.get("dc") == 1)
    credit = sum(float(item.get("amount", 0) or 0) for item in entries if isinstance(item, Mapping) and item.get("dc") == -1)
    transaction_amount = _first_money_value(extracted, "transactionAmount")
    if transaction_amount is not None:
        return (
            extracted.get("amountValidated") is True
            and bool(extracted.get("amountSource"))
            and _money_equal(debit, credit)
            and _money_equal(transaction_amount, debit)
        )
    extracted_total = _first_money_value(extracted, "totalAmountWithTax", "totalAmount")
    extracted_amount = _first_money_value(extracted, "amountWithoutTax", "amount")
    extracted_tax = _first_money_value(extracted, "taxAmount")
    if extracted_total is None or extracted_amount is None or extracted_tax is None:
        return False
    return (
        _money_equal(debit, credit)
        and _money_equal(extracted_total, debit)
        and _money_equal(float(extracted_amount) + float(extracted_tax), extracted_total)
    )


def build_preupload_report(receipt_directory: Path, output_path: Path, run_parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    analysis_path = receipt_directory.parent.parent / "ocr" / receipt_directory.name / "template_analysis.json"
    try:
        analysis_by_invoice = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        analysis_by_invoice = {}
    for receipt_path in sorted(receipt_directory.glob("receipt_*/receipt.json")):
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        voucher = payload.get("voucher", {})
        invoice_codes = voucher.get("invoiceCodes", [])
        invoice_code = str(invoice_codes[0]) if invoice_codes else ""
        analysis = analysis_by_invoice.get(invoice_code, {}) if isinstance(analysis_by_invoice, Mapping) else {}
        entries = voucher.get("entries", []) if isinstance(voucher.get("entries"), list) else []
        debit = sum(float(item.get("amount", 0) or 0) for item in entries if isinstance(item, Mapping) and item.get("dc") == 1)
        credit = sum(float(item.get("amount", 0) or 0) for item in entries if isinstance(item, Mapping) and item.get("dc") == -1)
        record = {
            "receipt": str(receipt_path.resolve()),
            "receiptId": payload.get("receiptId", ""),
            "invoiceCodes": invoice_codes,
            "date": voucher.get("date", ""),
            "groupId": voucher.get("groupId", ""),
            "groupName": voucher.get("groupName", ""),
            "summary": voucher.get("summary", ""),
            "userName": voucher.get("userName", ""),
            "debitTotal": round(debit, 2),
            "creditTotal": round(credit, 2),
            "attachments": voucher.get("attachments", 0),
            "analysisReference": str(analysis_path.resolve()),
            "templatePath": analysis.get("templatePath", "") if isinstance(analysis, Mapping) else "",
            "analysisConfidence": analysis.get("confidence", 0) if isinstance(analysis, Mapping) else 0,
            "entries": entries,
        }
        if not _analysis_is_corroborated(analysis, voucher):
            warnings.append({"receipt": record["receipt"], "type": "analysis_not_corroborated", "invoiceCode": invoice_code, "analysisReference": record["analysisReference"]})
        for field in ("date", "groupId", "summary", "userName"):
            if voucher.get(field) in (None, ""):
                warnings.append({"receipt": record["receipt"], "type": "voucher_field_missing", "field": field})
        if not invoice_codes:
            warnings.append({"receipt": record["receipt"], "type": "invoice_code_missing"})
        if len(entries) < 2:
            warnings.append({"receipt": record["receipt"], "type": "entries_incomplete", "count": len(entries)})
        for index, entry in enumerate(entries, 1):
            missing = [field for field in ("accountId", "accountNumber", "accountName", "amount", "dc") if entry.get(field) in (None, "")]
            if missing:
                warnings.append({"receipt": record["receipt"], "type": "entry_field_missing", "line": index, "fields": missing})
        receipts.append(record)
    report = {
        "reportVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "reviewStatus": "待人工审查",
        "reviewRequired": True,
        "runParameters": dict(run_parameters or {}),
        "summary": {"receiptCount": len(receipts), "warningCount": len(warnings), "readyCount": len(receipts) - len({item["receipt"] for item in warnings})},
        "warnings": warnings,
        "receipts": receipts,
        "reviewInstructions": ["核对日期、凭证字、摘要和制单人", "核对entries科目、借贷方向、金额和辅助核算", "核对附件与发票号", "分析依据保存在analysisReference指向的独立报告中", "审查通过后将reviewStatus改为已确认并填写reviewedBy/reviewedAt"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def require_review_confirmation(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    warning_count = int(report.get("summary", {}).get("warningCount", 0) or 0)
    if warning_count:
        raise PreuploadReviewError(f"正式上传被阻断：预审报告仍有 {warning_count} 条警告：{report_path}")
    return report
