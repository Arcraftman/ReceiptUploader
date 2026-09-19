"""Invoice and miscellaneous receipt workflow orchestration."""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from kdzwy_receipt_uploader.accountbook_resolver import resolve_defaults
from kdzwy_receipt_uploader.api import KdzwyApi
from kdzwy_receipt_uploader.auxiliary_items import create_auxiliary_item
from kdzwy_receipt_uploader.config import AppConfig
from kdzwy_receipt_uploader.exception_ledger import (
    append_exception,
    replace_analysis_exception_stages,
    replace_stage_exceptions,
)
from kdzwy_receipt_uploader.final_template_sample import (
    build_final_template_context,
    load_final_template_sample,
)
from kdzwy_receipt_uploader.item_class import (
    build_auxiliary_expectation,
    resolve_item_class_id,
)
from kdzwy_receipt_uploader.item_class_maps import ItemClassMapStore
from kdzwy_receipt_uploader.matching import match_month_directory
from kdzwy_receipt_uploader.ocr.rendering import compact_analysis_for_storage
from kdzwy_receipt_uploader.ocr.selector import OpenAICompatibleTemplateSelector
from kdzwy_receipt_uploader.ocr.service import analyze_ocr_and_choose_template
from kdzwy_receipt_uploader.pipeline_paths import (
    resolve_config_path,
    resolve_item_class_labels,
)
from kdzwy_receipt_uploader.preload_items import (
    apply_preloaded_items,
    collect_map_item_names,
    preload_items,
)
from kdzwy_receipt_uploader.preupload_review import build_preupload_report
from kdzwy_receipt_uploader.purchase_map import (
    build_purchase_map,
    build_purchase_map_from_pdfs,
    finalize_purchase_pdf_map,
)
from kdzwy_receipt_uploader.receipt_generation import (
    discover_source_pdfs,
    generate_receipts,
)
from kdzwy_receipt_uploader.sales_map import (
    build_sales_map_from_pdfs,
    finalize_sales_pdf_map,
)
from kdzwy_receipt_uploader.user_context import resolve_current_user

from .context import PipelineContext


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


