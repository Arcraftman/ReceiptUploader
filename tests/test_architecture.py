"""Guard dependency direction and public entry-point behavior."""
from __future__ import annotations

import ast
import importlib
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from kdzwy_receipt_uploader import pipeline_runner

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/kdzwy_receipt_uploader"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            yield node.module or ""
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_dependency_direction():
    # Business consumers must not depend on the diagnostics runner or legacy facade.
    for path in PACKAGE.rglob("*.py"):
        if path.name == "receipts_ocr.py":
            continue
        for name in imports(path):
            assert not name.endswith("receipts_ocr"), path
            if path != PACKAGE / "commands/test_read_apis.py":
                assert not name.endswith("read_api_checks"), path
    for path in (PACKAGE / "ocr").glob("*.py"):
        for name in imports(path):
            assert "application" not in name and "pipeline_runner" not in name, path


def test_bank_rules_can_load_without_io_dependencies():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from kdzwy_receipt_uploader.bank_rules import employee_payment_kind
assert employee_payment_kind(dict(counterpartyName='张三', flowDirection='outflow', remark='8月费用报销')) == 'reimbursement'
assert not {'openpyxl', 'requests', 'pymupdf', 'rapidocr_onnxruntime'} & sys.modules.keys()
"""], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_ocr_exports_share_canonical_types():
    legacy = importlib.import_module("kdzwy_receipt_uploader.receipts_ocr")
    for name, module in {
        "OcrArtifact": "models", "OcrPipelineError": "models",
        "run_ocr_stage": "engine", "extract_invoice_fields": "fields",
        "OpenAICompatibleTemplateSelector": "selector",
        "enforce_template_explanation": "rendering",
        "analyze_ocr_and_choose_template": "service",
    }.items():
        canonical = importlib.import_module(f"kdzwy_receipt_uploader.ocr.{module}")
        assert getattr(legacy, name) is getattr(canonical, name)


@pytest.mark.parametrize("source", ["bank", "sales", "purchase", "misc", "all"])
def test_pipeline_entrypoint_preserves_paths_flags_and_exit_code(tmp_path, monkeypatch, source):
    settings = {
        "company": "company_1", "company_name": "资料公司", "accountbook_name": "目标账套",
        "month": "2026-09", "source": source, "workspace_root": "workspaces/{company}/{month}",
        "templates_file": "templates/company_1/index.json", "pdf_folders": ["sales", "purchase"],
        "mode": "analysis-only", "input": {"usage_filename": "用途.xlsx", "usage_column": "E"},
        "paths": {key: "workspaces/{company}/{month}/" + value for key, value in {
            "month_dir": "month", "input_dir": "input", "map_file": "maps/map.json",
            "sales_map_file": "maps/sales.json", "sales_map_report_file": "maps/sales.report.json",
            "purchase_map_file": "maps/purchase.json", "purchase_map_report_file": "maps/purchase.report.json",
            "receipt_dir": "receipts",
        }.items()},
    }
    (tmp_path / "run.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(pipeline_runner, "project_root", lambda: tmp_path)
    monkeypatch.setattr(pipeline_runner, "configure_pipeline_logger", lambda *a, **k: logging.getLogger("test-dispatch"))
    monkeypatch.setattr(pipeline_runner, "install_console_transcript", lambda *a, **k: tmp_path / "console.log")
    seen = []

    def run(context):
        seen.append(context)
        return 17

    def wrong_route(context):
        pytest.fail("Selected the wrong source workflow")

    monkeypatch.setattr(pipeline_runner, "run_bank_pipeline", run if source == "bank" else wrong_route)
    monkeypatch.setattr(pipeline_runner, "run_invoice_pipeline", wrong_route if source == "bank" else run)
    monkeypatch.setattr(sys, "argv", ["run_pipeline", "--run-config", "run.json", "--app-config", "config/app.json",
                                     "--limit", "3", "--receipt-id", "example", "--test-upload", "--concise"])
    assert pipeline_runner.main() == 17
    context, = seen
    assert context.root == tmp_path
    assert context.app_config_path == tmp_path / "config/app.json"
    assert context.receipt_dir == tmp_path / "workspaces/company_1/2026-09/receipts"
    assert context.document_entity_name == "资料公司" and context.expected_company == "目标账套"
    assert context.args.limit == 3 and context.args.receipt_id == "example"
    assert context.args.test_upload and context.args.concise
    assert context.pipeline_source_key == source
