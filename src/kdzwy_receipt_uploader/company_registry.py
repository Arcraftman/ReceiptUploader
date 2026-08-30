from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .source_profile import BUILT_IN_SOURCES, normalize_source_key


class CompanyRegistryError(ValueError):
    pass


MONTH_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def normalize_month(value: object) -> str:
    month = str(value or "").strip()
    if not MONTH_PATTERN.fullmatch(month):
        raise CompanyRegistryError("月份必须严格使用 YYYY-MM，例如 2026-08")
    return month


def safe_company_filename_part(value: str) -> str:
    result = re.sub(r'[<>:"/\\|?*]+', "_", str(value or "")).strip(" .")
    if not result:
        raise CompanyRegistryError("真实公司名不能生成有效文件名")
    return result


def company_config_filename(company_id: str, company_name: str) -> str:
    normalized_id = str(company_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized_id):
        raise CompanyRegistryError(f"company_id 不能生成有效文件名：{company_id}")
    return f"company_{normalized_id}_{safe_company_filename_part(company_name)}.json"


def workspace_relative_path(login_account: str, accountbook_key: str, source_company_key: str, month: str) -> Path:
    parts = tuple(str(value or "").strip() for value in (login_account, accountbook_key, source_company_key, month))
    if any(not part or Path(part).name != part or part in {".", ".."} for part in parts):
        raise CompanyRegistryError(f"工作区标识不能包含路径字符：{parts}")
    login, accountbook, source_company, period = parts
    root = Path("workspaces") / login / accountbook
    if source_company != accountbook:
        root /= f"from_{source_company}"
    return root / period


@dataclass(frozen=True)
class AccountbookProfile:
    key: str
    name: str
    session_file: str
    login_account: str = "default"
    company_id: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class DatasetProfile:
    key: str
    entity_name: str
    data_root: str
    enabled: bool = True


@dataclass(frozen=True)
class TemplateCompanyProfile:
    key: str
    name: str
    directory: str
    enabled: bool = True


@dataclass(frozen=True)
class CompanyProfile:
    key: str
    company_id: str
    name: str
    template_company: str
    data_root: str


@dataclass(frozen=True)
class CompanyJob:
    accountbook: str
    dataset: str
    month: str
    mode: str = "analysis-only"
    source: str = "all"
    purpose: str = "production"
    allow_cross_entity: bool = False
    enabled: bool = True
    overrides: dict[str, Any] = field(default_factory=dict)
    template_company: str = ""
    target_company_id: str = ""
    target_company_name: str = ""
    input_config: dict[str, str] = field(default_factory=dict)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanyRegistryError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"配置必须是 JSON 对象：{path}")
    return value


def _required_text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CompanyRegistryError(f"缺少 {label}")
    return result


def _strict_bool(value: Any, label: str, *, default: bool = True) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CompanyRegistryError(f"{label} 必须是 JSON 布尔值 true 或 false")
    return value


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise CompanyRegistryError(f"{label} 包含不支持的字段：{', '.join(unknown)}")


def load_accountbooks(path: Path) -> dict[str, AccountbookProfile]:
    payload = _read_object(path)
    if payload.get("version") != 2:
        raise CompanyRegistryError(f"账套注册表版本必须为 2：{path}")
    _reject_unknown_fields(payload, {"version", "accountbooks"}, "accountbooks.json")
    rows = payload.get("accountbooks")
    if not isinstance(rows, list):
        raise CompanyRegistryError("accountbooks.json.accountbooks 必须是数组")
    result: dict[str, AccountbookProfile] = {}
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"accountbooks[{index}] 必须是对象")
        _reject_unknown_fields(
            row,
            {"key", "name", "company_id", "login_account", "enabled", "session_file"},
            f"accountbooks[{index}]",
        )
        key = _required_text(row.get("key"), f"accountbooks[{index}].key")
        name = _required_text(row.get("name"), f"accountbooks[{index}].name")
        login_account = str(row.get("login_account") or "default").strip()
        identity = (login_account, name)
        if key in result or identity in identities:
            raise CompanyRegistryError(f"账套 key 或账号内公司名称重复：{key}/{login_account}/{name}")
        result[key] = AccountbookProfile(
            key=key,
            name=name,
            session_file=_required_text(row.get("session_file"), f"{key}.session_file"),
            login_account=login_account,
            company_id=_required_text(row.get("company_id"), f"{key}.company_id"),
            enabled=_strict_bool(row.get("enabled"), f"{key}.enabled"),
        )
        identities.add(identity)
    if not result:
        raise CompanyRegistryError("账套注册表为空")
    return result


