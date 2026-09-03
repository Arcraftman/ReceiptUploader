"""Bank LLM inputs and final receipt generation after approved existing analysis."""
from __future__ import annotations

import json
from pathlib import Path
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from .receipts_ocr import OcrArtifact


class BankFinalReceiptError(RuntimeError):
    pass


MAX_EXPLANATION_LENGTH = 255


def _bounded_explanation(value: Any, required_suffix: str = "") -> str:
    text = str(value or "").strip()
    if len(text) <= MAX_EXPLANATION_LENGTH:
        return text
    suffix = required_suffix if required_suffix and text.endswith(required_suffix) else ""
    if not suffix:
        return text[:MAX_EXPLANATION_LENGTH].rstrip()
    body = text[: -len(suffix)].rstrip()
    body_limit = MAX_EXPLANATION_LENGTH - len(suffix)
    return body[:body_limit].rstrip() + suffix


def record_key(bank_key: str, index: str) -> str:
    return f"{bank_key}__{index}"


def load_bank_records(map_path: Path, report_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    try:
        bank_map = json.loads(map_path.read_text(encoding="utf-8-sig"))
        bank_report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BankFinalReceiptError(f"银行 map/report 无法读取：{exc}") from exc
    matched: dict[str, dict[str, Any]] = {}
    unmatched: dict[str, dict[str, Any]] = {}
    for bank_key, bank in (bank_map.get("banks") or {}).items():
        for index, entry in (bank.get("entries") or {}).items():
            key = record_key(str(bank_key), str(index))
            matched[key] = {"key": key, "bankKey": str(bank_key), "index": str(index), **dict(entry)}
    for bank_key, bank in (bank_report.get("banks") or {}).items():
        for entry in bank.get("unmatchedStatements", []) or []:
            index = str(entry.get("index") or "")
            if not index:
                continue
            key = record_key(str(bank_key), index)
            unmatched[key] = {"key": key, "bankKey": str(bank_key), "index": index, **dict(entry)}
    return matched, unmatched


def source_values(record: Mapping[str, Any]) -> dict[str, Any]:
    amount = record.get("transactionAmount")
    amount_source = str(record.get("amountSource") or "").strip()
    if amount in (None, "") or not amount_source or record.get("amountValidated") is not True:
        raise BankFinalReceiptError(
            "银行记录缺少已验证 transactionAmount："
            f"{record.get('bankKey')} / {record.get('index')}"
        )
    try:
        normalized_amount = Decimal(str(amount)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BankFinalReceiptError(
            "银行 transactionAmount 不是有效金额："
            f"{record.get('bankKey')} / {record.get('index')} / {amount}"
        ) from exc
    if normalized_amount <= 0:
        raise BankFinalReceiptError(
            "银行 transactionAmount 必须大于零："
            f"{record.get('bankKey')} / {record.get('index')} / {amount}"
        )
    amount = format(normalized_amount, "f")
    company_housing_fund = None
    employee_housing_fund = None
    try:
        total = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        company_half = (total / Decimal("2")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        employee_half = total - company_half
        company_housing_fund = format(company_half, "f")
        employee_housing_fund = format(employee_half, "f")
    except (InvalidOperation, TypeError, ValueError):
        pass
    values = {
        "amount": amount,
        "totalAmount": amount,
        "transactionAmount": amount,
        "statementAmount": amount,
        "amountSource": amount_source,
        "amountValidated": True,
        "configCompany": record.get("configCompany"),
        "bankKey": record.get("bankKey"),
        "bankAccountNumber": record.get("bankAccountNumber"),
        "statementIndex": record.get("index"),
        "flowDirection": record.get("flowDirection"),
        "bankDebitAmount": record.get("bankDebitAmount"),
        "bankCreditAmount": record.get("bankCreditAmount"),
        "ourDebitAmount": record.get("ourDebitAmount"),
        "ourCreditAmount": record.get("ourCreditAmount"),
        "invoiceNumbers": list(record.get("invoiceNumbers") or []),
        # 微誉历史账簿中的公积金银行付款固定按公司/个人各半结清。
        # These fields are consumed only by the housing-fund template.
        "companyHousingFund": company_housing_fund,
        "employeeHousingFund": employee_housing_fund,
        "counterpartyName": record.get("counterpartyName"),
        "counterpartyType": record.get("counterpartyType"),
        "itemClass": record.get("itemClass"),
        "supplierName": record.get("supplierName"),
        "customerName": record.get("customerName"),
        "customName": record.get("customName"),
    }
    receipt = record.get("receipt")
    if not str(values.get("counterpartyName") or "").strip() and isinstance(receipt, Mapping):
        text_path = Path(str(receipt.get("ocrText") or ""))
        try:
            text = text_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for pattern in (
            r"(?:对方户名|对方名称|收款人户名|付款人户名|收款人名称|付款人名称)\s*[:：]?\s*([^\r\n]+)",
            r"(?:收款人|付款人)\s*[:：]?\s*([^\r\n]+)",
        ):
            match = re.search(pattern, text)
            if match:
                values["counterpartyName"] = match.group(1).strip()
                break
    return values


def build_bank_ocr_artifacts(records: Mapping[str, Mapping[str, Any]]) -> list[OcrArtifact]:
    artifacts: list[OcrArtifact] = []
    for key, record in sorted(records.items()):
        receipt = record.get("receipt")
        if not isinstance(receipt, Mapping):
            raise BankFinalReceiptError(f"已匹配银行记录缺少 receipt：{key}")
        pdf_path = Path(str(receipt.get("pdf") or ""))
        text_path = Path(str(receipt.get("ocrText") or ""))
        metadata_path = Path(str(receipt.get("ocrMetadata") or ""))
        if not pdf_path.is_file() or not text_path.is_file() or not metadata_path.is_file():
            raise BankFinalReceiptError(f"银行 OCR 产物不完整：{key}")
        text = text_path.read_text(encoding="utf-8")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        artifacts.append(OcrArtifact(
            invoice_code=key,
            source_pdf=pdf_path.resolve(),
            source_folder="bank",
            source_side="bank",
            output_dir=metadata_path.parent.resolve(),
            text_path=text_path.resolve(),
            metadata_path=metadata_path.resolve(),
            text=text,
            engine=str(metadata.get("engine") or "bank-ocr"),
            status=str(metadata.get("status") or "success"),
        ))
    return artifacts


def _date_from_analysis(analysis: Mapping[str, Any]) -> str:
    deterministic_date = str(analysis.get("bankTransactionDate") or "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", deterministic_date):
        return deterministic_date
    fields = analysis.get("extractedFields")
    if not isinstance(fields, Mapping):
        fields = analysis.get("ocrFields") if isinstance(analysis.get("ocrFields"), Mapping) else {}
    for key in ("transactionDate", "date", "issueDate"):
        value = str(fields.get(key) or "").strip()
        match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", value)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def _entries_from_analysis(analysis: Mapping[str, Any], amount: Any) -> list[dict[str, Any]]:
    raw_entries = analysis.get("filledEntries")
    entries: list[dict[str, Any]] = []
    if isinstance(raw_entries, list):
        for line_no, raw in enumerate(raw_entries, 1):
            if not isinstance(raw, Mapping):
                continue
            entry = dict(raw)
            entry["lineNo"] = line_no
            entry.setdefault("amount", amount)
            entry.setdefault("amountFor", entry.get("amount"))
            entry.setdefault("cur", "RMB")
            entry.setdefault("rate", "1")
            entry.setdefault("explanation", str(analysis.get("explanation") or ""))
            transaction_date = str(analysis.get("bankTransactionDate") or "").strip()
            required_suffix = (
                f" {transaction_date}"
                if transaction_date and "银行存款" in str(entry.get("accountName") or "")
                else ""
            )
            entry["explanation"] = _bounded_explanation(
                entry.get("explanation"), required_suffix
            )
            auxiliary = entry.pop("auxiliary", None)
            if isinstance(auxiliary, Mapping) and auxiliary.get("id") not in (None, ""):
                prefix = {1: "customer", 5: "supplier", 3: "emp", 4: "project", 2: "inventory", 6: "dept"}.get(int(auxiliary.get("itemClassId") or 0))
                if prefix:
                    entry[f"{prefix}Id"] = str(auxiliary["id"])
                    entry[f"{prefix}Number"] = str(auxiliary.get("number") or "")
                    entry[f"{prefix}Name"] = str(auxiliary.get("name") or "")
            entries.append(entry)
    if entries:
        return entries
    return [
        {"lineNo": 1, "accountId": "", "accountNumber": "", "accountName": "", "dc": 1, "amount": amount, "amountFor": amount, "explanation": "", "cur": "RMB", "rate": "1"},
        {"lineNo": 2, "accountId": "", "accountNumber": "", "accountName": "", "dc": -1, "amount": amount, "amountFor": amount, "explanation": "", "cur": "RMB", "rate": "1"},
    ]


def validate_bank_analysis_rules(
    record: Mapping[str, Any], analysis: Mapping[str, Any]
) -> None:
    normalized = source_values(record)
    transaction_amount = Decimal(str(normalized["transactionAmount"]))
    extracted = analysis.get("extractedFields")
    if not isinstance(extracted, Mapping):
        raise BankFinalReceiptError(
            f"银行分析缺少 extractedFields：{record.get('bankKey')} / {record.get('index')}"
        )
    if extracted.get("amountValidated") is not True:
        raise BankFinalReceiptError(
            f"银行分析金额尚未通过校验：{record.get('bankKey')} / {record.get('index')}"
        )
    if str(extracted.get("amountSource") or "") != str(normalized["amountSource"]):
        raise BankFinalReceiptError(
            f"银行分析金额来源不一致：{record.get('bankKey')} / {record.get('index')}"
        )
    try:
        analyzed_amount = Decimal(str(extracted.get("transactionAmount"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BankFinalReceiptError(
            f"银行分析缺少 transactionAmount：{record.get('bankKey')} / {record.get('index')}"
        ) from exc
    if analyzed_amount != transaction_amount:
        raise BankFinalReceiptError(
            f"银行分析金额与流水不一致：{record.get('bankKey')} / {record.get('index')}，"
            f"流水={transaction_amount}，分析={analyzed_amount}"
        )
    required_number = str(record.get("bankAccountNumber") or "").strip()
    if not re.fullmatch(r"[0-9]+", required_number):
        raise BankFinalReceiptError(
            f"银行记录缺少固定银行存款科目号：{record.get('bankKey')} / {record.get('index')}"
        )
    entries = analysis.get("filledEntries")
    if not isinstance(entries, list):
        raise BankFinalReceiptError(
            f"银行分析缺少 filledEntries：{record.get('bankKey')} / {record.get('index')}"
        )
    bank_entries = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and "银行存款" in str(entry.get("accountName") or "")
    ]
    if len(bank_entries) != 1:
        raise BankFinalReceiptError(
            f"银行分析必须恰好包含一条银行存款分录：{record.get('bankKey')} / {record.get('index')}，实际={len(bank_entries)}"
        )
    debit_total = sum(
        Decimal(str(entry.get("amount") or 0))
        for entry in entries
        if isinstance(entry, Mapping) and int(entry.get("dc") or 0) == 1
    )
    credit_total = sum(
        Decimal(str(entry.get("amount") or 0))
        for entry in entries
        if isinstance(entry, Mapping) and int(entry.get("dc") or 0) == -1
    )
    if debit_total != transaction_amount or credit_total != transaction_amount:
        raise BankFinalReceiptError(
            f"银行分录金额必须与 transactionAmount 完全一致："
            f"{record.get('bankKey')} / {record.get('index')}，"
            f"transactionAmount={transaction_amount}，借={debit_total}，贷={credit_total}"
        )
    actual_number = str(bank_entries[0].get("accountNumber") or "").strip()
    if actual_number != required_number:
        raise BankFinalReceiptError(
            f"银行存款科目号与项目配置不一致：{record.get('bankKey')} / {record.get('index')}，"
            f"配置={required_number}，分析={actual_number}"
        )
    transaction_date = str(analysis.get("bankTransactionDate") or "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", transaction_date) is None:
        raise BankFinalReceiptError(
            f"银行分析缺少 OCR 交易日期：{record.get('bankKey')} / {record.get('index')}"
        )
    bank_explanation = str(bank_entries[0].get("explanation") or "")
    if not bank_explanation.endswith(f" {transaction_date}"):
        raise BankFinalReceiptError(
            f"银行存款分录摘要必须以空格加 OCR 交易日期结尾："
            f"{record.get('bankKey')} / {record.get('index')}"
        )
    invoice_numbers = [
        str(value)
        for value in record.get("invoiceNumbers") or []
        if re.fullmatch(r"\d{8,20}", str(value))
    ]
    if str(record.get("flowDirection") or "") == "inflow" and invoice_numbers:
        expected_body = " ".join(invoice_numbers)
        actual_body = str(analysis.get("explanation_body") or "").strip()
        if actual_body != expected_body:
            raise BankFinalReceiptError(
                f"银行贷方数字文本必须直接成为 explanation_body："
                f"{record.get('bankKey')} / {record.get('index')}，"
                f"expected={expected_body}，actual={actual_body}"
            )


def generate_bank_final_receipts(
    matched: Mapping[str, Mapping[str, Any]],
    analysis: Mapping[str, Mapping[str, Any]],
    output_root: Path,
    company: str,
    month: str,
    voucher_defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate final-shape draft receipts only during prepare+existing."""
    output_root.mkdir(parents=True, exist_ok=True)
    generated = 0
    reused = 0
    blocked = 0
    artifacts: list[dict[str, Any]] = []
    blocked_artifacts: list[dict[str, Any]] = []
    for key, record in sorted(matched.items()):
        current_analysis = analysis.get(key)
        is_ready = isinstance(current_analysis, Mapping) and current_analysis.get("analysisStatus") == "ready_for_review"
        if not is_ready:
            blocked += 1
            blocked_artifacts.append({
                "key": key,
                "bankKey": record.get("bankKey"),
                "statementIndex": record.get("index"),
                "analysisStatus": (
                    current_analysis.get("analysisStatus", "blocked")
                    if isinstance(current_analysis, Mapping)
                    else "missing"
                ),
                "reason": (
                    str(current_analysis.get("reason") or "")
                    if isinstance(current_analysis, Mapping)
                    else "missing analysis"
                ),
            })
            continue
        validate_bank_analysis_rules(record, current_analysis)
        amount = source_values(record)["transactionAmount"]
        receipt_id = f"bank-{company}-{month}-{key}"
        path = output_root / f"receipt_{key}" / "receipt.json"
        if path.exists():
            reused += 1
        else:
            receipt = record.get("receipt") if isinstance(record.get("receipt"), Mapping) else {}
            pdf_path = str(receipt.get("pdf") or "")
            entries = _entries_from_analysis(current_analysis or {}, amount)
            transaction_date = _date_from_analysis(current_analysis or {})
            payload = {
                "schemaVersion": "1.0",
                "draft": True,
                "receiptId": receipt_id,
                "voucher": {
                    "date": transaction_date,
                    "groupId": str(voucher_defaults.get("group_id") or ""),
                    "groupName": str(voucher_defaults.get("group_name") or "记"),
                    "summary": _bounded_explanation(
                        (current_analysis or {}).get("explanation"),
                        f" {transaction_date}" if transaction_date else "",
                    ),
                    "attachments": 1 if pdf_path else 0,
                    "attachmentFiles": ([{"path": pdf_path}] if pdf_path else []),
                    "invoiceCodes": [],
                    "userName": str(voucher_defaults.get("user_name") or ""),
                    "entries": entries,
                },
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            generated += 1
        artifacts.append({
            "key": key,
            "bankKey": record.get("bankKey"),
            "statementIndex": record.get("index"),
            "receiptId": receipt_id,
            "receipt": str(path.resolve()),
            "analysisStatus": (current_analysis or {}).get("analysisStatus", "blocked"),
            "draft": True,
        })
    report = {
        "version": 1,
        "stage": "prepare_existing",
        "summary": {
            "matchedRecordCount": len(matched),
            "receiptCount": len(artifacts),
            "generatedCount": generated,
            "reusedCount": reused,
            "blockedAnalysisCount": blocked,
        },
        "artifacts": artifacts,
        "blocked": blocked_artifacts,
    }
    (output_root / "bank_receipt_generation.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
