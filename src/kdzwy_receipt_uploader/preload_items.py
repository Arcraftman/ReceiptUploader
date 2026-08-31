"""Preload and, when explicitly enabled, create all source-column items once."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .item_class import AUXILIARY_ITEM_CLASSES, resolve_item_class_id
from .xlsx_cache import load_read_only_workbook


@dataclass
class PreloadedItems:
    by_class: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
    source_columns: dict[str, list[str]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)

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


def preload_bank_counterparties(
    api: Any,
    records: Mapping[str, Mapping[str, Any]],
    *,
    create_missing: bool = True,
) -> PreloadedItems:
    """Resolve/create bank customers and suppliers from the authoritative statement map."""
    wanted: dict[int, set[str]] = {1: set(), 5: set()}
    for record in records.values():
        name = str(record.get("counterpartyName") or "").strip()
        config_company = str(record.get("configCompany") or "").strip()
        if not name or (config_company and name == config_company):
            continue
        direction = str(record.get("flowDirection") or "").strip().lower()
        if direction == "inflow":
            wanted[1].add(name)
        elif direction == "outflow":
            wanted[5].add(name)

    result = PreloadedItems(
        source_columns={
            str(class_id): sorted(names) for class_id, names in wanted.items()
        }
    )
    for class_id, names in sorted(wanted.items()):
        data = api.get_items_v1(class_id, page_size=500)
        rows = list(data.get("rows", [])) if isinstance(data, dict) else []
        bucket = result.by_class.setdefault(class_id, {})
        for row in rows:
            if isinstance(row, dict) and str(row.get("name", "")).strip():
                bucket[str(row["name"]).strip()] = dict(row)
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
