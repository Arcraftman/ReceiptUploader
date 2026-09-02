"""List marker-only bank statement rows that have never matched a receipt."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    normalize_month,
    resolve_project_path,
    resolve_target_accountbook,
    workspace_relative_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="列出未匹配回单或个人姓名排除的流水标记")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--month", required=True)
    args = parser.parse_args()
    try:
        company = load_company_profile(args.config.resolve())
        dataset = dataset_from_company(company)
        month = normalize_month(args.month)
        project_path = resolve_project_path(ROOT, f"{dataset.data_root}/{month}/project.json")
        jobs = load_company_jobs(project_path, company)
        bank_jobs = [job for job in jobs if job.source == "bank"]
        if len(bank_jobs) != 1:
            raise CompanyRegistryError(f"月份配置中必须恰好有一个 bank 业务：{project_path}")
        accountbooks = load_accountbooks(ROOT / "runtime" / "registry" / "accountbooks.json")
        accountbook = resolve_target_accountbook(bank_jobs[0], accountbooks)
        workspace = ROOT / workspace_relative_path(
            accountbook.login_account or "default", accountbook.key, dataset.key, month
        )
        report_path = workspace / "generated" / "maps" / "bank" / "bank_map.report.json"
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        exception_path = workspace / "generated" / "maps" / "bank" / "bank_exceptions.json"
        exception_map = json.loads(exception_path.read_text(encoding="utf-8-sig"))
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"无法读取银行排除标记：{exc}", file=sys.stderr)
        return 2

    exception_keys = set((exception_map.get("entries") or {}).keys())
    rows: list[dict] = []
    for bank_key, bank in sorted((report.get("banks") or {}).items()):
        for row in bank.get("unmatchedStatements", []) or []:
            key = f"{bank_key}__{row.get('index')}" if isinstance(row, dict) else ""
            if isinstance(row, dict) and key not in exception_keys:
                rows.append(
                    {"bankKey": bank_key, "markerReason": "receipt_not_found", **row}
                )
        for row in bank.get("skippedPersonNameStatements", []) or []:
            key = f"{bank_key}__{row.get('index')}" if isinstance(row, dict) else ""
            if isinstance(row, dict) and key not in exception_keys:
                rows.append({"bankKey": bank_key, **row})
    print("[普通未匹配银行流水] 已配置特殊对象请使用 exceptions 命令查看")
    print(f"  资料公司：{company.name}")
    print(f"  目标账套：{accountbook.name}")
    print(f"  会计月份：{month}")
    print(f"  记录数：{len(rows)}")
    role_names = {"supplier": "供应商", "customer": "客户", "person": "个人姓名"}
    reason_names = {
        "receipt_not_found": "未匹配回单",
        "person_name": "个人姓名跳过",
    }
    for row in rows:
        statement = row.get("statement") if isinstance(row.get("statement"), dict) else {}
        amount = row.get("transactionAmount") or "-"
        print(
            f"  - {row.get('index') or '-'} | {row['bankKey']} | "
            f"{reason_names.get(str(row.get('markerReason') or ''), '排除')} | "
            f"{role_names.get(str(row.get('counterpartyType') or ''), '-')} | "
            f"{row.get('counterpartyName') or '-'} | 金额={amount}"
        )
        print(
            f"    来源：{Path(str(statement.get('xlsx') or '')).name} / "
            f"{statement.get('sheet') or '-'} / row {statement.get('row') or '-'}"
        )
    if not rows:
        print("[完成] 当前没有被排除的银行流水记录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
