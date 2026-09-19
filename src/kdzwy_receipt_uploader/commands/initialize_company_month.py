"""Initialize one company's standard data project for a specific accounting month."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
from kdzwy_receipt_uploader.project_runtime import process_environment
import sys
from pathlib import Path
from typing import Any


from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    load_accountbooks,
    load_company_profile,
    resolve_company_template,
    normalize_month,
    validate_bank_configs,
    validate_bank_statement_columns,
    validate_bank_exceptions,
)
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES  # noqa: E402




def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanyRegistryError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"Configuration must be a JSON object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_default_bank_exceptions(path: Path | None = None) -> list[str]:
    defaults_path = path or (project_root() / "config" / "bank_exception.defaults.json")
    payload = read_object(defaults_path)
    if (
        set(payload) != {"version", "exceptions", "pdf_keywords"}
        or payload.get("version") != 2
    ):
        raise CompanyRegistryError(
            "Bank exception defaults must be version 2 and contain only "
            f"version、exceptions、pdf_keywords: {defaults_path}"
        )
    exceptions = validate_bank_exceptions(
        payload.get("exceptions"), "bank_exception.defaults.json.exceptions"
    )
    keyword_rules = payload.get("pdf_keywords")
    if not isinstance(keyword_rules, dict):
        raise CompanyRegistryError(
            "bank_exception.defaults.json.pdf_keywords must be an object"
        )
    for raw_name, raw_keywords in keyword_rules.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise CompanyRegistryError("pdf_keywords names must not be empty")
        if (
            not isinstance(raw_keywords, list)
            or not raw_keywords
            or any(
                not isinstance(keyword, str) or not keyword.strip()
                for keyword in raw_keywords
            )
        ):
            raise CompanyRegistryError(
                f"pdf_keywords.{raw_name} must be a non-empty array of strings"
            )
    return exceptions


def normalize_source_settings(
    value: object,
    bank_exception_defaults: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Declare every built-in source with explicit core run settings."""
    if value is None:
        existing: dict[str, Any] = {}
    elif isinstance(value, dict):
        existing = value
    else:
        raise CompanyRegistryError("project.sources must be an object")
    unknown = sorted(str(key) for key in existing if key not in BUILT_IN_SOURCES)
    if unknown:
        raise CompanyRegistryError(f"project.sources contains unsupported source directories: {', '.join(unknown)}")

    result: dict[str, dict[str, Any]] = {}
    common_allowed = {
        "enabled",
        "stage",
        "analysis_validation",
        "ocr_workers",
        "llm_workers",
        "purpose",
        "only_mapped_invoices",
    }
    for source in BUILT_IN_SOURCES:
        current = existing.get(source)
        if isinstance(current, dict):
            settings = dict(current)
        elif current is None:
            settings = {}
        else:
            raise CompanyRegistryError(f"sources.{source} must be an object")
        allowed = set(common_allowed)
        if source == "bank":
            allowed.add("banks")
            allowed.add("exceptions")
            allowed.add("statement_columns")
        if source == "purchase":
            allowed.add("usage_confirmation_enabled")
        unsupported = sorted(set(settings) - allowed)
        if unsupported:
            raise CompanyRegistryError(
                f"sources.{source} contains unsupported fields: {', '.join(unsupported)}"
            )
        enabled = settings.get("enabled", False)
        if not isinstance(enabled, bool):
            raise CompanyRegistryError(f"sources.{source}.enabled must be a JSON boolean (true or false)")
        settings["enabled"] = enabled
        settings.setdefault("stage", "ocr")
        if settings["stage"] not in {"ocr", "llm", "prepare", "send", "all"}:
            raise CompanyRegistryError(f"sources.{source}.stage must be ocr, llm, prepare, send or all")
        if source == "bank":
            settings.setdefault("banks", {})
            bank_configs = validate_bank_configs(
                settings["banks"], "sources.bank.banks"
            )
            settings.setdefault("statement_columns", {})
            settings["statement_columns"] = validate_bank_statement_columns(
                settings["statement_columns"],
                "sources.bank.statement_columns",
                bank_configs,
            )
            settings["banks"] = bank_configs
            if "exceptions" not in settings:
                defaults = (
                    bank_exception_defaults
                    if bank_exception_defaults is not None
                    else load_default_bank_exceptions()
                )
                settings["exceptions"] = copy.deepcopy(defaults)
            settings["exceptions"] = validate_bank_exceptions(
                settings["exceptions"], "sources.bank.exceptions"
            )
        if source == "purchase":
            usage_confirmation_enabled = settings.get("usage_confirmation_enabled", True)
            if not isinstance(usage_confirmation_enabled, bool):
                raise CompanyRegistryError(
                    "sources.purchase.usage_confirmation_enabled must be a JSON boolean (true or false)"
                )
            settings["usage_confirmation_enabled"] = usage_confirmation_enabled
        result[source] = settings
    return result


