from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.receipts_ocr import (
    OcrArtifact,
    OpenAICompatibleTemplateSelector,
    analyze_ocr_and_choose_template,
    compact_analysis_for_storage,
    enforce_template_explanation,
    extract_bank_transaction_date,
    _save_analysis_memory,
    run_ocr_stage,
)


class FakeSelector(OpenAICompatibleTemplateSelector):
    def __init__(self):
        super().__init__("fake", "http://fake")

    def choose(self, ocr_text, templates, invoice_code=""):
        assert "采购" in ocr_text
        assert len(templates) == 1
        return {"templateId": "purchase-raw-material", "templatePath": "purchase/采购原材料_template.json", "confidence": 0.96, "reason": "识别为采购原材料发票", "extractedFields": {"businessType": "采购原材料"}}


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        month = root / "data" / "inbox" / "company_17867515_上海微誉信息技术有限公司" / "2026-08"
        input_root = month / "input"
        source = input_root / "purchase"
        source.mkdir(parents=True)
        pdf = source / "dzfp_123456_采购发票.pdf"
        pdf.write_bytes(b"%PDF")
        output = root / "workspaces" / "account_1" / "company_17867515" / "2026-08" / "generated" / "ocr" / "purchase"
        report = run_ocr_stage(input_root, output, ["purchase"], lambda path: ("采购\n价税合计 113.00", "fake-ocr"), company="测试公司")
        assert report["summary"]["pdfCount"] == 1
        artifact = next(output.glob("123456/ocr.json"))
        metadata = json.loads(artifact.read_text(encoding="utf-8"))
        assert metadata["engine"] == "fake-ocr"
        assert metadata["sourcePdf"] == str(pdf.resolve())
        assert sorted(path.name for path in artifact.parent.iterdir()) == ["ocr.json", "ocr.txt"]
        templates = root / "templates"
        templates.mkdir()
        (templates / "index.json").write_text(json.dumps({"version": "5.0", "templatePattern": "*_template.json"}, ensure_ascii=False), encoding="utf-8")
        target = templates / "purchase" / "采购原材料_template.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "id": "purchase-raw-material", "name": "采购原材料", "enabled": True,
            "documentBlock": "采购", "documentType": "增值税发票", "settlementMethod": "往来结算",
            "businessType": "采购原材料", "currency": "人民币", "when": {},
            "matchRules": {"sourceFolders": ["purchase"], "anyKeywords": ["采购"]}, "entries": [],
        }, ensure_ascii=False), encoding="utf-8")
        prompts = templates / "prompts"
        prompts.mkdir()
        (prompts / "purchase.md").write_text("只允许选择 purchase 模板。", encoding="utf-8")
        from kdzwy_receipt_uploader.receipts_ocr import run_pdf_ocr
        selected = analyze_ocr_and_choose_template(run_pdf_ocr(pdf, output, lambda p: ("采购", "fake"), source_month_directory=input_root, company="demo"), templates, FakeSelector())
        assert selected["status"] == "success"
        assert selected["allowedTemplateBlocks"] == ["采购", "费用"]
        assert selected["templatePath"].startswith("purchase/")
    print("receipts_ocr 与 Qwen 模板选择测试通过")


def test_receipts_ocr() -> None:
    main()


