"""Flat detailed-name template catalog."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .voucher_templates import TemplateContext, TemplateError, VoucherTemplateEngine


_DOCUMENT_CODES = {
    "增值税发票": "VAT_INVOICE",
    "费用单据": "EXPENSE_DOCUMENT",
    "银行回单": "BANK_RECEIPT",
}
_SETTLEMENT_CODES = {
    "往来结算": "AP_AR",
    "银行支付": "BANK_PAYMENT",
    "银行结算": "BANK_SETTLEMENT",
}
_CURRENCY_CODES = {"人民币": "CNY", "美元": "USD"}


def _decision_code(record: Mapping[str, Any], template: Mapping[str, Any]) -> str:
    """Build a stable five-segment semantic code shown to the classifier."""
    rules = template.get("matchRules") if isinstance(template.get("matchRules"), Mapping) else {}
    source_folders = rules.get("sourceFolders") if isinstance(rules.get("sourceFolders"), list) else []
    path_parts = Path(str(record.get("path") or "")).parts
    source = str(source_folders[0] if len(source_folders) == 1 else path_parts[0] if path_parts else "misc").upper()
    document = _DOCUMENT_CODES.get(str(template.get("documentType") or ""), str(template.get("documentType") or "UNKNOWN_DOCUMENT"))
    settlement = _SETTLEMENT_CODES.get(str(template.get("settlementMethod") or ""), str(template.get("settlementMethod") or "UNKNOWN_SETTLEMENT"))
    business = re.sub(r"[\s.|/\\]+", "_", str(template.get("businessType") or "UNKNOWN_BUSINESS").strip())
    currency = _CURRENCY_CODES.get(str(template.get("currency") or ""), str(template.get("currency") or "UNKNOWN_CURRENCY"))
    return ".".join((source, document, settlement, business, currency))


class TemplateCatalog:
    def __init__(self, root: Path, index: Mapping[str, Any]) -> None:
        self.root = root.resolve()
        self.index = dict(index)
        self.records = [dict(item) for item in self.index.get("templates", [])]

    @classmethod
    def load(cls, root: Path) -> "TemplateCatalog":
        root = root.resolve()
        index_path = root / "index.json"
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TemplateError(f"模板索引无效：{index_path}")
        pattern = str(payload.get("templatePattern", "*_template.json"))
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for path in sorted(root.rglob(pattern)):
            if path.name == "index.json" or not path.is_file():
                continue
            relative_path = path.relative_to(root).as_posix()
            template = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(template, dict):
                raise TemplateError(f"模板内容无效：{path}")
            template_id = str(template.get("id") or "").strip()
            template_name = str(template.get("name") or "").strip()
            if not template_id or not template_name:
                raise TemplateError(f"模板必须直接声明 id 和 name：{path}")
            if template_id in seen_ids:
                raise TemplateError(f"模板 id 重复：{template_id}")
            enabled = template.get("enabled", True)
            if not isinstance(enabled, bool):
                raise TemplateError(f"模板 enabled 必须是布尔值：{path}")
            records.append({
                "id": template_id,
                "name": template_name,
                "path": relative_path,
                "enabled": enabled,
                "version": str(template.get("version") or "1.0"),
            })
            seen_ids.add(template_id)
        if not records:
            raise TemplateError(f"模板目录中没有匹配 {pattern} 的模板：{root}")
        payload["templates"] = records
        return cls(root, payload)

    def load_template(self, record: Mapping[str, Any]) -> dict[str, Any]:
        relative = Path(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise TemplateError("模板路径必须位于 templates 目录内")
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise TemplateError("模板路径越过 templates 目录") from exc
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TemplateError(f"模板内容无效：{path}")
        payload["source"] = str(record.get("path"))
        payload["templateFileName"] = path.name
        payload.setdefault("version", record.get("version", "1.0"))
        payload.setdefault("name", record.get("name", ""))
        payload.setdefault("when", record.get("when", {}))
        payload.setdefault("decisionCode", _decision_code(record, payload))
        payload.setdefault(
            "decisionName",
            "｜".join(str(payload.get(key) or "") for key in ("documentBlock", "documentType", "settlementMethod", "businessType", "currency")),
        )
        return payload

    def select(self, context: TemplateContext) -> tuple[dict[str, Any], dict[str, Any]]:
        candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for record in self.records:
            if not bool(record.get("enabled", True)):
                continue
            template = self.load_template(record)
            engine = VoucherTemplateEngine([template])
            condition = template.get("when", {})
            if engine._matches(condition, context):
                candidates.append((engine._specificity(condition), record, template))
        if not candidates:
            raise TemplateError("templates 根目录没有匹配的模板")
        highest = max(score for score, _, _ in candidates)
        best = [(record, template) for score, record, template in candidates if score == highest]
        if len(best) != 1:
            raise TemplateError(f"四级模板匹配冲突：{[x[0].get('path') for x in best]}")
        return best[0]

    def render_for(self, context: TemplateContext, template_path: str | None = None) -> dict[str, Any]:
        if template_path:
            matches = [record for record in self.records if str(record.get("path", "")) == template_path]
            if len(matches) != 1:
                raise TemplateError(f"指定模板路径不存在或不唯一：{template_path}")
            record = matches[0]
            template = self.load_template(record)
            rendered_context = TemplateContext(
                invoice_code=context.invoice_code,
                sales_map=context.sales_map,
                accountbook=context.accountbook,
                source=context.source,
                purchase_map=context.purchase_map,
                template_name=str(template.get("name", "")),
            )
            rendered = VoucherTemplateEngine([template]).render(template, rendered_context)
        else:
            record, template = self.select(context)
            rendered = VoucherTemplateEngine([template]).render_for(context)
        rendered["templatePath"] = str(record["path"])
        rendered["templateFileName"] = Path(str(record["path"])).name
        rendered["templateBlock"] = record.get("documentBlock", template.get("documentBlock", ""))
        rendered["templateUnitPriceName"] = record.get("unitPriceName", "")
        rendered["templateSettlementMethod"] = record.get("settlementMethod", template.get("settlementMethod", ""))
        rendered["templateBusinessType"] = record.get("businessType", template.get("businessType", ""))
        return rendered
