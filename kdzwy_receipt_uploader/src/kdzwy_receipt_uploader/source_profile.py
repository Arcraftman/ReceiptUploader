"""Centralized source kind definitions and filename conventions.

统一使用语义来源名：
- sales（销项发票）
- purchase（进项发票）
- bank（银行凭证）
- misc（杂项凭证）
- all
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


CANONICAL_SOURCE_NAMES = {
    "sales": "销售发票",
    "purchase": "进项发票",
    "bank": "银行凭证",
    "misc": "杂项凭证",
}

_ALIASES = {
    "sales": {"sales", "sale", "selling", "salesinvoice", "销售", "销项", "销项发票", "销售发票"},
    "purchase": {"purchase", "payable", "expense", "采购", "进项", "进项发票", "采购发票"},
    "bank": {"bank", "banks", "banking", "银行", "银行回单", "银行流水", "bank_receipts", "banking_receipts", "银行凭证"},
    "misc": {"misc", "miscellaneous", "other", "杂项", "其他", "杂项凭证"},
}

_SOURCE_FOLDER_HINTS = {
    "sales": ("sales", "销项", "销售", "销售发票", "salesinvoice", "salesinvoice"),
    "purchase": ("purchase", "采购", "进项", "进项发票", "payable", "expense"),
    "bank": ("bank", "银行", "银行回单", "银行流水", "银行凭证", "banking", "banks"),
    "misc": ("misc", "杂项", "其他", "miscellaneous"),
}

_SOURCE_PATTERNS = {
    "sales": ("sales*", "销售*", "销售发票*"),
    "purchase": ("purchase*", "采购*", "进项*", "进项发票*", "expense*"),
    "bank": ("bank*", "banks*", "banking*", "银行*", "银行回单*", "银行流水*"),
    "misc": ("misc*", "杂项*", "其他*", "miscellaneous*"),
}


def normalize_source_key(value: str | None) -> str:
    """Return canonical source key (sales/purchase/bank/misc/all) or empty when invalid."""
    if value is None:
        return ""
    normalized = str(value).strip().lower().replace(" ", "")
    if not normalized:
        return ""
    if normalized in {"sales", "purchase", "bank", "misc", "all"}:
        return normalized

    for canonical, aliases in _ALIASES.items():
        if normalized in aliases:
            return canonical
    return ""


def source_patterns(source: str, configured: Iterable[str] | None = None) -> list[str]:
    """Return folder-glob candidates for a source.

    `configured` keeps external overrides untouched.
    """
    normalized = normalize_source_key(source)
    if not normalized:
        return list(configured or ("sales*", "purchase*"))
    if normalized == "all":
        return list(configured or ("sales*", "purchase*", "bank*", "misc*"))
    return list(_SOURCE_PATTERNS.get(normalized, configured or ("sales*", "purchase*")))


def source_from_folder_name(folder_name: str) -> str:
    """Infer source key from a folder name.

    仅识别语义目录（不再兼容 x/j/y/z 前缀）。
    """
    value = (folder_name or "").strip().lower()
    if not value:
        return ""
    for canonical, hints in _SOURCE_FOLDER_HINTS.items():
        for hint in hints:
            if value == hint or value.startswith(f"{hint}_") or value.startswith(f"{hint}-") or hint in value:
                return canonical
    return ""


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
        SourceLayout("sales", CANONICAL_SOURCE_NAMES["sales"], _SOURCE_PATTERNS["sales"]),
        SourceLayout("purchase", CANONICAL_SOURCE_NAMES["purchase"], _SOURCE_PATTERNS["purchase"]),
        SourceLayout("bank", CANONICAL_SOURCE_NAMES["bank"], _SOURCE_PATTERNS["bank"]),
        SourceLayout("misc", CANONICAL_SOURCE_NAMES["misc"], _SOURCE_PATTERNS["misc"]),
    ]

