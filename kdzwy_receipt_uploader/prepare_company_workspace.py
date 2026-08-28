"""Create the data and runtime directory layout declared by one company config."""
from __future__ import annotations

import argparse
import configparser
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    load_accountbooks,
    load_datasets,
    load_template_companies,
)
from kdzwy_receipt_uploader.source_profile import normalize_source_key  # noqa: E402


def required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CompanyRegistryError(f"公司配置缺少 {label}")
    return text


def safe_filename_part(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "month"


def ensure_inside(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise CompanyRegistryError(f"{label} 必须位于 {parent} 内：{resolved}") from exc
    return resolved


def write_month_config(path: Path, dataset_key: str, month: str, options: dict[str, object]) -> None:
    parser = configparser.ConfigParser()
    parser["processing"] = {
        "company": dataset_key,
        "month": month,
        "enabled": "true",
        "income_cost_filename": str(options.get("income_cost_filename") or "收入成本表.xlsx"),
        "usage_filename": str(options.get("usage_filename") or "用途确认信息.xlsx"),
        "usage_column": str(options.get("usage_column") or "E"),
        "output_dirname": "maps",
    }
    with path.open("w", encoding="utf-8", newline="") as stream:
        parser.write(stream)


def main() -> int:
    parser = argparse.ArgumentParser(description="根据公司配置自动创建 data 和 runtime 工作区")
    parser.add_argument("--config", type=Path, required=True, help="config/companies/<company_key>.json")
    args = parser.parse_args()

    try:
        config_path = args.config.resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CompanyRegistryError("公司配置必须是 JSON 对象")
        if not bool(payload.get("enabled", True)):
            print(f"公司配置未启用，未创建工作区：{config_path}")
            return 0

        company_key = required_text(payload.get("company_key"), "company_key")
        dataset_key = required_text(payload.get("dataset"), "dataset")
        template_key = required_text(payload.get("template_company"), "template_company")
        month = required_text(payload.get("month"), "month")
        sources = payload.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise CompanyRegistryError("公司配置缺少 sources")

        datasets = load_datasets(ROOT / "config" / "datasets.json")
        dataset = datasets.get(dataset_key)
        if dataset is None or not dataset.enabled:
            raise CompanyRegistryError(f"数据集不存在或未启用：{dataset_key}")
        templates = load_template_companies(ROOT / "config" / "template_companies.json")
        template = templates.get(template_key)
        if template is None or not template.enabled:
            raise CompanyRegistryError(f"模板公司不存在或未启用：{template_key}")
        template_index = ROOT / "templates" / template.directory / "index.json"
        if not template_index.is_file():
            raise CompanyRegistryError(f"模板缺少 index.json：{template_index}")

        accountbooks = load_accountbooks(ROOT / "config" / "accountbooks.json")
        accountbook = accountbooks.get(company_key)
        if accountbook is None or not accountbook.enabled:
            raise CompanyRegistryError(f"账套不存在或未启用：{company_key}")
        login_account = accountbook.login_account or "default"

        inbox_root = (ROOT / "data" / "inbox").resolve()
        dataset_root = ensure_inside(ROOT / dataset.data_root, inbox_root, "dataset.data_root")
        month_root = ensure_inside(dataset_root / month, dataset_root, "月份目录")
        month_root.mkdir(parents=True, exist_ok=True)

        workspaces_root = (ROOT / "workspaces").resolve()
        workspace_root = ensure_inside(
            workspaces_root / login_account / company_key / dataset_key / safe_filename_part(month),
            workspaces_root,
            "账套工作区",
        )
        workspace_generated = workspace_root / "generated"

        created_directories: list[str] = []
        canonical_sources: list[str] = []
        for source_name in sources:
            source = normalize_source_key(str(source_name))
            if source not in {"sales", "purchase", "bank", "misc"}:
                raise CompanyRegistryError(f"不支持的 source：{source_name}")
            if source in canonical_sources:
                continue
            canonical_sources.append(source)
            directories = (
                month_root / "input" / source,
                workspace_generated / "maps" / source,
                workspace_generated / "receipts" / source,
                workspace_generated / "ocr" / source,
                workspace_root / "state" / source,
                workspace_root / "logs" / source,
            )
            for directory in directories:
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    created_directories.append(str(directory))

        existing_configs = sorted(month_root.glob("*.conf"))
        month_config_path = existing_configs[0] if existing_configs else month_root / f"{dataset_key}_{safe_filename_part(month)}.conf"
        created_config = False
        if not existing_configs:
            options = payload.get("month_config") or {}
            if not isinstance(options, dict):
                raise CompanyRegistryError("month_config 必须是对象")
            write_month_config(month_config_path, dataset_key, month, options)
            created_config = True

        print(json.dumps({
            "status": "ok",
            "company_key": company_key,
            "dataset": dataset_key,
            "template_company": template_key,
            "month": month,
            "month_directory": str(month_root),
            "workspace_directory": str(workspace_root),
            "sources": canonical_sources,
            "created_directories": created_directories,
            "month_config": str(month_config_path),
            "month_config_created": created_config,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, CompanyRegistryError) as exc:
        print(f"工作区配置错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
