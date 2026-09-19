"""Bank receipt workflow orchestration."""
from __future__ import annotations

import json
import subprocess
from kdzwy_receipt_uploader.project_runtime import process_environment
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults
from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.bank_exception_filter import (
    BankExceptionFilterError,
    filter_bank_exception_pdfs,
    load_bank_exception_pdf_keywords,
    quarantine_bank_runtime_exceptions,
)
from kdzwy_receipt_uploader.bank_final_receipts import (
    BankFinalReceiptError,
    build_bank_ocr_artifacts,
    generate_bank_final_receipts,
    load_bank_records,
    validate_bank_analysis_rules,
)
from kdzwy_receipt_uploader.bank_final_receipts import (
    source_values as bank_source_values,
)
from kdzwy_receipt_uploader.bank_receipt_ocr import (
    BankReceiptOcrError,
    run_bank_receipt_ocr,
)
from kdzwy_receipt_uploader.bank_receipt_splitter import (
    BankReceiptSplitError,
    split_configured_bank_pdfs,
)
from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts
from kdzwy_receipt_uploader.bank_statement_matcher import (
    BankStatementMatchError,
    collect_person_name_exclusions,
    match_bank_statements,
)
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.final_template_sample import (
    build_final_template_context,
    load_final_template_sample,
)
from kdzwy_receipt_uploader.ocr.rendering import compact_analysis_for_storage
from kdzwy_receipt_uploader.ocr.selector import OpenAICompatibleTemplateSelector
from kdzwy_receipt_uploader.ocr.service import analyze_ocr_and_choose_template
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path
from kdzwy_receipt_uploader.preload_items import (
    collect_source_item_names,
    preload_bank_counterparties,
)
from kdzwy_receipt_uploader.user_context import resolve_current_user

from .context import PipelineContext


def run_bank_pipeline(context: PipelineContext) -> int:
    ROOT = context.root
    analysis_stage = context.analysis_stage
    app_config_path = context.app_config_path
    args = context.args
    checkpoint = context.checkpoint
    company = context.company
    config = context.config
    document_entity_name = context.document_entity_name
    expected_company = context.expected_company
    input_dir = context.input_dir
    logger = context.logger
    map_path = context.map_path
    mode = context.mode
    month = context.month
    paths_config = context.paths_config
    pipeline_source_key = context.pipeline_source_key
    receipt_dir = context.receipt_dir
    settings = context.settings
    template_root = context.template_root
    workspace_root = context.workspace_root
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

    if analysis_stage in {"ocr", "existing"}:
        checkpoint("bank_item_preload")
        preload_api = None
        try:
            preload_config = AppConfig.from_json(app_config_path, ROOT)
            preload_api = KdzwyApi(replace(preload_config, expected_company=expected_company))
            preload_api.get_dynamic_system_params()
            bank_role_evidence = collect_source_item_names(input_dir, config)
            bank_preload = preload_bank_counterparties(
                preload_api,
                bank_matched,
                create_missing=True,
                role_evidence=bank_role_evidence,
            )
            bank_preload_report_path = bank_map_path.parent / "item_preload.report.json"
            bank_preload_report_path.write_text(
                json.dumps(
                    {
                        "status": "success" if not bank_preload.unresolved else "incomplete_with_unresolved_counterparties",
                        "mode": "auto",
                        "sourceColumns": bank_preload.source_columns,
                        "resolved": bank_preload.resolved,
                        "unresolved": bank_preload.unresolved,
                        "created": bank_preload.created,
                        "summary": {
                            "matchedRecordCount": len(bank_matched),
                            "resolvedRecordCount": len(bank_preload.resolved),
                            "unresolvedRecordCount": len(bank_preload.unresolved),
                            "createdCount": len(bank_preload.created),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            print(
                "银行客户/供应商自动核对完成："
                f"新增={len(bank_preload.created)}，"
                f"已解析={len(bank_preload.resolved)}，"
                f"未解析={len(bank_preload.unresolved)}"
            )
            checkpoint(
                "bank_item_preload_complete",
                artifacts={"itemPreloadReport": str(bank_preload_report_path.resolve())},
                counters={
                    "createdCount": len(bank_preload.created),
                    "unresolvedCount": len(bank_preload.unresolved),
                },
            )
        except (OSError, ValueError, BankFinalReceiptError) as exc:
            print(f"银行客户/供应商自动核对失败：{exc}", file=sys.stderr)
            return 2
        finally:
            if preload_api is not None and hasattr(preload_api, "close"):
                preload_api.close()

    if analysis_stage == "ocr":
        if args.concise:
            print("[成功] 银行 OCR、流水匹配和辅助核算核对完成")
            print(f"  结果目录：{workspace_root / 'generated'}")
            print("  未生成 receipt；下一步：stage=llm")
        else:
            print("银行 OCR、流水匹配和辅助核算核对完成；本阶段不生成 receipt。下一步运行 stage=llm。")
        return 0

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
                    for key, value in saved_analysis.items():
                        if key not in bank_matched or not isinstance(value, dict) or value.get("analysisStatus") != "ready_for_review":
                            continue
                        try:
                            validate_bank_analysis_rules(bank_matched[key], value)
                        except BankFinalReceiptError:
                            continue
                        existing_analyzed[str(key)] = dict(value)

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
        if mode == "analysis-only":
            print("LLM 阶段完成；本阶段不生成 receipt。下一步将 stage 设置为 prepare。")
            return 0

    if analysis_stage not in {"existing", "all"}:
        print("银行最终 receipt 只能由 stage=prepare、send 或 all 生成。", file=sys.stderr)
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
    if mode == "prepare" or analysis_stage == "all":
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
                draft=False,
                overwrite=True,
            )
        except (BankFinalReceiptError, OSError, ValueError) as exc:
            print(f"银行最终 receipt 生成失败：{exc}", file=sys.stderr)
            return 2
        finally:
            if "prepare_api" in locals() and hasattr(prepare_api, "close"):
                prepare_api.close()
        print(
            f"银行 receipt 生成完成：匹配记录={generation['summary']['matchedRecordCount']}，"
            f"有效 receipt={generation['summary']['receiptCount']}，"
            f"新生成={generation['summary']['generatedCount']}，"
            f"已存在未覆盖={generation['summary']['reusedCount']}，"
            f"分析未就绪={generation['summary']['blockedAnalysisCount']}"
        )
        if mode == "prepare":
            print("待上传 receipt 已生成；下一步将 stage 设置为 send。")
            return 0
        if generation["summary"]["blockedAnalysisCount"]:
            print("stage=all 存在分析异常，已停止正式上传。", file=sys.stderr)
            return 3

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
        print("银行最终 receipt 尚未全部通过，禁止进入 send/all。", file=sys.stderr)
        return 3
    batch_command = [
        sys.executable,
        "-m", "kdzwy_receipt_uploader.commands.batch_receipts",
        "--project-root", str(ROOT),
        "--runtime-root", str(workspace_root),
        "--config", str(app_config_path),
        "--expected-company", expected_company,
        "--input-dir", str(receipt_dir),
        "--source", "bank",
    ]
    if mode == "confirm":
        batch_command.append("--confirm")
    return subprocess.call(batch_command, env=process_environment(ROOT))
