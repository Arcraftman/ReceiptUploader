"""Compatibility imports for the OCR public API.

Implementations live in :mod:`kdzwy_receipt_uploader.ocr`. Existing scripts
and callers may continue importing from this module.
"""
import time  # Historical patch point for analysis-memory retry waits.

from .ocr.engine import (
    _default_ocr,
    _get_ocr_engine,
    _merge_ocr_candidates,
    _ocr_candidate_complete,
    _ocr_candidate_quality,
    _source_fingerprint,
    discover_pdf_files,
    load_ocr_artifacts,
    run_ocr_stage,
    run_pdf_ocr,
)
from .ocr.fields import (
    _detect_invoice_document_kind,
    _enrich_transport_ticket_fields,
    apply_folder_party_rule,
    extract_invoice_fields,
)
from .ocr.memory import (
    _load_analysis_memory,
    _save_analysis_memory,
)
from .ocr.models import (
    OcrArtifact,
    OcrPipelineError,
)
from .ocr.rendering import (
    bank_amount_snapshot,
    compact_analysis_for_storage,
    enforce_dynamic_supplier_payables_exception,
    enforce_template_explanation,
    extract_bank_transaction_date,
)
from .ocr.rules import (
    _contains_foreign_currency,
    _enrich_bank_counterparty_roles,
    _keyword_matches,
    _normalize_match_text,
    _rule_candidates,
)
from .ocr.selector import (
    OpenAICompatibleTemplateSelector,
    _parse_json_object,
)
from .ocr.service import (
    analyze_ocr_and_choose_template,
)

__all__ = [
    'OcrPipelineError',
    'OcrArtifact',
    'extract_invoice_fields',
    'apply_folder_party_rule',
    'discover_pdf_files',
    'run_pdf_ocr',
    'run_ocr_stage',
    'load_ocr_artifacts',
    'OpenAICompatibleTemplateSelector',
    'extract_bank_transaction_date',
    'bank_amount_snapshot',
    'enforce_template_explanation',
    'enforce_dynamic_supplier_payables_exception',
    'compact_analysis_for_storage',
    'analyze_ocr_and_choose_template',
]
