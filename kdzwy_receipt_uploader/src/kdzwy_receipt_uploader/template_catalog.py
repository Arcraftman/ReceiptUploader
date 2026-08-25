"""Flat detailed-name template catalog."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .voucher_templates import TemplateContext, TemplateError, VoucherTemplateEngine


class TemplateCatalog:
    def __init__(self, root: Path, index: Mapping[str, Any]) -> None:
        self.root = root.resolve()
        self.index = dict(index)
        self.records = [dict(item) for item in self.index.get("templates", [])]
        rules_path = self.root / "purchase_business_rules.json"
        self.business_rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else {}

    @classmethod
    def load(cls, root: Path) -> "TemplateCatalog":
        root = root.resolve()
        index_path = root / "index.json"
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TemplateError(f"模板索引无效：{index_path}")
        configured = payload.get("templates") if isinstance(payload.get("templates"), list) else []
        records_by_path = {str(item.get("path")): dict(item) for item in configured if isinstance(item, Mapping) and item.get("path")}
        pattern = str(payload.get("templatePattern", "*_template.json"))
        for path in sorted(root.rglob(pattern)):
            if path.name == "index.json" or not path.is_file():
                continue
            relative_path = path.relative_to(root).as_posix()
            records_by_path.setdefault(relative_path, {"id": path.stem, "name": path.stem.removesuffix("_template"), "path": relative_path, "enabled": True})
        rules_path = root / "purchase_business_rules.json"
        rules_payload = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else {}
        template_rules = rules_payload.get("templates") if isinstance(rules_payload.get("templates"), Mapping) else {}
        for record in records_by_path.values():
            override = template_rules.get(str(record.get("id"))) if isinstance(template_rules, Mapping) else None
            if isinstance(override, Mapping) and "enabled" in override:
                record["enabled"] = bool(override.get("enabled"))
        payload["templates"] = list(records_by_path.values())
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
        template_rules = self.business_rules.get("templates") if isinstance(self.business_rules.get("templates"), Mapping) else {}
        override = template_rules.get(str(record.get("id"))) if isinstance(template_rules, Mapping) else None
        if isinstance(override, Mapping):
            if isinstance(override.get("keywords"), list):
                payload["keywords"] = list(override["keywords"])
            if isinstance(override.get("matchRules"), Mapping):
                payload["matchRules"] = {**dict(payload.get("matchRules") or {}), **dict(override["matchRules"])}
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
