"""Path and source helpers used by the run pipeline orchestration."""

from __future__ import annotations

from pathlib import Path
from .source_profile import normalize_source_key, source_patterns


def expand_path(value: str, company: str, month: str, source: str = "all") -> str:
    """Replace common placeholders in config path templates."""
    return value.replace("{company}", company).replace("{month}", month).replace("{source}", source)


def resolve_config_path(value: str, root: Path, company: str, month: str, source: str = "all") -> Path:
    """
    Resolve a path from config file value.
    Relative values are anchored at project root.
    """
    expanded = expand_path(value, company, month, source)
    candidate = Path(expanded)
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def resolve_source_folders(pipeline_source: str, configured_pdf_folders: list[str] | tuple[str, ...] | None) -> list[str]:
    """Resolve source patterns through the centralized source profile."""
    configured = list(configured_pdf_folders or [])
    source_key = normalize_source_key(str(pipeline_source or "all"))
    if source_key == "all":
        return source_patterns("all", configured or None)
    if source_key:
        return source_patterns(source_key)
    return configured or source_patterns("all")


def resolve_item_class_labels(item_class_id: int) -> list[str]:
    """
    Keep a single source of truth for item class labels when enriching auxiliary mapping.
    """
    labels = {1: "客户", 2: "职员", 3: "项目", 4: "存货", 5: "供应商", 6: "部门"}
    if item_class_id in labels:
        return [labels[item_class_id]]
    return [str(item_class_id)]
