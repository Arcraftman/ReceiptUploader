"""Config-driven voucher template selection and rendering.

Templates describe how a receipt should be shaped. They do not decide the
business meaning of date, amount, or auxiliary objects unless a later rule
explicitly supplies those values.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


def _resolve_placeholder(name: str, context: "TemplateContext") -> Any:
    if name == "invoiceCode":
        return context.invoice_code
    if name.startswith("sales_map."):
        value: Any = context.sales_map.get(context.invoice_code, {})
        for part in name.split(".")[1:]:
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part, "")
        return value
    if name.startswith("purchase_map."):
        value: Any = (context.purchase_map or {}).get(context.invoice_code, {})
        for part in name.split(".")[1:]:
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part, "")
        return value
    if name.startswith("source."):
        value: Any = context.source
        for part in name.split(".")[1:]:
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part, "")
        return value
    if name.startswith("accountbook."):
        value: Any = context.accountbook
        for part in name.split(".")[1:]:
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part, "")
        return value
    if name == "templateName":
        return context.template_name
    return ""


def render_text(value: Any, context: "TemplateContext") -> str:
    text = str(value or "")
    return re.sub(
        r"\{([A-Za-z_][A-Za-z0-9_.]*)\}",
        lambda match: str(_resolve_placeholder(match.group(1), context)),
        text,
    )


@dataclass(frozen=True)
class TemplateContext:
    invoice_code: str
    sales_map: Mapping[str, Any]
    accountbook: Mapping[str, Any]
    source: Mapping[str, Any]
    purchase_map: Mapping[str, Any] | None = None
    template_name: str = ""

    @property
    def item_class(self) -> str:
        return str(
            self.source.get("itemClass")
            or self.source.get("item_class")
            or self.accountbook.get("itemClass")
            or self.accountbook.get("item_class")
            or ""
        )

    @property
    def custom_name(self) -> str:
        return str(
            self.source.get("customName")
            or self.source.get("custom_name")
            or self.accountbook.get("customName")
            or self.accountbook.get("custom_name")
            or ""
        )


class TemplateError(ValueError):
    pass


class VoucherTemplateEngine:
    def __init__(self, templates: list[Mapping[str, Any]]) -> None:
        self.templates = [dict(template) for template in templates]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "VoucherTemplateEngine":
        templates = config.get("templates", config.get("voucher_templates", []))
        if not isinstance(templates, list):
            raise TemplateError("templates 必须是数组")
        return cls(templates)

    def select(self, context: TemplateContext) -> Mapping[str, Any]:
        matches: list[tuple[int, Mapping[str, Any]]] = []
        for template in self.templates:
            if not bool(template.get("enabled", True)):
                continue
            condition = template.get("when", {})
            if not isinstance(condition, Mapping):
                raise TemplateError("模板 when 必须是对象")
            if self._matches(condition, context):
                matches.append((self._specificity(condition), template))
        if not matches:
            raise TemplateError("没有匹配的凭证模板")
        highest = max(score for score, _ in matches)
        best = [template for score, template in matches if score == highest]
        if len(best) != 1:
            names = [str(item.get("name", "<unnamed>")) for item in best]
            raise TemplateError(f"最高优先级模板仍然冲突：{names}")
        return best[0]

    @staticmethod
    def _specificity(condition: Mapping[str, Any]) -> int:
        # More specific conditions override broad fallback templates.
        return sum(2 if key in {"customName", "customNameContains"} else 1 for key in condition)

    @staticmethod
    def _matches(condition: Mapping[str, Any], context: TemplateContext) -> bool:
        if "itemClass" in condition and str(condition["itemClass"]) != context.item_class:
            return False
        business_type = context.source.get("businessType") or context.accountbook.get("businessType") or context.source.get("business_type") or context.accountbook.get("business_type") or ""
        settlement_method = context.source.get("settlementMethod") or context.accountbook.get("settlementMethod") or context.source.get("settlement_method") or context.accountbook.get("settlement_method") or ""
        if "businessType" in condition and str(condition["businessType"]) != str(business_type):
            return False
        if "settlementMethod" in condition and str(condition["settlementMethod"]) != str(settlement_method):
            return False
        if "customName" in condition and str(condition["customName"]) != context.custom_name:
            return False
        if "customNameContains" in condition and str(condition["customNameContains"]) not in context.custom_name:
            return False
        return True

    def render(self, template: Mapping[str, Any], context: TemplateContext) -> dict[str, Any]:
        summary = template.get("summary", {})
        if not isinstance(summary, Mapping):
            raise TemplateError("模板 summary 必须是对象")
        header = render_text(summary.get("header", ""), context)
        body = render_text(summary.get("body", ""), context)
        separator = str(summary.get("separator", ""))
        full_summary = separator.join(part for part in (header, body) if part)
        explanation_header = render_text(template.get("explanation_header", header), context)
        explanation_body = render_text(template.get("explanation_body", body), context)
        explanation_separator = str(template.get("explanation_separator", " "))
        explanation = explanation_separator.join(part for part in (explanation_header, explanation_body) if part)

        entry_templates = template.get("entries", [])
        if not isinstance(entry_templates, list):
            raise TemplateError("模板 entries 必须是数组")
        entries: list[dict[str, Any]] = []
        for index, raw_entry in enumerate(entry_templates, start=1):
            if not isinstance(raw_entry, Mapping):
                raise TemplateError("模板 entries 中每一项必须是对象")
            entry = dict(raw_entry)
            entry["lineNo"] = entry.get("lineNo", index)
            # A voucher has one explanation. Individual entries may not let
            # DeepSeek or template fragments diverge from it.
            entry["explanation"] = explanation
            amount_from = entry.pop("amountFrom", None)
            amount_for_from = entry.pop("amountForFrom", None)
            if amount_from:
                entry["amount"] = _resolve_placeholder(str(amount_from), context)
            if amount_for_from:
                entry["amountFor"] = _resolve_placeholder(str(amount_for_from), context)
            auxiliary = entry.pop("auxiliary", None)
            if isinstance(auxiliary, Mapping):
                entry["_auxiliary"] = dict(auxiliary)
                field = str(auxiliary.get("field", ""))
                selector = str(auxiliary.get("selector", ""))
                if field and selector:
                    selected = _resolve_placeholder(selector if "." in selector else f"source.{selector}", context)
                    if selected not in (None, ""):
                        entry[field] = selected
            entries.append(entry)
        return {
            "templateName": str(template.get("name", "")),
            "templateVersion": str(template.get("version", "1.0")),
            "templateSource": str(template.get("source", "templates")),
            "summary_header": header,
            "summary_body": body,
            "summary": full_summary,
            "explanation_header": explanation_header,
            "explanation_body": explanation_body,
            "explanation": explanation,
            "entries": entries,
        }

    def render_for(self, context: TemplateContext) -> dict[str, Any]:
        template = self.select(context)
        rendered_context = TemplateContext(
            invoice_code=context.invoice_code,
            sales_map=context.sales_map,
            accountbook=context.accountbook,
            source=context.source,
            purchase_map=context.purchase_map,
            template_name=str(template.get("name", "")),
        )
        return self.render(template, rendered_context)
