"""ItemClassId mapping and save-field rules from the voucher UI."""
from __future__ import annotations

from typing import Any, Mapping

AUXILIARY_ITEM_CLASSES: dict[str, int] = {
    "客户": 1,
    "职员": 2,
    "项目": 3,
    "存货": 4,
    "供应商": 5,
    "部门": 6,
}

AUXILIARY_FIELD_BY_CLASS: dict[int, str] = {
    1: "customerId",
    2: "empId",
    3: "projectId",
    4: "inventoryId",
    5: "supplierId",
    6: "deptId",
}

ITEM_CLASS_BY_FIELD: dict[str, int] = {field: class_id for class_id, field in AUXILIARY_FIELD_BY_CLASS.items()}


def resolve_item_class_id(item_class: str | None = None, item_class_id: int | str | None = None) -> int:
    """Resolve a named or numeric ItemClassId without guessing."""
    if item_class_id not in (None, ""):
        value = int(item_class_id)
        if value < 1:
            raise ValueError("itemClassId 必须是正整数")
        return value
    label = str(item_class or "").strip()
    if label in AUXILIARY_ITEM_CLASSES:
        return AUXILIARY_ITEM_CLASSES[label]
    raise ValueError(f"无法解析 ItemClassId：{item_class or item_class_id}")


def auxiliary_field(item_class: str | None = None, item_class_id: int | str | None = None) -> str:
    class_id = resolve_item_class_id(item_class, item_class_id)
    return AUXILIARY_FIELD_BY_CLASS.get(class_id, "itemId")


def build_auxiliary_expectation(item: Mapping[str, Any], item_class: str | None = None, item_class_id: int | str | None = None) -> dict[str, Any]:
    """Normalize a live auxiliary row for receipt injection and read-back."""
    class_id = resolve_item_class_id(item_class or item.get("itemClass"), item_class_id or item.get("itemClassId"))
    field = AUXILIARY_FIELD_BY_CLASS.get(class_id, "itemId")
    item_id = item.get("id")
    name = str(item.get("name") or "").strip()
    number = str(item.get("number") or "").strip()
    if item_id in (None, "") or not name:
        raise ValueError(f"辅助对象缺少 id/name：itemClassId={class_id}")
    label = next((key for key, value in AUXILIARY_ITEM_CLASSES.items() if value == class_id), f"自定义辅助核算{class_id}")
    return {"itemClass": label, "itemClassId": class_id, "field": field, "id": str(item_id), "number": number, "name": name}
