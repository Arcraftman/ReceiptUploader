"""Read and create auxiliary-accounting items in the logged-in account book."""
from __future__ import annotations

from typing import Any, Mapping

from .item_class import AUXILIARY_ITEM_CLASSES, resolve_item_class_id

AUXILIARY_ITEM_CLASS_NAMES: dict[int, str] = {item_class_id: label for label, item_class_id in AUXILIARY_ITEM_CLASSES.items()}


def format_item_number(value: int | str) -> str:
    number = int(str(value).strip())
    if number < 1:
        raise ValueError("item 编号必须是正整数")
    return f"{number:03d}" if number < 1000 else str(number)


def auxiliary_items_endpoint(item_class_id: int) -> str:
    if item_class_id < 1:
        raise ValueError("item_class_id 必须是正整数")
    return f"/bs/item?m=findItem&itemClassId={item_class_id}"


def auxiliary_next_number_endpoint(item_class_id: int) -> str:
    class_id = resolve_item_class_id(item_class_id=item_class_id)
    return f"/bs/item?m=findNextNum&itemClassId={class_id}"


def auxiliary_save_endpoint(confirmed: bool = False) -> str:
    return f"/bs/item?m=save&confirmed={1 if confirmed else 0}"


def extract_auxiliary_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, Mapping):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def fetch_auxiliary_items(api: Any, item_class_id: int) -> dict[str, Any]:
    if hasattr(api, "get_items_v1"):
        data = api.get_items_v1(item_class_id, page_size=500)
        items = list(data.get("rows", [])) if isinstance(data, dict) else []
        label = AUXILIARY_ITEM_CLASS_NAMES.get(item_class_id, f"自定义辅助核算{item_class_id}")
        return {"label": label, "itemClassId": item_class_id, "endpoint": f"/jdy-fi/<DBID>/gl/v1/item/page?itemClassId={item_class_id}", "status": 0, "count": len(items), "sampleKeys": sorted(items[0].keys()) if items else [], "items": items}
    endpoint = auxiliary_items_endpoint(item_class_id)
    payload = api.post_form(endpoint, {})
    items = extract_auxiliary_items(payload)
    label = AUXILIARY_ITEM_CLASS_NAMES.get(item_class_id, f"自定义辅助核算{item_class_id}")
    return {"label": label, "itemClassId": item_class_id, "endpoint": endpoint, "status": payload.get("status", payload.get("code")), "httpStatus": payload.get("_httpStatus"), "count": len(items), "sampleKeys": sorted(items[0].keys()) if items else [], "items": items}


def create_auxiliary_item(api: Any, item_class_id: int | str, number: str, name: str, spec: str = "", unit: str = "", remote_max_number: int | None = None) -> dict[str, Any]:
    """Create one missing item through the same endpoints as voucher20.js."""
    class_id = resolve_item_class_id(item_class_id=item_class_id)
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("新增辅助项目名称不能为空")
    next_payload = api.get_json(auxiliary_next_number_endpoint(class_id))
    next_data = next_payload.get("data") if isinstance(next_payload, dict) else None
    suggested = next_data.get("num") if isinstance(next_data, dict) else None
    requested_number = int(str(number).strip()) if str(number or "").strip().isdigit() else 0
    suggested_number = int(str(suggested).strip()) if str(suggested or "").strip().isdigit() else 0
    final_number = format_item_number(max(requested_number, int(remote_max_number or 0) + 1, suggested_number) or 0) if max(requested_number, int(remote_max_number or 0) + 1, suggested_number) else ""
    if not final_number:
        raise ValueError(f"新增辅助项目未取得编码：itemClassId={class_id}")
    save_endpoint = auxiliary_save_endpoint(False)
    payload = api.post_form(save_endpoint, {"number": final_number, "name": clean_name, "itemClassId": class_id, "spec": spec, "unit": unit})
    status = payload.get("status", payload.get("code"))
    if status not in (None, 200, "200"):
        raise RuntimeError(f"新增辅助项目失败：itemClassId={class_id}, name={clean_name}, status={status}, msg={payload.get('msg') or payload.get('message')}")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("id") in (None, "", 0, "0"):
        raise RuntimeError(f"新增辅助项目未返回有效 id：itemClassId={class_id}, number={final_number}, name={clean_name}")
    return {"itemClassId": class_id, "id": str(data["id"]), "number": str(data.get("number") or final_number), "name": str(data.get("name") or clean_name), "raw": data}


def match_auxiliary_item(api: Any, item_class_id: int, name: str) -> dict[str, Any]:
    result = fetch_auxiliary_items(api, item_class_id)
    matches = [item for item in result["items"] if str(item.get("name", "")).strip() == name.strip()]
    if len(matches) != 1:
        raise ValueError(f"辅助核算名称无法唯一匹配：itemClassId={item_class_id}, name={name}, count={len(matches)}")
    item = matches[0]
    return {"itemClassId": item_class_id, "id": item.get("id"), "number": item.get("number", ""), "name": item.get("name", ""), "raw": item}


def fetch_all_auxiliary_items(api: Any) -> dict[str, dict[str, Any]]:
    return {label: fetch_auxiliary_items(api, item_class_id) for label, item_class_id in AUXILIARY_ITEM_CLASSES.items()}