def normalize_month_defaults(value: object) -> dict[str, Any]:
    if value is None:
        result: dict[str, Any] = {}
    elif isinstance(value, dict):
        result = dict(value)
    else:
        raise CompanyRegistryError("project.defaults must be an object")
    allowed = {
        "analysis_validation",
        "ocr_workers",
        "llm_workers",
        "purpose",
        "cross_company_upload_enabled",
        "only_mapped_invoices",
    }
    unsupported = sorted(set(result) - allowed)
    if unsupported:
        raise CompanyRegistryError(
            "project.defaults contains unsupported fields: " + ", ".join(unsupported)
        )
    result.setdefault("analysis_validation", "strict")
    result.setdefault("purpose", "production")
    result.setdefault("cross_company_upload_enabled", False)
    result.setdefault("only_mapped_invoices", False)
    return result


def normalize_input_settings(value: object) -> dict[str, str]:
    if value is None:
        result: dict[str, Any] = {}
    elif isinstance(value, dict):
        result = dict(value)
    else:
        raise CompanyRegistryError("project.input must be an object")
    allowed = {"usage_filename", "usage_column"}
    unsupported = sorted(set(result) - allowed)
    if unsupported:
        raise CompanyRegistryError("project.input contains unsupported fields: " + ", ".join(unsupported))
    normalized = {
        "usage_filename": str(result.get("usage_filename") or "用途确认信息.xlsx").strip(),
        "usage_column": str(result.get("usage_column") or "E").strip(),
    }
    if not all(normalized.values()):
        raise CompanyRegistryError("project.input filenames and column names must not be empty")
    return normalized


def resolve_company_config(selector: str) -> Path:
    name = str(selector or "").strip()
    if Path(name).name != name:
        raise CompanyRegistryError("Company config must be a filename without directories")
    if not name.lower().endswith(".json"):
        name += ".json"
    path = (project_root() / "config" / "companies" / name).resolve()
    if not path.is_file():
        raise CompanyRegistryError(f"Company config not found: {path}")
    return path


def choose_template(payload: dict[str, Any], company_name: str) -> str:
    selected = str(payload.get("template_company") or "").strip().lower()
    resolve_company_template(project_root(), selected, company_name)
    return selected


