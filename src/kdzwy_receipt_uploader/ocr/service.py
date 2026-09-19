"""Orchestrate OCR artifacts, template selection and analysis results."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .memory import _load_analysis_memory, _save_analysis_memory
from .models import OcrArtifact, OcrPipelineError
from .rendering import (
    enforce_dynamic_supplier_payables_exception,
    enforce_template_explanation,
)
from .rules import _enrich_bank_counterparty_roles, _rule_candidates
from .selector import OpenAICompatibleTemplateSelector


def analyze_ocr_and_choose_template(artifact: OcrArtifact, template_root: Path, selector: OpenAICompatibleTemplateSelector | None = None, final_template_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from ..template_catalog import TemplateCatalog

    catalog = TemplateCatalog.load(template_root)
    metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    fields = metadata.get("fields", {}) if isinstance(metadata.get("fields"), Mapping) else {}
    allowed_blocks = list(fields.get("allowedTemplateBlocks", []))
    allowed_block_set = set(allowed_blocks)
    candidate_records = []
    for record in catalog.records:
        if not bool(record.get("enabled", True)):
            continue
        enriched = dict(record)
        try:
            template_payload = catalog.load_template(record)
            enriched.update({key: template_payload.get(key) for key in ("decisionCode", "decisionName", "documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "matchRules", "amountSource", "exception") if template_payload.get(key) is not None})
        except Exception:
            pass
        enriched["templateFileName"] = Path(str(record.get("path", ""))).name
        if allowed_block_set and str(enriched.get("documentBlock", "")) not in allowed_block_set:
            continue
        candidate_records.append(enriched)
    if not candidate_records:
        raise OcrPipelineError(f"{artifact.source_folder} 没有符合买卖方方向的模板候选")
    source_key = artifact.source_side if artifact.source_side in {"sales", "purchase", "bank", "misc"} else "misc"
    runtime_map_values = (
        final_template_context.get("businessMapValues")
        if isinstance(final_template_context, Mapping)
        else None
    )
    if not isinstance(runtime_map_values, Mapping):
        runtime_map_values = {}
    required_bank_account_number = ""
    if source_key == "bank":
        runtime_map_values = _enrich_bank_counterparty_roles(
            final_template_context,
            runtime_map_values,
        )
        if isinstance(final_template_context, dict):
            final_template_context["businessMapValues"] = runtime_map_values
        required_bank_account_number = str(
            runtime_map_values.get("bankAccountNumber") or ""
        ).strip()
        if not re.fullmatch(r"[0-9]+", required_bank_account_number):
            raise OcrPipelineError(
                f"银行模板选择缺少固定银行存款科目号：invoice={artifact.invoice_code}"
            )
        for item in candidate_records:
            item["bankAccountNumber"] = required_bank_account_number
    scoped_records = []
    for item in candidate_records:
        rules = item.get("matchRules") if isinstance(item.get("matchRules"), Mapping) else {}
        configured_sources = {str(value).strip().lower() for value in rules.get("sourceFolders", [])}
        path_parts = Path(str(item.get("path", ""))).parts
        physical_source = path_parts[0].lower() if path_parts else ""
        if source_key in configured_sources or (not configured_sources and physical_source == source_key):
            scoped_records.append(item)
    if not scoped_records:
        raise OcrPipelineError(f"模板范围为空：source={source_key}，目录={template_root / source_key}")
    candidate_records = scoped_records
    from ..bank_rules import employee_payment_kind, is_person_name
    employee_kind = employee_payment_kind(runtime_map_values) if source_key == "bank" else ""
    if source_key == "bank" and is_person_name(runtime_map_values.get("counterpartyName")) and not employee_kind:
        raise OcrPipelineError("人名流水缺少明确且唯一的工资或费用报销付款备注")
    if employee_kind:
        business = "发放工资" if employee_kind == "salary" else "费用报销"
        rule_candidates = [item for item in candidate_records if item.get("businessType") == business]
        rejected = {}
        if len(rule_candidates) != 1:
            raise OcrPipelineError(f"员工付款必须有唯一的{business}模板")
    else:
        candidate_records = [item for item in candidate_records if item.get("businessType") != "费用报销"]
        rule_candidates, rejected = _rule_candidates(candidate_records, artifact, runtime_map_values)
    if not rule_candidates:
        raise OcrPipelineError(
            "OCR文字没有命中可审计的模板规则，禁止退回全部候选猜测："
            + json.dumps(rejected, ensure_ascii=False)
        )
    rule_fallback = False
    prompt_path = template_root / "prompts" / f"{source_key}.md"
    if not prompt_path.is_file():
        raise OcrPipelineError(f"缺少{source_key}固定提示词：{prompt_path}")
    from kdzwy_receipt_uploader.fixed_prompt_rules import FIXED_LLM_RULES

    business_rules = (
        FIXED_LLM_RULES.rstrip()
        + "\n\n# 公司与业务自定义规则\n"
        + prompt_path.read_text(encoding="utf-8")
        .replace("{{source_company}}", str(metadata.get("configCompany") or ""))
        .replace("{{template_directory}}", f"templates/{template_root.name}/{source_key}")
    )
    if source_key == "bank":
        business_rules += (
            "\n\n# 当前银行固定科目\n"
            f"bankAccountNumber={required_bank_account_number}。"
            "该值来自 project.json，模板选择、模板渲染和最终分录中的银行存款科目号必须完全一致；禁止模型修改或猜测。"
        )
    memory_path = artifact.output_dir.parent / "analysis_memory.json"
    memory = _load_analysis_memory(memory_path)
    active_selector = selector or OpenAICompatibleTemplateSelector.from_settings({})
    choose_parameters = inspect.signature(active_selector.choose).parameters
    if employee_kind:
        chosen_employee = rule_candidates[0]
        decision = {"status": "success", "templatePath": chosen_employee["path"],
                    "templateId": chosen_employee["id"], "confidence": 1.0,
                    "selectionMode": "employee_remark", "llmAttempted": False,
                    "employeePaymentKind": employee_kind,
                    "employeePaymentEvidence": {key: runtime_map_values.get(key) for key in ("counterpartyName", "remark", "flowDirection")},
                    "reason": "交易对方为人名，按流水备注明确选择员工付款模板"}
    elif "business_rules" in choose_parameters:
        choose_kwargs = {
            "final_template_context": final_template_context,
            "business_rules": business_rules,
            "verified_memory": list(memory.get("verifiedDecisions", [])),
        }
        if "prompt_path" in choose_parameters:
            choose_kwargs["prompt_path"] = template_root / "prompts" / "invoice_classifier_prompt.txt"
        decision = active_selector.choose(
            artifact.text, rule_candidates, artifact.invoice_code,
            **choose_kwargs,
        )
    else:
        decision = active_selector.choose(artifact.text, rule_candidates, artifact.invoice_code)
    allowed_paths = {str(item.get("path", "")) for item in rule_candidates}
    decision.setdefault("status", "success" if decision.get("templatePath") else "pending")
    chosen = str(decision.get("templatePath", ""))
    if chosen and chosen not in allowed_paths:
        decision["status"] = "invalid"
        decision["reason"] = "Qwen 返回的模板路径不在 templates 根目录候选中"
        decision["templatePath"] = ""
    chosen_record = next((item for item in rule_candidates if str(item.get("path", "")) == str(decision.get("templatePath", ""))), None)
    exception_ready: bool | None = None
    if chosen_record and isinstance(chosen_record.get("exception"), Mapping):
        exception_ready = enforce_dynamic_supplier_payables_exception(
            decision,
            artifact,
            template_root,
            final_template_context,
            chosen_record,
        )
    else:
        enforce_template_explanation(decision, artifact, template_root, final_template_context)
    decision["ocrFields"] = fields
    decision["allowedTemplateBlocks"] = allowed_blocks
    decision["sourceFolder"] = artifact.source_folder
    decision["sourceSide"] = artifact.source_side
    decision["configCompany"] = metadata.get("configCompany", "")
    decision["partyRule"] = metadata.get("partyRule", {})
    decision["ocrTextPath"] = str(artifact.text_path.resolve())
    decision["ocrMetadataPath"] = str(artifact.metadata_path.resolve())
    decision["sourcePdf"] = str(artifact.source_pdf.resolve())
    decision["templateCandidates"] = len(rule_candidates)
    decision["templateCandidatesBeforeRules"] = len(candidate_records)
    decision["ruleRejectedCandidates"] = rejected
    decision["ruleFallbackUsed"] = rule_fallback
    if source_key == "bank":
        decision["remark"] = str(runtime_map_values.get("remark") or "")
    if chosen_record:
        expected_id = str(chosen_record.get("id") or "")
        returned_id = str(decision.get("templateId") or "")
        if returned_id != expected_id:
            decision["status"] = "invalid"
            decision["reason"] = f"模型返回的templateId与templatePath不一致：expected={expected_id}, actual={returned_id}"
        decision["decisionCode"] = str(chosen_record.get("decisionCode") or "")
        decision["decisionName"] = str(chosen_record.get("decisionName") or "")
    validation = {
        "folderRule": (
            str(chosen_record.get("documentBlock", "")) == "银行"
            if source_key == "bank" and chosen_record
            else bool(set(fields.get("allowedTemplateBlocks", [])) & {str(chosen_record.get("documentBlock", ""))}) if chosen_record else False
        ),
        "sourceFolderRule": bool(chosen_record) and (
            not chosen_record.get("matchRules", {}).get("sourceFolders")
            or artifact.source_folder.lower()
            in {str(value).strip().lower() for value in chosen_record.get("matchRules", {}).get("sourceFolders", [])}
        ),
        "confidenceRule": float(decision.get("confidence", 0) or 0) >= 0.9,
        "mapSourceRule": (
            str(chosen_record.get("amountSource", "")) == "source"
            if source_key == "bank" and chosen_record
            else (str(chosen_record.get("amountSource", "")) == str(fields.get("mapSource", ""))) if chosen_record else False
        ),
    }
    if source_key == "bank":
        configured_directions = {
            str(value).strip().lower()
            for value in (
                chosen_record.get("matchRules", {}).get("flowDirections", [])
                if chosen_record
                else []
            )
        }
        actual_direction = str(runtime_map_values.get("flowDirection") or "").lower()
        validation["flowDirectionRule"] = bool(
            chosen_record
            and actual_direction
            and (
                not configured_directions
                or actual_direction in configured_directions
            )
        )
        extracted_fields = decision.get("extractedFields")
        validation["amountRule"] = bool(
            isinstance(extracted_fields, Mapping)
            and extracted_fields.get("amountValidated") is True
            and extracted_fields.get("transactionAmount") not in (None, "")
            and extracted_fields.get("amountSource")
        )
    from ..final_template_sample import validate_filled_entries
    sample_errors = validate_filled_entries(decision, final_template_context or {}) if final_template_context and exception_ready is not False else list(decision.get("exceptionValidationErrors") or [])
    decision["finalTemplateValidationErrors"] = sample_errors
    validation["finalTemplateRule"] = not sample_errors if final_template_context else True
    if exception_ready is not None:
        validation["exceptionRule"] = exception_ready
    decision["validation"] = validation
    if exception_ready is False:
        decision["analysisStatus"] = "exception_pending"
    else:
        decision["analysisStatus"] = "ready_for_review" if decision.get("status") == "success" and all(validation.values()) else "blocked"
    if decision["analysisStatus"] != "ready_for_review":
        decision.setdefault("blockReason", "系统强校验未全部通过，禁止进入可提交receipt")
    decision["businessPrompt"] = str(prompt_path.resolve())
    decision["memoryFile"] = str(memory_path.resolve())
    _save_analysis_memory(memory_path, memory, artifact.invoice_code, decision)
    return decision
