"""CLI configuration and dispatch for source-specific receipt workflows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kdzwy_receipt_uploader.month_config import MonthConfig, MonthConfigError
from kdzwy_receipt_uploader.pipeline_paths import (
    resolve_config_path,
    resolve_source_folders,
)
from kdzwy_receipt_uploader.pipeline_state import PipelineStateStore
from kdzwy_receipt_uploader.simple_logging import (
    configure_pipeline_logger,
    install_console_transcript,
)
from kdzwy_receipt_uploader.source_profile import normalize_source_key
from kdzwy_receipt_uploader.template_catalog import TemplateCatalog

from .application.bank_pipeline import run_bank_pipeline
from .application.context import PipelineContext
from .application.contracts import PipelineOptions
from .application.invoice_pipeline import run_invoice_pipeline

from .project_runtime import project_root

def _cleanup_obsolete_source_maps(map_directory: Path, source: str) -> list[Path]:
    obsolete_names = {
        "sales": {
            "purchase_map.json",
            "purchase_map.report.json",
            "xlsx_pdf_map.json",
            "xlsx_pdf_map.report.json",
        },
        "purchase": {"sales_map.json", "sales_map.report.json"},
        "bank": {
            "sales_map.json",
            "sales_map.report.json",
            "purchase_map.json",
            "purchase_map.report.json",
            "xlsx_pdf_map.json",
            "xlsx_pdf_map.report.json",
        },
        "misc": {
            "sales_map.json",
            "sales_map.report.json",
            "purchase_map.json",
            "purchase_map.report.json",
            "xlsx_pdf_map.json",
            "xlsx_pdf_map.report.json",
        },
    }.get(source, set())
    removed: list[Path] = []
    for name in sorted(obsolete_names):
        candidate = map_directory / name
        if candidate.is_file():
            candidate.unlink()
            removed.append(candidate)
    if map_directory.is_dir() and not any(map_directory.iterdir()):
        map_directory.rmdir()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="从指定的运行配置执行 map、receipt 生成和批量处理")
    parser.add_argument("--run-config", type=Path, required=True, help="运行配置路径；通常由 run_companies.py 动态生成")
    parser.add_argument("--app-config", type=Path, default=project_root() / "config" / "app.json")
    parser.add_argument("--limit", type=int, default=0, help="传递给上传阶段的单证限制（仅 confirm 阶段生效）")
    parser.add_argument("--receipt-id", type=str, default="", help="传递给上传阶段的单个 receiptId（仅 confirm 阶段生效）")
    parser.add_argument("--test-upload", action="store_true", help="传递给上传阶段的 test-upload 标记（仅 confirm 阶段生效）")
    parser.add_argument("--state-file", type=Path, default=None, help="任务级状态文件；通常由 run_companies.py 传入")
    parser.add_argument("--concise", action="store_true", help="控制台只显示标准阶段结果；详细日志仍写入文件")
    args = PipelineOptions.from_namespace(parser.parse_args())
    state_store = PipelineStateStore(args.state_file) if args.state_file else None

    def checkpoint(phase: str, *, artifacts=None, counters=None, event: str = "phase_changed") -> None:
        if state_store is not None:
            state_store.update(phase=phase, artifacts=artifacts, counters=counters, event=event)

    run_config_path = args.run_config if args.run_config.is_absolute() else project_root() / args.run_config
    app_config_path = args.app_config if args.app_config.is_absolute() else project_root() / args.app_config
    settings = json.loads(run_config_path.read_text(encoding="utf-8"))
    company = str(settings["company"])
    document_entity_name = str(settings.get("document_entity_name") or settings.get("company_name") or company)
    expected_company = str(settings.get("accountbook_name") or document_entity_name)
    month = str(settings["month"])
    pipeline_source = str(settings.get("source", "all")).lower()
    pipeline_source_key = normalize_source_key(pipeline_source) or "all"
    workspace_root = resolve_config_path(
        str(settings["workspace_root"]),
        project_root(), company, month, pipeline_source_key,
    )
    logger = configure_pipeline_logger(
        workspace_root / "logs" / pipeline_source_key,
        "run_pipeline",
        to_console=not args.concise,
    )
    transcript_path = install_console_transcript(
        workspace_root / "logs" / pipeline_source_key,
        "run_pipeline",
    )
    logger.info("完整控制台日志：%s", transcript_path)
    paths_config = settings.get("paths", settings)
    month_dir = resolve_config_path(str(paths_config["month_dir"]), project_root(), company, month, pipeline_source_key)
    input_dir = resolve_config_path(str(paths_config["input_dir"]), project_root(), company, month, pipeline_source_key)
    map_path = resolve_config_path(str(paths_config["map_file"]), project_root(), company, month, pipeline_source_key)
    sales_map_path = resolve_config_path(str(paths_config["sales_map_file"]), project_root(), company, month, pipeline_source_key)
    sales_map_report_path = resolve_config_path(str(paths_config["sales_map_report_file"]), project_root(), company, month, pipeline_source_key)
    purchase_map_path = resolve_config_path(str(paths_config["purchase_map_file"]), project_root(), company, month, pipeline_source_key)
    purchase_map_report_path = resolve_config_path(str(paths_config["purchase_map_report_file"]), project_root(), company, month, pipeline_source_key)
    removed_obsolete_maps = _cleanup_obsolete_source_maps(map_path.parent, pipeline_source_key)
    if removed_obsolete_maps:
        logger.info(
            "清理当前业务的旧重复 map：%s",
            ", ".join(path.name for path in removed_obsolete_maps),
        )
    template_path = resolve_config_path(str(settings["templates_file"]), project_root(), company, month, pipeline_source_key)
    template_root = template_path.parent
    template_catalog = TemplateCatalog.load(template_root) if template_path.name == "index.json" and template_path.is_file() else None
    pdf_folders = resolve_source_folders(pipeline_source, list(settings["pdf_folders"]))
    receipt_dir = resolve_config_path(str(paths_config["receipt_dir"]), project_root(), company, month, pipeline_source_key)
    workflow_stage = str(settings.get("workflow_stage", "ocr"))
    mode = str(settings.get("mode", "analysis-only"))
    analysis_stage = str(settings.get("analysis_stage", "ocr"))
    analysis_validation = str(settings.get("analysis_validation", "strict")).strip().lower()
    if analysis_validation not in {"strict", "relaxed", "exceptions"}:
        raise ValueError('analysis_validation 只支持 "strict"、"relaxed" 或 "exceptions"')
    if mode not in {"prepare", "analysis-only", "confirm", "verify"} or (mode == "verify" and pipeline_source_key != "bank"):
        print(f"不支持的 mode：{mode}")
        return 2
    logger.info("开始任务：source_company=%s accountbook=%s document_entity=%s month=%s stage=%s source=%s", company, expected_company, document_entity_name, month, workflow_stage, settings.get("source", "all"))
    checkpoint("workspace_ready", artifacts={"runConfig": str(run_config_path.resolve()), "appConfig": str(app_config_path.resolve()), "monthDirectory": str(month_dir.resolve())})
    try:
        config = MonthConfig.from_mapping(company, month, settings.get("input"))
    except MonthConfigError as exc:
        print(f"月份输入配置错误：{exc}")
        return 2
    context = PipelineContext(
        root=project_root(),
        analysis_stage=analysis_stage,
        app_config_path=app_config_path,
        args=args,
        checkpoint=checkpoint,
        company=company,
        config=config,
        document_entity_name=document_entity_name,
        expected_company=expected_company,
        input_dir=input_dir,
        logger=logger,
        map_path=map_path,
        mode=mode,
        month=month,
        month_dir=month_dir,
        paths_config=paths_config,
        pdf_folders=pdf_folders,
        pipeline_source_key=pipeline_source_key,
        purchase_map_path=purchase_map_path,
        purchase_map_report_path=purchase_map_report_path,
        receipt_dir=receipt_dir,
        run_config_path=run_config_path,
        sales_map_path=sales_map_path,
        sales_map_report_path=sales_map_report_path,
        settings=settings,
        template_catalog=template_catalog,
        template_path=template_path,
        template_root=template_root,
        workflow_stage=workflow_stage,
        workspace_root=workspace_root,
    )
    if pipeline_source_key == "bank":
        return run_bank_pipeline(context)
    return run_invoice_pipeline(context)
