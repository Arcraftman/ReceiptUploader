"""Initialize one company's standard data project for a specific accounting month."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    load_accountbooks,
    load_company_profile,
    load_template_companies,
    normalize_month,
)
from kdzwy_receipt_uploader.source_profile import BUILT_IN_SOURCES  # noqa: E402

ACCOUNTBOOKS_PATH = ROOT / "runtime" / "registry" / "accountbooks.json"


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanyRegistryError(f"无法读取配置 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"配置必须是 JSON 对象：{path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_source_settings(value: object) -> dict[str, dict[str, Any]]:
    """Declare every built-in source without changing its execution authorization."""
    if value is None:
        existing: dict[str, Any] = {}
    elif isinstance(value, dict):
        existing = value
    else:
        raise CompanyRegistryError("project.sources 必须是对象")
    unknown = sorted(str(key) for key in existing if key not in BUILT_IN_SOURCES)
    if unknown:
        raise CompanyRegistryError(f"project.sources 包含非标准业务目录：{', '.join(unknown)}")

    result: dict[str, dict[str, Any]] = {}
    allowed = {
        "enabled",
        "mode",
        "analysis_stage",
        "analysis_validation",
        "ocr_workers",
        "llm_workers",
        "preload_items",
        "purpose",
        "allow_cross_entity",
        "only_mapped_invoices",
    }
    for source in BUILT_IN_SOURCES:
        current = existing.get(source)
        if isinstance(current, dict):
            settings = dict(current)
        elif current is None:
            settings = {}
        else:
            raise CompanyRegistryError(f"sources.{source} 必须是对象")
        unsupported = sorted(set(settings) - allowed)
        if unsupported:
            raise CompanyRegistryError(
                f"sources.{source} 包含不支持的字段：{', '.join(unsupported)}"
            )
        enabled = settings.get("enabled", False)
        if not isinstance(enabled, bool):
            raise CompanyRegistryError(f"sources.{source}.enabled 必须是 JSON 布尔值 true 或 false")
        settings["enabled"] = enabled
        result[source] = settings
    return result


def normalize_month_defaults(value: object) -> dict[str, Any]:
    if value is None:
        result: dict[str, Any] = {}
    elif isinstance(value, dict):
        result = dict(value)
    else:
        raise CompanyRegistryError("project.defaults 必须是对象")
    allowed = {
        "mode",
        "analysis_stage",
        "analysis_validation",
        "ocr_workers",
        "llm_workers",
        "preload_items",
        "purpose",
        "allow_cross_entity",
        "only_mapped_invoices",
    }
    unsupported = sorted(set(result) - allowed)
    if unsupported:
        raise CompanyRegistryError(
            "project.defaults 包含不支持的字段：" + ", ".join(unsupported)
        )
    result.setdefault("mode", "analysis-only")
    result.setdefault("analysis_stage", "ocr")
    result.setdefault("analysis_validation", "strict")
    result.setdefault("preload_items", False)
    result.setdefault("purpose", "production")
    result.setdefault("allow_cross_entity", False)
    result.setdefault("only_mapped_invoices", False)
    return result


def normalize_input_settings(value: object) -> dict[str, str]:
    if value is None:
        result: dict[str, Any] = {}
    elif isinstance(value, dict):
        result = dict(value)
    else:
        raise CompanyRegistryError("project.input 必须是对象")
    allowed = {"income_cost_filename", "usage_filename", "usage_column"}
    unsupported = sorted(set(result) - allowed)
    if unsupported:
        raise CompanyRegistryError("project.input 包含不支持的字段：" + ", ".join(unsupported))
    normalized = {
        "income_cost_filename": str(result.get("income_cost_filename") or "收入成本表.xlsx").strip(),
        "usage_filename": str(result.get("usage_filename") or "用途确认信息.xlsx").strip(),
        "usage_column": str(result.get("usage_column") or "E").strip(),
    }
    if not all(normalized.values()):
        raise CompanyRegistryError("project.input 的文件名和列名不能为空")
    return normalized


def resolve_company_config(selector: str) -> Path:
    name = str(selector or "").strip()
    if Path(name).name != name:
        raise CompanyRegistryError("公司配置参数只能是文件名，不能包含目录")
    if not name.lower().endswith(".json"):
        name += ".json"
    path = (ROOT / "config" / "companies" / name).resolve()
    if not path.is_file():
        raise CompanyRegistryError(f"公司配置不存在：{path}")
    return path


def choose_template(payload: dict[str, Any], templates_path: Path) -> str:
    templates = load_template_companies(templates_path)
    selected = str(payload.get("template_company") or "").strip().lower()
    template = templates.get(selected)
    if template is None or not template.enabled:
        available = ", ".join(sorted(key for key, item in templates.items() if item.enabled))
        raise CompanyRegistryError(
            "公司尚未准备可用模板；请通过 commands/start.bat 的 "
            f"month SOURCE_COMPANY_ID YYYY-MM [TARGET_COMPANY_ID] 自动初始化。当前可用模板：{available or '无'}"
        )
    if not (ROOT / "templates" / template.directory / "index.json").is_file():
        raise CompanyRegistryError(f"模板缺少 index.json：templates/{template.directory}")
    return selected


def resolve_target_accountbook_selector(accountbooks: dict[str, Any], selector: str) -> Any:
    normalized = str(selector or "").strip()
    if not normalized:
        raise CompanyRegistryError("目标账套选择不能为空")
    matches = [
        profile
        for profile in accountbooks.values()
        if profile.enabled
        and normalized in {profile.key, profile.company_id, profile.name}
    ]
    if len(matches) != 1:
        raise CompanyRegistryError(f"无法唯一匹配目标账套：{selector}，matches={len(matches)}")
    target = matches[0]
    if not target.company_id:
        raise CompanyRegistryError(f"目标账套缺少 company_id：{target.key}")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为一家公司初始化指定月份的标准资料项目")
    parser.add_argument("company_config_name", help="config/companies 下的配置文件名，可省略 .json")
    parser.add_argument("month", help="会计月份，严格使用 YYYY-MM")
    parser.add_argument(
        "target_accountbook",
        nargs="?",
        default="",
        help="目标账套的 company_id、accountbook key 或精确公司名；省略时新项目默认与资料公司相同",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config_path = resolve_company_config(args.company_config_name)
        month = normalize_month(args.month)
        payload = read_object(config_path)
        company = load_company_profile(config_path)
        company_key = company.key
        company_id = company.company_id
        company_name = company.name

        accountbooks = load_accountbooks(ACCOUNTBOOKS_PATH)

        template_key = choose_template(payload, ROOT / "config" / "template_companies.json")
        data_root = (ROOT / company.data_root).resolve()
        inbox_root = (ROOT / "data" / "inbox").resolve()
        try:
            data_root.relative_to(inbox_root)
        except ValueError as exc:
            raise CompanyRegistryError(f"公司资料目录必须位于 data/inbox：{data_root}") from exc
        month_root = (data_root / month).resolve()
        try:
            month_root.relative_to(data_root)
        except ValueError as exc:
            raise CompanyRegistryError(f"月份目录越过公司资料目录：{month_root}") from exc

        project_config_path = month_root / "project.json"
        project_payload = read_object(project_config_path) if project_config_path.is_file() else {}
        if project_payload and project_payload.get("version") != 5:
            raise CompanyRegistryError(f"月份配置版本必须为 5：{project_config_path}")
        existing_target: dict[str, Any] = {}
        if project_payload:
            raw_existing_target = project_payload.get("target")
            if not isinstance(raw_existing_target, dict):
                if not args.target_accountbook:
                    raise CompanyRegistryError(
                        "已有 v5 月份配置缺少显式 project.target；请删除无效配置后重新创建，或明确指定目标账套"
                    )
            else:
                existing_target = raw_existing_target
            if not args.target_accountbook:
                existing_key = str(existing_target.get("accountbook_key") or "").strip()
                existing_id = str(existing_target.get("company_id") or "").strip()
                existing_name = str(existing_target.get("company_name") or "").strip()
                if not existing_key or not existing_id or not existing_name:
                    raise CompanyRegistryError(
                        "已有 v5 月份配置的 target 必须包含 accountbook_key/company_id/company_name"
                    )
        target_selector = str(
            args.target_accountbook
            or existing_target.get("accountbook_key")
            or company_key
        )
        target_accountbook = resolve_target_accountbook_selector(accountbooks, target_selector)
        if project_payload and not args.target_accountbook:
            if (
                str(existing_target.get("company_id")) != target_accountbook.company_id
                or str(existing_target.get("company_name")) != target_accountbook.name
            ):
                raise CompanyRegistryError(
                    "已有月份配置的 target 身份与 accountbooks.json 不一致；请明确指定目标账套后重新初始化"
                )
        safe_defaults = normalize_month_defaults(project_payload.get("defaults"))
        input_settings = normalize_input_settings(project_payload.get("input"))
        source_settings = normalize_source_settings(project_payload.get("sources"))
        execution_enabled_sources = [
            source for source in BUILT_IN_SOURCES if source_settings[source]["enabled"]
        ]
        normalized_project = {
            "version": 5,
            "company_key": company_key,
            "company_id": company_id,
            "company_name": company_name,
            "month": month,
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
                str(ROOT / "scripts" / "commands" / "prepare_company_workspace.py"),
                "--config",
                str(config_path),
                "--month",
                month,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CompanyRegistryError(f"标准目录生成失败：{detail}")
        print(json.dumps({
            "status": "ok",
            "company_config": str(config_path),
            "company_id": company_id,
            "company_name": company_name,
            "company_key": company_key,
            "template_company": template_key,
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
            "next": "目标账套已显式写入 project.json；把资料放入 input，并设置 mode、analysis_stage 和 sources 后运行。",
        }, ensure_ascii=False, indent=2))
        return 0
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"月份项目初始化失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