def dataset_from_company(company: CompanyProfile) -> DatasetProfile:
    return DatasetProfile(
        key=company.key,
        entity_name=company.name,
        data_root=company.data_root,
    )


def load_template_companies(path: Path) -> dict[str, TemplateCompanyProfile]:
    payload = _read_object(path)
    if payload.get("version") != 2:
        raise CompanyRegistryError(f"模板注册表版本必须为 2：{path}")
    _reject_unknown_fields(
        payload,
        {"version", "default_base_template", "template_companies"},
        "template_companies.json",
    )
    rows = payload.get("template_companies")
    if not isinstance(rows, list):
        raise CompanyRegistryError("template_companies.json.template_companies 必须是数组")
    result: dict[str, TemplateCompanyProfile] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"template_companies[{index}] 必须是对象")
        _reject_unknown_fields(
            row,
            {"key", "name", "directory", "enabled"},
            f"template_companies[{index}]",
        )
        key = _required_text(row.get("key"), f"template_companies[{index}].key")
        if key in result:
            raise CompanyRegistryError(f"模板公司 key 重复：{key}")
        directory = _required_text(row.get("directory"), f"{key}.directory")
        candidate = Path(directory)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise CompanyRegistryError(f"模板公司目录必须是 templates 下的相对路径：{directory}")
        result[key] = TemplateCompanyProfile(
            key=key,
            name=_required_text(row.get("name"), f"{key}.name"),
            directory=directory,
            enabled=_strict_bool(row.get("enabled"), f"{key}.enabled"),
        )
    if not result:
        raise CompanyRegistryError("模板公司注册表为空")
    return result


def load_company_profile(path: Path) -> CompanyProfile:
    payload = _read_object(path)
    _reject_unknown_fields(
        payload,
        {"version", "company_key", "company_id", "company_name", "template_company"},
        f"公司配置 {path.name}",
    )
    company_id = _required_text(payload.get("company_id"), "company_id")
    company_name = _required_text(payload.get("company_name"), "company_name")
    expected_name = company_config_filename(company_id, company_name)
    if path.name != expected_name:
        raise CompanyRegistryError(f"公司配置文件名不符合统一规则：应为 {expected_name}")
    if payload.get("version") != 3:
        raise CompanyRegistryError(f"公司配置版本必须为 3：{path}")
    company_key = _required_text(payload.get("company_key"), "company_key")
    expected_key = f"company_{company_id.lower()}"
    if company_key != expected_key:
        raise CompanyRegistryError(f"company_key 必须统一为 {expected_key}：{path}")
    return CompanyProfile(
        key=company_key,
        company_id=company_id,
        name=company_name,
        template_company=_required_text(payload.get("template_company"), "template_company"),
        data_root=(Path("data") / "inbox" / path.stem).as_posix(),
    )


