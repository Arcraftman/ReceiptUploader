"""Serial source-company/accountbook orchestrator with isolated per-job configs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from kdzwy_receipt_uploader.project_runtime import process_environment
import sys
from dataclasses import replace
from pathlib import Path

from kdzwy_receipt_uploader.project_runtime import project_root

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
    workflow_stage_plan,
)
from kdzwy_receipt_uploader.simple_logging import configure_pipeline_logger, install_console_transcript
from kdzwy_receipt_uploader.pipeline_state import (
    PipelineStateError,
    PipelineStateStore,
    exclusive_job_lock,
)


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CompanyRegistryError(f"Configuration must be a JSON object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def safe_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_") or "job"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process monthly datasets sequentially for the configured target accountbooks")
    parser.add_argument("--accountbooks-config", type=Path, default=project_root() / "runtime" / "registry" / "accountbooks.json")
    parser.add_argument("--jobs-config", type=Path, default=None, help="Company configuration; defaults to config/companies/*.json")
    parser.add_argument("--defaults-config", type=Path, default=project_root() / "config" / "pipeline.defaults.json")
    parser.add_argument("--app-config", type=Path, default=project_root() / "config" / "app.json")
    parser.add_argument("--accountbook", action="append", default=[], help="Run only this accountbook key; repeatable")
    parser.add_argument("--month", action="append", required=True, help="Explicit month in YYYY-MM format; repeatable")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc", "all"], default=None, help="Run selected enabled sources; all selects all enabled sources")
    parser.add_argument("--stage", choices=["ocr", "llm", "prepare", "send", "all"], default=None, help="Override the workflow stage from the monthly project.json")
    parser.add_argument("--plan", action="store_true", help="Validate and show the plan without executing it")
    parser.add_argument("--allow-confirm", action="store_true", help="Authorize real uploads through confirm-one/confirm-all")
    parser.add_argument("--limit", type=int, default=0, help="Forward to run_pipeline: limit")
    parser.add_argument("--receipt-id", type=str, default="", help="Forward to run_pipeline: receipt-id")
    parser.add_argument("--test-upload", action="store_true", help="Forward to run_pipeline: test-upload")
    parser.add_argument("--concise", action="store_true", help="Show a concise summary; write full timestamped logs to files")
    args = parser.parse_args(argv)
    try:
        target_months = list(dict.fromkeys(normalize_month(value) for value in args.month))
    except CompanyRegistryError as exc:
        parser.error(str(exc))
    logger = configure_pipeline_logger(
        project_root() / "runtime" / "logs",
        "run_companies",
        to_console=not args.concise,
    )
    transcript_path = install_console_transcript(project_root() / "runtime" / "logs", "run_companies")
    logger.info("Full console log: %s", transcript_path)
    logger.info("start run_companies: jobs=%s accountbooks=%s", args.jobs_config or "config/companies/*.json", args.accountbooks_config)

    try:
        accountbooks = load_accountbooks(args.accountbooks_config.resolve())
        company_config_paths = [args.jobs_config.resolve()] if args.jobs_config else sorted((project_root() / "config" / "companies").glob("*.json"))
        if not company_config_paths:
            raise CompanyRegistryError("No company configs under config/companies")
        jobs = []
        companies_by_key = {}
        for company_config_path in company_config_paths:
            company = load_company_profile(company_config_path)
            if company.key in companies_by_key:
                raise CompanyRegistryError(f"Duplicate company key: {company.key}")
            companies_by_key[company.key] = company
            if not company.template_company:
                raise CompanyRegistryError(f"Company config is missing shared template_company: {company_config_path}")
            dataset = dataset_from_company(company)
            for month in target_months:
                project_path = resolve_project_path(project_root(), f"{dataset.data_root}/{month}/project.json")
                if not project_path.is_file():
                    raise CompanyRegistryError(
                        f"Month config not found: {project_path}; run start month first"
                    )
                month_jobs = load_company_jobs(project_path, company)
                for job in month_jobs:
                    resolve_target_accountbook(job, accountbooks)
                jobs.extend(month_jobs)
        defaults = load_pipeline_defaults(args.defaults_config.resolve())
        base_app = read_object(args.app_config.resolve())
    except (CompanyRegistryError, OSError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
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
        if args.stage:
            job = replace(job, stage=args.stage)
        selected.append((accountbook, dataset, job))
    if not selected:
        print("No enabled jobs match the filters.", file=sys.stderr)
        return 2

    plans = []
    for accountbook, dataset, job in selected:
        try:
            template_company_key = job.template_company
            template_company = resolve_company_template(
                project_root(),
                template_company_key,
                dataset.entity_name,
            )
            template_root = (project_root() / "templates" / template_company.directory).resolve()
            try:
                template_root.relative_to((project_root() / "templates").resolve())
            except ValueError as exc:
                raise CompanyRegistryError(f"Template directory is outside templates: {template_company.directory}") from exc
            if not (template_root / "index.json").is_file():
                raise CompanyRegistryError(f"Template company is missing index.json: {template_root}")
            source_key = str(job.source or "all").lower()
            required_sources = ("sales", "purchase", "bank", "misc") if source_key == "all" else (source_key,)
            for required_source in required_sources:
                source_directory = template_root / required_source
                prompt_file = template_root / "prompts" / f"{required_source}.md"
                if not source_directory.is_dir():
                    raise CompanyRegistryError(f"Template company is missing source directory: {source_directory}")
                if not prompt_file.is_file() or not prompt_file.read_text(encoding="utf-8").strip():
                    raise CompanyRegistryError(f"Template company is missing source prompt: {prompt_file}")
            cross_entity = accountbook.key != dataset.key
            internal_mode, analysis_stage = workflow_stage_plan(job.stage)
            if internal_mode == "confirm" and not args.allow_confirm:
                raise CompanyRegistryError("stage=send/all requires confirm-one or confirm-all")
            settings = build_job_settings(defaults, accountbook, dataset, job)
            settings["template_company_key"] = template_company.key
            settings["template_company_name"] = template_company.name
            settings["templates_file"] = str((Path("templates") / template_company.directory / "index.json").as_posix())
            settings["final_template_sample"] = str((Path("templates") / template_company.directory / "final_template_sample.json").as_posix())
            month_dir = resolve_project_path(project_root(), f"{dataset.data_root}/{job.month}")
            if not month_dir.is_dir():
                raise CompanyRegistryError(f"Input directory does not exist: {month_dir}")
            session_path = None
            print("[Info] Auxiliary preload is auto; the target accountbook will be checked and missing customers/suppliers created.")
            needs_session = True
            if settings.get("accountbook_source", "live") == "live" and needs_session:
                session_path = validate_accountbook_session(project_root(), accountbook)
            workspace_root = resolve_project_path(project_root(), str(settings["workspace_root"]))
            runtime_dir = workspace_root / "state" / safe_part(source_key)
            plans.append((accountbook, dataset, job, settings, runtime_dir, session_path))
            relation = "cross-entity" if cross_entity else "same-entity"
            logger.info("Planned job: source_company=%s target_accountbook=%s month=%s stage=%s source=%s relation=%s", dataset.entity_name, accountbook.name, job.month, job.stage, job.source, relation)
            if not args.concise:
                print(
                    f"Plan: dataset={dataset.entity_name} / target={accountbook.name} / {job.month} / "
                    f"stage={job.stage} / {job.source} / {relation}; template={template_company.name}({template_company.key}); "
                    f"input={month_dir}; session={session_path or 'not needed for snapshot mode'}"
                )
        except CompanyRegistryError as exc:
            print(f"Job preflight failed: {job.dataset}->{job.accountbook}/{job.month}: {exc}", file=sys.stderr)
            logger.error("Job preflight failed: %s->%s/%s: %s", job.dataset, job.accountbook, job.month, exc)
            return 3
    if args.plan:
        print(f"Plan validated: {len(plans)} jobs; no workflow executed.")
        return 0

    for index, (accountbook, dataset, job, settings, runtime_dir, session_path) in enumerate(plans, start=1):
        logger.info("Starting job %s/%s: source_company=%s accountbook=%s month=%s", index, len(plans), dataset.entity_name, accountbook.name, job.month)
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
        workflow_stage = job.stage
        if args.concise:
            print("[Job] bank" if job.source == "bank" else f"[Job] {job.source}")
            print(f"  Dataset: {dataset.entity_name}")
            print(f"  Target: {accountbook.name}")
            print(f"  Month: {job.month}")
            if job.source == "bank":
                print(f"  Workflow stage: {workflow_stage}")
                if workflow_stage == "ocr":
                    bank_stage_text = "Split -> separate exceptions -> OCR remaining PDFs -> match remaining transactions"
                elif workflow_stage in {"llm", "all"}:
                    bank_stage_text = "Reuse exception and matching results -> LLM analysis"
                else:
                    bank_stage_text = "Reuse LLM analysis -> generate or validate final receipts"
                print(f"  Execution stage: {bank_stage_text}", flush=True)
            else:
                print(f"  Scope: {job.source} / {workflow_stage}", flush=True)
        else:
            print(f"Starting job {index}/{len(plans)}: {dataset.entity_name} -> {accountbook.name}/{job.month}")
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
                state.begin(identity, mode=str(settings["mode"]), stage=workflow_stage)
                completed = subprocess.run([
                    sys.executable,
                    "-m", "kdzwy_receipt_uploader.commands.run_pipeline",
                    "--run-config", str(run_path),
                    "--app-config", str(app_path),
                    "--state-file", str(state_path),
                    *(["--limit", str(args.limit)] if args.limit > 0 else []),
                    *(["--receipt-id", args.receipt_id] if args.receipt_id else []),
                    *(["--test-upload"] if args.test_upload else []),
                    *(["--concise"] if args.concise else []),
                ], cwd=project_root(), env=process_environment(), check=False)
                if completed.returncode == 0:
                    state.update(status="succeeded", exit_code=0, event="run_succeeded")
                    logger.info("Job succeeded: %s/%s/%s/%s", dataset.key, accountbook.key, job.month, job.source)
                else:
                    state.update(status="failed", exit_code=completed.returncode, error=f"Pipeline exit code={completed.returncode}", event="run_failed")
        except KeyboardInterrupt:
            state.update(status="cancelled", exit_code=130, error="Interrupted by user", event="run_cancelled")
            print("Job interrupted; state saved.", file=sys.stderr)
            return 130
        except PipelineStateError as exc:
            print(f"Job state error: {exc}", file=sys.stderr)
            logger.error("Job state error: %s", exc)
            return 3
        if completed.returncode != 0:
            print(f"Job failed; remaining jobs stopped: {dataset.key}->{accountbook.key}/{job.month}, Exit code={completed.returncode}", file=sys.stderr)
            logger.error("Job failed; remaining jobs stopped: %s->%s/%s code=%s", dataset.key, accountbook.key, job.month, completed.returncode)
            return completed.returncode
    if not args.concise:
        print(f"Sequential jobs completed: {len(plans)}.")
    logger.info("All jobs completed: %s", len(plans))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
