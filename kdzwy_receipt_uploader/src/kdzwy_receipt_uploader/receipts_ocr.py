"""OCR preparation and DeepSeek-assisted template selection.

This module is deliberately independent from voucher upload. It reads source
PDFs in place and creates an inspectable ``ocr/<source>/<invoice>`` artifact
containing only OCR text and metadata, then optionally asks DeepSeek to choose
a concrete four-level template.
"""
from __future__ import annotations

import copy
import json
import inspect
import os
import re
import logging
import time
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .matching import pdf_invoice_number
from .receipt_generation import discover_source_pdfs
from .source_profile import source_from_folder_name
from .voucher_templates import TemplateContext, VoucherTemplateEngine


class OcrPipelineError(ValueError):
    pass


_OCR_ENGINE: Any | None = None
_OCR_CACHE_VERSION = 3


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _source_fingerprint(pdf_path: Path) -> dict[str, Any]:
    stat = pdf_path.stat()
    try:
        engine_version = importlib_metadata.version("rapidocr-onnxruntime")
    except importlib_metadata.PackageNotFoundError:
        engine_version = "unavailable"
    return {
        "size": stat.st_size,
        "modifiedNs": stat.st_mtime_ns,
        "ocrEngine": "rapidocr-onnxruntime",
        "ocrEngineVersion": engine_version,
        "nativePdfTextFirst": False,
        "renderDpi": 300,
        "minimumScore": 0.35,
    }


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


def discover_pdf_files(month_directory: Path, folder_patterns: Iterable[str] = ("sales*", "purchase*", "bank*", "misc*"), allowed_invoice_codes: set[str] | None = None) -> list[Path]:
    indexed, _ = discover_source_pdfs(month_directory, list(folder_patterns))
    pdfs = sorted({pdf.resolve() for paths in indexed.values() for pdf in paths})
    if allowed_invoice_codes is None:
        return pdfs
    return [pdf for pdf in pdfs if pdf_invoice_number(pdf) in allowed_invoice_codes]


def _default_ocr(pdf_path: Path) -> tuple[str, str]:
    """Render every PDF page and run image OCR in accuracy-first mode."""
    try:
        import pymupdf  # type: ignore
        with pymupdf.open(str(pdf_path)) as document:
            engine = _get_ocr_engine()
            lines: list[tuple[float, float, str]] = []
            for page in document:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False)
                result, _ = engine(pixmap.tobytes("png"))
                for row in result or []:
                    box, text, score = row
                    if float(score) >= 0.35 and text:
                        x = min(float(point[0]) for point in box)
                        y = min(float(point[1]) for point in box)
                        lines.append((y, x, str(text).strip()))
            text = "\n".join(item[2] for item in sorted(lines)).strip()
            if text:
                return text, "rapidocr-onnxruntime"
    except Exception as exc:
        return "", f"ocr_error:{type(exc).__name__}"
    return "", "ocr_unavailable"


def extract_invoice_fields(text: str) -> dict[str, Any]:
    """Extract auditable invoice fields from OCR text without guessing.

    The parser keeps the raw matched value and a confidence per field. The
    buyer/seller labels are handled independently because Chinese invoices
    place both "名称" lines under different section headers.
    """
    normalized = text.replace("\uFF0F", "/").replace("\uFF1A", ":")
    lines = [re.sub(r"\s+", "", line).strip() for line in normalized.splitlines() if line.strip()]
    fields: dict[str, Any] = {}

    def first_match(patterns: list[str]) -> str:
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        return ""

    invoice_number = first_match([r"发票号码[:：]?([0-9]{8,24})", r"发票号[:：]?([0-9]{8,24})"])
    issue_date = first_match([r"开票日期[:：]?([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", r"开票日期[:：]?([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})"])
    total_amount = first_match([r"[（(]小写[）)][:：]?￥?([0-9,]+(?:\.[0-9]{1,2})?)", r"价税合计[:：]?￥?([0-9,]+(?:\.[0-9]+)?)"])
    tax_rate = first_match([r"税率/征收率[:：]?([0-9]+(?:\.[0-9]+)?%)", r"税率[:：]?([0-9]+(?:\.[0-9]+)?%)", r"([0-9]+(?:\.[0-9]+)?%)"])
    buyer = ""
    seller = ""
    for index, line in enumerate(lines):
        if line in {"购买方信息", "购买方"}:
            window = lines[index + 1:index + 8]
            buyer = next((item.split("名称:", 1)[1] for item in window if item.startswith("名称:") and len(item.split("名称:", 1)) == 2), "")
        if line in {"销售方信息", "销售方"}:
            window = lines[index + 1:index + 8]
            seller = next((item.split("名称:", 1)[1] for item in window if item.startswith("名称:") and len(item.split("名称:", 1)) == 2), "")
    # Some PDF layouts place both section labels first and the two names after
    # them. In that layout the first name is seller and the second is buyer.
    if not buyer and not seller:
        name_lines = [item.split("名称:", 1)[1] for item in lines if item.startswith("名称:") and len(item.split("名称:", 1)) == 2]
        if len(name_lines) >= 2:
            seller, buyer = name_lines[0], name_lines[1]
    elif buyer == seller:
        name_lines = [item.split("名称:", 1)[1] for item in lines if item.startswith("名称:") and len(item.split("名称:", 1)) == 2]
        if len(name_lines) >= 2:
            seller, buyer = name_lines[0], name_lines[1]
    if not buyer:
        buyer = first_match([r"购买方名称[:：]?(.+)"])
    if not seller:
        seller = first_match([r"销售方名称[:：]?(.+)"])
    fields["_normalizedText"] = "\n".join(lines)
    fields["invoiceNumber"] = invoice_number
    fields["issueDate"] = issue_date
    fields["buyer"] = buyer
    fields["seller"] = seller
    fields["totalAmountWithTax"] = total_amount.replace(",", "") if total_amount else ""
    fields["taxRate"] = tax_rate
    fields["fieldConfidence"] = {
        "invoiceNumber": 1.0 if invoice_number else 0.0,
        "issueDate": 0.95 if issue_date else 0.0,
        "buyer": 0.95 if buyer else 0.0,
        "seller": 0.95 if seller else 0.0,
        "totalAmountWithTax": 0.9 if total_amount else 0.0,
        "taxRate": 0.85 if tax_rate else 0.0,
    }
    fields["criticalFieldsReady"] = all(fields["fieldConfidence"][key] >= 0.85 for key in ("invoiceNumber", "buyer", "seller", "totalAmountWithTax"))
    return fields