def load_company_jobs(path: Path, company: CompanyProfile) -> list[CompanyJob]:
    """Load one company's one-month project.json as the only run configuration."""
    payload = _read_object(path)
    if payload.get("version") != 5:
        raise CompanyRegistryError(f"月份配置版本必须为 5：{path}")
    _reject_unknown_fields(
        payload,
        {"version", "company_key", "company_id", "company_name", "month", "target", "input", "defaults", "sources"},
        "project.json",
    )
    project_company_key = _required_text(payload.get("company_key"), "project.company_key")
    project_company_id = _required_text(payload.get("company_id"), "project.company_id")
    project_company_name = _required_text(payload.get("company_name"), "project.company_name")
    month = normalize_month(_required_text(payload.get("month"), "project.month"))
    if (
        project_company_key != company.key
        or project_company_id != company.company_id
        or project_company_name != company.name
    ):
        raise CompanyRegistryError(
            f"月份配置与公司身份不一致：{path}"
        )
    target = payload.get("target")
    if not isinstance(target, dict):
        raise CompanyRegistryError("project.target 必须是显式目标账套对象")
    _reject_unknown_fields(target, {"accountbook_key", "company_id", "company_name"}, "project.target")
    target_accountbook = _required_text(target.get("accountbook_key"), "project.target.accountbook_key")
    target_company_id = _required_text(target.get("company_id"), "project.target.company_id")
    target_company_name = _required_text(target.get("company_name"), "project.target.company_name")
    input_config = payload.get("input")
    if not isinstance(input_config, dict):
        raise CompanyRegistryError("project.input 必须是对象")
    _reject_unknown_fields(
        input_config,
        {"income_cost_filename", "usage_filename", "usage_column"},
        "project.input",
    )
    normalized_input = {
        "income_cost_filename": _required_text(input_config.get("income_cost_filename"), "project.input.income_cost_filename"),
        "usage_filename": _required_text(input_config.get("usage_filename"), "project.input.usage_filename"),
        "usage_column": _required_text(input_config.get("usage_column"), "project.input.usage_column"),
    }
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise CompanyRegistryError("project.defaults 必须是对象")
    configurable_fields = {
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
    _reject_unknown_fields(defaults, configurable_fields, "project.defaults")
    default_mode = _required_text(defaults.get("mode"), "project.defaults.mode")
    _required_text(defaults.get("analysis_stage"), "project.defaults.analysis_stage")
    for label in ("allow_cross_entity", "only_mapped_invoices"):
        if label in defaults:
            _strict_bool(defaults[label], f"project.defaults.{label}", default=False)

    def effective_source_settings(row: dict[str, Any], label: str) -> dict[str, Any]:
        _reject_unknown_fields(row, {"enabled", *configurable_fields}, label)
        for boolean_key in ("allow_cross_entity", "only_mapped_invoices"):
            if boolean_key in row:
                _strict_bool(row[boolean_key], f"{label}.{boolean_key}", default=False)
        result: dict[str, Any] = {}
        for key in (
            "analysis_stage",
            "analysis_validation",
            "ocr_workers",
            "llm_workers",
            "preload_items",
            "only_mapped_invoices",
        ):
            if key in defaults:
                result[key] = copy.deepcopy(defaults[key])
            if key in row:
                result[key] = copy.deepcopy(row[key])
        preload_value = result.get("preload_items", False)
        if preload_value is True:
            result["preload_items"] = "once"
        elif preload_value is False or preload_value is None:
            result["preload_items"] = False
        elif str(preload_value).strip().lower() in {"once", "auto"}:
            result["preload_items"] = str(preload_value).strip().lower()
        else:
            raise CompanyRegistryError('preload_items 只支持 false、"once" 或 "auto"')
        return result

    sources = payload.get("sources")
    if not isinstance(sources, dict):
        raise CompanyRegistryError("project.sources 必须是对象")
    if set(sources) != set(BUILT_IN_SOURCES):
        raise CompanyRegistryError("project.sources 必须固定包含 sales、purchase、bank、misc")
    result: list[CompanyJob] = []
    for source in BUILT_IN_SOURCES:
        row = sources[source]
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"sources.{source} 必须是对象")
        job_enabled = row.get("enabled", False)
        if not isinstance(job_enabled, bool):
            raise CompanyRegistryError(f"sources.{source}.enabled 必须是 JSON 布尔值 true 或 false")
        overrides = effective_source_settings(row, f"sources.{source}")
        result.append(CompanyJob(
            accountbook=target_accountbook,
            dataset=company.key,
            month=month,
            template_company=company.template_company,
            mode=str(row.get("mode", default_mode)),
            source=source,
            purpose=str(row.get("purpose", defaults.get("purpose", "production"))),
            allow_cross_entity=_strict_bool(
                row.get("allow_cross_entity", defaults.get("allow_cross_entity")),
                f"sources.{source}.allow_cross_entity",
                default=False,
            ),
            enabled=job_enabled,
            overrides=overrides,
            target_company_id=target_company_id,
            target_company_name=target_company_name,
            input_config=normalized_input,
        ))
    return result


