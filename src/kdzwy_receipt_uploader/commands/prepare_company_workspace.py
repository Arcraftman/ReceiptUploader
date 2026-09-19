"""Create the data and runtime directory layout declared by one company config."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    dataset_from_company,
    load_company_jobs,
    load_company_profile,
    load_accountbooks,
    normalize_month,
    resolve_company_template,
    resolve_target_accountbook,
    workspace_relative_path,
)
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES  # noqa: E402


def required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CompanyRegistryError(f"Company config is missing {label}")
    return text


def safe_filename_part(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "month"


def ensure_inside(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise CompanyRegistryError(f"{label} must be inside {parent}: {resolved}") from exc
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create data and runtime workspaces from company configuration")
    parser.add_argument("--config", type=Path, required=True, help="config/companies/company_<company_id>_<company_name>.json")
    parser.add_argument("--month", required=True, help="Explicit accounting month in YYYY-MM format")
    parser.add_argument("--quiet", action="store_true", help="Suppress workspace JSON on success; errors remain visible")
    args = parser.parse_args(argv)

    try:
        config_path = args.config.resolve()
        company = load_company_profile(config_path)
        company_id = company.company_id
        company_name = company.name
        company_key = company.key
        dataset = dataset_from_company(company)
        source_company_key = dataset.key
        template_key = required_text(company.template_company, "template_company")
        month = normalize_month(args.month)

        template = resolve_company_template(project_root(), template_key, company_name)
        template_index = project_root() / "templates" / template.directory / "index.json"
        if not template_index.is_file():
            raise CompanyRegistryError(f"Template is missing index.json: {template_index}")

        accountbooks = load_accountbooks(project_root() / "runtime" / "registry" / "accountbooks.json")

        inbox_root = (project_root() / "data" / "inbox").resolve()
        dataset_root = ensure_inside(project_root() / dataset.data_root, inbox_root, "Dataset directory")
        month_root = ensure_inside(dataset_root / month, dataset_root, "Month directory")
        project_config_path = month_root / "project.json"
        if not project_config_path.is_file():
            raise CompanyRegistryError(
                f"Month config not found: {project_config_path}; run start month first"
            )
        project_payload = json.loads(project_config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(project_payload, dict):
            raise CompanyRegistryError(f"Month config must be a JSON object: {project_config_path}")
        jobs = load_company_jobs(project_config_path, company)
        if any(job.month != month for job in jobs):
            raise CompanyRegistryError(f"Month config does not match the requested month: {project_config_path}")
        target_keys = {job.accountbook for job in jobs}
        if len(target_keys) != 1:
            raise CompanyRegistryError(f"All sources in one month must use the same explicit target: {sorted(target_keys)}")
        accountbook = resolve_target_accountbook(jobs[0], accountbooks)
        login_account = accountbook.login_account or "default"
        source_execution_flags = {job.source: job.enabled for job in jobs}

        workspaces_root = (project_root() / "workspaces").resolve()
        workspace_root = ensure_inside(
            project_root() / workspace_relative_path(login_account, accountbook.key, source_company_key, safe_filename_part(month)),
            workspaces_root,
            "Accountbook workspace",
        )
        workspace_generated = workspace_root / "generated"

        created_directories: list[str] = []
        for source in BUILT_IN_SOURCES:
            directories = (
                month_root / "input" / source,
                workspace_generated / "receipts" / source,
                workspace_generated / "ocr" / source,
                workspace_root / "state" / source,
                workspace_root / "logs" / source,
            )
            for directory in directories:
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    created_directories.append(str(directory))

        execution_enabled_sources = [
            source for source in BUILT_IN_SOURCES if source_execution_flags[source]
        ]
        result = {
            "status": "ok",
            "dataset": {
                "company_key": company_key,
                "company_id": company_id,
                "company_name": company_name,
            },
            "template_company": template_key,
            "target": {
                "accountbook_key": accountbook.key,
                "company_id": accountbook.company_id,
                "company_name": accountbook.name,
            },
            "month": month,
            "month_directory": str(month_root),
            "workspace_directory": str(workspace_root),
            "sources": list(BUILT_IN_SOURCES),
            "execution_enabled_sources": execution_enabled_sources,
            "created_directories": created_directories,
            "created_files": [],
            "project_config": str(project_config_path),
        }
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, CompanyRegistryError) as exc:
        print(f"Workspace configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