def apply_folder_party_rule(fields: Mapping[str, Any], source_folder: str, config_company: str) -> dict[str, Any]:
    """Attach the authoritative sales/purchase party direction to OCR fields.

    The OCR labels are retained as raw observations. Accounting direction must
    never be inferred from a possibly misordered OCR layout: sales is always the
    configured company as seller (sales-side map), while purchase is always the
    configured company as buyer (purchase-side map).
    """
    folder = str(source_folder or "")
    folder_key = source_from_folder_name(folder) or ""
    corrected = dict(fields)
    if folder_key == "sales":
        rule = {
            "configuredCompanyRole": "seller",
            "counterpartyRole": "buyer",
            "mapSource": "sales_map",
            "allowedTemplateBlocks": ["销售"],
        }
    elif folder_key == "purchase":
        rule = {
            "configuredCompanyRole": "buyer",
            "counterpartyRole": "seller",
            "mapSource": "purchase_map",
            "allowedTemplateBlocks": ["采购", "费用"],
        }
    else:
        rule = {
            "configuredCompanyRole": "unknown",
            "counterpartyRole": "unknown",
            "mapSource": "",
            "allowedTemplateBlocks": [],
        }
    corrected["ocrRawBuyer"] = str(fields.get("buyer", ""))
    corrected["ocrRawSeller"] = str(fields.get("seller", ""))
    names = []
    normalized_text = str(fields.pop("_normalizedText", "") or "")
    for line in normalized_text.splitlines():
        match = re.search(r"名称[:：](.+)", line)
        if match and match.group(1).strip() and match.group(1).strip() not in names:
            names.append(match.group(1).strip())
    # The OCR parser may swap or duplicate the two 名称 lines depending on PDF
    # layout. The folder rule is authoritative for the configured company;
    # choose the first distinct OCR name as its counterparty.
    others = [name for name in names if name != str(config_company or "")]
    if folder_key == "purchase":
        corrected["buyer"] = str(config_company or "")
        corrected["seller"] = others[0] if others else str(fields.get("seller", ""))
    elif folder_key == "sales":
        corrected["seller"] = str(config_company or "")
        corrected["buyer"] = others[0] if others else str(fields.get("buyer", ""))
    corrected["configuredCompany"] = str(config_company or "")
    corrected["configuredCompanyRole"] = rule["configuredCompanyRole"]
    corrected["counterpartyRole"] = rule["counterpartyRole"]
    corrected["mapSource"] = rule["mapSource"]
    corrected["allowedTemplateBlocks"] = rule["allowedTemplateBlocks"]
    corrected["folderRuleAuthoritative"] = True
    return corrected


