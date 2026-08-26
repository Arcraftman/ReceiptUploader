"""Serial accountbook/dataset orchestrator with isolated per-job configs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (  # noqa: E402
    CompanyRegistryError,
    build_job_settings,
    load_accountbooks,
    load_company_jobs,
    load_datasets,
    load_template_companies,
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
        "analysis-only": {"ocr", "deepseek", "existing", "all"},
        "prepare": {"deepseek", "existing", "all"},
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
    parser = argparse.ArgumentParser(description="按任务清单串行处理数据集并写入指定账套")
    parser.add_argument("--accountbooks-config", type=Path, default=ROOT / "config" / "accountbooks.json")
    parser.add_argument("--datasets-config", type=Path, default=ROOT / "config" / "datasets.json")
    parser.add_argument("--jobs-config", type=Path, default=None, help="单个公司配置；默认加载 config/companies/*.json")
    parser.add_argument("--template-companies-config", type=Path, default=ROOT / "config" / "template_companies.json")
    parser.add_argument("--defaults-config", type=Path, default=ROOT / "config" / "pipeline.defaults.json")
    parser.add_argument("--app-config", type=Path, default=ROOT / "config" / "app.json")
    parser.add_argument("--accountbook", action="append", default=[], help="只运行指定账套 key，可重复")
    parser.add_argument("--dataset", action="append", default=[], help="只运行指定数据集 key，可重复")
    parser.add_argument("--month", action="append", default=[], help="只运行指定月份，可重复")
    parser.add_argument("--company-template", default="", help="选择模板公司 key，例如 xinghai；覆盖任务配置")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc", "all"], default=None, help="覆盖任务的业务板块")
    parser.add_argument("--mode", choices=["analysis-only", "prepare", "dry-run", "confirm"], default=None)
    parser.add_argument("--stage", choices=["ocr", "deepseek", "existing", "all"], default=None, help="覆盖公司配置中的分析阶段；不兼容的 mode/stage 组合会在预检时停止")
    parser.add_argument("--plan", action="store_true", help="只检查并显示计划，不执行流水线")
    parser.add_argument(
        "--allow-cross-entity-confirm",
        action="store_true",
        help="显式允许将法定主体与账套名不同的数据写入目标账套；每次 confirm 都必须传入",
    )
    parser.add_argument("--limit", type=int, default=0, help="透传给 run_pipeline 的 limit")
    parser.add_argument("--receipt-id", type=str, default="", help="透传给 run_pipeline 的 receipt-id")
    parser.add_argument("--test-upload", action="store_true", help="透传给 run_pipeline 的 test-upload")
    args = parser.parse_args()
    if args.company_template and not re.fullmatch(r"[a-z][a-z0-9_-]*", args.company_template):
        parser.error("--company-template 只允许英文小写 key：字母开头，可包含数字、下划线和连字符")
    logger = configure_pipeline_logger(ROOT / "runtime" / "logs", "run_companies")
    logger.info("start run_companies: jobs=%s datasets=%s accountbooks=%s", args.jobs_config or "config/companies/*.json", args.datasets_config, args.accountbooks_config)

    try:
        accountbooks = load_accountbooks(args.accountbooks_config.resolve())
        datasets = load_datasets(args.datasets_config.resolve())
        template_companies = load_template_companies(args.template_companies_config.resolve())
        jobs_config_paths = [args.jobs_config.resolve()] if args.jobs_config else sorted((ROOT / "config" / "companies").glob("*.json"))
        if not jobs_config_paths:
            raise CompanyRegistryError("config/companies 中没有公司配置")
        jobs = [job for path in jobs_config_paths for job in load_company_jobs(path)]
        defaults = read_object(args.defaults_config.resolve())
        base_app = read_object(args.app_config.resolve())
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    selected = []
    for job in jobs:
        accountbook = accountbooks.get(job.accountbook)
        dataset = datasets.get(job.dataset)
        if not job.enabled or accountbook is None or dataset is None:
            continue
        if not accountbook.enabled or not dataset.enabled:
            continue
        if args.accountbook and job.accountbook not in args.accountbook:
            continue
        if args.dataset and job.dataset not in args.dataset:
            continue
        if args.month and job.month not in args.month:
            continue
        if args.mode:
            job = replace(job, mode=args.mode)
        if args.source:
            job = replace(job, source=args.source)
        if args.company_template:
            job = replace(job, template_company=args.company_template)
        selected.append((accountbook, dataset, job))
    if not selected:
        print("没有启用且匹配筛选条件的任务。", file=sys.stderr)
        return 2

    plans = []
    for accountbook, dataset, job in selected:
        try:
            template_company_key = job.template_company or accountbook.key
            template_company = template_companies.get(template_company_key)
            if template_company is None or not template_company.enabled:
                raise CompanyRegistryError(f"模板公司不存在或未启用：{template_company_key}")
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", template_company.directory):
                raise CompanyRegistryError(f"模板目录必须使用英文小写 key：{template_company.directory}")
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
            if not list(month_dir.glob("*.conf")):
                raise CompanyRegistryError(f"月份目录缺少 .conf：{month_dir}")
            session_path = None
            # ItemClass preload is an independent company setting. It is not coupled
            # to mode or analysis_stage.
            effective_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
            validate_mode_stage(job.mode, effective_stage)
            preload_mode = settings.get("preload_items", False)
            if preload_mode is True or preload_mode == "once":
                print("[提示] preload_items=once；仅首次或输入Excel变化后预加载，并创建远端不存在的客户/供应商。")
            elif preload_mode == "auto":
                print("[警告] preload_items=auto；每次都会检查并创建远端不存在的客户/供应商。")
            needs_session = job.mode != "analysis-only" or effective_stage in {"deepseek", "existing", "all"}
            if settings.get("accountbook_source", "live") == "live" and needs_session:
                session_path = validate_accountbook_session(ROOT, accountbook)
            runtime_dir = ROOT / "runtime" / "jobs" / safe_part(accountbook.key) / safe_part(dataset.key) / safe_part(job.month) / safe_part(source_key)
            plans.append((accountbook, dataset, job, settings, runtime_dir, session_path))
            relation = "跨主体测试" if cross_entity else "同主体"
            logger.info("计划任务: dataset=%s accountbook=%s month=%s mode=%s source=%s relation=%s", dataset.entity_name, accountbook.name, job.month, job.mode, job.source, relation)
            print(
                f"计划：数据={dataset.entity_name} / 账套={accountbook.name} / {job.month} / "
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
        logger.info("开始任务 %s/%s: dataset=%s accountbook=%s month=%s", index, len(plans), dataset.entity_name, accountbook.name, job.month)
        run_path = runtime_dir / "run.json"
        app_path = runtime_dir / "app.json"
        app_payload = dict(base_app)
        if session_path is not None:
            app_payload["cookie_file"] = str(session_path)
            app_payload["expected_company"] = accountbook.name
        write_json(run_path, settings)
        write_json(app_path, app_payload)
        print(f"开始第 {index}/{len(plans)} 个任务：{dataset.entity_name} -> {accountbook.name}/{job.month}")
        state_path = runtime_dir / "state.json"
        state = PipelineStateStore(state_path)
        effective_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
        identity = {
            "accountbook": accountbook.key,
            "accountbookName": accountbook.name,
            "dataset": dataset.key,
            "datasetName": dataset.entity_name,
            "month": job.month,
            "source": str(job.source),
            "templateCompany": str(settings.get("template_company_key", "")),
        }
        try:
            with exclusive_job_lock(runtime_dir / "job.lock"):
                state.begin(identity, mode=job.mode, stage=effective_stage)
                completed = subprocess.run([
                    sys.executable,
                    str(ROOT / "run_pipeline.py"),
                    "--run-config", str(run_path),
                    "--app-config", str(app_path),
                    "--state-file", str(state_path),
                    *(["--limit", str(args.limit)] if args.limit > 0 else []),
                    *(["--receipt-id", args.receipt_id] if args.receipt_id else []),
                    *(["--test-upload"] if args.test_upload else []),
                    *(["--stage", args.stage] if args.stage else []),
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
    print(f"串行任务完成：{len(plans)} 个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