def resolve_target_accountbook(
    job: CompanyJob,
    accountbooks: dict[str, AccountbookProfile],
) -> AccountbookProfile:
    """Resolve and verify the explicit target declared by a month project."""
    accountbook = accountbooks.get(job.accountbook)
    if accountbook is None or not accountbook.enabled:
        raise CompanyRegistryError(f"目标账套不存在或未启用：{job.accountbook}")
    if not job.target_company_id or not job.target_company_name:
        raise CompanyRegistryError("月份任务缺少显式 target.company_id 或 target.company_name")
    if not accountbook.company_id:
        raise CompanyRegistryError(f"目标账套缺少 company_id：{accountbook.key}")
    if accountbook.company_id != job.target_company_id:
        raise CompanyRegistryError(
            f"目标账套 company_id 不一致：project={job.target_company_id}，accountbooks={accountbook.company_id}"
        )
    if accountbook.name != job.target_company_name:
        raise CompanyRegistryError(
            f"目标账套公司名不一致：project={job.target_company_name}，accountbooks={accountbook.name}"
        )
    return accountbook


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


DEFAULT_PIPELINE_PATHS = {
    "month_dir": "data/inbox/{company}/{month}",
    "input_dir": "data/inbox/{company}/{month}/input",
    "map_file": "{workspace_root}/generated/maps/{source}/xlsx_pdf_map.json",
    "sales_map_file": "{workspace_root}/generated/maps/{source}/sales_map.json",
    "sales_map_report_file": "{workspace_root}/generated/maps/{source}/sales_map.report.json",
    "purchase_map_file": "{workspace_root}/generated/maps/{source}/purchase_map.json",
    "purchase_map_report_file": "{workspace_root}/generated/maps/{source}/purchase_map.report.json",
    "bank_split_config_file": "data/inbox/{company}/{month}/input/bank/bank_split.json",
    "bank_split_output_dir": "{workspace_root}/generated/bank_receipts",
    "bank_split_report_file": "{workspace_root}/generated/bank_receipts/split.report.json",
    "item_class_map_file": "{workspace_root}/generated/maps/{source}/item_class_maps.json",
    "upload_map_file": "{workspace_root}/generated/maps/{source}/upload_pdf_map.json",
    "receipt_dir": "{workspace_root}/generated/receipts/{source}",
    "receipts_ocr_dir": "{workspace_root}/generated/ocr/{source}",
    "preupload_review_file": "{workspace_root}/generated/maps/{source}/preupload_review.report.json",
}


