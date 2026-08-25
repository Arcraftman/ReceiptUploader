"""Persistent number-to-name maps for dynamically discovered ItemClass items."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .item_class import AUXILIARY_ITEM_CLASSES, resolve_item_class_id

AUXILIARY_ITEM_CLASS_NAMES: dict[int, str] = {value: key for key, value in AUXILIARY_ITEM_CLASSES.items()}


def format_item_number(value: int | str) -> str:
    """Format item numbers as 001..999, then 1000.. without truncation."""
    number = int(str(value).strip())
    if number < 1:
        raise ValueError("item 编号必须是正整数")
    return f"{number:03d}" if number < 1000 else str(number)


def _class_label(item_class_id: int) -> str:
    return AUXILIARY_ITEM_CLASS_NAMES.get(item_class_id, f"自定义辅助核算{item_class_id}")


class ItemClassMapStore:
    """Keep one independent number->name map for every ItemClassId.

    ``items`` is intentionally the simple map requested by the workflow:
    ``{"001": "客户A", "002": "客户B"}``. Remote IDs are kept in a
    separate side-map so the human-facing number/name mapping stays stable.
    """

    def __init__(self, path: Path, payload: dict[str, Any] | None = None) -> None:
        self.path = path.resolve()
        self.payload: dict[str, Any] = payload or {"version": "1.0", "maps": {}}
        self.payload.setdefault("version", "1.0")
        self.payload.setdefault("maps", {})

    @classmethod
    def load(cls, path: Path) -> "ItemClassMapStore":
        path = path.resolve()
        if not path.is_file():
            return cls(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"ItemClass map 无法读取：{path}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("maps"), dict):
            raise ValueError(f"ItemClass map 结构无效：{path}")
        return cls(path, payload)

    def _bucket(self, item_class_id: int, item_class: str | None = None) -> dict[str, Any]:
        key = str(item_class_id)
        bucket = self.payload["maps"].setdefault(key, {
            "itemClassId": item_class_id,
            "itemClass": item_class or _class_label(item_class_id),
            "items": {},
            "remoteIds": {},
        })
        bucket.setdefault("itemClassId", item_class_id)
        bucket.setdefault("itemClass", item_class or _class_label(item_class_id))
        bucket.setdefault("items", {})
        bucket.setdefault("remoteIds", {})
        return bucket

    def seed_remote(self, item_class_id: int | str, rows: Iterable[Mapping[str, Any]], item_class: str | None = None) -> dict[str, Any]:
        """Seed one map from the first/current remote ItemClass response."""
        class_id = resolve_item_class_id(item_class, item_class_id)
        bucket = self._bucket(class_id, item_class)
        conflicts: list[dict[str, str]] = []
        added = 0
        remote_max_number = 0
        remote_items: dict[str, str] = {}
        remote_ids: dict[str, str] = {}
        for row in rows:
            raw_number = row.get("number")
            name = str(row.get("name") or "").strip()
            if raw_number in (None, "") or not name:
                continue
            code = format_item_number(raw_number)
            remote_max_number = max(remote_max_number, int(code))
            remote_items[code] = name
            if row.get("id") not in (None, ""):
                remote_ids[code] = str(row["id"])
            old_name = str(bucket["items"].get(code) or "").strip()
            if old_name and old_name != name:
                conflicts.append({"number": code, "existingName": old_name, "remoteName": name})
                continue
            if not old_name:
                bucket["items"][code] = name
                added += 1
        # The remote list is authoritative for existing items. This removes
        # stale local rows such as a previous false alarm, while preserving
        # only numbers/names currently known by the account book.
        bucket["items"] = remote_items
        bucket["remoteIds"] = remote_ids
        bucket["remoteMaxNumber"] = remote_max_number
        self._check_unique_names(bucket)
        return {"itemClassId": class_id, "itemClass": bucket["itemClass"], "added": added, "conflicts": conflicts, "count": len(bucket["items"]), "remoteMaxNumber": remote_max_number}

    def _check_unique_names(self, bucket: Mapping[str, Any]) -> None:
        seen: dict[str, str] = {}
        for code, raw_name in bucket.get("items", {}).items():
            name = str(raw_name).strip()
            if not name:
                continue
            previous = seen.get(name)
            if previous and previous != code:
                raise ValueError(f"ItemClassId={bucket.get('itemClassId')} 名称重复：{name} ({previous}/{code})")
            seen[name] = code

    def resolve_name(self, item_class_id: int | str, name: str, item_class: str | None = None) -> dict[str, Any]:
        """Reuse an existing number or append the next formatted number.

        A newly appended map row deliberately has ``remoteId=None``. It is
        only a local reservation until ``create_remote_item`` succeeds.
        """
        class_id = resolve_item_class_id(item_class, item_class_id)
        target = str(name or "").strip()
        if not target:
            raise ValueError("itemName 不能为空")
        bucket = self._bucket(class_id, item_class)
        self._check_unique_names(bucket)
        for code, existing_name in bucket["items"].items():
            if str(existing_name).strip() == target:
                return {"itemClassId": class_id, "itemClass": bucket["itemClass"], "number": format_item_number(code), "name": target, "remoteId": bucket["remoteIds"].get(code), "created": False}
        numeric_codes = [int(str(code)) for code in bucket["items"] if str(code).isdigit()]
        map_max_number = max(numeric_codes, default=0)
        remote_max_number = int(bucket.get("remoteMaxNumber") or 0)
        next_code = format_item_number(max(map_max_number, remote_max_number) + 1)
        bucket["items"][next_code] = target
        return {"itemClassId": class_id, "itemClass": bucket["itemClass"], "number": next_code, "name": target, "remoteId": None, "created": True}

    def attach_remote_id(self, item_class_id: int | str, number: str, remote_id: Any, name: str, item_class: str | None = None) -> dict[str, Any]:
        """Commit the remote ID for a locally reserved number."""
        class_id = resolve_item_class_id(item_class, item_class_id)
        bucket = self._bucket(class_id, item_class)
        code = format_item_number(number)
        if code not in bucket["items"] or str(bucket["items"][code]).strip() != str(name).strip():
            raise ValueError(f"本地 map 中不存在匹配的预留 item：ItemClassId={class_id}, number={code}, name={name}")
        if remote_id in (None, "", 0, "0"):
            raise ValueError(f"远端新增 item 未返回有效 id：ItemClassId={class_id}, number={code}")
        bucket["remoteIds"][code] = str(remote_id)
        return {"itemClassId": class_id, "number": code, "name": str(name).strip(), "remoteId": str(remote_id)}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def simple_maps(self) -> dict[str, dict[str, str]]:
        return {key: dict(bucket.get("items", {})) for key, bucket in self.payload["maps"].items()}