def run_invoice_pipeline(context: PipelineContext) -> int:
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
    month_dir = context.month_dir
    paths_config = context.paths_config
    pdf_folders = context.pdf_folders
    pipeline_source_key = context.pipeline_source_key
    purchase_map_path = context.purchase_map_path
    purchase_map_report_path = context.purchase_map_report_path
    receipt_dir = context.receipt_dir
    run_config_path = context.run_config_path
    sales_map_path = context.sales_map_path
    sales_map_report_path = context.sales_map_report_path
    settings = context.settings
    template_catalog = context.template_catalog
    template_path = context.template_path
    template_root = context.template_root
    workflow_stage = context.workflow_stage
    workspace_root = context.workspace_root
    checkpoint("mapping")
    usage_confirmation_enabled = bool(settings.get("usage_confirmation_enabled", True))
    map_report = _empty_match_report()
    if pipeline_source_key in {"purchase", "all"} and usage_confirmation_enabled:
        map_report = match_month_directory(input_dir, config, map_path.parent)
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
        sales_map_report = build_sales_map_from_pdfs(
            input_dir / "sales",
            sales_map_path,
            sales_map_report_path,
        )
    if pipeline_source_key in {"purchase", "all"}:
        if usage_confirmation_enabled:
            purchase_map_report = build_purchase_map(
                usage_path, purchase_map_path, purchase_map_report_path
            )
        else:
            purchase_map_report = build_purchase_map_from_pdfs(
                input_dir / "purchase", purchase_map_path, purchase_map_report_path
            )
            map_report = {
                "map": {
                    code: str(values.get("sourcePdf") or "")
                    for code, values in purchase_map_report["map"].items()
                },
                "summary": {
                    "usageConfirmNumberCount": 0,
                    "matchedCount": len(purchase_map_report["map"]),
                    "emptyCount": 0,
                },
                "usageConfirmationEnabled": False,
            }
            map_path.parent.mkdir(parents=True, exist_ok=True)
            map_path.write_text(
                json.dumps(map_report["map"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            map_path.with_name("xlsx_pdf_map.report.json").write_text(
                json.dumps(
                    {
                        **map_report,
                        "scope": "小规模纳税人：由input/purchase实际PDF直接建立附件映射，不读取XLSX",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    if preload_result is not None:
        apply_preloaded_items(sales_map_report["map"], preload_result, 1, "customName", "customerId", "customerNumber")
        apply_preloaded_items(purchase_map_report["map"], preload_result, 5, "supplierName", "supplierId", "supplierNumber")
    # The workbook path is narrower than the raw purchase map: only
    # usage-confirmed codes with a matched purchase PDF remain in scope.
    if pipeline_source_key in {"purchase", "all"} and usage_confirmation_enabled:
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
        from kdzwy_receipt_uploader.ocr.engine import load_ocr_artifacts, run_ocr_stage
        from kdzwy_receipt_uploader.ocr.models import OcrPipelineError

        configured_company = document_entity_name
        purchase_mapped_codes = set(map_report.get("map", {})) if pipeline_source_key in {"all", "purchase"} else set()
        sales_mapped_codes = set(sales_map_report["map"]) if pipeline_source_key in {"all", "sales"} else set()
        mapped_ocr_codes = purchase_mapped_codes | sales_mapped_codes
        source_pdf_index, source_invalid_pdfs = discover_source_pdfs(input_dir, pdf_folders)
        source_pdf_codes = set(source_pdf_index)
        only_mapped_invoices = (
            False
            if pipeline_source_key == "sales"
            else bool(settings.get("only_mapped_invoices", False))
        )
        allowed_ocr_codes = set(source_pdf_codes)
        if only_mapped_invoices:
            allowed_ocr_codes.intersection_update(mapped_ocr_codes)
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
                    "salesScope": "actual_pdfs" if pipeline_source_key == "sales" else None,
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
        inventory_scope = (
            "销售范围：仅实际PDF"
            if pipeline_source_key == "sales"
            else f"未匹配Excel：{len(source_pdf_codes - mapped_ocr_codes)}"
        )
        print(
            f"PDF真实数量：{raw_pdf_count}；有效单张：{single_pdf_count}；"
            f"重复文件：{duplicate_pdf_count}；无效文件：{len(source_invalid_pdfs)}；"
            f"{inventory_scope}；清点报告：{pdf_inventory_path}"
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
            sales_ocr_result = finalize_sales_pdf_map(
                sales_map_report,
                receipts_ocr_dir,
                configured_company,
                month,
                sales_map_path,
                sales_map_report_path,
            )
            sales_ocr_blocked_codes = {
                str(row.get("documentId") or "") for row in sales_ocr_result["blocked"]
            }
            replace_stage_exceptions(
                workflow_exception_path,
                pipeline_source_key,
                "sales_pdf_ocr",
                sales_ocr_result["blocked"],
            )
            if sales_ocr_blocked_codes:
                allowed_ocr_codes.difference_update(sales_ocr_blocked_codes)
                ocr_artifacts = [
                    artifact for artifact in ocr_artifacts
                    if artifact.invoice_code in allowed_ocr_codes
                ]
                print(
                    f"[异常分流] 销售PDF精确OCR校验未通过 "
                    f"{len(sales_ocr_blocked_codes)} 张，已写入：{workflow_exception_path}"
                )
            if sales_ocr_result["ready"]:
                logger.info(
                    "销售PDF通过精确OCR校验：%s 张",
                    len(sales_ocr_result["ready"]),
                )

        if pipeline_source_key in {"purchase", "all"} and not usage_confirmation_enabled:
            purchase_ocr_result = finalize_purchase_pdf_map(
                purchase_map_report,
                receipts_ocr_dir,
                configured_company,
                month,
                purchase_map_path,
                purchase_map_report_path,
            )
            purchase_ocr_blocked_codes = {
                str(row.get("documentId") or "") for row in purchase_ocr_result["blocked"]
            }
            replace_stage_exceptions(
                workflow_exception_path,
                pipeline_source_key,
                "purchase_pdf_ocr",
                purchase_ocr_result["blocked"],
            )
            if purchase_ocr_blocked_codes:
                allowed_ocr_codes.difference_update(purchase_ocr_blocked_codes)
                ocr_artifacts = [
                    artifact for artifact in ocr_artifacts
                    if artifact.invoice_code in allowed_ocr_codes
                ]
                print(
                    f"[异常分流] 采购PDF精确OCR校验未通过 "
                    f"{len(purchase_ocr_blocked_codes)} 张，已写入：{workflow_exception_path}"
                )
            if purchase_ocr_result["ready"]:
                logger.info("小规模采购PDF通过精确OCR校验：%s 张", len(purchase_ocr_result["ready"]))

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
        elif pipeline_source_key == "purchase" and not usage_confirmation_enabled:
            print(
                f"采购PDF真实发票号：{len(source_pdf_codes)}，"
                f"当前通过范围：{len(allowed_ocr_codes)}；小规模纳税人不使用用途确认信息.xlsx"
            )
        else:
            catalog_note = "只读读取账套目录" if account_api_for_analysis is not None else "使用本地快照"
        print(f"分析模式完成：stage={analysis_stage}，{catalog_note}，不生成receipt、不保存凭证、不上传附件。")
        print(f"OCR目录：{receipts_ocr_dir}")
        if analysis_stage in {"llm", "existing", "all"}:
            print(f"Qwen分析报告：{analysis_report_path}")
        else:
            print("Qwen未执行；下一步请使用 --stage llm。")
        if pipeline_source_key == "sales":
            print(
                f"销售PDF真实发票号：{len(source_pdf_codes)}，"
                f"当前通过范围：{len(allowed_ocr_codes)}；未使用收入成本表"
            )
        else:
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
            False,
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
        if usage_confirmation_enabled:
            print(f"purchase_map：{purchase_map_path}，原始发票数：{purchase_map_report['report']['summary'].get('rawInvoiceCount', purchase_map_report['report']['summary']['invoiceCount'])}，用途确认+PDF筛选后：{purchase_map_report['report']['summary'].get('filteredInvoiceCount', len(purchase_map_report['map']))}，排除：{purchase_map_report['report']['summary'].get('excludedByUsagePdfFilterCount', 0)}，日期冲突：{purchase_map_report['report']['summary']['dateConflictCount']}，供应商冲突：{purchase_map_report['report']['summary']['supplierConflictCount']}")
        else:
            print(f"purchase_map：{purchase_map_path}，小规模纳税人按实际PDF：{len(purchase_map_report['map'])}，用途确认信息.xlsx：不需要，OCR阻断：{purchase_map_report['report']['summary'].get('ocrBlockedCount', 0)}")
    print(f"正式上传前审查报告：{review_path}，状态：{review_report['reviewStatus']}，警告：{review_report['summary']['warningCount']}")
    checkpoint("preupload_review_complete", artifacts={"preuploadReview": str(review_path.resolve())}, counters={"preuploadWarningCount": review_report["summary"]["warningCount"]})
    if mode == "prepare":
        print("准备阶段完成：已生成待上传 receipt，但没有调用真实上传接口。")
        print("复核 receipt 后，将对应业务的 stage 改为 send。")
        return 0
    command = [sys.executable, str(ROOT / "scripts" / "commands" / "batch_receipts.py"), "--project-root", str(ROOT), "--runtime-root", str(workspace_root), "--config", str(app_config_path), "--expected-company", expected_company, "--input-dir", str(receipt_dir), "--pdf-map", str(upload_map_path), "--source", pipeline_source_key]
    if mode == "confirm":
        from kdzwy_receipt_uploader.preupload_review import (
            PreuploadReviewError,
            require_review_confirmation,
        )
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
    print(f"流程阶段：{workflow_stage}")
    print("开始批量处理：" + " ".join(command))
    checkpoint("upload")
    logger.info("开始调用 batch_receipts: %s", " ".join(command))
    return_code = subprocess.call(command)
    checkpoint("upload_complete" if return_code == 0 else "batch_failed", counters={"batchExitCode": return_code})
    return return_code
