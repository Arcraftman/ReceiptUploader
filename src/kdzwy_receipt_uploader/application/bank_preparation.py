"""Bank receipt preparation using existing validated analysis."""
from __future__ import annotations

import json
import sys
from dataclasses import replace

from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults
from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.bank_final_receipts import BankFinalReceiptError, generate_bank_final_receipts, validate_bank_analysis_rules
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path
from kdzwy_receipt_uploader.user_context import resolve_current_user

from .context import PipelineContext



def run_bank_preparation(context: PipelineContext, bank_matched):
    ROOT = context.root
    analysis_stage = context.analysis_stage
    app_config_path = context.app_config_path
    company = context.company
    expected_company = context.expected_company
    mode = context.mode
    month = context.month
    receipt_dir = context.receipt_dir
    settings = context.settings
    bank_ocr_output = resolve_config_path(str(context.paths_config["receipts_ocr_dir"]), context.root, context.company, context.month, context.pipeline_source_key)
    bank_analysis_path = bank_ocr_output / "template_analysis.json"
    from .bank_pipeline import _verify_pdf_gate, _submit_existing_bank
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
        context.checkpoint("bank_prepare")
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
            print("待上传 receipt 已生成，进入verify检查PDF绑定。")
            return _verify_pdf_gate(context)
        if generation["summary"]["blockedAnalysisCount"]:
            print("stage=all 存在分析异常，已停止正式上传。", file=sys.stderr)
            return 3

    return _submit_existing_bank(context)



