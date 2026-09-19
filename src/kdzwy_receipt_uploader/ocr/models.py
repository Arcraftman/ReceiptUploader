"""Shared OCR artifact and error types."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class OcrPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class OcrArtifact:
    invoice_code: str
    source_pdf: Path
    source_folder: str
    source_side: str
    output_dir: Path
    text_path: Path
    metadata_path: Path
    text: str
    engine: str
    status: str
