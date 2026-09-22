"""Bank template analysis using existing statement maps and OCR."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.bank_final_receipts import BankFinalReceiptError, build_bank_ocr_artifacts, validate_bank_analysis_rules
from kdzwy_receipt_uploader.bank_final_receipts import source_values as bank_source_values
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.final_template_sample import build_final_template_context, load_final_template_sample
from kdzwy_receipt_uploader.ocr.rendering import compact_analysis_for_storage
from kdzwy_receipt_uploader.ocr.selector import OpenAICompatibleTemplateSelector
from kdzwy_receipt_uploader.ocr.service import analyze_ocr_and_choose_template
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path
from kdzwy_receipt_uploader.preload_items import collect_source_item_names, preload_bank_counterparties

from .context import PipelineContext



def run_bank_analysis(context: PipelineContext, bank_matched):
    ROOT = context.root
    app_config_path = context.app_config_path
    checkpoint = context.checkpoint
    config = context.config
    expected_company = context.expected_company
    input_dir = context.input_dir
    logger = context.logger
    mode = context.mode
    settings = context.settings
    template_root = context.template_root
    bank_ocr_output = resolve_config_path(str(context.paths_config["receipts_ocr_dir"]), context.root, context.company, context.month, context.pipeline_source_key)
    bank_map_path = context.map_path.parent / "bank_map.json"
    bank_analysis_path = bank_ocr_output / "template_analysis.json"
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
                "银行辅助核算目录核对结果："
                f"账套客户总数={len(bank_preload.by_class.get(1, {}))}，"
                f"账套供应商总数={len(bank_preload.by_class.get(5, {}))}，"
                f"新增={len(bank_preload.created)}，"
                f"未解析={len(bank_preload.unresolved)}"
            )
            print(f"本批交易={len(bank_matched)}，已解析={len(bank_preload.resolved)}，未解析={len(bank_preload.unresolved)}，未进入客户供应商解析={len(bank_matched)-len(bank_preload.resolved)-len(bank_preload.unresolved)}；报告={bank_preload_report_path}")
            for unresolved in bank_preload.unresolved:
                print(f"[待核对] {unresolved.get('recordKey')}：{unresolved.get('name')}，方向={unresolved.get('flowDirection')}，原因={unresolved.get('reason')}")
            if bank_preload.unresolved:
                print(
                    "[警告] 银行预加载未完全结束："
                    f"{len(bank_preload.unresolved)} 条交易方缺少有效组织名称、金额方向或目录证据；"
                    "本次不会把空结果记录为成功，后续运行将继续核对。",
                    file=sys.stderr,
                )
        elif bank_preload_reused:
            print("银行客户/供应商预加载已完成且 bank_map 未变化，本次复用。")
        logger.info("开始读取目标账套科目目录，准备银行模板分析")
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
                        validate_bank_analysis_rules(bank_matched[key], value, account_catalog)
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
        logger.info("银行 LLM 开始：待分析=%s，复用=%s，工作线程=%s", len(artifacts), len(existing_analyzed), worker_count)
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

    return 0


