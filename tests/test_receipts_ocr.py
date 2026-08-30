from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.receipts_ocr import OpenAICompatibleTemplateSelector, analyze_ocr_and_choose_template, run_ocr_stage


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


if __name__ == "__main__":
    main()
