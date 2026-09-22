"""Resolve one configured month and show bank receipt draft readiness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_pdf_links  # noqa: E402
from kdzwy_receipt_uploader.bank_final_receipts import (  # noqa: E402
    BankFinalReceiptError,
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
        if bank_jobs[0].stage not in {"prepare", "verify", "send", "all"}:
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
        report = verify_bank_pdf_links(receipt_root)
        # Verification does not regenerate receipt files or perform remote writes.
        from kdzwy_receipt_uploader.pipeline_state import PipelineStateStore
        state = PipelineStateStore(workspace / "state" / "bank" / "state.json")
        if state.path.exists() and state.load().get("status") != "running":
            state.update(status="waiting_for_pdf_binding" if report["missingPdfCount"] else "succeeded",
                         phase="waiting_for_pdf_binding" if report["missingPdfCount"] else "verified",
                         counters={"missingPdfCount": report["missingPdfCount"]},
                         exit_code=4 if report["missingPdfCount"] else 0, event="pdf_verified")

    except (
        BankFinalReceiptError,
        CompanyRegistryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Bank receipt validation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 4 if report["missingPdfCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
