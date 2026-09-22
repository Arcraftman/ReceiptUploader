"""Stage isolation prevents accidental OCR regeneration and remote uploads."""
import json
from types import SimpleNamespace

import pytest
from kdzwy_receipt_uploader.application import bank_pipeline as pipeline
from kdzwy_receipt_uploader.application import bank_preprocessing as preprocessing
from kdzwy_receipt_uploader.application import bank_analysis as analysis
from kdzwy_receipt_uploader.application import bank_preparation as preparation
from kdzwy_receipt_uploader.company_registry import workflow_stage_plan


def context(tmp_path, stage):
    mode, internal = workflow_stage_plan(stage)
    return SimpleNamespace(root=tmp_path, analysis_stage=internal, mode=mode,
        company="company_1", month="2026-08", pipeline_source_key="bank",
        paths_config={"receipts_ocr_dir": str(tmp_path / "ocr")}, map_path=tmp_path / "maps/map.json")


@pytest.mark.parametrize("stage,expected", [
    ("ocr", ["ocr", "match"]), ("match", ["match"]), ("llm", ["llm"]),
    ("prepare", ["prepare"]), ("all", ["ocr", "match", "llm", "prepare"]),
    ("send", ["send"]), ("verify", ["verify"])])
def test_stages_only_execute_their_dependencies(tmp_path, monkeypatch, stage, expected):
    ctx = context(tmp_path, stage)
    (tmp_path / "ocr").mkdir(); (tmp_path / "maps").mkdir()
    (tmp_path / "ocr/ocr_stage.report.json").write_text("{}")
    (tmp_path / "maps/bank_exceptions.json").write_text("{}")
    calls = []
    monkeypatch.setattr(preprocessing, "run_bank_ocr", lambda c: calls.append("ocr") or {})
    monkeypatch.setattr(preprocessing, "run_bank_match", lambda c, r: calls.append("match") or 0)
    monkeypatch.setattr(pipeline, "load_bank_records", lambda *a: ({}, []))
    monkeypatch.setattr(analysis, "run_bank_analysis", lambda *a: calls.append("llm") or 0)
    monkeypatch.setattr(preparation, "run_bank_preparation", lambda *a: calls.append("prepare") or 0)
    monkeypatch.setattr(pipeline, "_submit_existing_bank", lambda c: calls.append("send") or 0)
    monkeypatch.setattr(pipeline, "_verify_pdf_gate", lambda c: calls.append("verify") or 0)
    assert pipeline.run_bank_pipeline(ctx) == 0
    assert calls == expected


@pytest.mark.parametrize("stage", ["match", "llm", "prepare"])
def test_missing_inputs_fail_without_falling_back_to_ocr(tmp_path, monkeypatch, stage):
    def forbidden(*a, **kw):
        pytest.fail("Missing prerequisites must not trigger OCR, model calls or upload")
    monkeypatch.setattr(preprocessing, "run_bank_ocr", forbidden)
    monkeypatch.setattr(analysis, "run_bank_analysis", forbidden)
    monkeypatch.setattr(preparation, "run_bank_preparation", forbidden)
    assert pipeline.run_bank_pipeline(context(tmp_path, stage)) == 2


def test_all_stops_on_ocr_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocessing, "run_bank_ocr", lambda c: 2)
    monkeypatch.setattr(preprocessing, "run_bank_match", lambda *a: pytest.fail("Must stop"))
    assert pipeline.run_bank_pipeline(context(tmp_path, "all")) == 2
