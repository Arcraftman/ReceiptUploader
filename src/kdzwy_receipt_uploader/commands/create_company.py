from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


from kdzwy_receipt_uploader.project_runtime import project_root

from kdzwy_receipt_uploader.company_registry import company_config_filename
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES


SOURCES = BUILT_IN_SOURCES


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Top-level JSON must be an object: {path}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create reusable company templates from an accountbook company name")
    parser.add_argument("--name", required=True, help="Exact full company name")
    parser.add_argument("--base-template", default=None, help="Source template key; defaults to registry default_base_template")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    company_name = args.name.strip()
    if not company_name:
        raise SystemExit("--name must not be empty")
    accountbooks_path = project_root() / "runtime" / "registry" / "accountbooks.json"
    accountbooks_payload = read_json_object(accountbooks_path)
    accountbooks = accountbooks_payload.get("accountbooks")
    if not isinstance(accountbooks, list):
        raise SystemExit(f"Invalid accountbook configuration: {accountbooks_path}")
    matches = [
        record for record in accountbooks
        if isinstance(record, dict)
        and str(record.get("company_name") or record.get("companyName") or record.get("name") or "").strip() == company_name
    ]
    if len(matches) != 1:
        raise SystemExit(f"Cannot uniquely resolve accountbook by full company name: name={company_name}, matches={len(matches)}; run discover and login first")
    company_id = str(matches[0].get("company_id") or "").strip()
    if not company_id:
        raise SystemExit(f"Accountbook is missing company_id: {company_name}; run discover again")
    company_key = f"company_{company_id.lower()}"
    if str(matches[0].get("key") or "").strip().lower() != company_key:
        raise SystemExit(f"Invalid accountbook key; expected {company_key}; run discover again")

    target_template_dir = project_root() / "templates" / company_key
    company_config_path = project_root() / "config" / "companies" / company_config_filename(company_id, company_name)
    registry_path = project_root() / "config" / "template_companies.json"
    if company_config_path.exists():
        raise SystemExit(f"Company config already exists; not overwritten: {company_config_path}")
    registry = read_json_object(registry_path)
    base_template_key = str(args.base_template or registry.get("default_base_template") or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", base_template_key):
        raise SystemExit(f"Invalid --base-template: {base_template_key}")

    records = registry.get("template_companies")
    if not isinstance(records, list):
        raise SystemExit(f"Invalid template registry: {registry_path}")
    base_matches = [
        record
        for record in records
        if isinstance(record, dict)
        and str(record.get("key") or "").strip().lower() == base_template_key
        and bool(record.get("enabled", True))
    ]
    if len(base_matches) != 1:
        available = ", ".join(
            sorted(
                str(record.get("key"))
                for record in records
                if isinstance(record, dict) and bool(record.get("enabled", True)) and record.get("key")
            )
        )
        raise SystemExit(f"Base template missing or disabled: {base_template_key}; available templates: {available or 'none'}")
    source_directory = str(base_matches[0].get("directory") or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", source_directory):
        raise SystemExit(f"Invalid base template directory: {source_directory}")
    source_template_dir = project_root() / "templates" / source_directory
    if not source_template_dir.is_dir():
        raise SystemExit(f"Template source does not exist: {source_template_dir}")
    if target_template_dir.exists():
        raise SystemExit(f"Target template directory exists; not overwritten: {target_template_dir}")

    if any(str(record.get("key")) == company_key for record in records if isinstance(record, dict)):
        raise SystemExit(f"Template company already registered; not modified: {company_key}")

    shutil.copytree(source_template_dir, target_template_dir)
    for source in SOURCES:
        (target_template_dir / source).mkdir(parents=True, exist_ok=True)

    index_path = target_template_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    index["description"] = f"{company_name}业务模板；基础模板={base_template_key}，与目标账套动态目录独立。"
    write_json(index_path, index)

    company_config = {
        "version": 3,
        "company_key": company_key,
        "company_id": company_id,
        "company_name": company_name,
        "template_company": company_key,
    }
    write_json(company_config_path, company_config)

    records.append({
        "key": company_key,
        "name": company_name,
        "directory": company_key,
        "enabled": True,
    })
    write_json(registry_path, registry)

    print(json.dumps({
        "status": "ok",
        "company_key": company_key,
        "company_name": company_name,
        "company_config": str(company_config_path),
        "base_template": base_template_key,
        "template_directory": str(target_template_dir),
        "next": "Run start month COMPANY_CONFIG_NAME YYYY-MM TARGET to create the four source directories.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
