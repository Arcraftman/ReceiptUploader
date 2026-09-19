"""Command-line entry point for the human-readable Qwen analysis report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.company_registry import (
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    normalize_month,
    resolve_target_accountbook,
    workspace_relative_path,
)
from kdzwy_receipt_uploader.concise_template_analysis import write_concise_analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an accounting summary from template_analysis.json")
    parser.add_argument("--month", default="", help="Month directory, e.g. 2026-08")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc"], default="", help="source")
    parser.add_argument("--company", default="", help="Read config/companies/<filename>.json; .json is optional")
    parser.add_argument("--input", type=Path, default=None, help="Path to template_analysis.json")
    parser.add_argument("--output", type=Path, default=None, help="Output Markdown path")
    args = parser.parse_args(argv)
    if args.month:
        try:
            args.month = normalize_month(args.month)
        except ValueError as exc:
            parser.error(str(exc))

    if args.input is None:
        source_company_key, month, source = "", args.month.strip(), args.source.strip()
        workspace_input: Path | None = None
        if not args.company:
            parser.error("--company and --month are required without --input")
        if args.company:
            selector = args.company.strip()
            if Path(selector).name != selector:
                parser.error("--company must be a config filename without directories")
            config_name = selector[:-5] if selector.lower().endswith(".json") else selector
            company_path = project_root() / "config" / "companies" / f"{config_name}.json"
            if not company_path.is_file():
                parser.error(f"Company config not found: {company_path}")
            try:
                company = load_company_profile(company_path)
            except ValueError as exc:
                parser.error(str(exc))
            dataset_profile = dataset_from_company(company)
            source_company_key = dataset_profile.key
            if not month:
                parser.error("--month is required with --company")
            project_path = project_root() / dataset_profile.data_root / month / "project.json"
            if not project_path.is_file():
                parser.error(f"Month config not found: {project_path}")
            try:
                jobs = load_company_jobs(project_path, company)
            except ValueError as exc:
                parser.error(str(exc))
            if not source:
                enabled = [job.source for job in jobs if job.enabled]
                if len(enabled) != 1:
                    parser.error("Enable exactly one source in project.json or specify --source")
                source = enabled[0]
            selected_job = next((job for job in jobs if job.source == source), None)
            if selected_job is None:
                parser.error(f"Month config does not contain source: {source}")
            source_company_key = selected_job.dataset
            try:
                accountbook = resolve_target_accountbook(
                    selected_job,
                    load_accountbooks(project_root() / "runtime" / "registry" / "accountbooks.json"),
                )
            except ValueError as exc:
                parser.error(str(exc))
            workspace_input = (
                project_root()
                / workspace_relative_path(accountbook.login_account or "default", accountbook.key, source_company_key, month)
                / "generated"
                / "ocr"
                / source
                / "template_analysis.json"
            )
        if not source_company_key or not month or not source:
            parser.error("Without --input, specify --company and --month and enable or select one --source")
        if workspace_input is None:
            parser.error("Use --company to locate an isolated workspace, or --input for maintenance")
        input_path = workspace_input
    else:
        input_path = args.input if args.input.is_absolute() else project_root() / args.input
    if not input_path.is_file():
        parser.error(f"Analysis file not found: {input_path}")
    output_path = args.output or input_path.with_name("concise_template_analysis.md")
    if not output_path.is_absolute():
        output_path = project_root() / output_path
    report = write_concise_analysis(input_path, output_path)
    print(f"Summary generated: {report['invoiceCount']} invoices")
    print(f"Output: {report['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