def run_pdf_ocr(pdf_path: Path, output_dir: Path, ocr_runner: Callable[[Path], tuple[str, str]] | None = None, company: str = "", source_month_directory: Path | None = None) -> OcrArtifact:
    invoice_code = pdf_invoice_number(pdf_path)
    if not invoice_code:
        raise OcrPipelineError(f"PDF 文件名无法提取发票号：{pdf_path}")
    source_root = (source_month_directory or pdf_path.parent).resolve()
    try:
        relative = pdf_path.resolve().relative_to(source_root)
        source_folder = next((part for part in relative.parts if source_from_folder_name(part)), pdf_path.parent.name)
    except ValueError:
        source_folder = pdf_path.parent.name
    source_key = source_from_folder_name(source_folder)
    source_side = "sales" if source_key == "sales" else "purchase" if source_key == "purchase" else "bank" if source_key == "bank" else "misc" if source_key == "misc" else "unknown"
    # output_dir is already source-specific (for example generated/ocr/sales).
    # Do not append source_folder again or copy the source PDF into OCR output.
    target = output_dir / invoice_code
    target.mkdir(parents=True, exist_ok=True)
    text_path = target / "ocr.txt"
    metadata_path = target / "ocr.json"
    source_fingerprint = _source_fingerprint(pdf_path)
    if ocr_runner is None and text_path.is_file() and metadata_path.is_file():
        try:
            cached_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            same_source = Path(str(cached_metadata.get("sourcePdf", ""))).resolve() == pdf_path.resolve()
            same_company = str(cached_metadata.get("configCompany", "")) == str(company or "")
            same_fingerprint = cached_metadata.get("sourceFingerprint") == source_fingerprint
            same_cache_version = cached_metadata.get("ocrCacheVersion") == _OCR_CACHE_VERSION
            if same_source and same_company and same_fingerprint and same_cache_version and cached_metadata.get("status") == "success":
                cached_text = text_path.read_text(encoding="utf-8")
                return OcrArtifact(
                    invoice_code,
                    pdf_path,
                    source_folder,
                    source_side,
                    target,
                    text_path,
                    metadata_path,
                    cached_text,
                    str(cached_metadata.get("engine", "cached")),
                    "success",
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    text, engine = (ocr_runner or _default_ocr)(pdf_path)
    text_path.write_text(text, encoding="utf-8")
    metadata = {
        "invoiceCode": invoice_code,
        "sourceFolder": source_folder,
        "sourceSide": source_side,
        "configCompany": company,
        "partyRule": {
            "salesFolderCompanyRole": "seller",
            "purchaseFolderCompanyRole": "buyer",
            "counterpartySource": "OCR",
            "sameCompanyCounterpartyAllowed": True,
            "authoritative": "sourceFolder",
        },
        "fields": apply_folder_party_rule(extract_invoice_fields(text), source_folder, company),
        "sourcePdf": str(pdf_path.resolve()),
        "sourceFingerprint": source_fingerprint,
        "ocrCacheVersion": _OCR_CACHE_VERSION,
        "ocrText": str(text_path.resolve()),
        "engine": engine,
        "status": "success" if text else engine,
        "textLength": len(text),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return OcrArtifact(invoice_code, pdf_path, source_folder, source_side, target, text_path, metadata_path, text, engine, metadata["status"])


def run_ocr_stage(
    month_directory: Path,
    output_directory: Path,
    folder_patterns: Iterable[str] = ("sales*", "purchase*", "bank*", "misc*"),
    ocr_runner: Callable[[Path], tuple[str, str]] | None = None,
    company: str = "",
    allowed_invoice_codes: set[str] | None = None,
    return_artifacts: bool = False,
    workers: int | None = None,
) -> dict[str, Any] | tuple[dict[str, Any], list[OcrArtifact]]:
    logger = logging.getLogger("run_pipeline")
    logger.setLevel(logging.INFO)
    artifacts: list[OcrArtifact] = []
    errors: list[dict[str, str]] = []
    all_pdfs = discover_pdf_files(month_directory, folder_patterns, allowed_invoice_codes=allowed_invoice_codes)
    max_files = os.environ.get("OCR_MAX_FILES")
    try:
        max_count = int(max_files) if max_files else None
    except ValueError:
        max_count = None
    if max_count is not None and max_count > 0:
        all_pdfs = all_pdfs[:max_count]
        logger.warning("OCR_MAX_FILES 生效：仅处理前 %s 张 PDF", max_count)
    total = len(all_pdfs)
    configured_workers = workers if workers is not None else os.environ.get("OCR_WORKERS", "2")
    try:
        worker_count = max(1, int(configured_workers))
    except (TypeError, ValueError):
        worker_count = 2
    worker_count = min(worker_count, max(1, total))
    # Custom runners may be closures and are not guaranteed to be process-safe.
    if ocr_runner is not None:
        worker_count = 1
    progress_every = int(os.environ.get("OCR_PROGRESS_EVERY", "10"))
    if progress_every <= 0:
        progress_every = 10
    start = time.time()
    if worker_count > 1:
        logger.info("OCR 有限并行已启用：%s 个工作进程", worker_count)
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            pending = [
                (pdf, executor.submit(run_pdf_ocr, pdf, output_directory, None, company, month_directory))
                for pdf in all_pdfs
            ]
            for index, (pdf, future) in enumerate(pending, start=1):
                if index == 1 or index % progress_every == 0 or index == total:
                    logger.info("OCR 处理中 %s/%s：%s", index, total, pdf.name)
                try:
                    artifacts.append(future.result())
                except Exception as exc:
                    errors.append({"pdf": str(pdf), "error": str(exc)})
    else:
        for index, pdf in enumerate(all_pdfs, start=1):
            if index == 1 or index % progress_every == 0 or index == total:
                logger.info("OCR 处理中 %s/%s：%s", index, total, pdf.name)
            try:
                artifacts.append(run_pdf_ocr(pdf, output_directory, ocr_runner, company=company, source_month_directory=month_directory))
            except Exception as exc:
                errors.append({"pdf": str(pdf), "error": str(exc)})
    elapsed = time.time() - start
    text_success_count = sum(bool(item.text) for item in artifacts)
    empty_text_count = len(artifacts) - text_success_count
    logger.info(
        "OCR 阶段完成：%s 有文本、%s 无文本、%s 异常，耗时 %.1f 秒",
        text_success_count,
        empty_text_count,
        len(errors),
        elapsed,
    )
    report = {
        "sourceDirectory": str(month_directory.resolve()),
        "outputDirectory": str(output_directory.resolve()),
        "allowedInvoiceCodes": sorted(allowed_invoice_codes) if allowed_invoice_codes is not None else None,
        "filterRule": "仅处理 allowedInvoiceCodes；purchase 的 allowedInvoiceCodes 必须来自用途确认信息.xlsx 匹配后且存在PDF的发票号",
        "artifacts": [str(item.metadata_path.resolve()) for item in artifacts],
        "errors": errors,
        "summary": {
            "pdfCount": len(artifacts),
            "workerCount": worker_count,
            "allowedInvoiceCodeCount": len(allowed_invoice_codes) if allowed_invoice_codes is not None else None,
            "errorCount": len(errors),
            "successTextCount": text_success_count,
            "emptyTextCount": empty_text_count,
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "ocr_stage.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if return_artifacts:
        return report, artifacts
    return report


def load_ocr_artifacts(output_directory: Path, allowed_invoice_codes: set[str] | None = None) -> list[OcrArtifact]:
    """Load completed OCR artifacts without opening or OCR-rendering source PDFs."""
    report_path = output_directory / "ocr_stage.report.json"
    if not report_path.is_file():
        raise OcrPipelineError(f"DeepSeek阶段缺少OCR报告，请先运行 --stage ocr：{report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifacts: list[OcrArtifact] = []
    for raw_path in report.get("artifacts", []):
        metadata_path = Path(str(raw_path))
        if not metadata_path.is_file():
            raise OcrPipelineError(f"OCR元数据不存在：{metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        invoice_code = str(metadata.get("invoiceCode") or "")
        if allowed_invoice_codes is not None and invoice_code not in allowed_invoice_codes:
            continue
        text_path = Path(str(metadata.get("ocrText") or metadata_path.with_name("ocr.txt")))
        source_pdf = Path(str(metadata.get("sourcePdf") or ""))
        if not invoice_code or not text_path.is_file():
            raise OcrPipelineError(f"OCR产物不完整：{metadata_path}")
        artifacts.append(OcrArtifact(
            invoice_code=invoice_code,
            source_pdf=source_pdf,
            source_folder=str(metadata.get("sourceFolder") or metadata_path.parent.parent.name),
            source_side=str(metadata.get("sourceSide") or "unknown"),
            output_dir=metadata_path.parent,
            text_path=text_path,
            metadata_path=metadata_path,
            text=text_path.read_text(encoding="utf-8"),
            engine=str(metadata.get("engine") or "saved-ocr"),
            status=str(metadata.get("status") or "unknown"),
        ))
    if not artifacts:
        raise OcrPipelineError("没有可供DeepSeek分析的OCR产物，请先运行 --stage ocr")
    return artifacts


class DeepSeekTemplateSelector:
    def __init__(self, api_key: str | None, endpoint: str, model: str = "deepseek-chat", timeout: int = 60) -> None:
        self.api_key = api_key or ""
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls, endpoint: str = "https://api.deepseek.com/chat/completions", model: str = "deepseek-chat") -> "DeepSeekTemplateSelector":
        return cls(os.environ.get("DEEPSEEK_API_KEY"), os.environ.get("DEEPSEEK_API_URL", endpoint), model)

    def choose(self, ocr_text: str, templates: list[Mapping[str, Any]], invoice_code: str = "", final_template_context: Mapping[str, Any] | None = None, business_rules: str = "", verified_memory: list[Mapping[str, Any]] | None = None, prompt_path: Path | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "待提供DeepSeek API", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": "未配置 DEEPSEEK_API_KEY", "raw": None, "textLength": len(ocr_text)}
        catalog = [{key: value for key, value in item.items() if key in {"id", "name", "path", "documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "summary", "entries", "matchRules", "amountSource"}} for item in templates]
        sample = dict(final_template_context or {})
        default_prompt_path = Path(__file__).resolve().parents[2] / "templates" / "deepseek_invoice_classifier_prompt.txt"
        selected_prompt_path = prompt_path if prompt_path and prompt_path.is_file() else default_prompt_path
        prompt = selected_prompt_path.read_text(encoding="utf-8")
        replacements = {
            "<<BUSINESS_RULES>>": business_rules,
            "<<VERIFIED_MEMORY>>": json.dumps(list(verified_memory or [])[-25:], ensure_ascii=False),
            "<<INVOICE_CODE>>": invoice_code,
            "<<OCR_TEXT>>": ocr_text[:30000],
            "<<TEMPLATE_CATALOG>>": json.dumps(catalog, ensure_ascii=False),
            "<<FINAL_CONTEXT>>": json.dumps(sample, ensure_ascii=False),
        }
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        body = {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": "你只负责严格分类和字段提取，不生成会计分录。"}, {"role": "user", "content": prompt}]}
        request = urllib.request.Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            parsed = _parse_json_object(content)
            parsed["status"] = "success"
            parsed["invoiceCode"] = invoice_code
            parsed["raw"] = payload
            parsed["textLength"] = len(ocr_text)
            return parsed
        except (OSError, KeyError, IndexError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {"status": "error", "invoiceCode": invoice_code, "templatePath": "", "confidence": 0, "reason": str(exc), "raw": None, "textLength": len(ocr_text)}


def enforce_template_explanation(
    decision: dict[str, Any],
    artifact: OcrArtifact,
    template_root: Path,
    final_template_context: Mapping[str, Any] | None,
) -> None:
    """Replace every model-provided explanation with the selected user template."""
    if decision.get("ruleFallbackUsed"):
        raise OcrPipelineError(
            "模板候选未通过用户规则，禁止回退选中："
            + json.dumps(decision.get("ruleRejectedCandidates") or {}, ensure_ascii=False)
        )
    relative_path = str(decision.get("templatePath") or "").strip()
    if not relative_path:
        return
    root = template_root.resolve()
    template_path = (root / relative_path).resolve()
    try:
        template_path.relative_to(root)
    except ValueError as exc:
        raise OcrPipelineError(f"DeepSeek模板路径越过模板目录：{relative_path}") from exc
    if not template_path.is_file():
        raise OcrPipelineError(f"DeepSeek选择的模板不存在：{template_path}")
    template = json.loads(template_path.read_text(encoding="utf-8-sig"))
    if not isinstance(template, dict):
        raise OcrPipelineError(f"DeepSeek选择的模板不是JSON对象：{template_path}")

    context_values = dict(final_template_context or {})
    map_values = context_values.get("businessMapValues")
    if not isinstance(map_values, Mapping):
        map_values = {}
    source_key = source_from_folder_name(artifact.source_folder) or artifact.source_side
    sales_map = {artifact.invoice_code: dict(map_values)} if source_key == "sales" else {}
    purchase_map = {artifact.invoice_code: dict(map_values)} if source_key == "purchase" else {}
    rendered = VoucherTemplateEngine([template]).render(
        template,
        TemplateContext(
            invoice_code=artifact.invoice_code,
            sales_map=sales_map,
            purchase_map=purchase_map,
            accountbook={},
            source=dict(map_values),
            template_name=str(template.get("name") or ""),
        ),
    )
    explanation = str(rendered.get("explanation") or "")
    template_entries = template.get("entries") if isinstance(template.get("entries"), list) else []
    account_container = context_values.get("dynamicAccountCatalog")
    account_rows = account_container.get("accounts", []) if isinstance(account_container, Mapping) else []
    accounts_by_number: dict[str, list[Mapping[str, Any]]] = {}
    for account in account_rows:
        if isinstance(account, Mapping):
            accounts_by_number.setdefault(str(account.get("number") or ""), []).append(account)

    entries: list[dict[str, Any]] = []
    for index, template_entry in enumerate(template_entries, 1):
        if not isinstance(template_entry, Mapping):
            raise OcrPipelineError(f"模板分录不是对象：{relative_path} entries[{index}]")
        selector = template_entry.get("accountSelector")
        if not isinstance(selector, Mapping):
            raise OcrPipelineError(f"模板分录缺少accountSelector：{relative_path} entries[{index}]")
        account_number = str(selector.get("number") or "").strip()
        account_matches = accounts_by_number.get(account_number, [])
        if len(account_matches) != 1:
            raise OcrPipelineError(
                f"模板科目无法在当前账套唯一解析：number={account_number}, matches={len(account_matches)}"
            )
        account = account_matches[0]
        amount_reference = str(template_entry.get("amountFrom") or "").strip()
        amount_field = amount_reference.rsplit(".", 1)[-1]
        amount_value = map_values.get(amount_field)
        if amount_value in (None, ""):
            raise OcrPipelineError(
                f"模板金额来源不存在：invoice={artifact.invoice_code}, source={amount_reference}"
            )
        try:
            amount = round(float(amount_value), 2)
        except (TypeError, ValueError) as exc:
            raise OcrPipelineError(
                f"模板金额不是有效数字：invoice={artifact.invoice_code}, source={amount_reference}, value={amount_value}"
            ) from exc
        entries.append({
            "dc": int(template_entry.get("dc")),
            "accountNumber": account_number,
            "accountName": str(account.get("fullName") or ""),
            "accountId": str(account.get("id") or ""),
            "amount": amount,
            "amountFor": amount,
            "explanation": explanation,
            "cur": "RMB",
            "rate": "1",
        })
    decision["filledEntries"] = entries
    item_catalog = context_values.get("dynamicItemClassCatalog")

    def find_auxiliary(item_class_id: int, name: str) -> tuple[dict[str, Any] | None, int]:
        matches: dict[str, dict[str, Any]] = {}

        def visit(value: Any, inherited_class_id: int | None = None) -> None:
            if isinstance(value, Mapping):
                own_class_id = value.get("itemClassId", inherited_class_id)
                try:
                    current_class_id = int(own_class_id) if own_class_id not in (None, "") else inherited_class_id
                except (TypeError, ValueError):
                    current_class_id = inherited_class_id
                item_id = value.get("id")
                if current_class_id == item_class_id and item_id not in (None, "", 0, "0") and str(value.get("name") or "").strip() == name:
                    matches[str(item_id)] = dict(value)
                for child in value.values():
                    visit(child, current_class_id)
            elif isinstance(value, list):
                for child in value:
                    visit(child, inherited_class_id)

        visit(item_catalog)
        if len(matches) != 1:
            return None, len(matches)
        return next(iter(matches.values())), 1

    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            entry["explanation"] = explanation
            template_entry = template_entries[index] if index < len(template_entries) and isinstance(template_entries[index], Mapping) else {}
            auxiliary_rule = template_entry.get("auxiliary") if isinstance(template_entry, Mapping) else None
            if not isinstance(auxiliary_rule, Mapping):
                entry.pop("auxiliary", None)
                continue
            item_class_id = int(auxiliary_rule.get("itemClassId"))
            counterparty_name = str(
                map_values.get("customName") if source_key == "sales"
                else map_values.get("supplierName") if source_key == "purchase"
                else ""
            ).strip()
            if not counterparty_name:
                raise OcrPipelineError(f"业务映射缺少交易对方名称：source={source_key}, invoice={artifact.invoice_code}")
            mapped = map_values.get("auxiliaryItem")
            if not isinstance(mapped, Mapping) or str(mapped.get("name") or "").strip() != counterparty_name or mapped.get("id") in (None, "", 0, "0"):
                mapped, match_count = find_auxiliary(item_class_id, counterparty_name)
                if mapped is None:
                    reason = f"动态辅助核算目录无法唯一解析：itemClassId={item_class_id}, name={counterparty_name}, matches={match_count}"
                    decision["status"] = "blocked"
                    decision["analysisStatus"] = "blocked"
                    decision["blockReason"] = reason
                    errors = decision.get("finalTemplateValidationErrors")
                    if not isinstance(errors, list):
                        errors = []
                        decision["finalTemplateValidationErrors"] = errors
                    if reason not in errors:
                        errors.append(reason)
                    entry["auxiliary"] = {
                        "itemClassId": item_class_id,
                        "itemClass": str(map_values.get("itemClass") or ""),
                        "id": "",
                        "number": "",
                        "name": counterparty_name,
                        "field": str(auxiliary_rule.get("field") or ""),
                    }
                    continue
            entry["auxiliary"] = {
                "itemClassId": item_class_id,
                "itemClass": str(map_values.get("itemClass") or mapped.get("itemClass") or ""),
                "id": str(mapped.get("id")),
                "number": str(mapped.get("number") or ""),
                "name": counterparty_name,
                "field": str(auxiliary_rule.get("field") or ""),
            }
    decision["explanation_header"] = str(rendered.get("explanation_header") or "")
    decision["explanation_body"] = str(rendered.get("explanation_body") or "")
    decision["explanation"] = explanation


def compact_analysis_for_storage(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Persist only fields needed by review and later receipt generation."""
    result: dict[str, Any] = {}
    for key in (
        "templatePath", "templateId", "confidence", "reason", "status", "analysisStatus",
        "explanation_header", "explanation_body", "explanation", "sourceFolder", "configCompany",
        "partyRule", "sourcePdf", "validation",
    ):
        if key in decision:
            result[key] = copy.deepcopy(decision[key])

    extracted = decision.get("extractedFields")
    if isinstance(extracted, Mapping):
        result["extractedFields"] = {key: copy.deepcopy(value) for key, value in extracted.items() if key != "invoiceCode"}

    stored_entries: list[dict[str, Any]] = []
    for raw_entry in decision.get("filledEntries", []) if isinstance(decision.get("filledEntries"), list) else []:
        if not isinstance(raw_entry, Mapping):
            continue
        entry = copy.deepcopy(dict(raw_entry))
        for key in ("entryId", "amountFrom", "explanation"):
            entry.pop(key, None)
        if entry.get("auxiliary") is None:
            entry.pop("auxiliary", None)
        stored_entries.append(entry)
    result["filledEntries"] = stored_entries

    ocr_fields = decision.get("ocrFields")
    if isinstance(ocr_fields, Mapping):
        result["ocrFields"] = {key: copy.deepcopy(value) for key, value in ocr_fields.items() if key != "_normalizedText"}

    diagnostics = {
        key: copy.deepcopy(decision[key])
        for key in ("ruleRejectedCandidates", "ruleFallbackUsed", "finalTemplateValidationErrors", "blockReason")
        if decision.get(key)
    }
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("DeepSeek 返回不是JSON对象")
    return value


def _rule_candidates(candidate_records: list[dict[str, Any]], artifact: OcrArtifact) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    fields = metadata.get("fields", {}) if isinstance(metadata.get("fields"), Mapping) else {}
    text = artifact.text.lower()
    folder = artifact.source_folder.lower()
    eligible=[]
    reasons={}
    for item in candidate_records:
        rules=item.get("matchRules", {}) if isinstance(item.get("matchRules"), Mapping) else {}
        source_folders=[str(x).lower() for x in rules.get("sourceFolders", [])]
        if source_folders and not any(re.fullmatch(pattern.replace("*", ".*"), folder) for pattern in source_folders):
            reasons[str(item.get("path"))]="sourceFolder不匹配"
            continue
        blocks=set(str(x) for x in fields.get("allowedTemplateBlocks", []))
        if blocks and str(item.get("documentBlock", "")) not in blocks:
            reasons[str(item.get("path"))]="销售/进项目录业务板块不匹配"
            continue
        required=[str(x).lower() for x in rules.get("requiredKeywords", [])]
        if required and not all(x in text for x in required):
            reasons[str(item.get("path"))]="缺少requiredKeywords"
            continue
        excluded=[str(x).lower() for x in rules.get("excludeKeywords", [])]
        if excluded and any(x in text for x in excluded):
            reasons[str(item.get("path"))]="命中excludeKeywords"
            continue
        any_keywords=[str(x).lower() for x in rules.get("anyKeywords", [])]
        score=sum(1 for keyword in any_keywords if keyword in text)
        if any_keywords and score == 0:
            reasons[str(item.get("path"))]="未命中业务关键词"
            continue
        eligible.append((score, int(rules.get("priority", 0) or 0), item))
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _score, _priority, item in eligible], reasons


def _load_analysis_memory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "processed": [], "verifiedDecisions": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"version": 1, "processed": [], "verifiedDecisions": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "processed": [], "verifiedDecisions": []}


def _save_analysis_memory(path: Path, memory: dict[str, Any], invoice_code: str, decision: Mapping[str, Any]) -> None:
    processed = [item for item in memory.get("processed", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
    processed.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "analysisStatus": decision.get("analysisStatus", ""), "confidence": decision.get("confidence", 0)})
    memory["processed"] = processed
    verified = [item for item in memory.get("verifiedDecisions", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
    if decision.get("analysisStatus") == "ready_for_review":
        fields = decision.get("extractedFields", {}) if isinstance(decision.get("extractedFields"), Mapping) else {}
        verified.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "businessType": decision.get("businessType", ""), "sellerName": fields.get("sellerName", ""), "buyerName": fields.get("buyerName", ""), "confidence": decision.get("confidence", 0)})
    memory["verifiedDecisions"] = verified
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def analyze_ocr_and_choose_template(artifact: OcrArtifact, template_root: Path, selector: DeepSeekTemplateSelector | None = None, final_template_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from .template_catalog import TemplateCatalog

    catalog = TemplateCatalog.load(template_root)
    metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
    fields = metadata.get("fields", {}) if isinstance(metadata.get("fields"), Mapping) else {}
    allowed_blocks = list(fields.get("allowedTemplateBlocks", []))
    allowed_block_set = set(allowed_blocks)
    candidate_records = []
    for record in catalog.records:
        if not bool(record.get("enabled", True)):
            continue
        enriched = dict(record)
        try:
            template_payload = catalog.load_template(record)
            enriched.update({key: template_payload.get(key) for key in ("documentBlock", "documentType", "settlementMethod", "businessType", "currency", "keywords", "matchRules", "amountSource") if template_payload.get(key) is not None})
        except Exception:
            pass
        enriched["templateFileName"] = Path(str(record.get("path", ""))).name
        if allowed_block_set and str(enriched.get("documentBlock", "")) not in allowed_block_set:
            continue
        candidate_records.append(enriched)
    if not candidate_records:
        raise OcrPipelineError(f"{artifact.source_folder} 没有符合买卖方方向的模板候选")
    source_key = artifact.source_side if artifact.source_side in {"sales", "purchase", "bank", "misc"} else "misc"
    scoped_records = [
        item for item in candidate_records
        if Path(str(item.get("path", ""))).parts
        and Path(str(item.get("path", ""))).parts[0].lower() == source_key
    ]
    if not scoped_records:
        raise OcrPipelineError(f"模板范围为空：source={source_key}，目录={template_root / source_key}")
    candidate_records = scoped_records
    rule_candidates, rejected = _rule_candidates(candidate_records, artifact)
    if not rule_candidates:
        rule_candidates = candidate_records
        rule_fallback = True
    else:
        rule_fallback = False
    prompt_path = template_root / "prompts" / f"{source_key}.md"
    if not prompt_path.is_file():
        raise OcrPipelineError(f"缺少{source_key}固定提示词：{prompt_path}")
    from kdzwy_receipt_uploader.fixed_prompt_rules import FIXED_DEEPSEEK_RULES

    business_rules = (
        FIXED_DEEPSEEK_RULES.rstrip()
        + "\n\n# 公司与业务自定义规则\n"
        + prompt_path.read_text(encoding="utf-8")
        .replace("{{dataset_company}}", str(metadata.get("configCompany") or ""))
        .replace("{{template_directory}}", f"templates/{template_root.name}/{source_key}")
    )
    memory_path = artifact.output_dir.parent / "analysis_memory.json"
    memory = _load_analysis_memory(memory_path)
    active_selector = selector or DeepSeekTemplateSelector.from_env()
    choose_parameters = inspect.signature(active_selector.choose).parameters
    if "business_rules" in choose_parameters:
        choose_kwargs = {
            "final_template_context": final_template_context,
            "business_rules": business_rules,
            "verified_memory": list(memory.get("verifiedDecisions", [])),
        }
        if "prompt_path" in choose_parameters:
            choose_kwargs["prompt_path"] = template_root / "prompts" / "deepseek_invoice_classifier_prompt.txt"
        decision = active_selector.choose(
            artifact.text, rule_candidates, artifact.invoice_code,
            **choose_kwargs,
        )
    else:
        decision = active_selector.choose(artifact.text, rule_candidates, artifact.invoice_code)
    allowed_paths = {str(item.get("path", "")) for item in rule_candidates}
    decision.setdefault("status", "success" if decision.get("templatePath") else "pending")
    chosen = str(decision.get("templatePath", ""))
    if chosen and chosen not in allowed_paths:
        decision["status"] = "invalid"
        decision["reason"] = "DeepSeek 返回的模板路径不在 templates 根目录候选中"
        decision["templatePath"] = ""
    enforce_template_explanation(decision, artifact, template_root, final_template_context)
    decision["ocrFields"] = fields
    decision["allowedTemplateBlocks"] = allowed_blocks
    decision["sourceFolder"] = artifact.source_folder
    decision["sourceSide"] = artifact.source_side
    decision["configCompany"] = metadata.get("configCompany", "")
    decision["partyRule"] = metadata.get("partyRule", {})
    decision["ocrTextPath"] = str(artifact.text_path.resolve())
    decision["ocrMetadataPath"] = str(artifact.metadata_path.resolve())
    decision["sourcePdf"] = str(artifact.source_pdf.resolve())
    decision["templateCandidates"] = len(rule_candidates)
    decision["templateCandidatesBeforeRules"] = len(candidate_records)
    decision["ruleRejectedCandidates"] = rejected
    decision["ruleFallbackUsed"] = rule_fallback
    chosen_record = next((item for item in rule_candidates if str(item.get("path", "")) == str(decision.get("templatePath", ""))), None)
    validation = {
        "folderRule": bool(set(fields.get("allowedTemplateBlocks", [])) & {str(chosen_record.get("documentBlock", ""))}) if chosen_record else False,
        "sourceFolderRule": bool(chosen_record) and (not chosen_record.get("matchRules", {}).get("sourceFolders") or artifact.source_folder.lower().startswith(tuple(str(x).replace("*", "").lower() for x in chosen_record.get("matchRules", {}).get("sourceFolders", [])))),
        "confidenceRule": float(decision.get("confidence", 0) or 0) >= 0.9,
        "mapSourceRule": (str(chosen_record.get("amountSource", "")) == str(fields.get("mapSource", ""))) if chosen_record else False,
    }
    from .final_template_sample import validate_filled_entries
    sample_errors = validate_filled_entries(decision, final_template_context or {}) if final_template_context else []
    decision["finalTemplateValidationErrors"] = sample_errors
    validation["finalTemplateRule"] = not sample_errors if final_template_context else True
    decision["validation"] = validation
    decision["analysisStatus"] = "ready_for_review" if decision.get("status") == "success" and all(validation.values()) else "blocked"
    if decision["analysisStatus"] == "blocked":
        decision["blockReason"] = "系统强校验未全部通过，禁止进入可提交receipt"
    decision["businessPrompt"] = str(prompt_path.resolve())
    decision["memoryFile"] = str(memory_path.resolve())
    _save_analysis_memory(memory_path, memory, artifact.invoice_code, decision)
    return decision
