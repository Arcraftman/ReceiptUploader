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

from kdzwy_receipt_uploader.month_config import MonthConfig
from kdzwy_receipt_uploader.receipt_generation import discover_source_pdfs, generate_receipts
from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults
from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.matching import match_month_directory
from kdzwy_receipt_uploader.user_context import resolve_current_user
from kdzwy_receipt_uploader.sales_map import build_sales_map
from kdzwy_receipt_uploader.purchase_map import build_purchase_map
from kdzwy_receipt_uploader.auxiliary_items import create_auxiliary_item
from kdzwy_receipt_uploader.item_class import build_auxiliary_expectation, resolve_item_class_id
from kdzwy_receipt_uploader.item_class_maps import ItemClassMapStore
from kdzwy_receipt_uploader.template_catalog import TemplateCatalog
from kdzwy_receipt_uploader.source_profile import normalize_source_key
from kdzwy_receipt_uploader.pipeline_paths import resolve_config_path, resolve_source_folders, resolve_item_class_labels
from kdzwy_receipt_uploader.receipts_ocr import analyze_ocr_and_choose_template, compact_analysis_for_storage
from kdzwy_receipt_uploader.preupload_review import build_preupload_report
from kdzwy_receipt_uploader.final_template_sample import build_final_template_context, load_final_template_sample
from kdzwy_receipt_uploader.preload_items import apply_preloaded_items, preload_items
from kdzwy_receipt_uploader.simple_logging import configure_pipeline_logger
from kdzwy_receipt_uploader.bank_receipt_splitter import BankReceiptSplitError, split_configured_bank_pdfs


