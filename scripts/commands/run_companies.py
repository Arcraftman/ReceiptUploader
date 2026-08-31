"""Serial source-company/accountbook orchestrator with isolated per-job configs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    build_job_settings,
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    load_pipeline_defaults,
    normalize_month,
    resolve_company_template,
    resolve_target_accountbook,
    resolve_project_path,
    validate_accountbook_session,
)
from kdzwy_receipt_uploader.simple_logging import configure_pipeline_logger
from kdzwy_receipt_uploader.pipeline_state import (
    PipelineStateError,
    PipelineStateStore,
    exclusive_job_lock,
)


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"配置必须是 JSON 对象：{path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def safe_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_") or "job"


def validate_mode_stage(mode: str, stage: str) -> None:
    allowed = {
        "analysis-only": {"ocr", "llm", "existing", "all"},
        "prepare": {"llm", "existing", "all"},
        "dry-run": {"existing"},
        "confirm": {"existing"},
    }
    if stage not in allowed.get(mode, set()):
        choices = ", ".join(sorted(allowed.get(mode, set()))) or "无"
        raise CompanyRegistryError(
            f"[警告] mode={mode} 不能与 analysis_stage={stage} 混用；"
            f"该模式只允许：{choices}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="按月份配置串行处理资料公司并写入指定账套")
    parser.add_argument("--accountbooks-config", type=Path, default=ROOT / "runtime" / "registry" / "accountbooks.json")
    parser.add_argument("--jobs-config", type=Path, default=None, help="单个公司配置；默认加载 config/companies/*.json")
    parser.add_argument("--defaults-config", type=Path, default=ROOT / "config" / "pipeline.defaults.json")
    parser.add_argument("--app-config", type=Path, default=ROOT / "config" / "app.json")
    parser.add_argument("--accountbook", action="append", default=[], help="只运行指定账套 key，可重复")
    parser.add_argument("--month", action="append", required=True, help="明确指定月份 YYYY-MM，可重复")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc", "all"], default=None, help="只运行指定的已启用业务；all 表示全部已启用业务")
    parser.add_argument("--mode", choices=["analysis-only", "prepare", "dry-run", "confirm"], default=None)
    parser.add_argument("--stage", choices=["ocr", "llm", "existing", "all"], default=None, help="临时覆盖本月 project.json 的分析阶段；不兼容的 mode/stage 组合会在预检时停止")
    parser.add_argument("--plan", action="store_true", help="只检查并显示计划，不执行流水线")
    parser.add_argument("--allow-confirm", action="store_true", help="仅供 confirm_one/confirm_all 安全入口授权真实上传")
    parser.add_argument(
        "--allow-cross-entity-confirm",
        action="store_true",
        help="显式允许将法定主体与账套名不同的数据写入目标账套；每次 confirm 都必须传入",
    )
    parser.add_argument("--limit", type=int, default=0, help="透传给 run_pipeline 的 limit")
    parser.add_argument("--receipt-id", type=str, default="", help="透传给 run_pipeline 的 receipt-id")
    parser.add_argument("--test-upload", action="store_true", help="透传给 run_pipeline 的 test-upload")
    parser.add_argument("--concise", action="store_true", help="控制台只显示标准任务摘要；详细时间戳日志仍写入文件")
    args = parser.parse_args()
    try:
        target_months = list(dict.fromkeys(normalize_month(value) for value in args.month))
    except CompanyRegistryError as exc:
        parser.error(str(exc))
    logger = configure_pipeline_logger(
        ROOT / "runtime" / "logs",
        "run_companies",
        to_console=not args.concise,
    )
    logger.info("start run_companies: jobs=%s accountbooks=%s", args.jobs_config or "config/companies/*.json", args.accountbooks_config)

    try:
        accountbooks = load_accountbooks(args.accountbooks_config.resolve())
        company_config_paths = [args.jobs_config.resolve()] if args.jobs_config else sorted((ROOT / "config" / "companies").glob("*.json"))
        if not company_config_paths:
            raise CompanyRegistryError("config/companies 中没有公司配置")
        jobs = []
        companies_by_key = {}
        for company_config_path in company_config_paths:
            company = load_company_profile(company_config_path)
            if company.key in companies_by_key:
                raise CompanyRegistryError(f"公司 key 重复：{company.key}")
            companies_by_key[company.key] = company
            if not company.template_company:
                raise CompanyRegistryError(f"公司配置缺少共享 template_company：{company_config_path}")
            dataset = dataset_from_company(company)
            for month in target_months:
                project_path = resolve_project_path(ROOT, f"{dataset.data_root}/{month}/project.json")
                if not project_path.is_file():
                    raise CompanyRegistryError(
                        f"月份配置不存在：{project_path}；请先执行 initialize_month.bat"
                    )
                month_jobs = load_company_jobs(project_path, company)
                for job in month_jobs:
                    resolve_target_accountbook(job, accountbooks)
                jobs.extend(month_jobs)
        defaults = load_pipeline_defaults(args.defaults_config.resolve())
        base_app = read_object(args.app_config.resolve())
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    selected = []
    for job in jobs:
        accountbook = accountbooks.get(job.accountbook)
        company = companies_by_key.get(job.dataset)
        dataset = dataset_from_company(company) if company is not None else None
        if not job.enabled or accountbook is None or dataset is None:
            continue
        if not accountbook.enabled or not dataset.enabled:
            continue
        if args.accountbook and job.accountbook not in args.accountbook:
            continue
        if job.month not in target_months:
            continue
        if args.source and args.source != "all" and job.source != args.source:
            continue
        if args.mode:
            job = replace(job, mode=args.mode)
        selected.append((accountbook, dataset, job))
    if not selected:
        print("没有启用且匹配筛选条件的任务。", file=sys.stderr)
        return 2

    plans = []
    for accountbook, dataset, job in selected:
        try:
            template_company_key = job.template_company
            template_company = resolve_company_template(
                ROOT,
                template_company_key,
                dataset.entity_name,
            )
            template_root = (ROOT / "templates" / template_company.directory).resolve()
            try:
                template_root.relative_to((ROOT / "templates").resolve())
            except ValueError as exc:
                raise CompanyRegistryError(f"模板目录越过 templates：{template_company.directory}") from exc
            if not (template_root / "index.json").is_file():
                raise CompanyRegistryError(f"模板公司缺少 index.json：{template_root}")
            source_key = str(job.source or "all").lower()
            required_sources = ("sales", "purchase", "bank", "misc") if source_key == "all" else (source_key,)
            for required_source in required_sources:
                source_directory = template_root / required_source
                prompt_file = template_root / "prompts" / f"{required_source}.md"
                if not source_directory.is_dir():
                    raise CompanyRegistryError(f"模板公司缺少业务目录：{source_directory}")
                if not prompt_file.is_file() or not prompt_file.read_text(encoding="utf-8").strip():
                    raise CompanyRegistryError(f"模板公司缺少业务提示词：{prompt_file}")
            cross_entity = accountbook.name != dataset.entity_name
            if job.mode == "confirm" and not args.allow_confirm:
                raise CompanyRegistryError("confirm 只能通过 confirm_one.bat 或 confirm_all.bat 执行")
            if cross_entity and not job.allow_cross_entity:
                raise CompanyRegistryError("数据法定主体与目标账套不同，但任务未声明 allow_cross_entity=true")
            if cross_entity and job.mode == "confirm" and not args.allow_cross_entity_confirm:
                raise CompanyRegistryError("跨主体 confirm 必须显式传入 --allow-cross-entity-confirm")
            settings = build_job_settings(defaults, accountbook, dataset, job)
            settings["template_company_key"] = template_company.key
            settings["template_company_name"] = template_company.name
            settings["templates_file"] = str((Path("templates") / template_company.directory / "index.json").as_posix())
            settings["final_template_sample"] = str((Path("templates") / template_company.directory / "final_template_sample.json").as_posix())
            month_dir = resolve_project_path(ROOT, f"{dataset.data_root}/{job.month}")
            if not month_dir.is_dir():
                raise CompanyRegistryError(f"待处理目录不存在：{month_dir}")
            session_path = None
            # ItemClass preload is an independent company setting. It is not coupled
            # to mode or analysis_stage.
            effective_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
            validate_mode_stage(job.mode, effective_stage)
            preload_mode = settings.get("preload_items", False)
            if preload_mode is True or preload_mode == "once":
                print("[提示] preload_items=once；每次按实际业务映射核对当前目标账套，仅创建远端缺失的客户/供应商。")
            elif preload_mode == "auto":
                print("[警告] preload_items=auto；每次都会检查并创建远端不存在的客户/供应商。")
            preload_needs_session = preload_mode is True or str(preload_mode).strip().lower() in {"once", "auto"}
            needs_session = (
                job.mode != "analysis-only"
                or effective_stage in {"llm", "existing", "all"}
                or preload_needs_session
            )
            if settings.get("accountbook_source", "live") == "live" and needs_session:
                session_path = validate_accountbook_session(ROOT, accountbook)
            workspace_root = resolve_project_path(ROOT, str(settings["workspace_root"]))
            runtime_dir = workspace_root / "state" / safe_part(source_key)
            plans.append((accountbook, dataset, job, settings, runtime_dir, session_path))
            relation = "跨主体测试" if cross_entity else "同主体"
            logger.info("计划任务: source_company=%s target_accountbook=%s month=%s mode=%s source=%s relation=%s", dataset.entity_name, accountbook.name, job.month, job.mode, job.source, relation)
            if not args.concise:
                print(
                    f"计划：资料公司={dataset.entity_name} / 目标账套={accountbook.name} / {job.month} / "
                    f"{job.mode} / stage={effective_stage} / {job.source} / {relation}；模板={template_company.name}({template_company.key})；"
                    f"资料={month_dir}；会话={session_path or '快照模式不需要'}"
                )
        except CompanyRegistryError as exc:
            print(f"任务预检失败：{job.dataset}->{job.accountbook}/{job.month}：{exc}", file=sys.stderr)
            logger.error("任务预检失败：%s->%s/%s：%s", job.dataset, job.accountbook, job.month, exc)
            return 3
    if args.plan:
        print(f"计划检查通过：{len(plans)} 个任务，未执行流水线。")
        return 0

    for index, (accountbook, dataset, job, settings, runtime_dir, session_path) in enumerate(plans, start=1):
        logger.info("开始任务 %s/%s: source_company=%s accountbook=%s month=%s", index, len(plans), dataset.entity_name, accountbook.name, job.month)
        run_path = runtime_dir / "run.json"
        app_path = runtime_dir / "app.json"
        app_payload = dict(base_app)
        if session_path is not None:
            app_payload["cookie_file"] = str(session_path)
            app_payload["expected_company"] = accountbook.name
        write_json(run_path, settings)
        write_json(app_path, app_payload)
        state_path = runtime_dir / "state.json"
        state = PipelineStateStore(state_path)
        effective_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
        if args.concise:
            print("[任务] 银行" if job.source == "bank" else f"[任务] {job.source}")
            print(f"  资料公司：{dataset.entity_name}")
            print(f"  目标账套：{accountbook.name}")
            print(f"  会计月份：{job.month}")
            if job.source == "bank":
                print(f"  安全模式：{job.mode}")
                if effective_stage == "ocr":
                    bank_stage_text = "裁剪 → 特殊对象分流 → 剩余 OCR → 剩余流水匹配"
                elif effective_stage in {"llm", "all"}:
                    bank_stage_text = "复用特殊对象分流和普通匹配结果 → LLM 分析"
                else:
                    bank_stage_text = "复用 LLM 分析 → 生成或检查最终 receipt"
                print(f"  执行阶段：{bank_stage_text}", flush=True)
            else:
                print(f"  执行范围：{job.source} / {job.mode} / {effective_stage}", flush=True)
        else:
            print(f"开始第 {index}/{len(plans)} 个任务：{dataset.entity_name} -> {accountbook.name}/{job.month}")
        identity = {
            "accountbook": accountbook.key,
            "accountbookName": accountbook.name,
            "targetCompanyId": accountbook.company_id,
            "targetCompanyName": accountbook.name,
            "loginAccount": accountbook.login_account,
            "sourceCompany": dataset.key,
            "sourceCompanyName": dataset.entity_name,
            "month": job.month,
            "source": str(job.source),
            "templateCompany": str(settings.get("template_company_key", "")),
        }
        try:
            with exclusive_job_lock(runtime_dir / "job.lock"):
                state.begin(identity, mode=job.mode, stage=effective_stage)
                completed = subprocess.run([
                    sys.executable,
                    str(ROOT / "scripts" / "commands" / "run_pipeline.py"),
                    "--run-config", str(run_path),
                    "--app-config", str(app_path),
                    "--state-file", str(state_path),
                    *(["--limit", str(args.limit)] if args.limit > 0 else []),
                    *(["--receipt-id", args.receipt_id] if args.receipt_id else []),
                    *(["--test-upload"] if args.test_upload else []),
                    *(["--stage", args.stage] if args.stage else []),
                    *(["--concise"] if args.concise else []),
                ], cwd=ROOT, check=False)
                if completed.returncode == 0:
                    state.update(status="succeeded", exit_code=0, event="run_succeeded")
                else:
                    state.update(status="failed", exit_code=completed.returncode, error=f"pipeline退出码={completed.returncode}", event="run_failed")
        except KeyboardInterrupt:
            state.update(status="cancelled", exit_code=130, error="用户中断", event="run_cancelled")
            print("用户中断任务；状态已安全保存。", file=sys.stderr)
            return 130
        except PipelineStateError as exc:
            print(f"任务状态错误：{exc}", file=sys.stderr)
            logger.error("任务状态错误：%s", exc)
            return 3
        if completed.returncode != 0:
            print(f"任务失败并停止后续任务：{dataset.key}->{accountbook.key}/{job.month}，退出码={completed.returncode}", file=sys.stderr)
            logger.error("任务失败并停止后续任务：%s->%s/%s code=%s", dataset.key, accountbook.key, job.month, completed.returncode)
            return completed.returncode
    if not args.concise:
        print(f"串行任务完成：{len(plans)} 个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
