"""Build the final DeepSeek filling sample with runtime account-book catalogs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SAMPLE = Path(__file__).resolve().parents[2] / "templates" / "final_template_sample.json"


def load_final_template_sample(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_SAMPLE
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != "1.0":
        raise ValueError("最终模板样例必须是 schemaVersion=1.0 的对象")
    return payload


def _account_rows(account_catalog: Any) -> list[dict[str, Any]]:
    rows = account_catalog if isinstance(account_catalog, list) else []
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        account_id = row.get("id") or row.get("accountId")
        number = row.get("number") or row.get("accountNumber")
        name = row.get("fullName") or row.get("accountName") or row.get("name")
        if account_id not in (None, "") and number not in (None, "") and name not in (None, ""):
            result.append({"id": str(account_id), "number": str(number), "fullName": str(name), "isItem": bool(row.get("isItem")), "isQtyaux": bool(row.get("isQtyaux")), "isCur": bool(row.get("isCur"))})
    return result


def _item_classes(item_catalog: Any) -> list[dict[str, Any]]:
    result = []
    source = item_catalog if isinstance(item_catalog, Mapping) else {}
    for label, report in source.items():
        if not isinstance(report, Mapping):
            continue
        rows = []
        for row in report.get("items", []):
            if isinstance(row, Mapping) and row.get("id") not in (None, ""):
                rows.append({"id": str(row.get("id")), "number": str(row.get("number", "")), "name": str(row.get("name", ""))})
        result.append({"itemClass": str(label), "itemClassId": int(report.get("itemClassId", 0) or 0), "items": rows})
    return result


def build_final_template_context(sample: Mapping[str, Any], account_catalog: Any = None, item_catalog: Any = None, source: str = "", map_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    context = copy.deepcopy(dict(sample))
    context["dynamicAccountCatalog"] = {"source": "runtime account-book response", "accounts": _account_rows(account_catalog)}
    context["dynamicItemClassCatalog"] = {"source": "runtime ItemClass response", "classes": _item_classes(item_catalog)}
    context["runtimeContext"] = {"source": source, "mapValues": dict(map_values or {})}
    return context


def validate_filled_entries(decision: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    accounts = {str(row.get("number")): row for row in context.get("dynamicAccountCatalog", {}).get("accounts", []) if isinstance(row, Mapping)}
    classes = {str(row.get("itemClassId")): row for row in context.get("dynamicItemClassCatalog", {}).get("classes", []) if isinstance(row, Mapping)}
    entries = decision.get("filledEntries")
    if not isinstance(entries, list) or not entries:
        return ["filledEntries 为空或不是数组"]
    debit = 0.0
    credit = 0.0
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, Mapping):
            errors.append(f"filledEntries[{index}] 不是对象")
            continue
        number = str(entry.get("accountNumber", ""))
        if number not in accounts:
            errors.append(f"filledEntries[{index}] 科目编码不在动态科目目录：{number}")
        if entry.get("dc") == 1:
            debit += float(entry.get("amount", 0) or 0)
        elif entry.get("dc") == -1:
            credit += float(entry.get("amount", 0) or 0)
        else:
            errors.append(f"filledEntries[{index}] dc 必须为1或-1")
        auxiliary = entry.get("auxiliary")
        if isinstance(auxiliary, Mapping) and auxiliary.get("id") not in (None, ""):
            class_id = str(auxiliary.get("itemClassId", ""))
            catalog = classes.get(class_id)
            if not catalog:
                errors.append(f"filledEntries[{index}] itemClassId不在动态目录：{class_id}")
            elif not any(str(row.get("id")) == str(auxiliary.get("id")) for row in catalog.get("items", [])):
                errors.append(f"filledEntries[{index}] 辅助对象ID不在动态ItemClass明细：{auxiliary.get('id')}")
    if round(debit, 2) != round(credit, 2):
        errors.append(f"filledEntries 借贷不平衡：{debit} != {credit}")
    return errors
