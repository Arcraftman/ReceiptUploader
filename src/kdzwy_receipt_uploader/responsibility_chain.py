"""Four-source responsibility chain for preprocessing and safe upload dispatch."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .models import ApiError
from .source_profile import normalize_source_key


class SourceKind(str, Enum):
    SALES = "sales"
    PURCHASE = "purchase"
    BANK = "bank"
    MISC = "misc"


@dataclass
class ChainContext:
    source: SourceKind
    month_dir: Path
    mode: str = "dry-run"
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


class ChainStep:
    name = "step"

    def __init__(self, next_step: "ChainStep | None" = None) -> None:
        self.next_step = next_step

    def handle(self, context: ChainContext) -> ChainContext:
        context.steps.append(self.name)
        self.run(context)
        if self.next_step:
            return self.next_step.handle(context)
        return context

    def run(self, context: ChainContext) -> None:
        raise NotImplementedError


class SourceFilterStep(ChainStep):
    name = "source_filter"

    def run(self, context: ChainContext) -> None:
        context.data["sourcePattern"] = f"{context.source.value}*"
        context.data["scopeReady"] = True


class OcrStep(ChainStep):
    name = "ocr"

    def run(self, context: ChainContext) -> None:
        context.data["ocrReady"] = True


class TemplateStep(ChainStep):
    name = "template_analysis"

    def run(self, context: ChainContext) -> None:
        context.data["templateReady"] = True


class ValidationStep(ChainStep):
    name = "validation"

    def run(self, context: ChainContext) -> None:
        context.data["validationReady"] = True


class BankPendingStep(ChainStep):
    name = "bank_pending"

    def run(self, context: ChainContext) -> None:
        raise ApiError("bank 业务规则尚未指定，责任链已安全阻断，未生成或提交凭证")


class MiscPendingStep(ChainStep):
    name = "misc_pending"

    def run(self, context: ChainContext) -> None:
        raise ApiError("misc 业务规则尚未指定，责任链已安全阻断，未生成或提交凭证")


def build_chain(source: SourceKind) -> ChainStep:
    tail: ChainStep = ValidationStep()
    if source is SourceKind.BANK:
        tail = BankPendingStep(tail)
    elif source is SourceKind.MISC:
        tail = MiscPendingStep(tail)
    else:
        tail = TemplateStep(tail)
    return SourceFilterStep(OcrStep(tail))


def parse_sources(value: str) -> list[SourceKind]:
    normalized = value.strip().lower()
    canonical = normalize_source_key(normalized)
    if canonical == "all":
        return [SourceKind.SALES, SourceKind.PURCHASE, SourceKind.BANK, SourceKind.MISC]
    result = []
    for item in normalized.replace(",", " ").split():
        key = normalize_source_key(item)
        if key in {"sales", "purchase", "bank", "misc"}:
            result.append(SourceKind(key))
            continue
        raise ValueError(f"不支持的来源：{item}，只能选择 sales/purchase/bank/misc/all")
    if not result:
        raise ValueError("至少选择一个来源：sales/purchase/bank/misc/all")
    return result


def run_selected_sources(month_dir: Path, selection: str, mode: str = "dry-run") -> list[ChainContext]:
    contexts = []
    for source in parse_sources(selection):
        context = ChainContext(source=source, month_dir=month_dir, mode=mode)
        contexts.append(build_chain(source).handle(context))
    return contexts


def run_selected_sources_safe(month_dir: Path, selection: str, mode: str = "dry-run") -> list[ChainContext]:
    contexts = []
    for source in parse_sources(selection):
        context = ChainContext(source=source, month_dir=month_dir, mode=mode)
        try:
            contexts.append(build_chain(source).handle(context))
        except ApiError as exc:
            context.data["blocked"] = True
            context.data["blockReason"] = str(exc)
            context.steps.append("blocked")
            contexts.append(context)
    return contexts
