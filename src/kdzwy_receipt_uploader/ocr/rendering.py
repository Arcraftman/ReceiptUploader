"""Template rendering and financial result validation."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from ..source_profile import source_from_folder_name
from ..voucher_templates import TemplateContext, VoucherTemplateEngine
from .models import OcrArtifact, OcrPipelineError
from .rules import _normalize_match_text


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


def bank_amount_snapshot(values: Mapping[str, Any]) -> dict[str, str | None]:
    """Bind cached analysis to the actual, independently supplied amount inputs."""
    result: dict[str, str | None] = {}
    for field in ("transactionAmount", "companyHousingFund", "employeeHousingFund", "companySocialSecurity", "employeeSocialSecurity"):
        value = values.get(field)
        result[field] = None if value in (None, "") else format(Decimal(str(value)).quantize(Decimal("0.01")), "f")
    return result


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
    from ..bank_rules import internal_transfer_account
    transfer_account = internal_transfer_account(map_values) if source_key == "bank" and decision.get("selectionMode") == "internal_transfer_remark" else ""
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
    from ..bank_rules import is_personal_reimbursement, resolve_employee_payable, flatten_account_rows
    account_container = context_values.get("dynamicAccountCatalog")
    account_rows = flatten_account_rows(account_container.get("accounts", []) if isinstance(account_container, Mapping) else [])
    person_evidence = None
    if source_key == "bank" and is_personal_reimbursement(map_values):
        try:
            person_evidence = resolve_employee_payable(map_values, account_rows)
        except ValueError as exc:
            raise OcrPipelineError(str(exc)) from exc
        decision["employeeAccountEvidence"] = person_evidence
        map_values = dict(map_values, employeePayableAccountNumber=person_evidence["accountNumber"])
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
    if bank_invoice_numbers and not transfer_account:
        explanation_body = " ".join(bank_invoice_numbers)
    from ..bank_rules import personal_reimbursement_summary
    if source_key == "bank" and is_personal_reimbursement(map_values):
        explanation_header = ""
        explanation_body = personal_reimbursement_summary(map_values)
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
    account_rows = flatten_account_rows(account_container.get("accounts", []) if isinstance(account_container, Mapping) else [])
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
        is_transfer_counter = bool(transfer_account) and account_number_from == "source.transferAccountNumber"
        if (
            source_key == "bank"
            and "银行存款" in str(selector.get("name") or "")
            and not is_bank_deposit_entry
            and not is_transfer_counter
        ):
            raise OcrPipelineError(
                "银行存款分录必须使用动态科目来源 "
                f"numberFrom=source.bankAccountNumber：{relative_path} entries[{index}]"
            )
        if is_bank_deposit_entry:
            bank_deposit_entry_count += 1
            account_number = bank_account_number
        elif is_transfer_counter:
            account_number = transfer_account
        elif account_number_from == "source.employeePayableAccountNumber":
            if not person_evidence:
                raise OcrPipelineError("人员科目模板缺少实时科目解析")
            account_number = person_evidence["accountNumber"]
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
                bank_entry_explanation if is_bank_deposit_entry or is_transfer_counter else explanation
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
        decision["bankSourceAmounts"] = bank_amount_snapshot(map_values)
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
                if current_class_id == item_class_id and item_id not in (None, "", 0, "0") and _normalize_match_text(value.get("name")) == _normalize_match_text(name):
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
            if not isinstance(mapped, Mapping) or _normalize_match_text(mapped.get("name")) != _normalize_match_text(counterparty_name) or mapped.get("id") in (None, "", 0, "0"):
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
        if not required_total.is_finite():
            raise ValueError("non-finite amount")
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
            if not amount.is_finite():
                raise ValueError("non-finite allocation")
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
                    and _normalize_match_text(value.get("name")) == _normalize_match_text(name)
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
        "templatePath", "templateId", "decisionCode", "decisionName", "selectionMode", "confidence", "reason", "status", "analysisStatus", "remark",
        "llmAttempted", "llmProvider", "llmModel", "llmRequestId",
        "explanation_header", "explanation_body", "explanation", "sourceFolder", "configCompany",
        "partyRule", "employeePaymentKind", "employeePaymentEvidence", "employeeAccountEvidence", "bankSourceAmounts", "bankAccountNumber", "bankTransactionDate", "invoiceNumbers", "sourcePdf", "validation",
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
