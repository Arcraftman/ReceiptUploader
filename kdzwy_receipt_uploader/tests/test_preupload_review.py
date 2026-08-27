from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.preupload_review import PreuploadReviewError, build_preupload_report, require_review_confirmation


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        receipt_dir = root / "receipts"
        item = receipt_dir / "receipt_123"; item.mkdir(parents=True)
        (item / "receipt.json").write_text(json.dumps({"receiptId": "r1", "templates": {"path": "采购发票/template.json"}, "ocr": {"criticalFieldsReady": False, "fields": {"buyer": "疑似错误"}}, "templateAnalysis": {"status": "success", "templatePath": "采购发票/template.json"}, "voucher": {"invoiceCodes": ["123"], "entries": [{"accountNumber": "1403"}]}} , ensure_ascii=False), encoding="utf-8")
        report_path = root / "preupload_review.report.json"
        report = build_preupload_report(receipt_dir, report_path, {"mode": "confirm"})
        assert report["reviewStatus"] == "待人工审查"
        assert report["summary"]["warningCount"] >= 1
        try:
            require_review_confirmation(report_path)
        except PreuploadReviewError:
            pass
        else:
            raise AssertionError("未确认报告不应允许正式上传")
        report["reviewStatus"] = "已确认"; report["reviewedBy"] = "tester"; report["reviewedAt"] = "2026-08-20T14:00:00+08:00"
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        assert require_review_confirmation(report_path)["reviewedBy"] == "tester"
    print("正式上传前审查报告测试通过")


if __name__ == "__main__":
    main()
