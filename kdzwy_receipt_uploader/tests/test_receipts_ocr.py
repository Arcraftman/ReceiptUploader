from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.receipts_ocr import DeepSeekTemplateSelector, analyze_ocr_and_choose_template, run_ocr_stage


class FakeSelector(DeepSeekTemplateSelector):
    def __init__(self):
        super().__init__("fake", "http://fake")

    def choose(self, ocr_text, templates, invoice_code=""):
        assert "采购" in ocr_text
        assert len(templates) == 1
        return {"templateId": "purchase-raw-material", "templatePath": "采购发票/含税单价/往来结算/采购原材料/template.json", "confidence": 0.96, "reason": "识别为采购原材料发票", "extractedFields": {"businessType": "采购原材料"}}


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        month = root / "month"
        source = month / "purchase1"
        source.mkdir(parents=True)
        pdf = source / "dzfp_123456_采购发票.pdf"
        pdf.write_bytes(b"%PDF")
        output = month / "receipts_ocr"
        report = run_ocr_stage(month, output, ["purchase*"], lambda path: ("采购\n价税合计 113.00", "fake-ocr"), company="测试公司")
        assert report["summary"]["pdfCount"] == 1
        artifact = next(output.glob("123456/ocr.json"))
        metadata = json.loads(artifact.read_text(encoding="utf-8"))
        assert metadata["engine"] == "fake-ocr"
        assert metadata["sourcePdf"] == str(pdf.resolve())
        assert sorted(path.name for path in artifact.parent.iterdir()) == ["ocr.json", "ocr.txt"]
        templates = root / "templates"
        templates.mkdir()
        (templates / "index.json").write_text(json.dumps({"templates": [{"id": "purchase-raw-material", "path": "采购发票/含税单价/往来结算/采购原材料/template.json", "documentBlock": "采购"}, {"id": "a", "path": "a.json"}, {"id": "b", "path": "b.json"}, {"id": "c", "path": "c.json"}]} , ensure_ascii=False), encoding="utf-8")
        for path in ["采购发票/含税单价/往来结算/采购原材料/template.json", "a.json", "b.json", "c.json"]:
            target = templates / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
        from kdzwy_receipt_uploader.receipts_ocr import run_pdf_ocr
        selected = analyze_ocr_and_choose_template(run_pdf_ocr(pdf, output, lambda p: ("采购", "fake"), source_month_directory=month, company="demo"), templates, FakeSelector())
        assert selected["status"] == "success"
        assert selected["allowedTemplateBlocks"] == ["采购", "费用"]
        assert selected["templatePath"].startswith("采购发票/")
    print("receipts_ocr 与 DeepSeek 模板选择测试通过")


if __name__ == "__main__":
    main()
