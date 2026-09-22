"""Bank OCR and statement matching stages."""
from __future__ import annotations

import json
import sys

from kdzwy_receipt_uploader.bank_exception_filter import BankExceptionFilterError, filter_bank_exception_pdfs, quarantine_bank_runtime_exceptions
from kdzwy_receipt_uploader.bank_final_receipts import BankFinalReceiptError, load_bank_records
from kdzwy_receipt_uploader.bank_receipt_ocr import BankReceiptOcrError, run_bank_receipt_ocr
from kdzwy_receipt_uploader.bank_receipt_splitter import BankReceiptSplitError, split_configured_bank_pdfs
from kdzwy_receipt_uploader.bank_statement_matcher import BankStatementMatchError, match_bank_statements
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path

from .context import PipelineContext



def run_bank_ocr(context: PipelineContext):
    ROOT = context.root
    args = context.args
    checkpoint = context.checkpoint
    company = context.company
    document_entity_name = context.document_entity_name
    input_dir = context.input_dir
    logger = context.logger
    map_path = context.map_path
    month = context.month
    paths_config = context.paths_config
    pipeline_source_key = context.pipeline_source_key
    settings = context.settings
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
            allow_missing_pdf=True,
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
        bank_exception_report = filter_bank_exception_pdfs(
            settings.get("banks"),
            [],
            bank_input_dir,
            bank_split_report,
            bank_exception_output,
            bank_exception_path,
            config_company=document_entity_name,
            pdf_keyword_rules={},
        )
    except (
        BankExceptionFilterError,
        BankStatementMatchError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"银行切割异常整理失败：{exc}", file=sys.stderr)
        return 2
    bank_exception_summary = bank_exception_report["summary"]
    if args.concise:
        print("[2/4] 切割异常整理：完成（不使用对象名单）")
        print(
            f"  名单排除（已停用）：{bank_exception_summary['exceptionStatementCount']}；"
            f"切割 exception：{bank_exception_summary['splitExceptionPdfCount']}；"
            f"技术异常PDF：{bank_exception_summary['exceptionPdfCount']}；"
            f"已复制：{bank_exception_summary['copiedPdfCount']}；"
            f"缺少PDF：{bank_exception_summary['missingPdfCount']}"
        )
        print(f"  技术异常目录：{bank_exception_output}")
    else:
        print(
            "银行切割异常整理完成："
            f"名单排除（已停用）={bank_exception_summary['exceptionStatementCount']}，"
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
        bank_ocr_report = run_bank_receipt_ocr(
            bank_split_report,
            bank_ocr_output,
            workers=int(settings.get("ocr_workers", 2) or 1),
            company=document_entity_name,
            excluded_indices={},
            progress=logger.info,
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
            f"有效处理={bank_ocr_summary['eligibleReceiptCount']}，"
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
    return {"ocr": bank_ocr_report, "exceptions": bank_exception_report}


def run_bank_match(context: PipelineContext, reports):
    args = context.args
    checkpoint = context.checkpoint
    document_entity_name = context.document_entity_name
    map_path = context.map_path
    receipt_dir = context.receipt_dir
    settings = context.settings
    bank_input_dir = context.input_dir / "bank"
    bank_split_output = resolve_config_path(str(context.paths_config["bank_split_output_dir"]), context.root, context.company, context.month, context.pipeline_source_key)
    bank_split_report_path = resolve_config_path(str(context.paths_config["bank_split_report_file"]), context.root, context.company, context.month, context.pipeline_source_key)
    bank_exception_path = context.map_path.parent / "bank_exceptions.json"
    bank_exception_output = bank_split_output.parent / "bank_exceptions"
    bank_ocr_output = resolve_config_path(str(context.paths_config["receipts_ocr_dir"]), context.root, context.company, context.month, context.pipeline_source_key)
    bank_map_path = context.map_path.parent / "bank_map.json"
    bank_map_report_path = context.map_path.parent / "bank_map.report.json"
    bank_ocr_report = reports["ocr"]
    bank_exception_report = reports["exceptions"]
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
            allow_without_pdf=True,
            remark_exception=settings.get("remark_exception", []),
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
        match_status = "全部匹配" if bank_match_report["status"] == "ok" else "部分匹配，详见未匹配与异常记录"
        print(f"[4/4] 剩余流水匹配：{match_status}")
        print(
            f"  流水：{bank_match_summary['statementRowCount']}；"
            f"特殊过滤：{bank_match_summary['exceptionFilteredStatementCount']}；"
            f"匹配：{bank_match_summary['matchedCount']}；"
            f"普通未匹配流水：{bank_match_summary['unmatchedStatementCount']}；"
            f"普通未匹配回单：{bank_match_summary['unmatchedReceiptCount']}；"
            f"个人姓名跳过：{bank_match_summary['skippedPersonNameCount']}；"
            f"备注单独处理：{bank_match_summary.get('remarkExceptionCount', 0)}；"
            f"内部转账跳过：{bank_match_summary.get('skippedInternalTransferCount', 0)}；"
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
            f"备注单独处理={bank_match_summary.get('remarkExceptionCount', 0)}，"
            f"内部转账跳过={bank_match_summary.get('skippedInternalTransferCount', 0)}，"
            f"无PDF流水={bank_match_summary.get('withoutPdfCount', 0)}，"
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
    # Remove explicitly excluded receipts from the upload input while retaining them for review.
    import shutil
    from datetime import datetime
    excluded_receipt_root = receipt_dir.parent / "remark_exception_receipts" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for bank_key, bank in bank_match_report.get("banks", {}).items():
        for row in bank.get("remarkExceptionStatements", []):
            for base in (receipt_dir, receipt_dir / "manual", receipt_dir / "automatic"):
                source = base / f"receipt_{bank_key}__{row['index']}"
                if source.is_dir():
                    source.resolve().relative_to(receipt_dir.resolve())
                    target = excluded_receipt_root / base.name / source.name
                    target.resolve().relative_to(receipt_dir.parent.resolve())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
    return 0