def load_pipeline_defaults(path: Path) -> dict[str, Any]:
    payload = _read_object(path)
    if payload.get("version") != 2:
        raise CompanyRegistryError(f"流水线技术配置版本必须为 2：{path}")
    _reject_unknown_fields(payload, {"version", "ocr_workers", "llm_workers", "llm"}, "pipeline.defaults.json")
    for key in ("ocr_workers", "llm_workers"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise CompanyRegistryError(f"pipeline.defaults.json.{key} 必须是正整数")
    llm = payload.get("llm")
    if not isinstance(llm, dict):
        raise CompanyRegistryError("pipeline.defaults.json.llm 必须是对象")
    _reject_unknown_fields(
        llm,
        {"provider_name", "model", "endpoint", "api_key_env", "enable_thinking", "timeout_seconds"},
        "pipeline.defaults.json.llm",
    )
    for key in ("provider_name", "model", "endpoint", "api_key_env"):
        _required_text(llm.get(key), f"pipeline.defaults.json.llm.{key}")
    _strict_bool(llm.get("enable_thinking"), "pipeline.defaults.json.llm.enable_thinking", default=False)
    timeout = llm.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise CompanyRegistryError("pipeline.defaults.json.llm.timeout_seconds 必须是正整数")
    return copy.deepcopy(payload)


def build_job_settings(defaults: dict[str, Any], accountbook: AccountbookProfile, dataset: DatasetProfile, job: CompanyJob) -> dict[str, Any]:
    if job.mode not in {"analysis-only", "prepare", "dry-run", "confirm"}:
        raise CompanyRegistryError(f"不支持的运行模式：{job.mode}")
    if normalize_source_key(job.source) not in {"sales", "purchase", "bank", "misc", "all"}:
        raise CompanyRegistryError(f"不支持的责任链来源：{job.source}")
    settings = deep_merge(defaults, job.overrides)
    settings.pop("version", None)
    settings.update({
        "accountbook_source": "live",
        "item_source_columns": [],
        "voucher_defaults": {},
        "entry_defaults": [],
        "pdf_folders": list(BUILT_IN_SOURCES),
        "generate_overwrite": True,
        "paths": copy.deepcopy(DEFAULT_PIPELINE_PATHS),
        "input": copy.deepcopy(job.input_config),
    })
    analysis_stage = str(settings.get("analysis_stage", "ocr"))
    if analysis_stage not in {"ocr", "llm", "existing", "all"}:
        raise CompanyRegistryError(f"不支持的分析阶段：{analysis_stage}")
    analysis_validation = str(settings.get("analysis_validation", "strict")).strip().lower()
    if analysis_validation not in {"strict", "relaxed"}:
        raise CompanyRegistryError('analysis_validation 只支持 "strict" 或 "relaxed"')
    settings["analysis_validation"] = analysis_validation
    paths = settings["paths"]
    if not isinstance(paths, dict):
        raise CompanyRegistryError("pipeline defaults 的 paths 必须是对象")
    login_account = accountbook.login_account or "default"
    workspace_root = workspace_relative_path(login_account, accountbook.key, dataset.key, job.month).as_posix()
    for key, value in list(paths.items()):
        if isinstance(value, str):
            if "workspaces/{login_account}/{accountbook}/{company}/{month}" in value:
                raise CompanyRegistryError(f"paths.{key} 仍使用已移除的旧工作区模板")
            value = value.replace("{workspace_root}", workspace_root)
            value = value.replace("{login_account}", login_account).replace("{accountbook}", accountbook.key)
            normalized_value = value.replace("\\", "/")
            if "/generated/" in normalized_value and normalized_value.startswith("data/inbox/"):
                raise CompanyRegistryError(f"生成目录不再支持 data/inbox 旧路径：{key}={value}")
            paths[key] = value.replace("data/inbox/{company}", dataset.data_root)
    paths["month_dir"] = f"{dataset.data_root}/{{month}}"
    paths["input_dir"] = f"{dataset.data_root}/{{month}}/input"
    settings.update({
        "company": dataset.key,
        "company_name": dataset.entity_name,
        "document_entity_name": dataset.entity_name,
        "accountbook_key": accountbook.key,
        "accountbook_name": accountbook.name,
        "target_company_id": accountbook.company_id,
        "target_company_name": accountbook.name,
        "login_account": login_account,
        "workspace_root": workspace_root,
        "source_company_key": dataset.key,
        "month": job.month,
        "mode": job.mode,
        "source": normalize_source_key(job.source) or job.source,
        "purpose": job.purpose,
        "cross_entity": accountbook.name != dataset.entity_name,
        "session_file": accountbook.session_file,
    })
    return settings


def resolve_project_path(project_root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def validate_accountbook_session(project_root: Path, accountbook: AccountbookProfile) -> Path:
    session_path = resolve_project_path(project_root, accountbook.session_file)
    payload = _read_object(session_path)
    actual = str(payload.get("company_name") or "").strip()
    if actual != accountbook.name:
        raise CompanyRegistryError(f"账套会话不匹配：配置={accountbook.name}，会话={actual or '未标明公司'}，文件={session_path}")
    if not isinstance(payload.get("cookies"), list) or not payload.get("access_token"):
        raise CompanyRegistryError(f"账套会话缺少 Cookie 或 access_token：{session_path}")
    return session_path
