"""Create the data and runtime directory layout declared by one company config."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    dataset_from_company,
    load_company_jobs,
    load_company_profile,
    load_accountbooks,
    load_template_companies,
    normalize_month,
    resolve_target_accountbook,
    workspace_relative_path,
)
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES  # noqa: E402


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


def main() -> int:
    parser = argparse.ArgumentParser(description="根据公司配置自动创建 data 和 runtime 工作区")
    parser.add_argument("--config", type=Path, required=True, help="config/companies/company_<company_id>_<真实公司名>.json")
    parser.add_argument("--month", required=True, help="明确指定会计月份 YYYY-MM")
    args = parser.parse_args()

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

        templates = load_template_companies(ROOT / "config" / "template_companies.json")
        template = templates.get(template_key)
        if template is None or not template.enabled:
            raise CompanyRegistryError(f"模板公司不存在或未启用：{template_key}")
        template_index = ROOT / "templates" / template.directory / "index.json"
        if not template_index.is_file():
            raise CompanyRegistryError(f"模板缺少 index.json：{template_index}")

        accountbooks = load_accountbooks(ROOT / "runtime" / "registry" / "accountbooks.json")

        inbox_root = (ROOT / "data" / "inbox").resolve()
        dataset_root = ensure_inside(ROOT / dataset.data_root, inbox_root, "资料公司目录")
        month_root = ensure_inside(dataset_root / month, dataset_root, "月份目录")
        project_config_path = month_root / "project.json"
        if not project_config_path.is_file():
            raise CompanyRegistryError(
                f"月份配置不存在：{project_config_path}；请先执行 initialize_month.bat"
            )
        project_payload = json.loads(project_config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(project_payload, dict):
            raise CompanyRegistryError(f"月份配置必须是 JSON 对象：{project_config_path}")
        jobs = load_company_jobs(project_config_path, company)
        if any(job.month != month for job in jobs):
            raise CompanyRegistryError(f"月份配置与命令月份不一致：{project_config_path}")
        target_keys = {job.accountbook for job in jobs}
        if len(target_keys) != 1:
            raise CompanyRegistryError(f"同一月份的业务必须使用同一个显式目标账套：{sorted(target_keys)}")
        accountbook = resolve_target_accountbook(jobs[0], accountbooks)
        login_account = accountbook.login_account or "default"
        source_execution_flags = {job.source: job.enabled for job in jobs}

        workspaces_root = (ROOT / "workspaces").resolve()
        workspace_root = ensure_inside(
            ROOT / workspace_relative_path(login_account, accountbook.key, source_company_key, safe_filename_part(month)),
            workspaces_root,
            "账套工作区",
        )
        workspace_generated = workspace_root / "generated"

        created_directories: list[str] = []
        for source in BUILT_IN_SOURCES:
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

        execution_enabled_sources = [
            source for source in BUILT_IN_SOURCES if source_execution_flags[source]
        ]
        print(json.dumps({
            "status": "ok",
            "company_key": company_key,
            "source_company_key": source_company_key,
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
            "project_config": str(project_config_path),
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, CompanyRegistryError) as exc:
        print(f"工作区配置错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
