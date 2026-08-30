"""Centralized source kind definitions and filename conventions.

统一使用语义来源名：
- sales（销项发票）
- purchase（进项发票）
- bank（银行凭证）
- misc（杂项凭证）
- all
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


CANONICAL_SOURCE_NAMES = {
    "sales": "销售发票",
    "purchase": "进项发票",
    "bank": "银行凭证",
    "misc": "杂项凭证",
}
BUILT_IN_SOURCES = tuple(CANONICAL_SOURCE_NAMES)

_SOURCE_FOLDERS = {
    "sales": ("sales",),
    "purchase": ("purchase",),
    "bank": ("bank",),
    "misc": ("misc",),
}


def normalize_source_key(value: str | None) -> str:
    """Return canonical source key (sales/purchase/bank/misc/all) or empty when invalid."""
    if value is None:
        return ""
    normalized = str(value).strip().lower()
    if not normalized:
        return ""
    if normalized in {"sales", "purchase", "bank", "misc", "all"}:
        return normalized

    return ""


def source_patterns(source: str, configured: Iterable[str] | None = None) -> list[str]:
    """Return exact standard folder names for a source selection."""
    normalized = normalize_source_key(source)
    configured_sources: list[str] = []
    for value in configured or ():
        key = normalize_source_key(str(value))
        if key in CANONICAL_SOURCE_NAMES and key not in configured_sources:
            configured_sources.append(key)
    if normalized in CANONICAL_SOURCE_NAMES:
        return [normalized]
    return configured_sources or list(CANONICAL_SOURCE_NAMES)


def source_from_folder_name(folder_name: str) -> str:
    """Return the canonical key only for an exact standard folder name."""
    value = (folder_name or "").strip().lower()
    return value if value in CANONICAL_SOURCE_NAMES else ""


def describe_source(value: str) -> str:
    key = normalize_source_key(value)
    return CANONICAL_SOURCE_NAMES.get(key, value)


@dataclass(frozen=True)
class SourceLayout:
    """Stable mapping between semantic source and on-disk folders."""

    source: str
    label: str
    patterns: tuple[str, ...]

    @property
    def canonical(self) -> str:
        return normalize_source_key(self.source)


def source_layouts() -> list[SourceLayout]:
    return [
        SourceLayout("sales", CANONICAL_SOURCE_NAMES["sales"], _SOURCE_FOLDERS["sales"]),
        SourceLayout("purchase", CANONICAL_SOURCE_NAMES["purchase"], _SOURCE_FOLDERS["purchase"]),
        SourceLayout("bank", CANONICAL_SOURCE_NAMES["bank"], _SOURCE_FOLDERS["bank"]),
        SourceLayout("misc", CANONICAL_SOURCE_NAMES["misc"], _SOURCE_FOLDERS["misc"]),
    ]

