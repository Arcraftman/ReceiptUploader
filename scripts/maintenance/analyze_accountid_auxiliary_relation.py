"""Analyze accountId in an exported request for historical evidence only.

This maintenance tool reads a local receipt draft and a previously exported
read-only auxiliary-item report. It does not call the website and does not
submit a voucher. The comparison answers whether entries[].accountId is an
auxiliary item ID or a subject/account ID.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

AUXILIARY_FIELD_BY_CLASS = {
    1: "customerId",
    2: "empId",
    3: "projectId",
    4: "inventoryId",
    5: "supplierId",
    6: "deptId",
}

DEFAULT_OUTPUT = PROJECT / "workspaces" / "diagnostics" / "accountid_auxiliary_relation.report.json"


def load_auxiliary_ids(path: Path) -> tuple[dict[int, set[str]], dict[str, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_class: dict[int, set[str]] = {}
    labels: dict[str, int] = {}
    for label, report in payload.get("lists", {}).items():
        item_class_id = int(report["itemClassId"])
        labels[label] = item_class_id
        by_class[item_class_id] = {
            str(item["id"])
            for item in report.get("items", [])
            if isinstance(item, dict) and item.get("id") not in (None, "")
        }
    return by_class, labels


def load_subject_ids(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            str(row.get("accountId") or row.get("id") or "")
            for row in csv.DictReader(handle)
            if (row.get("accountId") or row.get("id")) not in (None, "")
        }


def load_receipt_entries(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    voucher = payload.get("voucher", {})
    entries = voucher.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("receipt.voucher.entries 不是数组")
    return [entry for entry in entries if isinstance(entry, dict)]


def analyze_request(entries: list[dict[str, Any]], auxiliary_ids: dict[int, set[str]], subject_ids: set[str]) -> dict[str, Any]:
    all_auxiliary_ids = set().union(*auxiliary_ids.values()) if auxiliary_ids else set()
    account_rows: list[dict[str, Any]] = []
    auxiliary_rows: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        account_id = str(entry.get("accountId", ""))
        account_rows.append({
            "lineNo": entry.get("lineNo", index),
            "accountId": account_id,
            "accountIdIsAuxiliaryItemId": account_id in all_auxiliary_ids,
            "accountIdIsSubjectId": account_id in subject_ids if subject_ids else None,
        })
        for item_class_id, field_name in AUXILIARY_FIELD_BY_CLASS.items():
            value = entry.get(field_name)
            if value not in (None, "", 0, "0"):
                auxiliary_rows.append({
                    "lineNo": entry.get("lineNo", index),
                    "field": field_name,
                    "itemClassId": item_class_id,
                    "auxiliaryId": str(value),
                    "auxiliaryIdIsKnown": str(value) in auxiliary_ids.get(item_class_id, set()),
                })
    return {
        "request": {
            "endpoint": "/jdy-fi/<DBID>/gl/v1/voucher/save",
            "formFields": ["vchData"],
            "entryCount": len(entries),
        },
        "accountIdRows": account_rows,
        "auxiliaryFieldRows": auxiliary_rows,
        "summary": {
            "accountIdCount": len(account_rows),
            "accountIdMatchesAuxiliaryItemCount": sum(row["accountIdIsAuxiliaryItemId"] for row in account_rows),
            "accountIdMatchesSubjectCount": sum(row["accountIdIsSubjectId"] is True for row in account_rows),
            "explicitAuxiliaryFieldCount": len(auxiliary_rows),
            "knownExplicitAuxiliaryIdCount": sum(row["auxiliaryIdIsKnown"] for row in auxiliary_rows),
        },
        "conclusion": (
            "entries[].accountId 是科目/账户 ID；辅助核算条目 ID 应通过 customerId、supplierId、empId、projectId、inventoryId、deptId 等独立字段传递。"
            if not any(row["accountIdIsAuxiliaryItemId"] for row in account_rows)
            else "发现 accountId 与辅助核算条目 ID 重合，需要结合该分录的辅助字段和科目配置继续核对。"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="分析新版 voucher/save 请求中的 accountId 与辅助核算 ID 的关系")
    parser.add_argument("--auxiliary-report", type=Path, required=True, help="已导出的只读 auxiliary_items 报告")
    parser.add_argument("--receipt", type=Path, required=True, help="要检查的 generated/receipts/.../receipt.json")
    parser.add_argument("--subject-csv", type=Path, default=None, help="可选的科目 ID CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    auxiliary_ids, labels = load_auxiliary_ids(args.auxiliary_report)
    entries = load_receipt_entries(args.receipt)
    report = analyze_request(entries, auxiliary_ids, load_subject_ids(args.subject_csv))
    report["sources"] = {
        "auxiliaryReport": str(args.auxiliary_report.resolve()),
        "receiptDraft": str(args.receipt.resolve()),
        "subjectCsv": str(args.subject_csv.resolve()) if args.subject_csv else "",
        "auxiliaryLabels": labels,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入：{args.output.resolve()}")
    print("本工具只读本地报告和草稿，未调用保存、上传、更新或删除接口。")


if __name__ == "__main__":
    main()
