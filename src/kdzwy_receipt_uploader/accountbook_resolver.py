"""Resolve voucher and subject IDs from the currently authorized account book."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api import KdzwyApi
from .models import ApiError


@dataclass(frozen=True)
class ResolvedEntry:
    line_no: int
    dc: int
    account_id: str
    account_number: str
    account_name: str


@dataclass(frozen=True)
class AccountBookDefaults:
    group_id: str
    group_name: str
    entries: tuple[ResolvedEntry, ...]


def _data_list(endpoint: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = KdzwyApi.data(endpoint, payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "rows", "list", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ApiError(f"{endpoint} 未返回对象列表")


def resolve_group(api: KdzwyApi, group_name: str = "") -> tuple[str, str]:
    rows = api.get_voucher_groups_v1()
    valid = [
        row for row in rows
        if row.get("id") not in (None, "") and str(row.get("name") or row.get("groupName") or "").strip()
    ]
    if group_name:
        matches = [row for row in valid if str(row.get("name") or row.get("groupName") or "") == group_name]
        if len(matches) != 1:
            raise ApiError(f"当前账套无法唯一解析凭证字：{group_name}")
        selected = matches[0]
    else:
        if not valid:
            raise ApiError("当前账套没有可用的凭证字")
        preferred = [
            row for row in valid
            if any(bool(row.get(key)) for key in ("isDefault", "default", "defaultFlag", "isDefaultGroup"))
        ]
        selected = preferred[0] if preferred else valid[0]
    return str(selected["id"]), str(selected.get("name") or selected.get("groupName") or "")


def resolve_subject(api: KdzwyApi, account_number: str | None = None, account_name: str | None = None) -> dict[str, Any]:
    data = api.get_subject_tree(effective=0, expand=True)
    def flatten(rows):
        result = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                result.append(row)
                result.extend(flatten(row.get("child", [])))
        return result
    rows = flatten(data.get("rows", []))
    matches = []
    for row in rows:
        number_match = account_number and str(row.get("number") or "") == account_number
        name_match = account_name and str(row.get("fullName") or row.get("accountName") or "") == account_name
        if number_match or (not account_number and name_match):
            matches.append(row)
    if len(matches) != 1 or matches[0].get("id") in (None, ""):
        label = account_number or account_name or "未指定科目"
        raise ApiError(f"当前账套无法唯一解析科目：{label}")
    row = matches[0]
    return {
        "accountId": str(row["id"]),
        "accountNumber": str(row.get("number") or account_number or ""),
        "accountName": str(row.get("fullName") or row.get("accountName") or account_name or ""),
        # The current account-tree API exposes auxiliary accounting through
        # itemEnabled and per-class flags (customerEnabled, supplierEnabled...).
        "isItem": bool(row.get("itemEnabled", row.get("isItem"))),
        "isQtyaux": bool(row.get("isQtyaux")),
        "isCur": bool(row.get("isCur")),
        "limited": row.get("limited", 0),
    }


def resolve_defaults(api: KdzwyApi, settings: dict[str, Any]) -> dict[str, Any]:
    voucher = settings.get("voucher_defaults", {})
    group_id, group_name = resolve_group(api, str(voucher.get("group_name") or ""))
    entries: list[dict[str, Any]] = []
    for item in settings.get("entry_defaults", []):
        subject = resolve_subject(
            api,
            str(item.get("account_number")) if item.get("account_number") else None,
            str(item.get("account_name")) if item.get("account_name") else None,
        )
        entries.append({
            "line_no": item.get("line_no", len(entries) + 1),
            "dc": item.get("dc", 1),
            "account_id": subject["accountId"],
            "account_number": subject["accountNumber"],
            "account_name": subject["accountName"],
        })
    return {"voucher_defaults": {**voucher, "group_id": group_id, "group_name": group_name}, "entry_defaults": entries}
