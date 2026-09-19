"""Resolve one configured month and show bank receipt draft readiness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts  # noqa: E402
from kdzwy_receipt_uploader.bank_final_receipts import (  # noqa: E402
    BankFinalReceiptError,
    load_bank_records,
)
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
    parser = argparse.ArgumentParser(description="Show bank receipt draft and validation status")
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
        if bank_jobs[0].stage not in {"prepare", "send", "all"}:
            print(
                "[Not ready] Complete bank LLM analysis, then set stage to prepare."
            )
            return 1
        accountbooks = load_accountbooks(project_root() / "runtime" / "registry" / "accountbooks.json")
        accountbook = resolve_target_accountbook(bank_jobs[0], accountbooks)
        workspace = project_root() / workspace_relative_path(
            accountbook.login_account or "default",
            accountbook.key,
            dataset.key,
            month,
        )
        receipt_root = workspace / "generated" / "receipts" / "bank"
        bank_map_root = workspace / "generated" / "maps" / "bank"
        matched_records, _ = load_bank_records(
            bank_map_root / "bank_map.json",
            bank_map_root / "bank_map.report.json",
        )
        report = verify_bank_receipts(
            receipt_root, allowed_record_keys=set(matched_records)
        )
    except (
        BankFinalReceiptError,
        CompanyRegistryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Bank receipt validation failed: {exc}", file=sys.stderr)
        return 2

    summary = report["summary"]
    print("[Verify] Bank receipts")
    print(f"  Dataset: {company.name}")
    print(f"  Target: {accountbook.name}")
    print(f"  Month: {month}")
    print(
        f"  Total: {summary['receiptCount']}; draft=true: {summary['draftCount']}; "
        f"Ready: {summary['readyCount']}; Invalid: {summary['invalidCount']}; "
        f"Orphan/exception artifacts: {summary.get('orphanCount', 0)}"
    )
    if report["drafts"]:
        print("[Manual input required] These receipts still have draft=true:")
        for item in report["drafts"]:
            print(f"  - Number: {item['statementIndex'] or item['receiptId']}")
            print(f"    receiptId: {item['receiptId']}")
            print(f"    receipt.json: {item['receipt']}")
    if report["invalid"]:
        print("[Failed] These receipts have draft=false but invalid fields:")
        for item in report["invalid"]:
            print(f"  - {item.get('receiptId') or item['receipt']}: {item['error']}")
            print(f"    {item['receipt']}")
    print(f"  Validation report: {receipt_root / 'bank_receipts.verify.report.json'}")
    if report["status"] == "ready":
        print("[Passed] All bank receipts have draft=false and valid fields.")
        return 0
    if report["status"] == "empty":
        print("[Failed] No final bank receipts. Complete LLM analysis, then run stage=prepare.")
        return 1
    print("[Failed] Complete these receipts, manually set draft=false, then run verify again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
