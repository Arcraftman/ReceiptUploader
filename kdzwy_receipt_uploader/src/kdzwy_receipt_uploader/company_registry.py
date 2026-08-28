from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .source_profile import normalize_source_key


class CompanyRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class AccountbookProfile:
    key: str
    name: str
    session_file: str
    login_account: str = "default"
    company_id: str = ""
    enabled: bool = True
    pipeline_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetProfile:
    key: str
    entity_name: str
    data_root: str
    enabled: bool = True
    pipeline_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplateCompanyProfile:
    key: str
    name: str
    directory: str
    enabled: bool = True


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


def _overrides(row: dict[str, Any], label: str) -> dict[str, Any]:
    value = row.get("pipeline_overrides") or {}
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"{label}.pipeline_overrides 必须是对象")
    return value


def load_accountbooks(path: Path) -> dict[str, AccountbookProfile]:
    payload = _read_object(path)
    result: dict[str, AccountbookProfile] = {}
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(payload.get("accountbooks", []), start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"accountbooks[{index}] 必须是对象")
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
            company_id=str(row.get("company_id") or "").strip(),
            enabled=bool(row.get("enabled", True)),
            pipeline_overrides=_overrides(row, key),
        )
        identities.add(identity)
    if not result:
        raise CompanyRegistryError("账套注册表为空")
    return result


def load_datasets(path: Path) -> dict[str, DatasetProfile]:
    payload = _read_object(path)
    result: dict[str, DatasetProfile] = {}
    for index, row in enumerate(payload.get("datasets", []), start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"datasets[{index}] 必须是对象")
        key = _required_text(row.get("key"), f"datasets[{index}].key")
        if key in result:
            raise CompanyRegistryError(f"数据集 key 重复：{key}")
        result[key] = DatasetProfile(
            key=key,
            entity_name=_required_text(row.get("entity_name"), f"{key}.entity_name"),
            data_root=_required_text(row.get("data_root"), f"{key}.data_root"),
            enabled=bool(row.get("enabled", True)),
            pipeline_overrides=_overrides(row, key),
        )
    if not result:
        raise CompanyRegistryError("数据集注册表为空")
    return result


def load_template_companies(path: Path) -> dict[str, TemplateCompanyProfile]:
    payload = _read_object(path)
    result: dict[str, TemplateCompanyProfile] = {}
    for index, row in enumerate(payload.get("template_companies", []), start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"template_companies[{index}] 必须是对象")
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
            enabled=bool(row.get("enabled", True)),
        )
    if not result:
        raise CompanyRegistryError("模板公司注册表为空")
    return result


def load_company_jobs(path: Path) -> list[CompanyJob]:
    payload = _read_object(path)
    if not bool(payload.get("enabled", True)):
        return []
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise CompanyRegistryError("jobs.defaults 必须是对象")

    def pipeline_overrides(row: dict[str, Any], label: str) -> dict[str, Any]:
        default_values = defaults.get("overrides") or {}
        row_values = row.get("overrides") or {}
        if not isinstance(default_values, dict):
            raise CompanyRegistryError("defaults.overrides 必须是对象")
        if not isinstance(row_values, dict):
            raise CompanyRegistryError(f"{label}.overrides 必须是对象")
        result = deep_merge(default_values, row_values)
        for key in ("analysis_stage", "analysis_validation", "ocr_workers", "deepseek_workers", "preload_items"):
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
    if sources is not None:
        if not isinstance(sources, dict):
            raise CompanyRegistryError("company.sources 必须是对象")
        company_key = _required_text(payload.get("company_key"), "company_key")
        dataset = _required_text(payload.get("dataset"), "dataset")
        month = _required_text(payload.get("month"), "month")
        template_company = _required_text(payload.get("template_company"), "template_company")
        jobs: list[dict[str, Any]] = []
        for source, options in sources.items():
            if isinstance(options, bool):
                options = {"enabled": options}
            if not isinstance(options, dict):
                raise CompanyRegistryError(f"sources.{source} 必须是对象或布尔值")
            jobs.append({
                "accountbook": company_key,
                "dataset": options.get("dataset", dataset),
                "month": options.get("month", month),
                "template_company": options.get("template_company", template_company),
                "source": source,
                "mode": options.get("mode", defaults.get("mode", "analysis-only")),
                "purpose": options.get("purpose", defaults.get("purpose", "production")),
                "allow_cross_entity": options.get("allow_cross_entity", defaults.get("allow_cross_entity", False)),
                "enabled": options.get("enabled", False),
                "overrides": pipeline_overrides(options, f"sources.{source}"),
            })
        payload = {"defaults": defaults, "jobs": jobs}
    result: list[CompanyJob] = []
    for index, row in enumerate(payload.get("jobs", []), start=1):
        if not isinstance(row, dict):
            raise CompanyRegistryError(f"jobs[{index}] 必须是对象")
        overrides = pipeline_overrides(row, f"jobs[{index}]")
        result.append(CompanyJob(
            accountbook=_required_text(row.get("accountbook"), f"jobs[{index}].accountbook"),
            dataset=_required_text(row.get("dataset"), f"jobs[{index}].dataset"),
            month=_required_text(row.get("month"), f"jobs[{index}].month"),
            template_company=str(row.get("template_company", defaults.get("template_company", ""))).strip(),
            mode=str(row.get("mode", defaults.get("mode", "analysis-only"))),
            source=str(row.get("source", defaults.get("source", "all"))),
            purpose=str(row.get("purpose", defaults.get("purpose", "production"))),
            allow_cross_entity=bool(row.get("allow_cross_entity", defaults.get("allow_cross_entity", False))),
            enabled=bool(row.get("enabled", True)),
            overrides=overrides,
        ))
    return result


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_job_settings(defaults: dict[str, Any], accountbook: AccountbookProfile, dataset: DatasetProfile, job: CompanyJob) -> dict[str, Any]:
    if job.mode not in {"analysis-only", "prepare", "dry-run", "confirm"}:
        raise CompanyRegistryError(f"不支持的运行模式：{job.mode}")
    if normalize_source_key(job.source) not in {"sales", "purchase", "bank", "misc", "all"}:
        raise CompanyRegistryError(f"不支持的责任链来源：{job.source}")
    settings = deep_merge(defaults, accountbook.pipeline_overrides)
    settings = deep_merge(settings, dataset.pipeline_overrides)
    settings = deep_merge(settings, job.overrides)
    analysis_stage = str(settings.get("analysis_stage", "ocr"))
    if analysis_stage not in {"ocr", "deepseek", "existing", "all"}:
        raise CompanyRegistryError(f"不支持的分析阶段：{analysis_stage}")
    analysis_validation = str(settings.get("analysis_validation", "strict")).strip().lower()
    if analysis_validation not in {"strict", "relaxed"}:
        raise CompanyRegistryError('analysis_validation 只支持 "strict" 或 "relaxed"')
    settings["analysis_validation"] = analysis_validation
    paths = settings.setdefault("paths", {})
    if not isinstance(paths, dict):
        raise CompanyRegistryError("pipeline defaults 的 paths 必须是对象")
    login_account = accountbook.login_account or "default"
    workspace_parts = (login_account, accountbook.key, dataset.key, job.month)
    if any(Path(part).name != part or part in {".", ".."} for part in workspace_parts):
        raise CompanyRegistryError(f"工作区标识不能包含路径字符：{workspace_parts}")
    workspace_root = Path("workspaces").joinpath(*workspace_parts).as_posix()
    for key, value in list(paths.items()):
        if isinstance(value, str):
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
        "login_account": login_account,
        "workspace_root": workspace_root,
        "dataset_key": dataset.key,
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
