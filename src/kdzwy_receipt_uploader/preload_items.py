"""Preload and, when explicitly enabled, create all source-column items once."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from .item_class import AUXILIARY_ITEM_CLASSES, resolve_item_class_id
from .xlsx_cache import load_read_only_workbook


@dataclass
class PreloadedItems:
    by_class: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
    source_columns: dict[str, list[str]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    def resolve(self, item_class_id: int, name: str) -> dict[str, Any] | None:
        return self.by_class.get(int(item_class_id), {}).get(str(name).strip())


def _collect_column(path: Path, sheet_name: str, column: str, start_column: int, end_column: int) -> list[str]:
    if not path.is_file():
        return []
    workbook = load_read_only_workbook(path)
    try:
        sheet = workbook[sheet_name] if sheet_name in workbook.sheetnames else None
        if sheet is None:
            return []
        index = ord(column.upper()) - ord("A") + 1
        result = []
        for row_number, values in enumerate(sheet.iter_rows(min_col=index, max_col=index, values_only=True), start=1):
            value = str(values[0]).strip() if values and values[0] not in (None, "") else ""
            if value and value not in {"客户名称", "购买方名称", "销售方名称", "供应商名称", "名称", "发票清单"}:
                result.append(value)
        return sorted(set(result))
    finally:
        workbook.close()


def collect_source_item_names(month_dir: Path, config: Any, extra_columns: list[Mapping[str, Any]] | None = None) -> dict[int, list[str]]:
    names: dict[int, set[str]] = {}
    income_path = month_dir / config.income_cost_filename
    usage_path = month_dir / config.usage_filename
    columns = [
        {"itemClassId": 1, "path": income_path, "sheet": "信息汇总表", "column": "H", "label": "收入成本表H列"},
        {"itemClassId": 5, "path": usage_path, "sheet": "发票", "column": "J", "label": "用途确认表J列"},
    ]
    columns.extend(extra_columns or [])
    for spec in columns:
        class_id = resolve_item_class_id(item_class_id=spec.get("itemClassId"))
        values = _collect_column(Path(spec["path"]), str(spec["sheet"]), str(spec["column"]), 1, 1)
        names.setdefault(class_id, set()).update(values)
    return {class_id: sorted(values) for class_id, values in names.items()}


def collect_map_item_names(
    sales_map: Mapping[str, Mapping[str, Any]] | None,
    purchase_map: Mapping[str, Mapping[str, Any]] | None,
) -> dict[int, list[str]]:
    """Collect authoritative customer and supplier names from business maps."""
    result: dict[int, set[str]] = {1: set(), 5: set()}
    for values in (sales_map or {}).values():
        name = str(values.get("customName") or values.get("customerName") or "").strip()
        if name:
            result[1].add(name)
    for values in (purchase_map or {}).values():
        name = str(values.get("supplierName") or values.get("sellerName") or "").strip()
        if name:
            result[5].add(name)
    return {class_id: sorted(names) for class_id, names in result.items()}


def apply_preloaded_items(values_by_invoice: dict[str, dict[str, Any]], preloaded: PreloadedItems, item_class_id: int, name_field: str, id_field: str, number_field: str) -> None:
    for values in values_by_invoice.values():
        name = str(values.get(name_field, "")).strip()
        if not name:
            continue
        item = preloaded.resolve(item_class_id, name)
        if item:
            values[id_field] = str(item.get("id", ""))
            values[number_field] = str(item.get("number", ""))
            values["auxiliaryItem"] = {"itemClass": values.get("itemClass", ""), "itemClassId": item_class_id, "id": str(item.get("id", "")), "number": str(item.get("number", "")), "name": str(item.get("name", name))}


@lru_cache(maxsize=64)
def load_bank_counterparty_policy(template_root: Path) -> dict[str, Any]:
    """Load one company's cross-month bank decision policy."""
    path = Path(template_root).resolve() / "rules" / "bank_counterparties.json"
    if not path.is_file():
        return {"version": 1, "counterparties": {}, "separateHandling": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取公司银行规则 {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"公司银行规则必须是 version 1：{path}")
    if not isinstance(payload.get("counterparties", {}), dict):
        raise ValueError(f"counterparties 必须是对象：{path}")
    if not isinstance(payload.get("separateHandling", []), list):
        raise ValueError(f"separateHandling 必须是数组：{path}")
    return payload


def resolve_bank_counterparty_policy(
    policy: Mapping[str, Any], name: str, direction: str
) -> dict[str, Any]:
    """Resolve an exact company rule without learning one-time transaction IDs."""
    normalized_name = str(name).strip().casefold()
    normalized_direction = str(direction).strip().lower()
    for canonical_name, raw_profile in policy.get("counterparties", {}).items():
        if not isinstance(raw_profile, Mapping):
            continue
        names = {
            str(canonical_name).strip().casefold(),
            *{
                str(alias).strip().casefold()
                for alias in raw_profile.get("aliases", [])
                if str(alias).strip()
            },
        }
        if normalized_name not in names:
            continue
        roles = sorted({
            str(role).strip().lower()
            for role in raw_profile.get("roles", [])
            if str(role).strip().lower() in {"customer", "supplier"}
        })
        matching_rules = [
            dict(rule)
            for rule in raw_profile.get("rules", [])
            if isinstance(rule, Mapping)
            and str(rule.get("direction") or "").strip().lower()
            in {"", normalized_direction}
        ]
        if len(matching_rules) > 1:
            raise ValueError(
                f"公司银行规则同一方向存在多个决定：{canonical_name}/{normalized_direction}"
            )
        rule = matching_rules[0] if matching_rules else {}
        return {
            "canonicalName": str(canonical_name).strip(),
            "roles": roles,
            "preferredTemplatePath": str(rule.get("template") or "").strip().replace("\\", "/"),
            "businessType": str(rule.get("businessType") or "").strip(),
            "confirmedByUser": bool(raw_profile.get("confirmedByUser")),
            "accounts": list(rule.get("accounts") or []),
        }
    return {}


def load_bank_counterparty_role_overrides(template_root: Path) -> dict[str, int]:
    """Return confirmed single-role names used by bank auxiliary preloading."""
    role_ids = {"customer": 1, "supplier": 5}
    result: dict[str, int] = {}
    policy = load_bank_counterparty_policy(Path(template_root))
    for canonical_name, raw_profile in policy.get("counterparties", {}).items():
        if not isinstance(raw_profile, Mapping) or not bool(raw_profile.get("confirmedByUser")):
            continue
        roles = {
            str(value).strip().lower()
            for value in raw_profile.get("roles", [])
            if str(value).strip().lower() in role_ids
        }
        if len(roles) != 1:
            continue
        class_id = role_ids[next(iter(roles))]
        for name in [canonical_name, *(raw_profile.get("aliases", []) or [])]:
            normalized_name = str(name).strip()
            if normalized_name:
                result[normalized_name] = class_id
    return result


def load_bank_separate_handling(template_root: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Return company-level names and PDF keywords excluded from ordinary bank flow."""
    names: list[str] = []
    pdf_keywords: dict[str, list[str]] = {}
    policy = load_bank_counterparty_policy(Path(template_root))
    for record in policy.get("separateHandling", []):
        if not isinstance(record, Mapping):
            continue
        keywords = [
            str(value).strip()
            for value in record.get("pdfKeywords", [])
            if str(value).strip()
        ]
        for raw_name in record.get("counterpartyNames", []):
            name = str(raw_name).strip()
            if not name:
                continue
            names.append(name)
            if keywords:
                pdf_keywords[name] = keywords
    return list(dict.fromkeys(names)), pdf_keywords


def preload_bank_counterparties(
    api: Any,
    records: Mapping[str, Mapping[str, Any]],
    *,
    create_missing: bool = True,
    role_evidence: Mapping[int, list[str]] | None = None,
) -> PreloadedItems:
    """Resolve bank counterparties from evidence or validated debit/credit direction."""

    def normalize_name(value: Any) -> str:
        translated = str(value or "").strip().translate(
            str.maketrans({"（": "(", "）": ")", "【": "[", "】": "]", "　": " "})
        )
        return "".join(translated.split()).casefold()

    def has_positive_amount(*values: Any) -> bool:
        for value in values:
            if value in (None, ""):
                continue
            try:
                if float(str(value).replace(",", "").strip()) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def looks_like_organization(value: str) -> bool:
        normalized = normalize_name(value)
        if not normalized:
            return False
        non_entity_markers = (
            "网上电子汇划收入",
            "电子汇划收入",
            "公积金",
            "社保",
            "税款",
            "待报解预算收入",
            "手续费",
            "工资",
            "结息",
            "内部转账",
        )
        if any(normalize_name(marker) in normalized for marker in non_entity_markers):
            return False
        organization_markers = (
            "公司",
            "中心",
            "商行",
            "经营部",
            "事务所",
            "合伙企业",
            "银行",
            "工厂",
            "合作社",
            "委员会",
            "研究院",
            "学校",
            "医院",
        )
        return any(normalize_name(marker) in normalized for marker in organization_markers)

    normalized_evidence = {
        int(class_id): {
            normalize_name(name): str(name).strip()
            for name in names
            if str(name).strip()
        }
        for class_id, names in (role_evidence or {}).items()
        if int(class_id) in {1, 5}
    }
    all_catalogs = api.get_all_items_v1(class_ids=(1, 5), page_size=500)
    catalogs: dict[int, dict[str, dict[str, Any]]] = {}
    normalized_catalogs: dict[int, dict[str, dict[str, Any]]] = {}
    for class_id, label in ((1, "客户"), (5, "供应商")):
        data = all_catalogs.get(label, {})
        rows = list(data.get("items", [])) if isinstance(data, dict) else []
        catalogs[class_id] = {
            str(row["name"]).strip(): dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("name", "")).strip()
        }
        normalized_catalogs[class_id] = {
            normalize_name(row["name"]): dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("name", "")).strip()
        }

    wanted: dict[int, set[str]] = {1: set(), 5: set()}
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for record_key, record in records.items():
        name = str(record.get("counterpartyName") or "").strip()
        config_company = str(record.get("configCompany") or "").strip()
        if not name or (config_company and name == config_company):
            continue
        normalized = normalize_name(name)
        evidence_classes = [
            class_id
            for class_id in (1, 5)
            if normalized in normalized_evidence.get(class_id, {})
        ]
        existing_classes = [
            class_id
            for class_id in (1, 5)
            if normalized in normalized_catalogs[class_id]
        ]
        debit_has_amount = has_positive_amount(
            record.get("bankDebitAmount"), record.get("bankDebitRaw")
        )
        credit_has_amount = has_positive_amount(
            record.get("bankCreditAmount"), record.get("bankCreditRaw")
        )
        direction_class = (
            5
            if debit_has_amount and not credit_has_amount
            else 1
            if credit_has_amount and not debit_has_amount
            else None
        )
        if direction_class is not None and looks_like_organization(name):
            resolved_classes = [direction_class]
            resolution_source = "validated_bank_amount_direction"
        elif evidence_classes:
            resolved_classes = evidence_classes
            resolution_source = "source_business_evidence"
        elif existing_classes:
            resolved_classes = existing_classes
            resolution_source = "live_target_catalog"
        else:
            unresolved.append(
                {
                    "recordKey": str(record_key),
                    "name": name,
                    "flowDirection": str(record.get("flowDirection") or ""),
                    "bankDebitHasAmount": debit_has_amount,
                    "bankCreditHasAmount": credit_has_amount,
                    "reason": "no_reliable_organization_role_evidence",
                }
            )
            continue

        resolved_names: list[str] = []
        for class_id in resolved_classes:
            existing = normalized_catalogs[class_id].get(normalized)
            evidence_name = normalized_evidence.get(class_id, {}).get(normalized)
            canonical_name = str(
                (existing or {}).get("name") or evidence_name or name
            ).strip()
            wanted[class_id].add(canonical_name)
            resolved_names.append(canonical_name)
        resolved.append(
            {
                "recordKey": str(record_key),
                "name": name,
                "resolvedNames": resolved_names,
                "itemClassIds": resolved_classes,
                "source": resolution_source,
            }
        )

    result = PreloadedItems(
        by_class=catalogs,
        source_columns={
            str(class_id): sorted(names) for class_id, names in wanted.items()
        },
        resolved=resolved,
        unresolved=unresolved,
    )
    for class_id, names in sorted(wanted.items()):
        bucket = result.by_class[class_id]
        if not create_missing:
            continue
        for name in sorted(names):
            if name in bucket:
                continue
            number = api.get_next_item_number_v1(class_id)
            created = api.create_item_v1(class_id, number, name)
            bucket[name] = created
            result.created.append(
                {
                    "itemClassId": class_id,
                    "name": name,
                    "number": created.get("number"),
                    "id": created.get("id"),
                }
            )
    return result


def preload_items(api: Any, month_dir: Path, config: Any, extra_columns: list[Mapping[str, Any]] | None = None, create_missing: bool = True, wanted_items: Mapping[int, list[str]] | None = None) -> PreloadedItems:
    wanted = (
        {int(class_id): sorted({str(name).strip() for name in names if str(name).strip()}) for class_id, names in wanted_items.items()}
        if wanted_items is not None
        else collect_source_item_names(month_dir, config, extra_columns)
    )
    result = PreloadedItems(source_columns={str(class_id): names for class_id, names in wanted.items()})
    for class_id in sorted(set(wanted) | set(AUXILIARY_ITEM_CLASSES.values())):
        data = api.get_items_v1(class_id, page_size=500)
        rows = list(data.get("rows", [])) if isinstance(data, dict) else []
        bucket = result.by_class.setdefault(class_id, {})
        for row in rows:
            if isinstance(row, dict) and str(row.get("name", "")).strip():
                bucket[str(row["name"]).strip()] = dict(row)
        if not create_missing:
            continue
        for name in wanted.get(class_id, []):
            if name in bucket:
                continue
            number = api.get_next_item_number_v1(class_id)
            created = api.create_item_v1(class_id, number, name)
            bucket[name] = created
            result.created.append({"itemClassId": class_id, "name": name, "number": created.get("number"), "id": created.get("id")})
    return result
