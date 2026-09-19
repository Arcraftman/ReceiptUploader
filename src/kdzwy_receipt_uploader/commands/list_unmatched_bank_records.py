"""List marker-only bank statement rows that have never matched a receipt."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List unmatched bank receipts and excluded personal transactions")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--month", required=True)
    args = parser.parse_args(argv)
    try:
        company = load_company_profile(args.config.resolve())
        dataset = dataset_from_company(company)
        month = normalize_month(args.month)
        project_path = resolve_project_path(project_root(), f"{dataset.data_root}/{month}/project.json")
        jobs = load_company_jobs(project_path, company)
        bank_jobs = [job for job in jobs if job.source == "bank"]
        if len(bank_jobs) != 1:
            raise CompanyRegistryError(f"Month config must contain exactly one bank source: {project_path}")
        accountbooks = load_accountbooks(project_root() / "runtime" / "registry" / "accountbooks.json")
        accountbook = resolve_target_accountbook(bank_jobs[0], accountbooks)
        workspace = project_root() / workspace_relative_path(
            accountbook.login_account or "default", accountbook.key, dataset.key, month
        )
        report_path = workspace / "generated" / "maps" / "bank" / "bank_map.report.json"
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        exception_path = workspace / "generated" / "maps" / "bank" / "bank_exceptions.json"
        exception_map = json.loads(exception_path.read_text(encoding="utf-8-sig"))
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read bank exclusion markers: {exc}", file=sys.stderr)
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
    print("[Unmatched bank transactions] Use exceptions for configured special counterparties")
    print(f"  Dataset: {company.name}")
    print(f"  Target: {accountbook.name}")
    print(f"  Month: {month}")
    print(f"  Records: {len(rows)}")
    role_names = {"supplier": "supplier", "customer": "customer", "person": "person"}
    reason_names = {
        "receipt_not_found": "receipt not found",
        "person_name": "person skipped",
    }
    for row in rows:
        statement = row.get("statement") if isinstance(row.get("statement"), dict) else {}
        amount = row.get("transactionAmount") or "-"
        print(
            f"  - {row.get('index') or '-'} | {row['bankKey']} | "
            f"{reason_names.get(str(row.get('markerReason') or ''), 'excluded')} | "
            f"{role_names.get(str(row.get('counterpartyType') or ''), '-')} | "
            f"{row.get('counterpartyName') or '-'} | Amount={amount}"
        )
        print(
            f"    Source: {Path(str(statement.get('xlsx') or '')).name} / "
            f"{statement.get('sheet') or '-'} / row {statement.get('row') or '-'}"
        )
    if not rows:
        print("[Done] No excluded bank transactions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