def main() -> int:
    parser = argparse.ArgumentParser(description="从指定的运行配置执行 map、receipt 生成和批量处理")
    parser.add_argument("--run-config", type=Path, required=True, help="运行配置路径；通常由 run_companies.py 动态生成")
    parser.add_argument("--app-config", type=Path, default=ROOT / "config" / "app.json")
    parser.add_argument("--mode", choices=["prepare", "analysis-only", "dry-run", "confirm"], default=None, help="覆盖配置中的 mode")
    parser.add_argument("--stage", choices=["ocr", "deepseek", "existing", "all"], default=None, help="分析阶段：OCR、DeepSeek、复用已批准分析或显式串行执行")
    parser.add_argument("--limit", type=int, default=0, help="传递给上传阶段的单证限制（仅 confirm 阶段生效）")
    parser.add_argument("--receipt-id", type=str, default="", help="传递给上传阶段的单个 receiptId（仅 confirm 阶段生效）")
    parser.add_argument("--test-upload", action="store_true", help="传递给上传阶段的 test-upload 标记（仅 confirm 阶段生效）")
    args = parser.parse_args()
    logger = configure_pipeline_logger(ROOT / "runtime" / "logs", "run_pipeline")
    run_config_path = args.run_config if args.run_config.is_absolute() else ROOT / args.run_config
    app_config_path = args.app_config if args.app_config.is_absolute() else ROOT / args.app_config
    settings = json.loads(run_config_path.read_text(encoding="utf-8"))
    company = str(settings["company"])
    document_entity_name = str(settings.get("document_entity_name") or settings.get("company_name") or company)
    expected_company = str(settings.get("accountbook_name") or document_entity_name)
    month = str(settings["month"])
    pipeline_source = str(settings.get("source", "all")).lower()
    pipeline_source_key = normalize_source_key(pipeline_source) or "all"
    paths_config = settings.get("paths", settings)
    month_dir = resolve_config_path(str(paths_config["month_dir"]), ROOT, company, month, pipeline_source_key)
    input_dir = resolve_config_path(str(paths_config.get("input_dir", "data/inbox/{company}/{month}/input")), ROOT, company, month, pipeline_source_key)
    map_path = resolve_config_path(str(paths_config["map_file"]), ROOT, company, month, pipeline_source_key)
    sales_map_path = resolve_config_path(str(paths_config.get("sales_map_file", "data/inbox/{company}/{month}/maps/{source}/sales_map.json")), ROOT, company, month, pipeline_source_key)
    sales_map_report_path = resolve_config_path(str(paths_config.get("sales_map_report_file", "data/inbox/{company}/{month}/maps/{source}/sales_map.report.json")), ROOT, company, month, pipeline_source_key)
    purchase_map_path = resolve_config_path(str(paths_config.get("purchase_map_file", "data/inbox/{company}/{month}/maps/{source}/purchase_map.json")), ROOT, company, month, pipeline_source_key)
    purchase_map_report_path = resolve_config_path(str(paths_config.get("purchase_map_report_file", "data/inbox/{company}/{month}/maps/{source}/purchase_map.report.json")), ROOT, company, month, pipeline_source_key)
    template_path = resolve_config_path(str(settings.get("templates_file", "templates/index.json")), ROOT, company, month, pipeline_source_key)
    template_root = template_path.parent
    template_catalog = TemplateCatalog.load(template_root) if template_path.name == "index.json" and template_path.is_file() else None
    pdf_folders = resolve_source_folders(pipeline_source, list(settings.get("pdf_folders", ["sales*", "purchase*", "bank*", "misc*"])))
    receipt_dir = resolve_config_path(str(paths_config.get("receipt_dir_sales_map", paths_config["receipt_dir"])), ROOT, company, month, pipeline_source_key)
    mode = args.mode or str(settings.get("mode", "prepare"))
    analysis_stage = args.stage or str(settings.get("analysis_stage", "ocr"))
    analysis_validation = str(settings.get("analysis_validation", "strict")).strip().lower()
    if analysis_validation not in {"strict", "relaxed"}:
        raise ValueError('analysis_validation 只支持 "strict" 或 "relaxed"')
    if mode not in {"prepare", "analysis-only", "dry-run", "confirm"}:
        print(f"不支持的 mode：{mode}")
        return 2
    confs = sorted(month_dir.glob("*.conf"))
    if not confs:
        print(f"月份目录没有 .conf：{month_dir}")
        return 2
    logger.info("开始任务：company=%s accountbook=%s dataset=%s month=%s mode=%s source=%s", company, expected_company, document_entity_name, month, mode, settings.get("source", "all"))
    config = MonthConfig.load(confs[0])
    if pipeline_source_key == "bank":
        bank_input_dir = input_dir / "bank"
        bank_split_config = resolve_config_path(
            str(paths_config.get("bank_split_config_file", "data/inbox/{company}/{month}/input/bank/bank_split.json")),
            ROOT, company, month, pipeline_source_key,
        )
        bank_split_output = resolve_config_path(
            str(paths_config.get("bank_split_output_dir", "data/inbox/{company}/{month}/generated/bank_receipts")),
            ROOT, company, month, pipeline_source_key,
        )
        bank_split_report_path = resolve_config_path(
            str(paths_config.get("bank_split_report_file", "data/inbox/{company}/{month}/generated/bank_receipts/split.report.json")),
            ROOT, company, month, pipeline_source_key,
        )
        try:
            bank_split_report = split_configured_bank_pdfs(
                bank_split_config, bank_input_dir, bank_split_output, bank_split_report_path
            )
        except BankReceiptSplitError as exc:
            print(f"银行回单裁剪失败：{exc}", file=sys.stderr)
            return 2
        print(
            f"银行回单裁剪完成：银行={bank_split_report['summary']['bankCount']}，"
            f"回单={bank_split_report['summary']['receiptCount']}，"
            f"新生成={bank_split_report['summary']['generatedBankCount']}，"
            f"复用={bank_split_report['summary']['reusedBankCount']}；"
            f"目录={bank_split_output}"
        )
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

    preload_state_path = map_path.parent / "item_preload.state.json"
    preload_fingerprint = {
        "accountbook": str(settings.get("accountbook_key", "")),
        "company": expected_company,
        "dataset": company,
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
    if preload_enabled and not preload_already_done and str(settings.get("accountbook_source", "live")) == "live":
        preload_config = AppConfig.from_json(app_config_path, ROOT)
        preload_api = KdzwyApi(replace(preload_config, expected_company=expected_company))
        preload_api.get_dynamic_system_params()
        preload_result = preload_items(preload_api, input_dir, config, extra_columns=settings.get("item_source_columns", []), create_missing=True)
        preload_report = {"sourceColumns": preload_result.source_columns, "created": preload_result.created, "counts": {str(class_id): len(rows) for class_id, rows in preload_result.by_class.items()}}
        preload_path = map_path.parent / "item_preload.report.json"
        preload_path.write_text(json.dumps(preload_report, ensure_ascii=False, indent=2), encoding="utf-8")
        if preload_mode == "once":
            preload_state_path.parent.mkdir(parents=True, exist_ok=True)
            preload_state_tmp = preload_state_path.with_suffix(".json.tmp")
            preload_state_tmp.write_text(
                json.dumps(
                    {"status": "success", "fingerprint": preload_fingerprint},
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            preload_state_tmp.replace(preload_state_path)
        print(f"ItemClass预加载完成：{preload_report['counts']}，新增：{len(preload_result.created)}")
    elif preload_already_done:
        print(f"ItemClass预加载已完成且输入Excel未变化，本次跳过：{settings.get('source', 'all')}")
    usage_path = input_dir / config.usage_filename
    sales_map_report = build_sales_map(income_path, sales_map_path, sales_map_report_path)
    purchase_map_report = build_purchase_map(usage_path, purchase_map_path, purchase_map_report_path)
    if preload_result is not None:
        apply_preloaded_items(sales_map_report["map"], preload_result, 1, "customName", "customerId", "customerNumber")
        apply_preloaded_items(purchase_map_report["map"], preload_result, 5, "supplierName", "supplierId", "supplierNumber")
    # purchase_map itself contains every numeric row in 用途确认信息; the pipeline
    # scope is narrower: only usage-confirmed codes with a matched j-folder PDF.
    purchase_map_total_count = len(purchase_map_report["map"])
    purchase_map_report["map"] = {
        code: values for code, values in purchase_map_report["map"].items()
        if map_report.get("map", {}).get(code, "")
    }
    purchase_map_report["report"]["scope"] = "仅保留用途确认信息.xlsx E列且j文件夹存在匹配PDF的发票号"
    purchase_map_report["report"]["summary"]["rawInvoiceCount"] = purchase_map_total_count
    purchase_map_report["report"]["summary"]["filteredInvoiceCount"] = len(purchase_map_report["map"])
    purchase_map_report["report"]["summary"]["excludedByUsagePdfFilterCount"] = purchase_map_total_count - len(purchase_map_report["map"])
    purchase_map_path.write_text(json.dumps(purchase_map_report["map"], ensure_ascii=False, indent=2), encoding="utf-8")
    purchase_map_report_path.write_text(json.dumps(purchase_map_report["report"], ensure_ascii=False, indent=2), encoding="utf-8")
    template_config = json.loads(template_path.read_text(encoding="utf-8")) if template_path.is_file() else {}
    final_sample_path = (ROOT / str(settings.get("final_template_sample", "templates/final_template_sample.json"))).resolve()
    if not final_sample_path.is_file():
        raise RuntimeError(f"最终模板样例不存在：{final_sample_path}")
    final_sample = load_final_template_sample(final_sample_path)
    receipts_ocr_dir = resolve_config_path(str(paths_config.get("receipts_ocr_dir", "data/inbox/{company}/{month}/receipts_ocr/{source}")), ROOT, company, month, pipeline_source_key)
    ocr_analysis_by_invoice: dict[str, dict[str, object]] = {}
    account_api_for_analysis = None
    runtime_account_catalog: list[dict[str, object]] = []
    runtime_item_catalog: dict[str, dict[str, object]] = {}
    needs_live_analysis_catalog = mode != "analysis-only" or analysis_stage in {"deepseek", "all"}
    if str(settings.get("accountbook_source", "live")) == "live" and needs_live_analysis_catalog:
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
    if template_catalog:
        from kdzwy_receipt_uploader.receipts_ocr import OcrPipelineError, load_ocr_artifacts, run_ocr_stage

        configured_company = document_entity_name
        purchase_allowed_codes = set(map_report.get("map", {})) if pipeline_source_key in {"all", "purchase"} else set()
        sales_allowed_codes = set(sales_map_report["map"]) if pipeline_source_key in {"all", "sales"} else set()
        allowed_ocr_codes = purchase_allowed_codes | sales_allowed_codes
        if analysis_stage == "existing":
            analysis_path = receipts_ocr_dir / "template_analysis.json"
            if not analysis_path.is_file():
                raise OcrPipelineError(f"缺少已批准DeepSeek分析：{analysis_path}")
            loaded_analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_analysis, dict):
                raise OcrPipelineError(f"DeepSeek分析文件不是JSON对象：{analysis_path}")
            missing_analysis = sorted(allowed_ocr_codes - set(loaded_analysis))
            blocked_analysis = sorted(
                code for code in allowed_ocr_codes
                if isinstance(loaded_analysis.get(code), dict)
                and loaded_analysis[code].get("analysisStatus") != "ready_for_review"
            )
            relaxed_non_upload = analysis_validation == "relaxed" and mode in {"prepare", "dry-run"}
            if missing_analysis or (blocked_analysis and not relaxed_non_upload):
                raise OcrPipelineError(
                    f"已批准分析不完整：缺少={missing_analysis[:5]}，未通过={blocked_analysis[:5]}"
                )
            if blocked_analysis:
                logger.warning(
                    "宽松分析校验已启用：允许 %s 张未通过分析进入 %s；正式上传仍会阻断",
                    len(blocked_analysis),
                    mode,
                )
                print(f"[警告] 宽松分析校验：{len(blocked_analysis)} 张未通过分析将生成草稿；confirm仍被禁止。")
            ocr_analysis_by_invoice = {code: dict(loaded_analysis[code]) for code in allowed_ocr_codes}
            for code, analysis in ocr_analysis_by_invoice.items():
                if code in sales_map_report["map"]:
                    sales_map_report["map"][code]["ocrAnalysis"] = analysis
                if code in purchase_map_report["map"]:
                    purchase_map_report["map"][code]["ocrAnalysis"] = analysis
            ocr_artifacts = load_ocr_artifacts(receipts_ocr_dir, allowed_ocr_codes)
            logger.info("复用已批准DeepSeek分析：%s 张，不运行OCR、不调用DeepSeek", len(ocr_analysis_by_invoice))
        elif analysis_stage in {"ocr", "all"}:
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
        else:
            logger.info("DeepSeek阶段：只读取已有OCR产物，不运行OCR")
            ocr_artifacts = load_ocr_artifacts(receipts_ocr_dir, allowed_ocr_codes)

        if analysis_stage in {"deepseek", "all"}:
            try:
                deepseek_worker_count = max(1, int(settings.get("deepseek_workers", 2) or 1))
            except (TypeError, ValueError):
                deepseek_worker_count = 2
            deepseek_worker_count = min(deepseek_worker_count, max(1, len(ocr_artifacts)))
            logger.info("DeepSeek有限并发：%s 个工作线程", deepseek_worker_count)

            def analyze_artifact(artifact):
                map_values = dict(sales_map_report["map"].get(artifact.invoice_code) or purchase_map_report["map"].get(artifact.invoice_code) or {})
                final_context = build_final_template_context(final_sample, runtime_account_catalog, runtime_item_catalog, source=artifact.source_folder, map_values=map_values)
                final_context["runtimeAccountMeta"] = runtime_account_catalog_meta
                final_context["businessMapValues"] = map_values
                return analyze_ocr_and_choose_template(artifact, template_root, final_template_context=final_context)

            with ThreadPoolExecutor(max_workers=deepseek_worker_count) as executor:
                pending_analysis = [
                    (artifact, executor.submit(analyze_artifact, artifact))
                    for artifact in ocr_artifacts
                ]
                for index, (artifact, future) in enumerate(pending_analysis, start=1):
                    source_pdf = artifact.source_pdf
                    logger.info("DeepSeek模板分析 %s/%s：%s", index, len(ocr_artifacts), source_pdf.name)
                    try:
                        analysis = future.result()
                        ocr_analysis_by_invoice[artifact.invoice_code] = analysis
                        if artifact.invoice_code in sales_map_report["map"]:
                            sales_map_report["map"][artifact.invoice_code]["ocrAnalysis"] = analysis
                        if artifact.invoice_code in purchase_map_report["map"]:
                            purchase_map_report["map"][artifact.invoice_code]["ocrAnalysis"] = analysis
                    except Exception as exc:
                        print(f"DeepSeek模板分析失败：{source_pdf}：{exc}")
            stored_analysis = {
                code: compact_analysis_for_storage(analysis)
                for code, analysis in ocr_analysis_by_invoice.items()
            }
            (receipts_ocr_dir / "template_analysis.json").write_text(json.dumps(stored_analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        allowed_ocr_codes = set()
        ocr_stage_report_path.parent.mkdir(parents=True, exist_ok=True)
        ocr_stage_report_path.write_text(
            json.dumps(
                {
                    "sourceDirectory": str(month_dir.resolve()),
                    "outputDirectory": str(receipts_ocr_dir.resolve()),
                    "allowedInvoiceCodes": [],
                    "filterRule": "template_catalog 缺失，已跳过 OCR 与 DeepSeek",
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
        review_path = resolve_config_path(str(paths_config.get("preupload_review_file", "data/inbox/{company}/{month}/maps/{source}/preupload_review.report.json")), ROOT, company, month, pipeline_source_key)
        if preload_result is not None:
            catalog_note = f"ItemClass已预加载，新增{len(preload_result.created)}个辅助核算对象"
        else:
            catalog_note = "只读读取账套目录" if account_api_for_analysis is not None else "使用本地快照"
        print(f"分析模式完成：stage={analysis_stage}，{catalog_note}，不生成receipt、不保存凭证、不上传附件。")
        print(f"OCR目录：{receipts_ocr_dir}")
        if analysis_stage in {"deepseek", "existing", "all"}:
            print(f"DeepSeek分析报告：{analysis_report_path}")
        else:
            print("DeepSeek未执行；下一步请使用 --stage deepseek。")
        print(f"有效销售发票：{len(sales_map_report['map'])}，有效进项发票：{len(purchase_map_report['map'])}，合计范围：{len(allowed_ocr_codes)}")
        return 0
    account_source = str(settings.get("accountbook_source", "live"))
    if account_source == "live":
        api_config = AppConfig.from_json(app_config_path, ROOT)
        account_api = KdzwyApi(replace(api_config, expected_company=expected_company))
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
            str(paths_config.get("item_class_map_file", "data/inbox/{company}/{month}/maps/{source}/item_class_maps.json")), ROOT, company, month, pipeline_source_key
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
    upload_map_path = resolve_config_path(str(paths_config.get("upload_map_file", "data/inbox/{company}/{month}/maps/{source}/upload_pdf_map.json")), ROOT, company, month, pipeline_source_key)
    all_pdfs, _ = discover_source_pdfs(input_dir, pdf_folders)
    logger.info("附件索引完成：待上传映射发票数=%s", len(all_pdfs))
    upload_map = {code: str(paths[0]) for code, paths in all_pdfs.items() if paths}
    upload_map_path.parent.mkdir(parents=True, exist_ok=True)
    upload_map_path.write_text(json.dumps(upload_map, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt_report = generate_receipts(
        input_dir,
        config,
        receipt_dir,
        bool(settings.get("generate_overwrite", False)),
        map_path,
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
    print(f"配置：{run_config_path}")
    print(f"月份目录：{month_dir}")
    print(f"输入目录：{input_dir}")
    print(f"map：{map_path}")
    print(f"生成 receipt：{receipt_report['summary']['generatedCount']}，已存在：{receipt_report['summary']['skippedCount']}，扫描 PDF：{receipt_report['summary']['pdfInvoiceCodeCount']}，重复：{receipt_report['summary']['duplicateInvoiceCodeCount']}，无效 PDF：{receipt_report['summary']['invalidPdfCount']}")
    review_path = resolve_config_path(str(paths_config.get("preupload_review_file", "data/inbox/{company}/{month}/maps/{source}/preupload_review.report.json")), ROOT, company, month, pipeline_source_key)
    review_report = build_preupload_report(
        receipt_dir,
        review_path,
        {
            "dataset": settings.get("dataset_key", company),
            "documentEntity": document_entity_name,
            "accountbook": settings.get("accountbook_key", ""),
            "accountbookName": expected_company,
            "month": month,
            "mode": mode,
            "purpose": settings.get("purpose", "production"),
            "crossEntity": bool(settings.get("cross_entity", False)),
            "templatesFile": str(template_path),
            "receiptsOcrDirectory": str(receipts_ocr_dir),
            "pdfFolders": list(settings.get("pdf_folders", ["sales*", "purchase*", "bank*", "misc*"])),
        },
    )
    print(f"用途确认发票码：{map_report['summary']['usageConfirmNumberCount']}，匹配 PDF：{map_report['summary']['matchedCount']}，空值：{map_report['summary']['emptyCount']}")
    print(f"purchase_map：{purchase_map_path}，原始发票数：{purchase_map_report['report']['summary'].get('rawInvoiceCount', purchase_map_report['report']['summary']['invoiceCount'])}，用途确认+PDF筛选后：{purchase_map_report['report']['summary'].get('filteredInvoiceCount', len(purchase_map_report['map']))}，排除：{purchase_map_report['report']['summary'].get('excludedByUsagePdfFilterCount', 0)}，日期冲突：{purchase_map_report['report']['summary']['dateConflictCount']}，供应商冲突：{purchase_map_report['report']['summary']['supplierConflictCount']}")
    print(f"正式上传前审查报告：{review_path}，状态：{review_report['reviewStatus']}，警告：{review_report['summary']['warningCount']}")
    if mode == "prepare":
        print("准备阶段完成：receipt 仍是待补业务字段草稿，未进入批量校验或真实提交。")
        print("补齐 receipt.json 的 date/groupId/summary/userName/entries 后，将对应任务配置的 mode 改为 dry-run。")
        return 0
    command = [sys.executable, str(ROOT / "batch_receipts.py"), "--project-root", str(ROOT), "--config", str(app_config_path), "--expected-company", expected_company, "--input-dir", str(receipt_dir), "--pdf-map", str(upload_map_path), "--source", pipeline_source_key]
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
    logger.info("开始调用 batch_receipts: %s", " ".join(command))
    return subprocess.call(command)
