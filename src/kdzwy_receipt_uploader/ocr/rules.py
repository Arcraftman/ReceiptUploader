"""Deterministic template candidate and counterparty rules."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Mapping

from .models import OcrArtifact


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
                and _normalize_match_text(value.get("name")) == _normalize_match_text(counterparty_name)
            ):
                role = "customer" if class_id == 1 else "supplier"
                matches[role] = dict(value)
            for child in value.values():
                visit(child, class_id)
        elif isinstance(value, list):
            for child in value:
                visit(child, inherited_class_id)

    visit(catalog)
    # Bank statement direction is authoritative for the role of this
    # transaction. The live catalog still supplies the auxiliary object ID,
    # but it must not reverse or erase a valid direction-derived role. This
    # also allows one organization to be a customer on an inflow and a
    # supplier on an outflow.
    flow_direction = str(values.get("flowDirection") or "").strip().lower()
    direction_role = {
        "inflow": "customer",
        "outflow": "supplier",
    }.get(flow_direction, "")
    roles = [direction_role] if direction_role else sorted(matches)
    values["counterpartyRoles"] = roles
    values["counterpartyRoleSource"] = (
        "statement_direction"
        if direction_role
        else "dynamic_item_catalog"
        if matches
        else "unresolved"
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
        if item.get("businessType") == "费用报销":
            from ..bank_rules import employee_payment_kind
            if employee_payment_kind(values) != "reimbursement":
                reasons[str(item.get("path"))] = "费用报销仅允许人名流水明确备注"
                continue
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
