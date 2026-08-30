"""Render Qwen template_analysis.json as a human-friendly voucher journal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _text(value: Any, default: str = "-") -> str:
    result = str(value or "").strip()
    return result or default


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f} 元"
    except (TypeError, ValueError):
        return "金额未识别"


def _template_name(value: Any) -> str:
    name = Path(_text(value, "未选择模板")).name
    return name.removesuffix("_template.json").removesuffix(".json")


def _entry_line(entry: dict[str, Any]) -> str:
    number = _text(entry.get("accountNumber"), "科目号缺失")
    name = _text(entry.get("accountName"), "科目名称缺失")
    result = f"{number} {name}　{_money(entry.get('amount'))}"
    auxiliary = entry.get("auxiliary")
    if isinstance(auxiliary, dict) and auxiliary.get("name"):
        aux_class = _text(auxiliary.get("itemClass"), f"辅助类别{auxiliary.get('itemClassId', '')}")
        aux_number = _text(auxiliary.get("number"), "")
        aux_name = _text(auxiliary.get("name"), "")
        result += f"　（{aux_class}：{' '.join(x for x in (aux_number, aux_name) if x)}）"
    return result


def _party_summary(fields: dict[str, Any]) -> str:
    parts = []
    if fields.get("invoiceDate"):
        parts.append(f"日期：{fields['invoiceDate']}")
    if fields.get("sellerName"):
        parts.append(f"销售方：{fields['sellerName']}")
    if fields.get("buyerName"):
        parts.append(f"购买方：{fields['buyerName']}")
    return "；".join(parts)


def render_concise_analysis(payload: dict[str, Any], source: Path | None = None) -> str:
    ready = sum(
        1 for value in payload.values()
        if isinstance(value, dict) and value.get("analysisStatus") == "ready_for_review"
    )
    blocked = sum(
        1 for value in payload.values()
        if isinstance(value, dict) and value.get("analysisStatus") == "blocked"
    )
    lines = [
        "# Qwen 模板分析简表",
        "",
        f"> 共 {len(payload)} 张发票；可复核 {ready}；被阻断 {blocked}。本文件仅用于人工查看，不代表凭证已经保存或上传。",
    ]
    if source is not None:
        lines.extend(["", f"> 数据来源：`{source}`"])

    for invoice_key, raw in payload.items():
        analysis = raw if isinstance(raw, dict) else {}
        fields = analysis.get("extractedFields") if isinstance(analysis.get("extractedFields"), dict) else {}
        invoice = _text(analysis.get("invoiceCode") or fields.get("invoiceNumber") or invoice_key)
        template = _template_name(analysis.get("templatePath") or analysis.get("templateId"))
        status = _text(analysis.get("analysisStatus") or analysis.get("status"), "未知")
        confidence = analysis.get("confidence")
        try:
            confidence_text = f"{float(confidence) * 100:.0f}%"
        except (TypeError, ValueError):
            confidence_text = "未提供"

        lines.extend([
            "",
            "---",
            "",
            f"## 发票号：{invoice}",
            "",
            f"**选择模板：{template}**",
            "",
            f"状态：{status}　置信度：{confidence_text}",
        ])
        party = _party_summary(fields)
        if party:
            lines.extend(["", party])

        entries = analysis.get("filledEntries") if isinstance(analysis.get("filledEntries"), list) else []
        debit = [entry for entry in entries if isinstance(entry, dict) and entry.get("dc") == 1]
        credit = [entry for entry in entries if isinstance(entry, dict) and entry.get("dc") in (-1, 0, "-1", "0")]
        unknown = [entry for entry in entries if isinstance(entry, dict) and entry not in debit and entry not in credit]
        lines.extend(["", "### 拟记账分录", ""])
        if debit:
            for index, entry in enumerate(debit):
                lines.append(("借：" if index == 0 else "　　") + _entry_line(entry))
        else:
            lines.append("借：未生成")
        lines.append("")
        if credit:
            for index, entry in enumerate(credit):
                lines.append(("贷：" if index == 0 else "　　") + _entry_line(entry))
        else:
            lines.append("贷：未生成")
        for entry in unknown:
            lines.append("方向未识别：" + _entry_line(entry))

        zero_dc = sum(1 for entry in credit if entry.get("dc") in (0, "0"))
        if zero_dc:
            lines.extend(["", f"> ⚠ Qwen 有 {zero_dc} 条分录返回 `dc=0`，简表按贷方展示，但系统要求贷方为 `dc=-1`。"])
        if analysis.get("blockReason"):
            lines.extend(["", f"> ⚠ 阻断原因：{analysis['blockReason']}"])
        errors = analysis.get("finalTemplateValidationErrors")
        if isinstance(errors, list) and errors:
            lines.extend(["", "> 系统校验：" + "；".join(str(item) for item in errors)])
        if analysis.get("reason"):
            lines.extend(["", f"分析说明：{analysis['reason']}"])
    lines.append("")
    return "\n".join(lines)


def write_concise_analysis(input_path: Path, output_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"template_analysis 必须是JSON对象：{input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_concise_analysis(payload, input_path.resolve()), encoding="utf-8")
    return {"invoiceCount": len(payload), "input": str(input_path.resolve()), "output": str(output_path.resolve())}
