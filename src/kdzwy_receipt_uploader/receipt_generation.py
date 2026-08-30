"""Generate one receipt skeleton for each invoice PDF in sales/purchase/bank/misc folders."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .matching import pdf_invoice_number
from .month_config import MonthConfig
from .source_profile import source_from_folder_name
from .voucher_templates import TemplateContext, VoucherTemplateEngine
from .template_catalog import TemplateCatalog


@lru_cache(maxsize=32)
def _discover_source_pdfs_cached(month_directory: str, patterns: tuple[str, ...]) -> tuple[dict[str, tuple[Path, ...]], tuple[dict[str, Any], ...]]:
    candidates: dict[str, list[Path]] = {}
    invalid: list[dict[str, Any]] = []
    directory = Path(month_directory)
    source_folders: list[str] = []
    for pattern in patterns:
        source = source_from_folder_name(pattern)
        if not source:
            raise ValueError(f"来源目录只支持 sales/purchase/bank/misc：{pattern}")
        if source not in source_folders:
            source_folders.append(source)
    folders = {(directory / source).resolve() for source in source_folders if (directory / source).is_dir()}
    for folder in sorted(folders):
        for pdf in sorted(folder.rglob("*.pdf")):
            key = pdf_invoice_number(pdf)
            if key is None:
                invalid.append({"pdf": str(pdf.resolve()), "reason": "PDF 文件名无法提取发票号"})
                continue
            candidates.setdefault(key, []).append(pdf.resolve())
    return {key: tuple(paths) for key, paths in candidates.items()}, tuple(invalid)


def discover_source_pdfs(month_directory: Path, folder_patterns: list[str] | None = None) -> tuple[dict[str, list[Path]], list[dict[str, Any]]]:
    patterns = tuple(folder_patterns or ["sales", "purchase", "bank", "misc"])
    cached_candidates, cached_invalid = _discover_source_pdfs_cached(str(month_directory.resolve()), patterns)
    return {key: list(paths) for key, paths in cached_candidates.items()}, [dict(item) for item in cached_invalid]


def generate_receipts(month_directory: Path, config: MonthConfig, output_directory: Path, overwrite: bool = False, map_file: Path | None = None, folder_patterns: list[str] | None = None, voucher_defaults: dict[str, Any] | None = None, entry_defaults: list[dict[str, Any]] | None = None, sales_map_values: dict[str, Any] | None = None, template_config: dict[str, Any] | None = None, only_mapped_invoices: bool = False, draft: bool = True, template_catalog: TemplateCatalog | None = None, purchase_map_values: dict[str, Any] | None = None, allowed_invoice_codes: set[str] | None = None) -> dict[str, Any]:
    month_directory = month_directory.resolve()
    output_directory = output_directory.resolve()
    pdfs, invalid = discover_source_pdfs(month_directory, folder_patterns)
    if allowed_invoice_codes is not None:
        pdfs = {code: paths for code, paths in pdfs.items() if code in allowed_invoice_codes}
    map_values: dict[str, Any] = {}
    if map_file and map_file.is_file():
        try:
            raw_map = json.loads(map_file.read_text(encoding="utf-8"))
            if isinstance(raw_map, dict):
                map_values = raw_map
        except (OSError, json.JSONDecodeError):
            invalid.append({"map": str(map_file), "reason": "map 文件无法读取"})
    output_directory.mkdir(parents=True, exist_ok=True)
    sales_map_values = sales_map_values or {}
    purchase_map_values = purchase_map_values or {}
    mapped_invoice_codes = set(sales_map_values) | set(purchase_map_values)
    template_engine = VoucherTemplateEngine.from_config(template_config or {}) if template_config else None
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []
    for invoice_code in sorted(pdfs):
        if only_mapped_invoices and invoice_code not in mapped_invoice_codes:
            filtered_out.append({"invoiceCode": invoice_code, "reason": "不在 sales_map 或 purchase_map 中"})
            continue
        receipt_dir = output_directory / f"receipt_{invoice_code}"
        receipt_path = receipt_dir / "receipt.json"
        if receipt_path.exists() and not overwrite:
            skipped.append({"invoiceCode": invoice_code, "receipt": str(receipt_path.resolve()), "reason": "已存在，未覆盖"})
            continue
        receipt_dir.mkdir(parents=True, exist_ok=True)
        safe_company = re.sub(r"[^A-Za-z0-9._-]", "_", config.company) or "company"
        safe_month = re.sub(r"[^A-Za-z0-9._-]", "_", config.month) or "month"
        defaults = voucher_defaults or {}
        x_values = dict(sales_map_values.get(invoice_code) or {})
        j_values = dict(purchase_map_values.get(invoice_code) or {})
        # Merge source metadata for template matching and receipt generation.
        # purchase_map takes precedence for supplier-side values; sales_map provides
        # customer-side fields when both contain the invoice.
        business_values = {**x_values, **j_values}
        if "supplierName" in j_values:
            business_values["customName"] = j_values["supplierName"]
            business_values["itemClass"] = "供应商"
            business_values["supplierItem"] = business_values.get("auxiliaryItem")
        template_rendered: dict[str, Any] = {}
        context = TemplateContext(invoice_code=invoice_code, sales_map=sales_map_values, accountbook=defaults, source=business_values, purchase_map=purchase_map_values)
        if template_catalog:
            analysis_path = str(business_values.get("ocrAnalysis", {}).get("templatePath", "")) if isinstance(business_values.get("ocrAnalysis"), dict) else ""
            template_rendered = template_catalog.render_for(context, analysis_path or None)
        elif template_engine:
            template_rendered = template_engine.render_for(context)
        entries = []
        filled_entries = business_values.get("ocrAnalysis", {}).get("filledEntries", []) if isinstance(business_values.get("ocrAnalysis"), dict) else []
        authoritative_template_entries = template_rendered.get("entries", []) if isinstance(template_rendered.get("entries"), list) else []
        source_entries = filled_entries or authoritative_template_entries or list(entry_defaults or [])
        subject_by_number = {
            str(item.get("account_number")): item
            for item in (entry_defaults or [])
            if item.get("account_number")
        }
        for source_index, item in enumerate(source_entries):
            entry = dict(item)
            # Qwen may fill account/amount/explanation, but it cannot add
            # auxiliary accounting to a line where the approved template does
            # not declare it. The live subject capabilities are authoritative.
            entry.pop("auxiliary", None)
            entry.pop("_auxiliary", None)
            if source_index < len(authoritative_template_entries):
                declared_auxiliary = authoritative_template_entries[source_index].get("_auxiliary")
                if isinstance(declared_auxiliary, dict):
                    entry["_auxiliary"] = dict(declared_auxiliary)
            selector = entry.pop("accountSelector", {})
            if not selector and entry.get("accountNumber"):
                selector = {"number": entry.get("accountNumber")}
            if isinstance(selector, dict):
                selected = subject_by_number.get(str(selector.get("number", "")))
                if selected:
                    entry.setdefault("accountId", selected.get("account_id", ""))
                    entry.setdefault("accountNumber", selected.get("account_number", ""))
                    entry.setdefault("accountName", selected.get("account_name", ""))
            entry["explanation"] = template_rendered.get("explanation", template_rendered.get("summary", defaults.get("summary", "")))
            entry["accountId"] = entry.get("accountId", entry.get("account_id", ""))
            entry["accountNumber"] = entry.get("accountNumber", entry.get("account_number", ""))
            entry["accountName"] = entry.get("accountName", entry.get("account_name", ""))
            for local_key in ("lineNo", "line_no", "account_id", "account_number", "account_name"):
                entry.pop(local_key, None)
            auxiliary = entry.pop("_auxiliary", None) or entry.pop("auxiliary", None)
            uses_auxiliary = isinstance(auxiliary, dict) and any(
                auxiliary.get(key) not in (None, "", 0, "0") for key in ("id", "field", "selector", "itemClass", "itemClassId")
            )
            item_class_hint = str((auxiliary or {}).get("itemClass") or defaults.get("itemClass") or defaults.get("item_class") or "")
            fallback_key = {"客户": "customer_item", "供应商": "supplier_item"}.get(item_class_hint)
            auxiliary_item = business_values.get("auxiliaryItem")
            if uses_auxiliary and auxiliary.get("id") not in (None, ""):
                auxiliary_item = auxiliary
            if not isinstance(auxiliary_item, dict) and fallback_key:
                auxiliary_item = defaults.get(fallback_key)
            if uses_auxiliary and isinstance(auxiliary_item, dict) and auxiliary_item.get("id") not in (None, ""):
                item_class = str(auxiliary_item.get("itemClass") or item_class_hint)
                prefix_by_class = {"客户": "customer", "供应商": "supplier", "职员": "emp", "项目": "project", "存货": "inventory", "部门": "dept"}
                prefix = prefix_by_class.get(item_class)
                if not prefix:
                    raise ValueError(f"不支持的辅助核算类型：{item_class}")
                entry[f"{prefix}Id"] = str(auxiliary_item["id"])
                entry["accountName"] = str(auxiliary_item.get("name", ""))
            entry["dc"] = entry.get("dc", 1)
            entry.setdefault("amount", "")
            entry.setdefault("cur", "RMB")
            entry.setdefault("rate", "1")
            entry.setdefault("amountFor", "")
            entries.append(entry)
        summary = template_rendered.get("explanation", template_rendered.get("summary", defaults.get("summary", "")))
        payload = {
            "schemaVersion": "1.0",
            "draft": draft,
            "receiptId": f"{safe_company}-{safe_month}-{invoice_code}",
            "voucher": {
                "date": str(business_values.get("date", "")),
                "groupId": str(defaults.get("group_id", "")),
                "groupName": str(defaults.get("group_name", "")),
                "summary": str(summary),
                "attachments": 1 if map_values.get(invoice_code, "") else 0,
                "invoiceCodes": [invoice_code],
                "userName": str(defaults.get("user_name", "")),
                "entries": entries
            }
        }
        receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        generated.append({"invoiceCode": invoice_code, "receipt": str(receipt_path.resolve()), "pdfCandidates": [str(path) for path in pdfs[invoice_code]]})
    report = {
        "company": config.company,
        "month": config.month,
        "monthDirectory": str(month_directory),
        "receiptDirectory": str(output_directory),
        "sourceFolders": list(folder_patterns or ["sales", "purchase", "bank", "misc"]),
        "filter": "sales_map 命中" if only_mapped_invoices else "全部 PDF 发票号",
        "draftStatus": "待补业务字段，不可直接提交",
        "requiredBeforeBatch": ["voucher.date", "voucher.groupId", "voucher.summary", "voucher.userName", "voucher.entries"],
        "summary": {
            "pdfInvoiceCodeCount": len(pdfs),
            "existingOrGeneratedCount": len(generated) + len(skipped),
            "generatedCount": len(generated),
            "skippedCount": len(skipped),
            "duplicateInvoiceCodeCount": sum(1 for paths in pdfs.values() if len(paths) > 1),
            "invalidPdfCount": len(invalid),
            "filteredOutCount": len(filtered_out),
        },
        "generated": generated,
        "skipped": skipped,
        "filteredOut": filtered_out,
        "duplicates": [{"invoiceCode": key, "pdfs": [str(path) for path in paths]} for key, paths in pdfs.items() if len(paths) > 1],
        "invalidPdfs": invalid,
    }
    (output_directory / "receipt_generation.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