def test_bank_template_uses_configured_bank_account_number() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        template_path = root / "bank" / "payment_template.json"
        template_path.parent.mkdir(parents=True)
        template_path.write_text(
            json.dumps(
                {
                    "id": "bank-payment",
                    "name": "银行付款",
                    "summary": {"header": "", "body": "付款", "separator": " "},
                    "entries": [
                        {
                            "dc": 1,
                            "accountSelector": {"number": "2202", "name": "应付账款"},
                            "amountFrom": "source.amount",
                        },
                        {
                            "dc": -1,
                            "accountSelector": {
                                "number": "100201",
                                "name": "银行存款_上海银行",
                            },
                            "amountFrom": "source.amount",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifact = OcrArtifact(
            invoice_code="bank__V001",
            source_pdf=root / "V001.pdf",
            source_folder="bank",
            source_side="bank",
            output_dir=root,
            text_path=root / "ocr.txt",
            metadata_path=root / "ocr.json",
            text="记账日期\n2026-07-21\n付款",
            engine="test",
            status="success",
        )
        decision = {"templatePath": "bank/payment_template.json"}
        enforce_template_explanation(
            decision,
            artifact,
            root,
            {
                "businessMapValues": {
                    "amount": "12.30",
                    "bankAccountNumber": "100204",
                    "flowDirection": "inflow",
                    "invoiceNumbers": [
                        "26312000004664982496",
                        "26312000004646763391",
                    ],
                },
                "dynamicAccountCatalog": {
                    "accounts": [
                        {"id": "a", "number": "2202", "fullName": "应付账款"},
                        {
                            "id": "b",
                            "number": "100204",
                            "fullName": "银行存款_招商银行",
                        },
                    ]
                },
                "dynamicItemClassCatalog": {"classes": []},
            },
        )
        assert decision["bankAccountNumber"] == "100204"
        assert decision["bankTransactionDate"] == "2026-07-21"
        assert decision["explanation_body"] == (
            "26312000004664982496 26312000004646763391"
        )
        assert decision["filledEntries"][0]["explanation"] == (
            "26312000004664982496 26312000004646763391"
        )
        assert decision["filledEntries"][1]["accountNumber"] == "100204"
        assert decision["filledEntries"][1]["accountName"] == "银行存款_招商银行"
        assert decision["filledEntries"][1]["explanation"] == (
            "26312000004664982496 26312000004646763391 2026-07-21"
        )
        decision["sourceFolder"] = "bank"
        compact = compact_analysis_for_storage(decision)
        assert compact["filledEntries"][1]["explanation"].endswith(" 2026-07-21")


def test_extract_bank_transaction_date_prefers_labelled_transaction_date() -> None:
    assert extract_bank_transaction_date("记账日期\n2026-07-21") == "2026-07-21"
    assert (
        extract_bank_transaction_date("记账日期：20260724\n打印时间：2026-08-02")
        == "2026-07-24"
    )
    assert extract_bank_transaction_date("没有日期") == ""


def test_bank_unique_rule_skips_llm_and_uses_bank_validation() -> None:
    class FailIfCalledSelector:
        def choose(self, *_args, **_kwargs):
            raise AssertionError("唯一银行规则不应调用 LLM")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        metadata = root / "ocr.json"
        metadata.write_text("{}", encoding="utf-8")
        text_path = root / "ocr.txt"
        text_path.write_text(
            "上海银行业务回单 入账 用途：货款 记账日期：2026-07-21",
            encoding="utf-8",
        )
        artifact = OcrArtifact(
            invoice_code="bank__V001",
            source_pdf=root / "V001.pdf",
            source_folder="bank",
            source_side="bank",
            output_dir=root,
            text_path=text_path,
            metadata_path=metadata,
            text=text_path.read_text(encoding="utf-8"),
            engine="test",
            status="success",
        )
        decision = analyze_ocr_and_choose_template(
            artifact,
            PROJECT / "templates" / "weiyu",
            selector=FailIfCalledSelector(),
            final_template_context={
                "businessMapValues": {
                    "amount": "100.00",
                    "bankAccountNumber": "100204",
                    "flowDirection": "inflow",
                    "invoiceNumbers": ["26312000004664982496"],
                    "counterpartyName": "测试客户",
                    "auxiliaryItem": {
                        "id": "customer-1",
                        "number": "C001",
                        "name": "测试客户",
                    },
                },
                "dynamicAccountCatalog": {
                    "accounts": [
                        {"id": "bank", "number": "100204", "fullName": "银行存款_招商银行"},
                        {"id": "ar", "number": "1122", "fullName": "应收账款"},
                    ]
                },
                "dynamicItemClassCatalog": {
                    "classes": [
                        {
                            "itemClassId": 1,
                            "items": [
                                {"id": "customer-1", "number": "C001", "name": "测试客户"}
                            ],
                        }
                    ]
                },
            },
        )
        assert decision["templateId"] == "bank-01"
        assert decision["selectionMode"] == "deterministic_rule"
        assert decision["analysisStatus"] == "ready_for_review"
        assert decision["validation"] == {
            "folderRule": True,
            "sourceFolderRule": True,
            "confidenceRule": True,
            "mapSourceRule": True,
            "flowDirectionRule": True,
            "finalTemplateRule": True,
        }


def test_jd_dynamic_payables_exception_is_pending_until_allocations_are_complete() -> None:
    class FailIfCalledSelector:
        def choose(self, *_args, **_kwargs):
            raise AssertionError("京东动态应付 exception 必须确定性选择，不应调用 LLM")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        metadata = root / "ocr.json"
        metadata.write_text("{}", encoding="utf-8")
        text_path = root / "ocr.txt"
        text_path.write_text(
            "上海银行业务回单 对方户名：重庆京东盛际小额贷款有限公司 "
            "用途：采购货款 记账日期：2026-07-03",
            encoding="utf-8",
        )
        artifact = OcrArtifact(
            invoice_code="shanghaiyinhang__V026070301320654",
            source_pdf=root / "V026070301320654.pdf",
            source_folder="bank",
            source_side="bank",
            output_dir=root,
            text_path=text_path,
            metadata_path=metadata,
            text=text_path.read_text(encoding="utf-8"),
            engine="test",
            status="success",
        )

        def context(allocations):
            return {
                "businessMapValues": {
                    "amount": "100.00",
                    "bankAccountNumber": "100201",
                    "flowDirection": "outflow",
                    "counterpartyName": "重庆京东盛际小额贷款有限公司",
                    "exceptionConfig": {
                        "handling": "dynamic_supplier_payables",
                        "template_id": "bank-jd-dynamic-ap-cny",
                        "party_type": "suppliers",
                        "counterparty_name": "重庆京东盛际小额贷款有限公司",
                        "record_key": artifact.invoice_code,
                        "allocations": allocations,
                    },
                },
                "dynamicAccountCatalog": {
                    "accounts": [
                        {"id": "ap", "number": "2202", "fullName": "应付账款"},
                        {"id": "bank", "number": "100201", "fullName": "银行存款_上海银行"},
                    ]
                },
                "dynamicItemClassCatalog": {
                    "classes": [
                        {
                            "itemClassId": 5,
                            "items": [
                                {"id": "supplier-1", "number": "S001", "name": "实际供应商甲"}
                            ],
                        }
                    ]
                },
            }

        pending = analyze_ocr_and_choose_template(
            artifact,
            PROJECT / "templates" / "weiyu",
            selector=FailIfCalledSelector(),
            final_template_context=context([]),
        )
        assert pending["templateId"] == "bank-jd-dynamic-ap-cny"
        assert pending["analysisStatus"] == "exception_pending"
        assert pending["exceptionStatus"] == "pending"
        assert pending["filledEntries"] == []

        ready = analyze_ocr_and_choose_template(
            artifact,
            PROJECT / "templates" / "weiyu",
            selector=FailIfCalledSelector(),
            final_template_context=context(
                [{"supplier_name": "实际供应商甲", "amount": "100.00"}]
            ),
        )
        assert ready["analysisStatus"] == "ready_for_review"
        assert ready["exceptionStatus"] == "resolved"
        assert [entry["accountNumber"] for entry in ready["filledEntries"]] == [
            "2202",
            "100201",
        ]
        assert ready["filledEntries"][0]["auxiliary"]["name"] == "实际供应商甲"
        assert all(entry["accountNumber"] != "1123" for entry in ready["filledEntries"])


def test_analysis_memory_parallel_writes_are_merged_atomically(tmp_path: Path) -> None:
    path = tmp_path / "analysis_memory.json"

    def save(index: int) -> None:
        _save_analysis_memory(
            path,
            {"version": 1, "processed": [], "verifiedDecisions": []},
            f"bank__{index:03d}",
            {
                "templatePath": "bank/template.json",
                "analysisStatus": "ready_for_review",
                "confidence": 0.99,
                "extractedFields": {},
            },
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(40)))

    memory = json.loads(path.read_text(encoding="utf-8"))
    assert len(memory["processed"]) == 40
    assert len(memory["verifiedDecisions"]) == 40


def test_analysis_memory_write_failure_does_not_block_analysis(tmp_path: Path) -> None:
    memory = {"version": 1, "processed": [], "verifiedDecisions": []}
    with patch.object(Path, "replace", side_effect=PermissionError("locked")), patch(
        "kdzwy_receipt_uploader.receipts_ocr.time.sleep"
    ):
        _save_analysis_memory(
            tmp_path / "analysis_memory.json",
            memory,
            "bank__001",
            {"analysisStatus": "blocked", "templatePath": "", "confidence": 0},
        )
    assert memory["processed"][0]["invoiceCode"] == "bank__001"


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class QwenSelectorTests(unittest.TestCase):
    def test_default_settings_use_dashscope_qwen_and_json_mode(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHttpResponse({
                "choices": [{"message": {"content": '{"templatePath":"sales/a.json"}'}}]
            })

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}, clear=False):
            prompt_path = Path(directory) / "prompt.txt"
            prompt_path.write_text("请返回严格 JSON：<<OCR_TEXT>> <<TEMPLATE_CATALOG>>", encoding="utf-8")
            selector = OpenAICompatibleTemplateSelector.from_settings({})
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = selector.choose(
                    "发票 OCR",
                    [{"id": "a", "path": "sales/a.json"}],
                    invoice_code="123",
                    prompt_path=prompt_path,
                )

        request = captured["request"]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(selector.model, "qwen3.7-flash")
        self.assertEqual(request.full_url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(captured["timeout"], 60)
        self.assertFalse(body["enable_thinking"])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["model"], "qwen3.7-flash")
        self.assertEqual(result["status"], "success")

    def test_missing_dashscope_key_blocks_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            selector = OpenAICompatibleTemplateSelector.from_settings({})
            result = selector.choose("OCR", [])
        self.assertEqual(result["status"], "待提供Qwen API")
        self.assertEqual(result["reason"], "未配置 DASHSCOPE_API_KEY")

    def test_missing_company_prompt_blocks_without_using_global_fallback(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}, clear=False):
            selector = OpenAICompatibleTemplateSelector.from_settings({})
            result = selector.choose("OCR", [], prompt_path=None)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "缺少当前公司的模板分类提示词")

    def test_http_400_includes_provider_error_body_without_blind_retry(self) -> None:
        with TemporaryDirectory() as directory:
            prompt_path = Path(directory) / "prompt.txt"
            prompt_path.write_text("<<OCR_TEXT>>", encoding="utf-8")
            selector = OpenAICompatibleTemplateSelector("secret", "https://example.test")
            error = urllib.error.HTTPError(
                "https://example.test",
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"code":"invalid_parameter","message":"context too long"}'),
            )
            with patch("urllib.request.urlopen", side_effect=error) as mocked:
                result = selector.choose(
                    "OCR",
                    [{"id": "a", "path": "bank/a.json"}],
                    prompt_path=prompt_path,
                )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 400", result["reason"])
        self.assertIn("context too long", result["reason"])


if __name__ == "__main__":
    main()
