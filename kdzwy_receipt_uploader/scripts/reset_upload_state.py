from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("sales", "purchase", "bank", "misc")


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清除指定公司当前期间的本地上传断点状态")
    parser.add_argument("company_key", help="config/companies 下的公司配置键")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取配置：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"配置必须是JSON对象：{path}")
    return value


def clear_receipt_markers(receipt_root: Path) -> tuple[set[str], int, int]:
    receipt_ids: set[str] = set()
    scanned = 0
    changed = 0
    for source in SOURCES:
        for path in sorted((receipt_root / source).glob("receipt_*/receipt.json")):
            payload = read_json(path)
            receipt_id = str(payload.get("receiptId", "")).strip()
            if receipt_id:
                receipt_ids.add(receipt_id)
            scanned += 1
            had_marker = payload.pop("uploaded", None) is not None
            had_upload = payload.pop("upload", None) is not None
            if had_marker or had_upload:
                write_json(path, payload)
                changed += 1
    return receipt_ids, scanned, changed


def reset_audit(receipt_ids: set[str]) -> tuple[Path | None, int]:
    audit_path = ROOT / "runtime" / "logs" / "run.jsonl"
    if not audit_path.is_file():
        return None, 0
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = audit_path.with_name(f"run.before-upload-reset.{timestamp}.jsonl")
    shutil.copy2(audit_path, backup_path)
    kept: list[str] = []
    removed = 0
    for line in audit_path.read_text(encoding="utf-8-sig").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(item, dict) and str(item.get("receiptId", "")) in receipt_ids:
            removed += 1
        else:
            kept.append(line)
    temporary = audit_path.with_suffix(audit_path.suffix + ".tmp")
    content = "\n".join(kept)
    temporary.write_text(content + ("\n" if content else ""), encoding="utf-8")
    temporary.replace(audit_path)
    return backup_path, removed


def main() -> int:
    args = parse_args()
    company_key = args.company_key.strip().lower()
    config_path = ROOT / "config" / "companies" / f"{company_key}.json"
    config = read_json(config_path)
    configured_key = str(config.get("company_key", "")).strip().lower()
    if configured_key != company_key:
        raise SystemExit(f"公司配置键不一致：参数={company_key}，配置={configured_key}")
    dataset = str(config.get("dataset", "")).strip()
    month = str(config.get("month", "")).strip()
    if not dataset or not month:
        raise SystemExit(f"公司配置缺少dataset或month：{config_path}")

    receipt_root = ROOT / "data" / "inbox" / dataset / month / "generated" / "receipts"
    receipt_ids, scanned, changed = clear_receipt_markers(receipt_root)
    backup_path, removed = reset_audit(receipt_ids)

    print(json.dumps({
        "status": "ok",
        "company_key": company_key,
        "dataset": dataset,
        "month": month,
        "receipt_root": str(receipt_root),
        "scanned_receipts": scanned,
        "cleared_receipt_markers": changed,
        "removed_audit_records": removed,
        "audit_backup": str(backup_path) if backup_path else "",
        "message": "本地上传状态已清除；下次confirm将从远端当前凭证号重新开始。",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
