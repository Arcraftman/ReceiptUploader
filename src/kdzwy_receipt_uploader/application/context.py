"""Explicit shared inputs for source-specific pipeline workflows."""
from __future__ import annotations

from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any

from .contracts import PipelineOptions, StageCheckpoint
from ..month_config import MonthConfig
from ..template_catalog import TemplateCatalog


@dataclass(frozen=True)
class PipelineContext:
    root: Path
    analysis_stage: str
    app_config_path: Path
    args: PipelineOptions
    checkpoint: StageCheckpoint
    company: str
    config: MonthConfig
    document_entity_name: str
    expected_company: str
    input_dir: Path
    logger: Logger
    map_path: Path
    mode: str
    month: str
    month_dir: Path
    paths_config: dict[str, Any]
    pdf_folders: list[str]
    pipeline_source_key: str
    purchase_map_path: Path
    purchase_map_report_path: Path
    receipt_dir: Path
    run_config_path: Path
    sales_map_path: Path
    sales_map_report_path: Path
    settings: dict[str, Any]
    template_catalog: TemplateCatalog | None
    template_path: Path
    template_root: Path
    workflow_stage: str
    workspace_root: Path
