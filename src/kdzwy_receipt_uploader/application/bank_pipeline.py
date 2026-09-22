"""Bank receipt workflow orchestration."""
from __future__ import annotations

import json
import subprocess
from kdzwy_receipt_uploader.project_runtime import process_environment
import sys

from kdzwy_receipt_uploader.bank_final_receipts import BankFinalReceiptError, load_bank_records
from kdzwy_receipt_uploader.bank_receipt_verifier import verify_bank_receipts, verify_bank_pdf_links
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path

from .context import PipelineContext



def run_bank_pipeline(context: PipelineContext) -> int:
    """Dispatch explicit stages; downstream stages never rerun upstream work."""
    if context.mode == "verify":
        return _verify_pdf_gate(context)
    if context.mode == "confirm" and context.analysis_stage == "existing":
        return _submit_existing_bank(context)
    from .bank_preprocessing import run_bank_ocr, run_bank_match
    from .bank_analysis import run_bank_analysis
    from .bank_preparation import run_bank_preparation
    stage = context.analysis_stage
    if stage not in {"ocr", "match", "llm", "existing", "all"}:
        print(f"不支持的银行分析阶段：{stage}", file=sys.stderr)
        return 2
    try:
        if stage in {"ocr", "all"}:
            reports = run_bank_ocr(context)
            if isinstance(reports, int):
                return reports
        elif stage == "match":
            ocr_root = resolve_config_path(str(context.paths_config["receipts_ocr_dir"]), context.root, context.company, context.month, context.pipeline_source_key)
            reports = {"ocr": json.loads((ocr_root / "ocr_stage.report.json").read_text(encoding="utf-8-sig")),
                       "exceptions": json.loads((context.map_path.parent / "bank_exceptions.json").read_text(encoding="utf-8-sig"))}
        if stage in {"ocr", "match", "all"}:
            result = run_bank_match(context, reports)
            if result:
                return result
            if stage != "all":
                print("银行OCR/匹配阶段完成；未执行LLM、生成或上传。")
                return 0
        bank_matched, _ = load_bank_records(context.map_path.parent / "bank_map.json", context.map_path.parent / "bank_map.report.json")
        if stage in {"llm", "all"}:
            result = run_bank_analysis(context, bank_matched)
            if result or stage == "llm":
                return result
        return run_bank_preparation(context, bank_matched)
    except (OSError, ValueError, BankFinalReceiptError) as exc:
        print(f"银行阶段输入缺失或无效，请先完成前置阶段：{exc}", file=sys.stderr)
        return 2


def _verify_pdf_gate(context: PipelineContext) -> int:
    context.checkpoint("verify")
    report = verify_bank_pdf_links(context.receipt_dir)
    phase = "waiting_for_pdf_binding" if report["missingPdfCount"] else "verified"
    context.checkpoint(phase, artifacts={"pdfVerifyReport": str(context.receipt_dir / "bank_pdf_links.verify.report.json")},
                       counters={"missingPdfCount": report["missingPdfCount"]})
    print(f"PDF verify：待上传={report['pendingReceiptCount']}，缺少绑定={report['missingPdfCount']}")
    for item in report["missing"]:
        print(f"[等待人工绑定] {item['receipt']}: {item['error']}")
    return 4 if report["missingPdfCount"] else 0


def _submit_existing_bank(context: PipelineContext) -> int:
    gate = _verify_pdf_gate(context)
    if gate:
        return gate
    ROOT = context.root
    workspace_root = context.workspace_root
    app_config_path = context.app_config_path
    expected_company = context.expected_company
    receipt_dir = context.receipt_dir
    mode = context.mode
    bank_matched, _ = load_bank_records(context.map_path.parent / "bank_map.json", context.map_path.parent / "bank_map.report.json")
    bank_split_output = resolve_config_path(str(context.paths_config["bank_split_output_dir"]), ROOT, context.company, context.month, context.pipeline_source_key)
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
    exit_code = subprocess.call(batch_command, env=process_environment(ROOT))
    from kdzwy_receipt_uploader.bank_upload_report import report_unuploaded_bank_pdfs
    exception_output = bank_split_output.parent / "bank_exception"
    report = report_unuploaded_bank_pdfs(bank_split_output, receipt_dir, exception_output)
    print(f"银行未上传PDF汇总：{report['unuploadedPdfCount']}份；清单及副本：{exception_output}")
    return exit_code
