"""List the authoritative special-object bank filter for one company month."""
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
    parser = argparse.ArgumentParser(description="List bank exceptions separated from the normal workflow")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--month", required=True)
    args = parser.parse_args(argv)
    try:
        company = load_company_profile(args.config.resolve())
        dataset = dataset_from_company(company)
        month = normalize_month(args.month)
        project_path = resolve_project_path(
            project_root(), f"{dataset.data_root}/{month}/project.json"
        )
        jobs = load_company_jobs(project_path, company)
        bank_job = next(job for job in jobs if job.source == "bank")
        accountbooks = load_accountbooks(
            project_root() / "runtime" / "registry" / "accountbooks.json"
        )
        accountbook = resolve_target_accountbook(bank_job, accountbooks)
        workspace = project_root() / workspace_relative_path(
            accountbook.login_account or "default",
            accountbook.key,
            dataset.key,
            month,
        )
        exception_path = (
            workspace / "generated" / "maps" / "bank" / "bank_exceptions.json"
        )
        exception_map = json.loads(exception_path.read_text(encoding="utf-8-sig"))
    except (
        CompanyRegistryError,
        OSError,
        json.JSONDecodeError,
        StopIteration,
    ) as exc:
        print(f"Cannot read bank exceptions: {exc}", file=sys.stderr)
        return 2

    entries = exception_map.get("entries")
    if not isinstance(entries, dict):
        print(f"Invalid bank exception list: {exception_path}", file=sys.stderr)
        return 2
    summary = exception_map.get("summary") if isinstance(exception_map.get("summary"), dict) else {}

    print("[Bank exceptions] Special counterparties separated; original split PDFs retained")
    print(f"  Dataset: {company.name}")
    print(f"  Target: {accountbook.name}")
    print(f"  Month: {month}")
    print(
        f"  Configured names: {summary.get('exceptionNameCount', '-')}; "
        f"Matching transactions: {summary.get('exceptionStatementCount', len(entries))}; "
        f"Split exceptions: {summary.get('splitExceptionPdfCount', '-')}; "
        f"Exception PDFs={summary.get('exceptionPdfCount', '-')}; "
        f"Copied={summary.get('copiedPdfCount', '-')}; "
        f"Missing PDFs={summary.get('missingPdfCount', '-')}"
    )
    for key, item in sorted(entries.items()):
        if not isinstance(item, dict):
            continue
        print(
            f"  - {key} | {item.get('counterpartyName') or '-'} | "
            f"Amount={item.get('amount') or '-'} | PDF={item.get('pdfStatus') or '-'} | "
            f"Match={item.get('matchMethod') or '-'}"
        )
        if item.get("copiedPdf"):
            print(f"    Exception copy: {item['copiedPdf']}")
        if item.get("sourcePdf"):
            print(f"    Split original: {item['sourcePdf']}")
    if not entries:
        print("[Done] No configured or matched bank exceptions.")
    print(f"  List file: {exception_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
