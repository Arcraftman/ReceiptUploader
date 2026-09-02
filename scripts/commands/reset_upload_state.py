from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    normalize_month,
    resolve_target_accountbook,
    workspace_relative_path,
)

SOURCES = ("sales", "purchase", "bank", "misc")


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清除指定公司、指定月份的本地上传断点状态")
    parser.add_argument("company_config_name", help="config/companies 下的配置文件名，可省略.json")
    parser.add_argument("month", help="会计月份，严格使用 YYYY-MM")
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all", help="只清除指定业务；默认清除全部业务")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取配置：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"配置必须是JSON对象：{path}")
    return value


def clear_receipt_markers(receipt_root: Path, sources: tuple[str, ...]) -> tuple[set[str], int, int]:
    receipt_ids: set[str] = set()
    scanned = 0
    changed = 0
    for source in sources:
        for path in sorted((receipt_root / source).glob("receipt_*/receipt.json")):
            payload = read_json(path)
            receipt_id = str(payload.get("receiptId", "")).strip()
            if receipt_id:
                receipt_ids.add(receipt_id)
            scanned += 1
            had_marker = payload.pop("uploaded", None) is not None
            had_upload = payload.pop("upload", None) is not None
            had_upload_result = payload.pop("uploadResult", None) is not None
            if had_marker or had_upload or had_upload_result:
                write_json(path, payload)
                changed += 1
    return receipt_ids, scanned, changed


def reset_audit(receipt_ids: set[str], audit_path: Path) -> tuple[Path | None, int]:
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
    selector = str(args.company_config_name).strip()
    if Path(selector).name != selector:
        raise SystemExit("公司配置参数只能是文件名，不能包含目录")
    config_name = selector[:-5] if selector.lower().endswith(".json") else selector
    config_path = ROOT / "config" / "companies" / f"{config_name}.json"
    try:
        company = load_company_profile(config_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    dataset_profile = dataset_from_company(company)
    dataset = dataset_profile.key
    try:
        month = normalize_month(args.month)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        project_path = ROOT / dataset_profile.data_root / month / "project.json"
        jobs = load_company_jobs(project_path, company)
        target_keys = {job.accountbook for job in jobs}
        if len(target_keys) != 1:
            raise ValueError(f"同一月份包含多个目标账套：{sorted(target_keys)}")
        accountbook = resolve_target_accountbook(
            jobs[0],
            load_accountbooks(ROOT / "runtime" / "registry" / "accountbooks.json"),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    login_account = accountbook.login_account or "default"
    workspace_root = ROOT / workspace_relative_path(login_account, accountbook.key, dataset, month)
    receipt_root = workspace_root / "generated" / "receipts"
    audit_path = workspace_root / "logs" / "run.jsonl"
    selected_sources = SOURCES if args.source == "all" else (args.source,)
    receipt_ids, scanned, changed = clear_receipt_markers(receipt_root, selected_sources)
    backup_path, removed = reset_audit(receipt_ids, audit_path)

    print(json.dumps({
        "status": "ok",
        "company_key": company.key,
        "target_accountbook": accountbook.key,
        "target_company_name": accountbook.name,
        "source_company_key": dataset,
        "month": month,
        "source": args.source,
        "login_account": login_account,
        "workspace_root": str(workspace_root),
        "receipt_root": str(receipt_root),
        "scanned_receipts": scanned,
        "cleared_receipt_markers": changed,
        "removed_audit_records": removed,
        "audit_backup": str(backup_path) if backup_path else "",
        "message": f"{args.source} 本地上传状态已清除；下次confirm将从远端当前凭证号重新开始。",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
