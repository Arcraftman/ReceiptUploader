"""Config-driven map, receipt generation, and batch dry-run pipeline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.month_config import MonthConfig, MonthConfigError
from kdzwy_receipt_uploader.receipt_generation import discover_source_pdfs, generate_receipts
from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults
from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.matching import match_month_directory
from kdzwy_receipt_uploader.user_context import resolve_current_user
from kdzwy_receipt_uploader.sales_map import (
    add_sales_pdf_fallback_candidates,
    build_sales_map,
    finalize_sales_ocr_fallbacks,
)
from kdzwy_receipt_uploader.purchase_map import build_purchase_map
from kdzwy_receipt_uploader.auxiliary_items import create_auxiliary_item
from kdzwy_receipt_uploader.item_class import build_auxiliary_expectation, resolve_item_class_id
from kdzwy_receipt_uploader.item_class_maps import ItemClassMapStore
from kdzwy_receipt_uploader.template_catalog import TemplateCatalog
from kdzwy_receipt_uploader.source_profile import normalize_source_key
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path, resolve_source_folders, resolve_item_class_labels
from kdzwy_receipt_uploader.receipts_ocr import (
    OpenAICompatibleTemplateSelector,
    analyze_ocr_and_choose_template,
    compact_analysis_for_storage,
)
from kdzwy_receipt_uploader.preupload_review import build_preupload_report
from kdzwy_receipt_uploader.exception_ledger import append_exception, replace_analysis_exception_stages, replace_stage_exceptions
from kdzwy_receipt_uploader.final_template_sample import build_final_template_context, load_final_template_sample
from kdzwy_receipt_uploader.preload_items import (
    apply_preloaded_items,
    collect_map_item_names,
    collect_source_item_names,
    preload_bank_counterparties,
    preload_items,
)
from kdzwy_receipt_uploader.simple_logging import configure_pipeline_logger
from kdzwy_receipt_uploader.bank_receipt_splitter import BankReceiptSplitError, split_configured_bank_pdfs
from kdzwy_receipt_uploader.bank_receipt_ocr import BankReceiptOcrError, run_bank_receipt_ocr
from kdzwy_receipt_uploader.bank_statement_matcher import (
    BankStatementMatchError,
    collect_person_name_exclusions,
    match_bank_statements,
)
from kdzwy_receipt_uploader.bank_final_receipts import (
    BankFinalReceiptError,
    build_bank_ocr_artifacts,
    generate_bank_final_receipts,
    load_bank_records,
    source_values as bank_source_values,
    validate_bank_analysis_rules,
)
from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts
from kdzwy_receipt_uploader.bank_exception_filter import (
    BankExceptionFilterError,
    filter_bank_exception_pdfs,
    load_bank_exception_pdf_keywords,
    quarantine_bank_runtime_exceptions,
)
from kdzwy_receipt_uploader.pipeline_state import PipelineStateStore


def _empty_map_report() -> dict[str, object]:
    return {"map": {}, "report": {"summary": {}}}


def _empty_match_report() -> dict[str, object]:
    return {
        "map": {},
        "summary": {
            "usageConfirmNumberCount": 0,
            "matchedCount": 0,
            "emptyCount": 0,
        },
    }


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
    parser.add_argument("--app-config", type=Path, default=ROOT / "config" / "app.json")
    parser.add_argument("--mode", choices=["prepare", "analysis-only", "dry-run", "confirm"], default=None, help="覆盖配置中的 mode")
    parser.add_argument("--stage", choices=["ocr", "llm", "existing", "all"], default=None, help="分析阶段：OCR、Qwen、复用已批准分析或显式串行执行")
    parser.add_argument("--limit", type=int, default=0, help="传递给上传阶段的单证限制（仅 confirm 阶段生效）")
    parser.add_argument("--receipt-id", type=str, default="", help="传递给上传阶段的单个 receiptId（仅 confirm 阶段生效）")
    parser.add_argument("--test-upload", action="store_true", help="传递给上传阶段的 test-upload 标记（仅 confirm 阶段生效）")
    parser.add_argument("--state-file", type=Path, default=None, help="任务级状态文件；通常由 run_companies.py 传入")
    parser.add_argument("--concise", action="store_true", help="控制台只显示标准阶段结果；详细日志仍写入文件")
    args = parser.parse_args()
    state_store = PipelineStateStore(args.state_file) if args.state_file else None

    def checkpoint(phase: str, *, artifacts=None, counters=None, event: str = "phase_changed") -> None:
        if state_store is not None:
            state_store.update(phase=phase, artifacts=artifacts, counters=counters, event=event)

    run_config_path = args.run_config if args.run_config.is_absolute() else ROOT / args.run_config
    app_config_path = args.app_config if args.app_config.is_absolute() else ROOT / args.app_config
    settings = json.loads(run_config_path.read_text(encoding="utf-8"))
    company = str(settings["company"])
    document_entity_name = str(settings.get("document_entity_name") or settings.get("company_name") or company)
    expected_company = str(settings.get("accountbook_name") or document_entity_name)
    month = str(settings["month"])
    pipeline_source = str(settings.get("source", "all")).lower()
    pipeline_source_key = normalize_source_key(pipeline_source) or "all"
    workspace_root = resolve_config_path(
        str(settings["workspace_root"]),
        ROOT, company, month, pipeline_source_key,
    )
    logger = configure_pipeline_logger(
        workspace_root / "logs" / pipeline_source_key,
        "run_pipeline",
        to_console=not args.concise,
    )
    paths_config = settings.get("paths", settings)
    month_dir = resolve_config_path(str(paths_config["month_dir"]), ROOT, company, month, pipeline_source_key)
    input_dir = resolve_config_path(str(paths_config["input_dir"]), ROOT, company, month, pipeline_source_key)
    map_path = resolve_config_path(str(paths_config["map_file"]), ROOT, company, month, pipeline_source_key)
    sales_map_path = resolve_config_path(str(paths_config["sales_map_file"]), ROOT, company, month, pipeline_source_key)
    sales_map_report_path = resolve_config_path(str(paths_config["sales_map_report_file"]), ROOT, company, month, pipeline_source_key)
    purchase_map_path = resolve_config_path(str(paths_config["purchase_map_file"]), ROOT, company, month, pipeline_source_key)
    purchase_map_report_path = resolve_config_path(str(paths_config["purchase_map_report_file"]), ROOT, company, month, pipeline_source_key)
    removed_obsolete_maps = _cleanup_obsolete_source_maps(map_path.parent, pipeline_source_key)
    if removed_obsolete_maps:
        logger.info(
            "清理当前业务的旧重复 map：%s",
            ", ".join(path.name for path in removed_obsolete_maps),
        )
    template_path = resolve_config_path(str(settings["templates_file"]), ROOT, company, month, pipeline_source_key)
    template_root = template_path.parent
    template_catalog = TemplateCatalog.load(template_root) if template_path.name == "index.json" and template_path.is_file() else None
    pdf_folders = resolve_source_folders(pipeline_source, list(settings["pdf_folders"]))
    receipt_dir = resolve_config_path(str(paths_config["receipt_dir"]), ROOT, company, month, pipeline_source_key)
    mode = args.mode or str(settings.get("mode", "prepare"))
    analysis_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
    analysis_validation = str(settings.get("analysis_validation", "strict")).strip().lower()
    if analysis_validation not in {"strict", "relaxed", "exceptions"}:
        raise ValueError('analysis_validation 只支持 "strict"、"relaxed" 或 "exceptions"')
    if mode not in {"prepare", "analysis-only", "dry-run", "confirm"}:
        print(f"不支持的 mode：{mode}")
        return 2
    logger.info("开始任务：source_company=%s accountbook=%s document_entity=%s month=%s mode=%s source=%s", company, expected_company, document_entity_name, month, mode, settings.get("source", "all"))
    checkpoint("workspace_ready", artifacts={"runConfig": str(run_config_path.resolve()), "appConfig": str(app_config_path.resolve()), "monthDirectory": str(month_dir.resolve())})
    try:
        config = MonthConfig.from_mapping(company, month, settings.get("input"))
    except MonthConfigError as exc:
        print(f"月份输入配置错误：{exc}")
        return 2
    if pipeline_source_key == "bank":
        checkpoint("bank_split")
        bank_input_dir = input_dir / "bank"
        bank_split_output = resolve_config_path(
            str(paths_config["bank_split_output_dir"]),
            ROOT, company, month, pipeline_source_key,
        )
        bank_split_report_path = resolve_config_path(
            str(paths_config["bank_split_report_file"]),
            ROOT, company, month, pipeline_source_key,
        )
        try:
            bank_split_report = split_configured_bank_pdfs(
                settings.get("banks"),
                bank_input_dir,
                bank_split_output,
                bank_split_report_path,
            )
        except BankReceiptSplitError as exc:
            print(f"银行回单裁剪失败：{exc}", file=sys.stderr)
            return 2
        if args.concise:
            print("[1/4] 回单裁剪：成功")
            print(
                f"  银行：{bank_split_report['summary']['bankCount']}；"
                f"回单：{bank_split_report['summary']['receiptCount']}；"
                f"切割时进入 bank exception：{bank_split_report['summary']['bankExceptionReceiptCount']}；"
                f"重新裁剪银行：{bank_split_report['summary']['generatedBankCount']}；"
                f"复用银行：{bank_split_report['summary']['reusedBankCount']}"
            )
        else:
            print(
                f"银行回单裁剪完成：银行={bank_split_report['summary']['bankCount']}，"
                f"回单={bank_split_report['summary']['receiptCount']}，"
                f"切割时进入bank_exception={bank_split_report['summary']['bankExceptionReceiptCount']}，"
                f"新生成={bank_split_report['summary']['generatedBankCount']}，"
                f"复用={bank_split_report['summary']['reusedBankCount']}；"
                f"目录={bank_split_output}"
            )
        checkpoint(
            "bank_split_complete",
            artifacts={"bankSplitReport": str(bank_split_report_path.resolve())},
            counters={"bankReceiptCount": bank_split_report["summary"]["receiptCount"]},
        )
        checkpoint("bank_exception_filter")
        bank_exception_path = map_path.parent / "bank_exceptions.json"
        bank_exception_output = bank_split_output.parent / "bank_exceptions"
        try:
            bank_exception_pdf_keywords = load_bank_exception_pdf_keywords(
                ROOT / "config" / "bank_exception.defaults.json"
            )
            bank_exception_report = filter_bank_exception_pdfs(
                settings.get("banks"),
                settings.get("exceptions") or [],
                bank_input_dir,
                bank_split_report,
                bank_exception_output,
                bank_exception_path,
                config_company=document_entity_name,
                pdf_keyword_rules=bank_exception_pdf_keywords,
            )
        except (
            BankExceptionFilterError,
            BankStatementMatchError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            print(f"银行特殊对象过滤失败：{exc}", file=sys.stderr)
            return 2
        bank_exception_summary = bank_exception_report["summary"]
        if args.concise:
            print("[2/4] 特殊对象过滤：成功")
            print(
                f"  名单流水：{bank_exception_summary['exceptionStatementCount']}；"
                f"切割 exception：{bank_exception_summary['splitExceptionPdfCount']}；"
                f"特殊PDF：{bank_exception_summary['exceptionPdfCount']}；"
                f"已复制：{bank_exception_summary['copiedPdfCount']}；"
                f"缺少PDF：{bank_exception_summary['missingPdfCount']}"
            )
            print(f"  特殊目录：{bank_exception_output}")
        else:
            print(
                "银行特殊对象过滤完成："
                f"名单流水={bank_exception_summary['exceptionStatementCount']}，"
                f"切割exception={bank_exception_summary['splitExceptionPdfCount']}，"
                f"PDF={bank_exception_summary['exceptionPdfCount']}，"
                f"已复制={bank_exception_summary['copiedPdfCount']}，"
                f"缺少PDF={bank_exception_summary['missingPdfCount']}；"
                f"目录={bank_exception_output}"
            )
        checkpoint(
            "bank_exception_filter_complete",
            artifacts={
                "bankExceptionMap": str(bank_exception_path.resolve()),
                "bankExceptionDirectory": str(bank_exception_output.resolve()),
            },
            counters={
                "bankExceptionStatementCount": bank_exception_summary[
                    "exceptionStatementCount"
                ],
                "bankExceptionPdfCount": bank_exception_summary[
                    "exceptionPdfCount"
                ],
            },
        )
        checkpoint("bank_ocr")
        bank_ocr_output = resolve_config_path(
            str(paths_config["receipts_ocr_dir"]),
            ROOT,
            company,
            month,
            pipeline_source_key,
        )
        try:
            person_name_exclusions = collect_person_name_exclusions(
                settings.get("banks"), bank_input_dir
            )
            bank_ocr_report = run_bank_receipt_ocr(
                bank_split_report,
                bank_ocr_output,
                workers=int(settings.get("ocr_workers", 2) or 1),
                company=document_entity_name,
                excluded_indices=person_name_exclusions,
                excluded_pdf_paths=bank_exception_report.get("excludedPdfPaths"),
            )
        except (
            BankReceiptOcrError,
            BankStatementMatchError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            print(f"银行回单 OCR 失败：{exc}", file=sys.stderr)
            return 2
        bank_ocr_summary = bank_ocr_report["summary"]
        if args.concise:
            print("[3/4] 剩余回单 OCR：成功")
            print(
                f"  回单：{bank_ocr_summary['receiptCount']}；"
                f"预先过滤：{bank_ocr_summary['excludedBeforeOcrCount']}；"
                f"实际 OCR：{bank_ocr_summary['eligibleReceiptCount']}；"
                f"有文本：{bank_ocr_summary['successTextCount']}；"
                f"无文本：{bank_ocr_summary['emptyTextCount']}；"
                f"异常：{bank_ocr_summary['errorCount']}；"
                f"新生成：{bank_ocr_summary['generatedCount']}；"
                f"复用：{bank_ocr_summary['reusedCount']}"
            )
        else:
            print(
                f"银行回单 OCR 完成：回单={bank_ocr_summary['receiptCount']}，"
                f"预先过滤={bank_ocr_summary['excludedBeforeOcrCount']}，"
                f"实际OCR={bank_ocr_summary['eligibleReceiptCount']}，"
                f"有文本={bank_ocr_summary['successTextCount']}，"
                f"无文本={bank_ocr_summary['emptyTextCount']}，"
                f"异常={bank_ocr_summary['errorCount']}，"
                f"新生成={bank_ocr_summary['generatedCount']}，"
                f"复用={bank_ocr_summary['reusedCount']}；目录={bank_ocr_output}"
            )
        checkpoint(
            "bank_ocr_complete",
            artifacts={
                "bankSplitReport": str(bank_split_report_path.resolve()),
                "bankOcrReport": str((bank_ocr_output / "ocr_stage.report.json").resolve()),
            },
            counters={
                "bankReceiptCount": bank_ocr_summary["receiptCount"],
                "bankExcludedBeforeOcrCount": bank_ocr_summary[
                    "excludedBeforeOcrCount"
                ],
                "bankOcrSuccessTextCount": bank_ocr_summary["successTextCount"],
                "bankOcrErrorCount": bank_ocr_summary["errorCount"],
            },
        )
        if bank_ocr_summary["errorCount"]:
            print(
                f"[警告] 银行回单 OCR 异常={bank_ocr_summary['errorCount']}；"
                "将归入 bank_exceptions，正常记录继续处理。"
            )
        checkpoint("bank_matching")
        bank_map_path = map_path.parent / "bank_map.json"
        bank_map_report_path = map_path.parent / "bank_map.report.json"
        try:
            bank_match_report = match_bank_statements(
                settings.get("banks"),
                bank_input_dir,
                bank_ocr_report,
                bank_map_path,
                bank_map_report_path,
                config_company=document_entity_name,
                excluded_statement_indices={
                    bank_key: set(indexes)
                    for bank_key, indexes in (
                        bank_exception_report.get("excludedStatementIndices") or {}
                    ).items()
                },
            )
        except (BankStatementMatchError, OSError, json.JSONDecodeError) as exc:
            print(f"银行流水匹配失败：{exc}", file=sys.stderr)
            return 2
        bank_match_summary = bank_match_report["summary"]
        bank_exception_report = quarantine_bank_runtime_exceptions(
            bank_exception_report,
            bank_exception_output,
            bank_exception_path,
            bank_ocr_report,
            bank_match_report,
        )
        bank_exception_summary = bank_exception_report["summary"]
        if args.concise:
            match_status = "成功" if str(bank_match_report["status"]).startswith("ok") else "不完整"
            print(f"[4/4] 剩余流水匹配：{match_status}")
            print(
                f"  流水：{bank_match_summary['statementRowCount']}；"
                f"特殊过滤：{bank_match_summary['exceptionFilteredStatementCount']}；"
                f"匹配：{bank_match_summary['matchedCount']}；"
                f"普通未匹配流水：{bank_match_summary['unmatchedStatementCount']}；"
                f"普通未匹配回单：{bank_match_summary['unmatchedReceiptCount']}；"
                f"个人姓名跳过：{bank_match_summary['skippedPersonNameCount']}；"
                f"方向异常：{bank_match_summary['directionErrorCount']}；"
                f"重复索引：{bank_match_summary['duplicateIndexCount']}"
            )
        else:
            print(
                f"银行流水匹配完成：状态={bank_match_report['status']}，"
                f"流水={bank_match_summary['statementRowCount']}，"
                f"特殊过滤={bank_match_summary['exceptionFilteredStatementCount']}，"
                f"匹配={bank_match_summary['matchedCount']}，"
                f"普通未匹配流水={bank_match_summary['unmatchedStatementCount']}，"
                f"普通未匹配回单={bank_match_summary['unmatchedReceiptCount']}，"
                f"个人姓名跳过={bank_match_summary['skippedPersonNameCount']}，"
                f"方向异常={bank_match_summary['directionErrorCount']}，"
                f"重复索引={bank_match_summary['duplicateIndexCount']}；"
                f"map={bank_map_path}"
            )
        checkpoint(
            "bank_matching_complete",
            artifacts={
                "bankSplitReport": str(bank_split_report_path.resolve()),
                "bankOcrReport": str((bank_ocr_output / "ocr_stage.report.json").resolve()),
                "bankMap": str(bank_map_path.resolve()),
                "bankMapReport": str(bank_map_report_path.resolve()),
                "bankExceptionMap": str(bank_exception_path.resolve()),
            },
            counters={
                "bankStatementRowCount": bank_match_summary["statementRowCount"],
                "bankMatchedCount": bank_match_summary["matchedCount"],
                "bankUnmatchedStatementCount": bank_match_summary["unmatchedStatementCount"],
                "bankUnmatchedReceiptCount": bank_match_summary["unmatchedReceiptCount"],
                "bankSkippedPersonNameCount": bank_match_summary[
                    "skippedPersonNameCount"
                ],
                "bankExceptionCount": bank_exception_summary[
                    "exceptionStatementCount"
                ],
                "bankExceptionCopiedPdfCount": bank_exception_summary[
                    "copiedPdfCount"
                ],
            },
        )
        runtime_exception_count = int(
            bank_exception_summary.get("runtimeExceptionCount", 0) or 0
        )
        if runtime_exception_count:
            print(
                f"[警告] 银行运行异常={runtime_exception_count}，已全部归入 "
                f"bank_exceptions；正常匹配={bank_match_summary['matchedCount']}，继续下一阶段。"
            )
        if analysis_stage == "ocr":
            if args.concise:
                print("[成功] 银行 OCR 与流水匹配完成")
                print(f"  结果目录：{workspace_root / 'generated'}")
                print("  未生成 receipt；下一步：analysis-only + llm")
            else:
                print("银行 OCR 与流水匹配完成；本阶段不生成 receipt。下一步运行 analysis-only + llm。")
            return 0

        try:
            bank_matched, _bank_unmatched_markers = load_bank_records(
                bank_map_path, bank_map_report_path
            )
        except BankFinalReceiptError as exc:
            print(f"银行后续阶段输入错误：{exc}", file=sys.stderr)
            return 2
        exception_entries = bank_exception_report.get("entries")
        exception_keys = (
            set(exception_entries)
            if isinstance(exception_entries, dict)
            else set()
        )
        leaked_exception_keys = sorted(set(bank_matched) & exception_keys)
        if leaked_exception_keys:
            print(
                "特殊对象仍出现在普通 bank_map，已阻断后续流程："
                f"{leaked_exception_keys[:10]}",
                file=sys.stderr,
            )
            return 2
        bank_analysis_path = bank_ocr_output / "template_analysis.json"

        if analysis_stage in {"llm", "all"}:
            checkpoint("bank_llm")
            try:
                final_sample_value = str(settings.get("final_template_sample") or "").strip()
                final_sample_path = (ROOT / final_sample_value).resolve()
                final_sample = load_final_template_sample(final_sample_path)
                analysis_config = AppConfig.from_json(app_config_path, ROOT)
                analysis_api = KdzwyApi(replace(analysis_config, expected_company=expected_company))
                analysis_api.get_dynamic_system_params()
                bank_preload_setting = settings.get("preload_items", False)
                if bank_preload_setting is True:
                    bank_preload_mode = "once"
                elif bank_preload_setting in (False, None):
                    bank_preload_mode = "off"
                else:
                    bank_preload_mode = str(bank_preload_setting).strip().lower()
                if bank_preload_mode not in {"off", "once", "auto"}:
                    raise BankFinalReceiptError(
                        'bank.preload_items 只支持 false、"once" 或 "auto"'
                    )
                bank_preload_state_path = bank_map_path.parent / "item_preload.state.json"
                # The configured source workbooks live under the month's input
                # directory. Reading from month_dir silently produced empty role
                # evidence and prevented bank counterparties from being created.
                bank_role_evidence = collect_source_item_names(input_dir, config)
                bank_preload_fingerprint = {
                    "target": expected_company,
                    "mapSize": bank_map_path.stat().st_size,
                    "mapMtimeNs": bank_map_path.stat().st_mtime_ns,
                    "roleEvidence": {
                        str(class_id): sorted(names)
                        for class_id, names in bank_role_evidence.items()
                    },
                }
                bank_preload_reused = False
                if bank_preload_mode == "once" and bank_preload_state_path.is_file():
                    try:
                        saved_preload_state = json.loads(
                            bank_preload_state_path.read_text(encoding="utf-8-sig")
                        )
                        bank_preload_reused = (
                            saved_preload_state.get("status") == "success"
                            and saved_preload_state.get("fingerprint")
                            == bank_preload_fingerprint
                        )
                    except (OSError, json.JSONDecodeError, TypeError):
                        bank_preload_reused = False
                if bank_preload_mode != "off" and not bank_preload_reused:
                    bank_preload = preload_bank_counterparties(
                        analysis_api,
                        bank_matched,
                        create_missing=True,
                        role_evidence=bank_role_evidence,
                    )
                    bank_preload_report = {
                        "status": (
                            "success"
                            if not bank_preload.unresolved
                            else "incomplete_with_unresolved_counterparties"
                        ),
                        "source": "source Excel and full live catalogs, with validated bank debit/credit direction for organization names",
                        "sourceColumns": bank_preload.source_columns,
                        "roleEvidence": {
                            str(class_id): sorted(names)
                            for class_id, names in bank_role_evidence.items()
                        },
                        "resolved": bank_preload.resolved,
                        "unresolved": bank_preload.unresolved,
                        "created": bank_preload.created,
                        "counts": {
                            str(class_id): len(rows)
                            for class_id, rows in bank_preload.by_class.items()
                        },
                        "summary": {
                            "matchedRecordCount": len(bank_matched),
                            "roleEvidenceCount": sum(
                                len(names) for names in bank_role_evidence.values()
                            ),
                            "resolvedRecordCount": len(bank_preload.resolved),
                            "unresolvedRecordCount": len(bank_preload.unresolved),
                            "createdCount": len(bank_preload.created),
                        },
                    }
                    bank_preload_report_path = bank_map_path.parent / "item_preload.report.json"
                    bank_preload_report_path.write_text(
                        json.dumps(bank_preload_report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    if bank_preload_mode == "once":
                        bank_preload_state_path.write_text(
                            json.dumps(
                                {
                                    "status": (
                                        "success"
                                        if not bank_preload.unresolved
                                        else "incomplete"
                                    ),
                                    "fingerprint": bank_preload_fingerprint,
                                    "unresolvedCount": len(bank_preload.unresolved),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                    print(
                        "银行客户/供应商预加载完成："
                        f"客户={len(bank_preload.by_class.get(1, {}))}，"
                        f"供应商={len(bank_preload.by_class.get(5, {}))}，"
                        f"新增={len(bank_preload.created)}，"
                        f"未解析={len(bank_preload.unresolved)}"
                    )
                    if bank_preload.unresolved:
                        print(
                            "[警告] 银行预加载未完全结束："
                            f"{len(bank_preload.unresolved)} 条交易方缺少有效组织名称、金额方向或目录证据；"
                            "本次不会把空结果记录为成功，后续运行将继续核对。",
                            file=sys.stderr,
                        )
                elif bank_preload_reused:
                    print("银行客户/供应商预加载已完成且 bank_map 未变化，本次复用。")
                account_data = analysis_api.get_subject_tree(effective=0, expand=True)

                def flatten_bank_accounts(rows):
                    flattened = []
                    for row in rows if isinstance(rows, list) else []:
                        if isinstance(row, dict):
                            flattened.append(row)
                            flattened.extend(flatten_bank_accounts(row.get("child", [])))
                    return flattened

                account_catalog = flatten_bank_accounts(account_data.get("rows", []))
                item_catalog = analysis_api.get_all_items_v1()
                account_meta = {
                    "accountClasses": analysis_api.get_account_classes(),
                    "voucherGroups": analysis_api.get_voucher_groups_v1(),
                    "currencies": analysis_api.get_currencies(),
                    "itemClasses": analysis_api.get_item_classes(),
                }
                existing_analyzed: dict[str, dict[str, object]] = {}
                if bank_analysis_path.is_file():
                    try:
                        saved_analysis = json.loads(
                            bank_analysis_path.read_text(encoding="utf-8-sig")
                        )
                    except (OSError, json.JSONDecodeError):
                        saved_analysis = {}
                    if isinstance(saved_analysis, dict):
                        existing_analyzed = {
                            str(key): dict(value)
                            for key, value in saved_analysis.items()
                            if key in bank_matched
                            and isinstance(value, dict)
                            and value.get("analysisStatus") == "ready_for_review"
                        }
                artifacts = [
                    artifact
                    for artifact in build_bank_ocr_artifacts(bank_matched)
                    if artifact.invoice_code not in existing_analyzed
                ]
                selector = OpenAICompatibleTemplateSelector.from_settings(settings)
                worker_count = min(max(1, int(settings.get("llm_workers", 2) or 1)), max(1, len(artifacts)))

                def analyze_bank_artifact(artifact):
                    values = bank_source_values(bank_matched[artifact.invoice_code])
                    context = build_final_template_context(
                        final_sample,
                        account_catalog,
                        item_catalog,
                        source="bank",
                        map_values=values,
                    )
                    context["runtimeAccountMeta"] = account_meta
                    context["businessMapValues"] = values
                    return analyze_ocr_and_choose_template(
                        artifact,
                        template_root,
                        selector=selector,
                        final_template_context=context,
                    )

                analyzed: dict[str, dict[str, object]] = dict(existing_analyzed)
                if existing_analyzed:
                    print(
                        "银行 LLM 复用已通过分析："
                        f"{len(existing_analyzed)} 张；仅重试未通过记录={len(artifacts)}"
                    )
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    pending = [(artifact, executor.submit(analyze_bank_artifact, artifact)) for artifact in artifacts]
                    for index, (artifact, future) in enumerate(pending, 1):
                        logger.info("银行 Qwen 分析 %s/%s：%s", index, len(artifacts), artifact.invoice_code)
                        try:
                            analyzed[artifact.invoice_code] = compact_analysis_for_storage(future.result())
                        except Exception as exc:
                            analyzed[artifact.invoice_code] = {
                                "status": "error",
                                "analysisStatus": "blocked",
                                "reason": str(exc),
                                "sourcePdf": str(artifact.source_pdf),
                            }
                bank_analysis_path.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except (BankFinalReceiptError, OSError, json.JSONDecodeError, ValueError) as exc:
                print(f"银行 LLM 分析失败：{exc}", file=sys.stderr)
                return 2
            finally:
                if "analysis_api" in locals() and hasattr(analysis_api, "close"):
                    analysis_api.close()
            ready_count = sum(item.get("analysisStatus") == "ready_for_review" for item in analyzed.values())
            blocked_count = len(analyzed) - ready_count
            api_attempted_count = sum(bool(item.get("llmAttempted")) for item in analyzed.values())
            api_success_count = sum(item.get("selectionMode") == "llm_api" for item in analyzed.values())
            amount_ready_count = sum(
                isinstance(item.get("extractedFields"), dict)
                and item["extractedFields"].get("amountValidated") is True
                and item["extractedFields"].get("transactionAmount") not in (None, "")
                for item in analyzed.values()
            )
            print(
                f"银行 LLM 分析完成：总数={len(analyzed)}，API已请求={api_attempted_count}，"
                f"API成功响应={api_success_count}，金额已标准化={amount_ready_count}，"
                f"可复核={ready_count}，blocked={blocked_count}"
            )
            print(f"分析文件：{bank_analysis_path}")
            checkpoint("bank_llm_complete", artifacts={"templateAnalysis": str(bank_analysis_path.resolve())}, counters={"analysisCount": len(analyzed), "analysisBlockedCount": blocked_count})
            if mode == "analysis-only" or analysis_stage != "existing":
                print("本阶段不生成 receipt；复核分析后设置 mode=prepare、analysis_stage=existing。")
                return 0

        if analysis_stage != "existing":
            print("银行最终 receipt 只能由 analysis_stage=existing 生成。", file=sys.stderr)
            return 2
        try:
            bank_analysis = json.loads(bank_analysis_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"缺少或无法读取已批准银行 LLM 分析：{bank_analysis_path}：{exc}", file=sys.stderr)
            return 2
        if not isinstance(bank_analysis, dict):
            print(f"银行 LLM 分析必须是 JSON 对象：{bank_analysis_path}", file=sys.stderr)
            return 2
        missing_bank_analysis = sorted(set(bank_matched) - set(bank_analysis))
        if missing_bank_analysis:
            print(f"银行 LLM 分析不完整，缺少：{missing_bank_analysis[:10]}", file=sys.stderr)
            return 2
        try:
            for key, record in bank_matched.items():
                current_analysis = bank_analysis.get(key)
                if (
                    isinstance(current_analysis, dict)
                    and current_analysis.get("analysisStatus") == "ready_for_review"
                ):
                    validate_bank_analysis_rules(record, current_analysis)
        except BankFinalReceiptError as exc:
            print(f"银行已有分析的固定科目校验失败：{exc}", file=sys.stderr)
            return 2

        if mode == "analysis-only":
            print("已批准银行 LLM 分析检查完成；analysis-only 不生成 receipt。")
            return 0
        if mode == "prepare":
            try:
                prepare_config = AppConfig.from_json(app_config_path, ROOT)
                prepare_api = KdzwyApi(replace(prepare_config, expected_company=expected_company))
                prepare_api.get_dynamic_system_params()
                resolved = resolve_defaults(prepare_api, settings)
                voucher_defaults = dict(resolved["voucher_defaults"])
                voucher_defaults["user_name"] = resolve_current_user(prepare_api)["userName"]
                generation = generate_bank_final_receipts(
                    bank_matched,
                    bank_analysis,
                    receipt_dir,
                    company,
                    month,
                    voucher_defaults,
                )
            except (BankFinalReceiptError, OSError, ValueError) as exc:
                print(f"银行最终 receipt 生成失败：{exc}", file=sys.stderr)
                return 2
            finally:
                if "prepare_api" in locals() and hasattr(prepare_api, "close"):
                    prepare_api.close()
            print(
                f"银行 prepare+existing 完成：匹配记录={generation['summary']['matchedRecordCount']}，"
                f"有效 receipt={generation['summary']['receiptCount']}，"
                f"新生成={generation['summary']['generatedCount']}，"
                f"已存在未覆盖={generation['summary']['reusedCount']}，"
                f"分析未就绪={generation['summary']['blockedAnalysisCount']}"
            )
            print("最终 receipt 已生成且保持 draft=true；人工复核完成后改为 false，再运行 verify。")
            return 0

        verification = verify_bank_receipts(
            receipt_dir, allowed_record_keys=set(bank_matched)
        )
        verification_summary = verification["summary"]
        print(
            f"银行提交前检查：receipt={verification_summary['receiptCount']}，"
            f"draft=true={verification_summary['draftCount']}，可提交={verification_summary['readyCount']}，"
            f"无效={verification_summary['invalidCount']}，"
            f"旧/特殊产物={verification_summary.get('orphanCount', 0)}"
        )
        if verification["status"] != "ready":
            print("银行最终 receipt 尚未全部通过，禁止进入 dry-run/confirm。", file=sys.stderr)
            return 3
        batch_command = [
            sys.executable,
            str(ROOT / "scripts" / "commands" / "batch_receipts.py"),
            "--project-root", str(ROOT),
            "--runtime-root", str(workspace_root),
            "--config", str(app_config_path),
            "--expected-company", expected_company,
            "--input-dir", str(receipt_dir),
            "--source", "bank",
        ]
        if mode == "confirm":
            batch_command.append("--confirm")
        return subprocess.call(batch_command)
    checkpoint("mapping")
    map_report = _empty_match_report()
    if pipeline_source_key in {"purchase", "all"}:
        map_report = match_month_directory(input_dir, config, map_path.parent)
    income_path = input_dir / config.income_cost_filename
    preload_report = None
    preload_result = None
    preload_setting = settings.get("preload_items", False)
    if preload_setting is True:
        preload_mode = "once"
    elif preload_setting is False or preload_setting is None:
        preload_mode = "off"
    else:
        preload_mode = str(preload_setting).strip().lower()
    if preload_mode not in {"off", "once", "auto"}:
        raise ValueError('preload_items 只支持 false、"once" 或 "auto"')
    if preload_mode != "off":
        map_path.parent.mkdir(parents=True, exist_ok=True)

    preload_state_path = map_path.parent / "item_preload.state.json"
    preload_fingerprint = {
        "accountbook": str(settings.get("accountbook_key", "")),
        "company": expected_company,
        "sourceCompany": company,
        "month": month,
        "source": str(settings.get("source", "all")),
        "inputs": [
            {
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in sorted(input_dir.rglob("*.xlsx"))
        ],
    }
    preload_already_done = False
    if preload_mode == "once" and preload_state_path.is_file():
        try:
            preload_state = json.loads(preload_state_path.read_text(encoding="utf-8-sig"))
            preload_already_done = (
                preload_state.get("status") == "success"
                and preload_state.get("fingerprint") == preload_fingerprint
            )
        except (OSError, ValueError, TypeError):
            preload_already_done = False

    preload_enabled = preload_mode != "off"
    if preload_enabled and str(settings.get("accountbook_source", "live")) == "live":
        if preload_already_done:
            print(f"ItemClass本地输入未变化；业务映射生成后仍会核对远端：{settings.get('source', 'all')}")
        else:
            print(f"ItemClass将在业务映射生成后核对远端：{settings.get('source', 'all')}")
    usage_path = input_dir / config.usage_filename
    sales_map_report = _empty_map_report()
    purchase_map_report = _empty_map_report()
    if pipeline_source_key in {"sales", "all"}:
        sales_map_report = build_sales_map(income_path, sales_map_path, sales_map_report_path)
        sales_map_report = add_sales_pdf_fallback_candidates(
            sales_map_report,
            input_dir / "sales",
            sales_map_path,
            sales_map_report_path,
        )
    if pipeline_source_key in {"purchase", "all"}:
        purchase_map_report = build_purchase_map(
            usage_path, purchase_map_path, purchase_map_report_path
        )
    if preload_result is not None:
        apply_preloaded_items(sales_map_report["map"], preload_result, 1, "customName", "customerId", "customerNumber")
        apply_preloaded_items(purchase_map_report["map"], preload_result, 5, "supplierName", "supplierId", "supplierNumber")
    # purchase_map itself contains every numeric row in 用途确认信息; the pipeline
    # scope is narrower: only usage-confirmed codes with a matched purchase PDF.
    if pipeline_source_key in {"purchase", "all"}:
        purchase_map_total_count = len(purchase_map_report["map"])
        purchase_map_report["map"] = {
            code: values for code, values in purchase_map_report["map"].items()
            if map_report.get("map", {}).get(code, "")
        }
        purchase_map_report["report"]["scope"] = "仅保留用途确认信息.xlsx E列且purchase目录存在匹配PDF的发票号"
        purchase_map_report["report"]["summary"]["rawInvoiceCount"] = purchase_map_total_count
        purchase_map_report["report"]["summary"]["filteredInvoiceCount"] = len(purchase_map_report["map"])
        purchase_map_report["report"]["summary"]["excludedByUsagePdfFilterCount"] = purchase_map_total_count - len(purchase_map_report["map"])
        purchase_map_path.write_text(json.dumps(purchase_map_report["map"], ensure_ascii=False, indent=2), encoding="utf-8")
        purchase_map_report_path.write_text(json.dumps(purchase_map_report["report"], ensure_ascii=False, indent=2), encoding="utf-8")
    if preload_enabled and str(settings.get("accountbook_source", "live")) == "live":
        checkpoint("item_preload")
        map_item_names = collect_map_item_names(
            sales_map_report.get("map", {}),
            purchase_map_report.get("map", {}),
        )
        preload_config = AppConfig.from_json(app_config_path, ROOT)
        preload_api = KdzwyApi(replace(preload_config, expected_company=expected_company))
        preload_api.get_dynamic_system_params()
        preload_result = preload_items(
            preload_api,
            input_dir,
            config,
            create_missing=True,
            wanted_items=map_item_names,
        )
        apply_preloaded_items(
            sales_map_report["map"], preload_result, 1,
            "customName", "customerId", "customerNumber",
        )
        apply_preloaded_items(
            purchase_map_report["map"], preload_result, 5,
            "supplierName", "supplierId", "supplierNumber",
        )
        if pipeline_source_key in {"sales", "all"}:
            sales_map_path.write_text(
                json.dumps(sales_map_report["map"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if pipeline_source_key in {"purchase", "all"}:
            purchase_map_path.write_text(
                json.dumps(purchase_map_report["map"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        preload_report = {
            "sourceColumns": preload_result.source_columns,
            "created": preload_result.created,
            "counts": {
                str(class_id): len(rows)
                for class_id, rows in preload_result.by_class.items()
            },
            "sourceKind": "generated_business_maps",
        }
        preload_path = map_path.parent / "item_preload.report.json"
        preload_path.write_text(
            json.dumps(preload_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if preload_mode == "once":
            preload_state_path.parent.mkdir(parents=True, exist_ok=True)
            preload_state_tmp = preload_state_path.with_suffix(".json.tmp")
            preload_state_tmp.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "fingerprint": preload_fingerprint,
                        "verifiedSourceColumns": preload_result.source_columns,
                        "sourceKind": "generated_business_maps",
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            preload_state_tmp.replace(preload_state_path)
        print(
            f"ItemClass远端核对完成：客户={len(map_item_names.get(1, []))}，"
            f"供应商={len(map_item_names.get(5, []))}，新增={len(preload_result.created)}"
        )
        checkpoint(
            "item_preload_complete",
            artifacts={"itemPreloadReport": str(preload_path.resolve())},
            counters={"itemPreloadCreatedCount": len(preload_result.created)},
        )

    mapping_artifacts = {}
    if pipeline_source_key in {"purchase", "all"}:
        mapping_artifacts.update({
            "xlsxPdfMap": str(map_path.resolve()),
            "purchaseMap": str(purchase_map_path.resolve()),
        })
    if pipeline_source_key in {"sales", "all"}:
        mapping_artifacts["salesMap"] = str(sales_map_path.resolve())
    checkpoint(
        "mapping_complete",
        artifacts=mapping_artifacts,
        counters={"salesInvoiceCount": len(sales_map_report["map"]), "purchaseInvoiceCount": len(purchase_map_report["map"])},
    )
    template_config = json.loads(template_path.read_text(encoding="utf-8")) if template_path.is_file() else {}
    final_sample_value = str(settings.get("final_template_sample") or "").strip()
    if not final_sample_value:
        raise RuntimeError("运行配置缺少所选模板公司的 final_template_sample")
    final_sample_path = (ROOT / final_sample_value).resolve()
    if not final_sample_path.is_file():
        raise RuntimeError(f"最终模板样例不存在：{final_sample_path}")
    final_sample = load_final_template_sample(final_sample_path)
    receipts_ocr_dir = resolve_config_path(str(paths_config["receipts_ocr_dir"]), ROOT, company, month, pipeline_source_key)
    ocr_analysis_by_invoice: dict[str, dict[str, object]] = {}
    account_api_for_analysis = None
    runtime_account_catalog: list[dict[str, object]] = []
    runtime_item_catalog: dict[str, dict[str, object]] = {}
    needs_live_analysis_catalog = mode != "analysis-only" or analysis_stage in {"llm", "all"}
    if str(settings.get("accountbook_source", "live")) == "live" and needs_live_analysis_catalog:
        checkpoint("dynamic_catalog")
        analysis_api_config = AppConfig.from_json(app_config_path, ROOT)
        account_api_for_analysis = KdzwyApi(replace(analysis_api_config, expected_company=expected_company))
        account_api_for_analysis.get_dynamic_system_params()
        if preload_result is not None:
            runtime_item_catalog = {
                label: {"itemClassId": class_id, "items": list(bucket.values())}
                for class_id, bucket in preload_result.by_class.items()
                for label in resolve_item_class_labels(class_id)
            }
        account_data = account_api_for_analysis.get_subject_tree(effective=0, expand=True)

        def flatten_accounts(rows):
            flattened = []
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    flattened.append(row)
                    flattened.extend(flatten_accounts(row.get("child", [])))
            return flattened

        runtime_account_catalog = flatten_accounts(account_data.get("rows", []))
        if preload_result is None:
            runtime_item_catalog = account_api_for_analysis.get_all_items_v1()
        runtime_account_catalog_meta = {
            "accountClasses": account_api_for_analysis.get_account_classes(),
            "voucherGroups": account_api_for_analysis.get_voucher_groups_v1(),
            "currencies": account_api_for_analysis.get_currencies(),
            "itemClasses": account_api_for_analysis.get_item_classes(),
        }
    else:
        runtime_account_catalog_meta = {}
    ocr_stage_report_path = receipts_ocr_dir / "ocr_stage.report.json"
    workflow_exception_path = map_path.parent / "workflow_exceptions.json"
    if template_catalog:
        from kdzwy_receipt_uploader.receipts_ocr import OcrPipelineError, load_ocr_artifacts, run_ocr_stage

        configured_company = document_entity_name
        purchase_mapped_codes = set(map_report.get("map", {})) if pipeline_source_key in {"all", "purchase"} else set()
        sales_mapped_codes = set(sales_map_report["map"]) if pipeline_source_key in {"all", "sales"} else set()
        mapped_ocr_codes = purchase_mapped_codes | sales_mapped_codes
        source_pdf_index, source_invalid_pdfs = discover_source_pdfs(input_dir, pdf_folders)
        source_pdf_codes = set(source_pdf_index)
        only_mapped_invoices = bool(settings.get("only_mapped_invoices", False))
        allowed_ocr_codes = (
            source_pdf_codes & mapped_ocr_codes
            if only_mapped_invoices
            else set(source_pdf_codes)
        )
        duplicate_pdf_groups = [
            {
                "invoiceCode": code,
                "pdfs": [str(path) for path in paths],
            }
            for code, paths in sorted(source_pdf_index.items())
            if len(paths) > 1
        ]
        raw_pdf_count = sum(len(paths) for paths in source_pdf_index.values()) + len(source_invalid_pdfs)
        single_pdf_count = sum(1 for paths in source_pdf_index.values() if len(paths) == 1)
        duplicate_pdf_count = sum(len(paths) for paths in source_pdf_index.values() if len(paths) > 1)
        pdf_inventory_path = map_path.parent / "pdf_inventory.report.json"
        pdf_inventory_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_inventory_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": pipeline_source_key,
                    "sourceOfTruth": "input PDF files",
                    "folders": list(pdf_folders),
                    "onlyMappedInvoices": only_mapped_invoices,
                    "summary": {
                        "rawPdfCount": raw_pdf_count,
                        "singlePdfCount": single_pdf_count,
                        "invoiceCodeCount": len(source_pdf_codes),
                        "duplicateInvoiceCodeCount": len(duplicate_pdf_groups),
                        "duplicatePdfCount": duplicate_pdf_count,
                        "invalidPdfCount": len(source_invalid_pdfs),
                        "mappedPdfInvoiceCodeCount": len(source_pdf_codes & mapped_ocr_codes),
                        "unmappedPdfInvoiceCodeCount": len(source_pdf_codes - mapped_ocr_codes),
                        "excelOnlyMappingCount": len(mapped_ocr_codes - source_pdf_codes),
                        "processingInvoiceCodeCount": len(allowed_ocr_codes),
                    },
                    "invalidPdfs": source_invalid_pdfs,
                    "duplicatePdfs": duplicate_pdf_groups,
                    "unmappedPdfInvoiceCodes": sorted(source_pdf_codes - mapped_ocr_codes),
                    "excelOnlyInvoiceCodes": sorted(mapped_ocr_codes - source_pdf_codes),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info(
            "PDF清点：source=%s raw=%s single=%s duplicate_files=%s invalid=%s mapped=%s unmapped=%s processing=%s",
            pipeline_source_key,
            raw_pdf_count,
            single_pdf_count,
            duplicate_pdf_count,
            len(source_invalid_pdfs),
            len(source_pdf_codes & mapped_ocr_codes),
            len(source_pdf_codes - mapped_ocr_codes),
            len(allowed_ocr_codes),
        )
        print(
            f"PDF真实数量：{raw_pdf_count}；有效单张：{single_pdf_count}；"
            f"重复文件：{duplicate_pdf_count}；无效文件：{len(source_invalid_pdfs)}；"
            f"未匹配Excel：{len(source_pdf_codes - mapped_ocr_codes)}；清点报告：{pdf_inventory_path}"
        )
        if analysis_stage == "existing":
            checkpoint("analysis_existing")
            analysis_path = receipts_ocr_dir / "template_analysis.json"
            if not analysis_path.is_file():
                raise OcrPipelineError(f"缺少已批准Qwen分析：{analysis_path}")
            loaded_analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_analysis, dict):
                raise OcrPipelineError(f"Qwen分析文件不是JSON对象：{analysis_path}")
            refreshed_ocr_fields = 0
            for code, analysis in loaded_analysis.items():
                if not isinstance(analysis, dict):
                    continue
                current_ocr_path = receipts_ocr_dir / str(code) / "ocr.json"
                if not current_ocr_path.is_file():
                    continue
                try:
                    current_ocr = json.loads(current_ocr_path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                current_fields = current_ocr.get("fields")
                if isinstance(current_fields, dict):
                    analysis["ocrFields"] = dict(current_fields)
                    refreshed_ocr_fields += 1
            if refreshed_ocr_fields:
                analysis_path.write_text(
                    json.dumps(loaded_analysis, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "已用当前精确OCR刷新既有Qwen分析审计字段：%s 张",
                    refreshed_ocr_fields,
                )
            missing_analysis = sorted(allowed_ocr_codes - set(loaded_analysis))
            blocked_analysis = sorted(
                code for code in allowed_ocr_codes
                if isinstance(loaded_analysis.get(code), dict)
                and loaded_analysis[code].get("analysisStatus") != "ready_for_review"
            )
            exception_codes = sorted(set(missing_analysis) | set(blocked_analysis))
            exception_rows = []
            for code in exception_codes:
                analysis = loaded_analysis.get(code)
                if not isinstance(analysis, dict):
                    exception_rows.append({
                        "invoiceCode": code,
                        "status": "missing",
                        "analysisStatus": "missing",
                        "reason": "缺少Qwen分析结果",
                        "templatePath": "",
                        "error": "",
                    })
                    continue
                exception_rows.append({
                    "invoiceCode": code,
                    "status": str(analysis.get("status") or "blocked"),
                    "analysisStatus": str(analysis.get("analysisStatus") or "blocked"),
                    "reason": str(analysis.get("reason") or ""),
                    "templatePath": str(analysis.get("templatePath") or ""),
                    "error": str(analysis.get("error") or ""),
                })
            replace_analysis_exception_stages(
                workflow_exception_path,
                pipeline_source_key,
                loaded_analysis,
                allowed_ocr_codes,
            )
            if exception_codes:
                exception_path = map_path.parent / "analysis_exceptions.json"
                exception_path.parent.mkdir(parents=True, exist_ok=True)
                exception_path.write_text(
                    json.dumps({
                        "version": 1,
                        "source": pipeline_source_key,
                        "mode": mode,
                        "exceptionCount": len(exception_rows),
                        "exceptions": exception_rows,
                    }, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                allowed_ocr_codes.difference_update(exception_codes)
                logger.warning(
                    "分析异常分流：%s 张进入 exceptions，%s 张继续 %s",
                    len(exception_codes),
                    len(allowed_ocr_codes),
                    mode,
                )
                print(
                    f"[异常分流] {len(exception_codes)} 张未通过分析，已排除且写入：{exception_path}；"
                    f"{len(allowed_ocr_codes)} 张继续处理。"
                )
                checkpoint(
                    "analysis_exceptions",
                    artifacts={"analysisExceptions": str(exception_path.resolve()), "workflowExceptions": str(workflow_exception_path.resolve())},
                    counters={"analysisExceptionCount": len(exception_codes)},
                )
            ocr_analysis_by_invoice = {code: dict(loaded_analysis[code]) for code in allowed_ocr_codes}
            for code, analysis in ocr_analysis_by_invoice.items():
                if code in sales_map_report["map"]:
                    sales_map_report["map"][code]["ocrAnalysis"] = analysis
                if code in purchase_map_report["map"]:
                    purchase_map_report["map"][code]["ocrAnalysis"] = analysis
            ocr_artifacts = load_ocr_artifacts(receipts_ocr_dir, allowed_ocr_codes)
            logger.info("复用已批准Qwen分析：%s 张，不运行OCR、不调用Qwen", len(ocr_analysis_by_invoice))
        elif analysis_stage in {"ocr", "all"}:
            checkpoint("ocr")
            logger.info("OCR 阶段开始：source=%s folders=%s allowed=%s", pipeline_source_key, pdf_folders, len(allowed_ocr_codes))
            ocr_report, ocr_artifacts = run_ocr_stage(
                input_dir, receipts_ocr_dir, pdf_folders, company=configured_company,
                allowed_invoice_codes=allowed_ocr_codes, return_artifacts=True,
                workers=int(settings.get("ocr_workers", 2) or 1),
            )
            success_text_count = int(ocr_report.get("summary", {}).get("successTextCount", 0) or 0)
            logger.info("OCR 完成：%s/%s 个有效文本", success_text_count, len(ocr_artifacts))
            if ocr_artifacts and success_text_count == 0:
                engines = sorted({artifact.engine for artifact in ocr_artifacts})
                raise OcrPipelineError(f"OCR 未产生任何有效文本；引擎状态：{engines}")
            checkpoint("ocr_complete", artifacts={"ocrReport": str(ocr_stage_report_path.resolve()), "ocrDirectory": str(receipts_ocr_dir.resolve())}, counters={"ocrArtifactCount": len(ocr_artifacts), "ocrSuccessTextCount": success_text_count})
        else:
            checkpoint("ocr_reuse")
            logger.info("Qwen阶段：只读取已有OCR产物，不运行OCR")
            ocr_artifacts = load_ocr_artifacts(receipts_ocr_dir, allowed_ocr_codes)

        ocr_ready_codes = {
            artifact.invoice_code for artifact in ocr_artifacts
            if str(getattr(artifact, "text", "") or "").strip()
        }
        ocr_exception_codes = sorted(allowed_ocr_codes - ocr_ready_codes)
        replace_stage_exceptions(
            workflow_exception_path,
            pipeline_source_key,
            "ocr",
            ({
                "documentId": code,
                "errorType": "ocr_missing_or_empty",
                "message": "OCR产物缺失或没有有效文本",
            } for code in ocr_exception_codes),
        )
        if ocr_exception_codes:
            allowed_ocr_codes.difference_update(ocr_exception_codes)
            ocr_artifacts = [artifact for artifact in ocr_artifacts if artifact.invoice_code in allowed_ocr_codes]
            print(f"[异常分流] OCR未通过 {len(ocr_exception_codes)} 张，已写入：{workflow_exception_path}")

        if pipeline_source_key in {"sales", "all"}:
            fallback_result = finalize_sales_ocr_fallbacks(
                sales_map_report,
                receipts_ocr_dir,
                configured_company,
                month,
                sales_map_path,
                sales_map_report_path,
            )
            fallback_blocked_codes = {
                str(row.get("documentId") or "") for row in fallback_result["blocked"]
            }
            replace_stage_exceptions(
                workflow_exception_path,
                pipeline_source_key,
                "sales_ocr_fallback",
                fallback_result["blocked"],
            )
            if fallback_blocked_codes:
                allowed_ocr_codes.difference_update(fallback_blocked_codes)
                ocr_artifacts = [
                    artifact for artifact in ocr_artifacts
                    if artifact.invoice_code in allowed_ocr_codes
                ]
                print(
                    f"[异常分流] 收入成本表外销售发票校验未通过 "
                    f"{len(fallback_blocked_codes)} 张，已写入：{workflow_exception_path}"
                )
            if fallback_result["ready"]:
                logger.info(
                    "收入成本表外销售发票通过精确OCR校验：%s 张",
                    len(fallback_result["ready"]),
                )

        if analysis_stage in {"llm", "all"}:
            checkpoint("llm")
            try:
                llm_worker_count = max(1, int(settings.get("llm_workers", 2) or 1))
            except (TypeError, ValueError):
                llm_worker_count = 2
            llm_worker_count = min(llm_worker_count, max(1, len(ocr_artifacts)))
            selector = OpenAICompatibleTemplateSelector.from_settings(settings)
            if not selector.api_key:
                raise OcrPipelineError(
                    f"未配置 {selector.api_key_env}；Qwen 分析不会发起请求。"
                )
            logger.info("Qwen有限并发：%s 个工作线程；model=%s", llm_worker_count, selector.model)

            def analyze_artifact(artifact):
                map_values = dict(sales_map_report["map"].get(artifact.invoice_code) or purchase_map_report["map"].get(artifact.invoice_code) or {})
                final_context = build_final_template_context(final_sample, runtime_account_catalog, runtime_item_catalog, source=artifact.source_folder, map_values=map_values)
                final_context["runtimeAccountMeta"] = runtime_account_catalog_meta
                final_context["businessMapValues"] = map_values
                return analyze_ocr_and_choose_template(
                    artifact,
                    template_root,
                    selector=selector,
                    final_template_context=final_context,
                )

            with ThreadPoolExecutor(max_workers=llm_worker_count) as executor:
                pending_analysis = [
                    (artifact, executor.submit(analyze_artifact, artifact))
                    for artifact in ocr_artifacts
                ]
                for index, (artifact, future) in enumerate(pending_analysis, start=1):
                    source_pdf = artifact.source_pdf
                    logger.info("Qwen模板分析 %s/%s：%s", index, len(ocr_artifacts), source_pdf.name)
                    try:
                        analysis = future.result()
                        ocr_analysis_by_invoice[artifact.invoice_code] = analysis
                        if artifact.invoice_code in sales_map_report["map"]:
                            sales_map_report["map"][artifact.invoice_code]["ocrAnalysis"] = analysis
                        if artifact.invoice_code in purchase_map_report["map"]:
                            purchase_map_report["map"][artifact.invoice_code]["ocrAnalysis"] = analysis
                    except Exception as exc:
                        print(f"Qwen模板分析失败：{source_pdf}：{exc}")
                        ocr_analysis_by_invoice[artifact.invoice_code] = {
                            "status": "exception",
                            "analysisStatus": "blocked",
                            "exceptionStatus": "pending",
                            "exceptionType": "template_analysis_error",
                            "llmAttempted": False,
                            "reason": str(exc),
                            "sourceFolder": artifact.source_folder,
                            "sourcePdf": str(source_pdf.resolve()),
                        }
            stored_analysis = {
                code: compact_analysis_for_storage(analysis)
                for code, analysis in ocr_analysis_by_invoice.items()
            }
            (receipts_ocr_dir / "template_analysis.json").write_text(json.dumps(stored_analysis, ensure_ascii=False, indent=2), encoding="utf-8")
            analysis_exception_codes = sorted(replace_analysis_exception_stages(
                workflow_exception_path,
                pipeline_source_key,
                stored_analysis,
                allowed_ocr_codes,
            ))
            allowed_ocr_codes.difference_update(analysis_exception_codes)
            checkpoint("llm_complete", artifacts={"templateAnalysis": str((receipts_ocr_dir / "template_analysis.json").resolve())}, counters={"analysisCount": len(stored_analysis), "analysisBlockedCount": sum(1 for item in stored_analysis.values() if item.get("analysisStatus") != "ready_for_review")})
    else:
        allowed_ocr_codes = set()
        ocr_stage_report_path.parent.mkdir(parents=True, exist_ok=True)
        ocr_stage_report_path.write_text(
            json.dumps(
                {
                    "sourceDirectory": str(month_dir.resolve()),
                    "outputDirectory": str(receipts_ocr_dir.resolve()),
                    "allowedInvoiceCodes": [],
                    "filterRule": "template_catalog 缺失，已跳过 OCR 与 Qwen",
                    "artifacts": [],
                    "errors": [],
                    "summary": {"pdfCount": 0, "allowedInvoiceCodeCount": 0, "errorCount": 0, "successTextCount": 0},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if mode == "analysis-only":
        analysis_report_path = receipts_ocr_dir / "template_analysis.json"
        review_path = resolve_config_path(str(paths_config["preupload_review_file"]), ROOT, company, month, pipeline_source_key)
        if preload_result is not None:
            catalog_note = f"ItemClass已预加载，新增{len(preload_result.created)}个辅助核算对象"
        else:
            catalog_note = "只读读取账套目录" if account_api_for_analysis is not None else "使用本地快照"
        print(f"分析模式完成：stage={analysis_stage}，{catalog_note}，不生成receipt、不保存凭证、不上传附件。")
        print(f"OCR目录：{receipts_ocr_dir}")
        if analysis_stage in {"llm", "existing", "all"}:
            print(f"Qwen分析报告：{analysis_report_path}")
        else:
            print("Qwen未执行；下一步请使用 --stage llm。")
        print(
            f"PDF真实发票号：{len(source_pdf_codes)}，"
            f"Excel已映射：{len(source_pdf_codes & mapped_ocr_codes)}，"
            f"Excel未映射：{len(source_pdf_codes - mapped_ocr_codes)}，"
            f"当前通过范围：{len(allowed_ocr_codes)}"
        )
        checkpoint("analysis_complete", artifacts={"analysisDirectory": str(receipts_ocr_dir.resolve())})
        return 0
    account_source = str(settings.get("accountbook_source", "live"))
    if account_source == "live":
        api_config = AppConfig.from_json(app_config_path, ROOT)
        account_api = KdzwyApi(replace(api_config, expected_company=expected_company))
        template_entry_defaults: list[dict[str, Any]] = []
        seen_template_subjects: set[tuple[str, str]] = set()
        if template_catalog is not None:
            for record in template_catalog.records:
                if record.get("enabled") is not True:
                    continue
                relative_template = Path(str(record.get("path") or ""))
                if pipeline_source_key != "all" and (
                    not relative_template.parts or relative_template.parts[0] != pipeline_source_key
                ):
                    continue
                template = template_catalog.load_template(record)
                for template_entry in template.get("entries", []):
                    if not isinstance(template_entry, dict):
                        continue
                    selector = template_entry.get("accountSelector")
                    if not isinstance(selector, dict):
                        continue
                    number = str(selector.get("number") or "").strip()
                    name = str(selector.get("name") or "").strip()
                    identity = (number, name)
                    if not (number or name) or identity in seen_template_subjects:
                        continue
                    seen_template_subjects.add(identity)
                    template_entry_defaults.append({
                        "line_no": len(template_entry_defaults) + 1,
                        "dc": template_entry.get("dc", 1),
                        "account_number": number,
                        "account_name": name,
                    })
        settings["entry_defaults"] = template_entry_defaults
        resolved = resolve_defaults(account_api, settings)
        settings["voucher_defaults"] = resolved["voucher_defaults"]
        settings["entry_defaults"] = resolved["entry_defaults"]
        user_context = resolve_current_user(account_api)
        settings["voucher_defaults"]["user_name"] = user_context["userName"]
        auxiliary_lists = {
            label: {"itemClassId": class_id, "items": list(bucket.values())}
            for class_id, bucket in preload_result.by_class.items()
            for label in resolve_item_class_labels(class_id)
        } if preload_result is not None else account_api.get_all_items_v1()
        default_item_class = str(settings.get("item_class", "客户"))
        default_item_class_id = resolve_item_class_id(default_item_class, settings.get("item_class_id"))
        item_class_map_path = resolve_config_path(
            str(paths_config["item_class_map_file"]), ROOT, company, month, pipeline_source_key
        )
        item_class_maps = ItemClassMapStore.load(item_class_map_path)
        for label, report in auxiliary_lists.items():
            item_class_maps.seed_remote(int(report["itemClassId"]), report["items"], label)
        if pipeline_source_key == "sales":
            auxiliary_map = dict(sales_map_report["map"])
        elif pipeline_source_key == "purchase":
            auxiliary_map = dict(purchase_map_report["map"])
        else:
            auxiliary_map = {**purchase_map_report["map"], **sales_map_report["map"]}
        for invoice_code, values in auxiliary_map.items():
            item_class_name = str(values.get("itemClass") or default_item_class)
            item_class_id = resolve_item_class_id(item_class_name, values.get("itemClassId"))
            item_class_result = next((report for report in auxiliary_lists.values() if int(report["itemClassId"]) == item_class_id), None)
            if not item_class_result:
                raise RuntimeError(f"未找到辅助核算类型接口映射：{item_class_name}/{item_class_id}")
            item_name = str(values.get("supplierName") or values.get("customName", "")).strip()
            matches = [row for row in item_class_result["items"] if str(row.get("name", "")).strip() == item_name]
            if len(matches) == 1:
                values["auxiliaryItem"] = build_auxiliary_expectation(
                    matches[0], item_class=item_class_name, item_class_id=item_class_id
                )
                mapped = item_class_maps.resolve_name(item_class_id, item_name, item_class_name)
                values["auxiliaryItem"]["number"] = mapped["number"]
            elif len(matches) == 0 and item_name:
                mapped = item_class_maps.resolve_name(item_class_id, item_name, item_class_name)
                if mapped.get("remoteId"):
                    remote_item = {"id": mapped["remoteId"], "number": mapped["number"], "name": mapped["name"]}
                else:
                    remote_max_number = int(item_class_result.get("remoteMaxNumber") or 0)
                    remote_item = create_auxiliary_item(account_api, item_class_id, mapped["number"], item_name, remote_max_number=remote_max_number)
                    item_class_maps.attach_remote_id(item_class_id, remote_item["number"], remote_item["id"], remote_item["name"], item_class_name)
                values["auxiliaryItem"] = build_auxiliary_expectation(
                    remote_item, item_class=item_class_name, item_class_id=item_class_id
                )
                values["auxiliaryItem"]["createdInRemote"] = True
            else:
                values["auxiliaryMatchError"] = {"itemClass": item_class_name, "itemClassId": item_class_id, "name": item_name, "matchCount": len(matches)}
        item_class_maps.save()
        unresolved_new_items = [
            {"invoiceCode": code, "itemClassId": values.get("itemClass"), "name": values.get("supplierName") or values.get("customName", "")}
            for code, values in auxiliary_map.items()
            if isinstance(values.get("auxiliaryItem"), dict) and values["auxiliaryItem"].get("id") in (None, "")
        ]
        if unresolved_new_items:
            raise RuntimeError(f"存在未取得远端 itemId 的辅助对象，禁止继续生成/提交凭证：{unresolved_new_items[:5]}")
        settings["voucher_defaults"]["itemClass"] = default_item_class
        settings["user_context"] = user_context
        print("账套 ID 来源：当前登录账套动态读取")
        print("制单人来源：当前账簿页面 SYSTEM.RealName")
    elif account_source == "snapshot":
        print("账套 ID 来源：配置/快照，不会访问当前登录账套")
    else:
        print(f"不支持的 accountbook_source：{account_source}")
        return 2
    upload_map_path = resolve_config_path(str(paths_config["upload_map_file"]), ROOT, company, month, pipeline_source_key)
    all_pdfs, _ = discover_source_pdfs(input_dir, pdf_folders)
    logger.info("附件索引完成：待上传映射发票数=%s", len(all_pdfs))
    upload_map = {code: str(paths[0]) for code, paths in all_pdfs.items() if paths}
    upload_map_path.parent.mkdir(parents=True, exist_ok=True)
    upload_map_path.write_text(json.dumps(upload_map, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint("receipt_generation")
    try:
        receipt_report = generate_receipts(
            input_dir,
            config,
            receipt_dir,
            bool(settings.get("generate_overwrite", False)),
            upload_map_path,
            pdf_folders,
            dict(settings.get("voucher_defaults", {})),
            list(settings.get("entry_defaults", [])),
            dict(sales_map_report["map"]),
            template_config,
            bool(settings.get("only_mapped_invoices", False)),
            mode == "prepare",
            template_catalog=template_catalog,
            purchase_map_values=(dict(purchase_map_report["map"]) if pipeline_source_key in {"all", "purchase"} else {}),
            allowed_invoice_codes=allowed_ocr_codes,
        )
    except Exception as exc:
        append_exception(
            workflow_exception_path,
            pipeline_source_key,
            "receipt_generation",
            "*",
            type(exc).__name__,
            str(exc),
            {"runConfig": str(run_config_path.resolve())},
        )
        raise
    generation_exceptions = [
        {
            "documentId": str(item.get("invoiceCode") or item.get("pdf") or "*"),
            "errorType": "invalid_pdf",
            "message": str(item.get("reason") or "PDF无效"),
            "details": item,
        }
        for item in receipt_report.get("invalidPdfs", [])
    ] + [
        {
            "documentId": str(item.get("invoiceCode") or "*"),
            "errorType": "duplicate_invoice_pdf",
            "message": "同一单据编号匹配到多个PDF",
            "details": item,
        }
        for item in receipt_report.get("duplicates", [])
    ]
    replace_stage_exceptions(workflow_exception_path, pipeline_source_key, "receipt_generation", generation_exceptions)
    print(f"配置：{run_config_path}")
    print(f"月份目录：{month_dir}")
    print(f"输入目录：{input_dir}")
    print(f"附件map：{upload_map_path}")
    print(f"生成 receipt：{receipt_report['summary']['generatedCount']}，已存在：{receipt_report['summary']['skippedCount']}，扫描 PDF：{receipt_report['summary']['pdfInvoiceCodeCount']}，重复：{receipt_report['summary']['duplicateInvoiceCodeCount']}，无效 PDF：{receipt_report['summary']['invalidPdfCount']}")
    checkpoint("receipt_generation_complete", artifacts={"receiptDirectory": str(receipt_dir.resolve()), "uploadPdfMap": str(upload_map_path.resolve())}, counters={"receiptGeneratedCount": receipt_report["summary"]["generatedCount"], "receiptInvalidPdfCount": receipt_report["summary"]["invalidPdfCount"]})
    review_path = resolve_config_path(str(paths_config["preupload_review_file"]), ROOT, company, month, pipeline_source_key)
    review_report = build_preupload_report(
        receipt_dir,
        review_path,
        {
            "sourceCompany": settings.get("source_company_key", company),
            "documentEntity": document_entity_name,
            "accountbook": settings.get("accountbook_key", ""),
            "accountbookName": expected_company,
            "month": month,
            "mode": mode,
            "purpose": settings.get("purpose", "production"),
            "crossEntity": bool(settings.get("cross_entity", False)),
            "templatesFile": str(template_path),
            "receiptsOcrDirectory": str(receipts_ocr_dir),
            "pdfFolders": list(settings.get("pdf_folders", ["sales", "purchase", "bank", "misc"])),
        },
    )
    review_invoice_by_receipt = {
        str(item.get("receipt") or ""): str((item.get("invoiceCodes") or ["*"])[0])
        for item in review_report.get("receipts", [])
        if isinstance(item, dict)
    }
    replace_stage_exceptions(
        workflow_exception_path,
        pipeline_source_key,
        "preupload_review",
        ({
            "documentId": str(item.get("invoiceCode") or review_invoice_by_receipt.get(str(item.get("receipt") or "")) or "*"),
            "errorType": str(item.get("type") or "preupload_warning"),
            "message": str(item.get("error") or item.get("type") or "正式上传前检查未通过"),
            "details": item,
        } for item in review_report.get("warnings", [])),
    )
    if pipeline_source_key in {"purchase", "all"}:
        print(f"用途确认发票码：{map_report['summary']['usageConfirmNumberCount']}，匹配 PDF：{map_report['summary']['matchedCount']}，空值：{map_report['summary']['emptyCount']}")
        print(f"purchase_map：{purchase_map_path}，原始发票数：{purchase_map_report['report']['summary'].get('rawInvoiceCount', purchase_map_report['report']['summary']['invoiceCount'])}，用途确认+PDF筛选后：{purchase_map_report['report']['summary'].get('filteredInvoiceCount', len(purchase_map_report['map']))}，排除：{purchase_map_report['report']['summary'].get('excludedByUsagePdfFilterCount', 0)}，日期冲突：{purchase_map_report['report']['summary']['dateConflictCount']}，供应商冲突：{purchase_map_report['report']['summary']['supplierConflictCount']}")
    print(f"正式上传前审查报告：{review_path}，状态：{review_report['reviewStatus']}，警告：{review_report['summary']['warningCount']}")
    checkpoint("preupload_review_complete", artifacts={"preuploadReview": str(review_path.resolve())}, counters={"preuploadWarningCount": review_report["summary"]["warningCount"]})
    if mode == "prepare":
        print("准备阶段完成：receipt 仍是待补业务字段草稿，未进入批量校验或真实提交。")
        print("补齐 receipt.json 的 date/groupId/summary/userName/entries 后，将对应任务配置的 mode 改为 dry-run。")
        return 0
    command = [sys.executable, str(ROOT / "scripts" / "commands" / "batch_receipts.py"), "--project-root", str(ROOT), "--runtime-root", str(workspace_root), "--config", str(app_config_path), "--expected-company", expected_company, "--input-dir", str(receipt_dir), "--pdf-map", str(upload_map_path), "--source", pipeline_source_key]
    if mode == "confirm":
        from kdzwy_receipt_uploader.preupload_review import require_review_confirmation, PreuploadReviewError
        try:
            require_review_confirmation(review_path)
        except PreuploadReviewError as exc:
            print(str(exc))
            return 3
        command.append("--confirm")
        if args.receipt_id:
            command.extend(["--receipt-id", args.receipt_id])
        if args.limit > 0:
            command.extend(["--limit", str(args.limit)])
        if args.test_upload:
            command.append("--test-upload")
    print(f"运行模式：{mode}")
    print("开始批量处理：" + " ".join(command))
    checkpoint("upload" if mode == "confirm" else "dry_run")
    logger.info("开始调用 batch_receipts: %s", " ".join(command))
    return_code = subprocess.call(command)
    checkpoint("upload_complete" if mode == "confirm" and return_code == 0 else "dry_run_complete" if return_code == 0 else "batch_failed", counters={"batchExitCode": return_code})
    return return_code