def resolve_target_accountbook_selector(accountbooks: dict[str, Any], selector: str) -> Any:
    normalized = str(selector or "").strip()
    if not normalized:
        raise CompanyRegistryError("Target accountbook must not be empty")
    matches = [
        profile
        for profile in accountbooks.values()
        if profile.enabled
        and normalized in {profile.key, profile.company_id, profile.name}
    ]
    if len(matches) != 1:
        raise CompanyRegistryError(f"Cannot uniquely resolve target accountbook: {selector}, matches={len(matches)}")
    target = matches[0]
    if not target.company_id:
        raise CompanyRegistryError(f"Target accountbook is missing company_id: {target.key}")
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a company dataset for a specific accounting month")
    parser.add_argument("company_config_name", help="Config filename under config/companies; .json is optional")
    parser.add_argument("month", help="Accounting month in YYYY-MM format")
    parser.add_argument(
        "target_accountbook",
        help="Explicit target company_id, accountbook key or exact company name",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config_path = resolve_company_config(args.company_config_name)
        month = normalize_month(args.month)
        payload = read_object(config_path)
        company = load_company_profile(config_path)
        company_key = company.key
        company_id = company.company_id
        company_name = company.name

        accountbooks = load_accountbooks((project_root() / "runtime/registry/accountbooks.json"))

        template_key = choose_template(payload, company_name)
        data_root = (project_root() / company.data_root).resolve()
        inbox_root = (project_root() / "data" / "inbox").resolve()
        try:
            data_root.relative_to(inbox_root)
        except ValueError as exc:
            raise CompanyRegistryError(f"Company dataset directory must be inside data/inbox: {data_root}") from exc
        month_root = (data_root / month).resolve()
        try:
            month_root.relative_to(data_root)
        except ValueError as exc:
            raise CompanyRegistryError(f"Month directory is outside the company dataset directory: {month_root}") from exc

        project_config_path = month_root / "project.json"
        project_payload = read_object(project_config_path) if project_config_path.is_file() else {}
        if project_payload:
            if project_payload.get("version") != 8:
                raise CompanyRegistryError(f"Month config version must be 8: {project_config_path}")
            existing_dataset = project_payload.get("dataset")
            if not isinstance(existing_dataset, dict):
                raise CompanyRegistryError("Existing v8 month config is missing an explicit project.dataset")
            if (
                str(existing_dataset.get("company_key") or "").strip() != company_key
                or str(existing_dataset.get("company_id") or "").strip() != company_id
                or str(existing_dataset.get("company_name") or "").strip() != company_name
            ):
                raise CompanyRegistryError(
                    "Existing month dataset identity does not match the company config; correct or remove the month config"
                )
        target_accountbook = resolve_target_accountbook_selector(accountbooks, args.target_accountbook)
        safe_defaults = normalize_month_defaults(project_payload.get("defaults"))
        safe_defaults["cross_company_upload_enabled"] = (
            target_accountbook.key != company_key
        )
        input_settings = normalize_input_settings(project_payload.get("input"))
        source_settings = normalize_source_settings(project_payload.get("sources"))
        execution_enabled_sources = [
            source for source in BUILT_IN_SOURCES if source_settings[source]["enabled"]
        ]
        normalized_project = {
            "version": 8,
            "month": month,
            "dataset": {
                "company_key": company_key,
                "company_id": company_id,
                "company_name": company_name,
            },
            "target": {
                "accountbook_key": target_accountbook.key,
                "company_id": target_accountbook.company_id,
                "company_name": target_accountbook.name,
            },
            "input": input_settings,
            "defaults": safe_defaults,
            "sources": source_settings,
        }

        write_json(project_config_path, normalized_project)
        completed = subprocess.run(
            [
                sys.executable,
                "-m", "kdzwy_receipt_uploader.commands.prepare_company_workspace",
                "--config",
                str(config_path),
                "--month",
                month,
            ],
            cwd=project_root(), env=process_environment(),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CompanyRegistryError(f"Failed to generate standard directories: {detail}")
        print(json.dumps({
            "status": "ok",
            "company_config": str(config_path),
            "company_id": company_id,
            "company_name": company_name,
            "company_key": company_key,
            "template_company": template_key,
            "dataset": {
                "company_key": company_key,
                "company_id": company_id,
                "company_name": company_name,
            },
            "target": {
                "accountbook_key": target_accountbook.key,
                "company_id": target_accountbook.company_id,
                "company_name": target_accountbook.name,
            },
            "month": month,
            "month_directory": str(month_root),
            "project_config": str(project_config_path),
            "sources": list(BUILT_IN_SOURCES),
            "execution_enabled_sources": execution_enabled_sources,
            "next": "Dataset and target saved to project.json. A different target enables cross_company_upload_enabled; setting it to false restores the dataset company as target. Add documents to input and configure source enabled and stage before running.",
        }, ensure_ascii=False, indent=2))
        return 0
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"Month initialization failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
