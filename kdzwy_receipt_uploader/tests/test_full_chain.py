from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kdzwy_receipt_uploader.models import AttachmentFile
from kdzwy_receipt_uploader.workflow import find_receipts, process_one


class FakeApi:
    """Local-only fake API that records the exact five-step workflow."""

    dbid = "fake-db"

    def __init__(self, pdf: Path) -> None:
        self.pdf = pdf
        self.calls: list[tuple[str, Any]] = []

    def get_dynamic_system_params(self) -> dict[str, Any]:
        self.calls.append(("/basedata/initParams?m=getSystemParams", None))
        return {"DBID": "fake-db"}

    def get_current_user_context(self) -> dict[str, str]:
        self.calls.append(("/default.jsp", None))
        return {"userName": "test-user", "userNo": "test-no", "dbid": "fake-db"}

    def get_voucher_number(self, date_value: str, group_id: str, voucher_id: str = "") -> dict[str, Any]:
        self.calls.append(("/gl/voucher?m=getvchNum", {"vchdate": date_value, "groupId": group_id, "vchId": voucher_id}))
        return {"vchNum": 88, "year": "2026", "period": "7", "yearPeriod": 202607}

    def save_voucher_v1(self, payload: dict[str, Any]) -> str:
        self.calls.append(("/jdy-fi/fake-db/gl/v1/voucher/save", payload))
        return "voucher-88"

    def get_voucher_v1(self, voucher_id: Any) -> dict[str, Any]:
        self.calls.append((f"/jdy-fi/fake-db/gl/v1/voucher/{voucher_id}", None))
        return {"id": voucher_id, "entries": [], "attachments": 1, "usedAttachments": 1}

    def upload_invoice_pdf_v1(self, file: AttachmentFile) -> dict[str, Any]:
        self.calls.append(("/jdy-fi-rpt/fake-db/v1/invoice/discern", [file.path.name]))
        return {"code": "0", "data": [{"fileId": "file-1", "uploadStatus": True}]}

    def bind_voucher_files_v1(self, voucher_id: Any, file_ids: list[str]) -> dict[str, Any]:
        self.calls.append(("/jdy-fi/fake-db/att/v1/file/bind-vch", {"vchId": voucher_id, "ids": file_ids}))
        return {"errcode": 0, "data": {}}

    def post_form(self, endpoint: str, form: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, form))
        raise AssertionError(f"unexpected legacy form endpoint: {endpoint}")

    def upload_pdf(self, endpoint: str, files: list[AttachmentFile]) -> dict[str, Any]:
        raise AssertionError("legacy upload_pdf must not be called")

    def post_json(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, body))
        raise AssertionError(f"unexpected direct JSON endpoint: {endpoint}")

    def get_json(self, endpoint: str) -> dict[str, Any]:
        self.calls.append((endpoint, None))
        raise AssertionError(f"unexpected direct GET endpoint: {endpoint}")

    @staticmethod
    def data(endpoint: str, payload: dict[str, Any]) -> Any:
        return payload["data"]

    @staticmethod
    def unwrap_data(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        return payload["data"]


def test_full_five_step_chain(tmp_path: Path) -> None:
    input_dir = tmp_path / "inbox" / "receipt-1"
    input_dir.mkdir(parents=True)
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    map_path = tmp_path / "xlsx_pdf_map.json"
    map_path.write_text(json.dumps({"INV-001": str(pdf)}), encoding="utf-8")
    from kdzwy_receipt_uploader.map_lookup import InvoicePdfMap
    payload = {
        "schemaVersion": "1.0",
        "receiptId": "chain-test-001",
        "voucher": {
            "date": "2026-07-31",
            "groupId": "group-1",
            "summary": "全链路本地测试",
            "attachments": 1,
            "invoiceCodes": ["INV-001"],
            "userName": "test-user",
            "entries": [
                {"lineNo": 1, "accountId": "1", "accountNumber": "1001", "accountName": "现金", "dc": 1, "amount": "1.00", "amountFor": "1.00", "cur": "RMB", "rate": "1"},
                {"lineNo": 2, "accountId": "2", "accountNumber": "1002", "accountName": "银行", "dc": -1, "amount": "1.00", "amountFor": "1.00", "cur": "RMB", "rate": "1"}
            ]
        }
    }
    receipt_path = input_dir / "receipt.json"
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pdf_map = InvoicePdfMap.load(map_path)
    valid, invalid = find_receipts(tmp_path / "inbox", {}, pdf_map)
    assert not invalid
    assert len(valid) == 1
    api = FakeApi(pdf)
    result = process_one(valid[0][1], api)
    assert result["voucherId"] == "voucher-88"
    assert result["attachmentFileIds"] == ["file-1"]
    assert [call[0] for call in api.calls] == [
        "/basedata/initParams?m=getSystemParams",
        "/default.jsp",
        "/gl/voucher?m=getvchNum",
        "/jdy-fi/fake-db/gl/v1/voucher/save",
        "/jdy-fi/fake-db/gl/v1/voucher/voucher-88",
        "/jdy-fi-rpt/fake-db/v1/invoice/discern",
        "/jdy-fi/fake-db/att/v1/file/bind-vch",
        "/jdy-fi/fake-db/gl/v1/voucher/voucher-88",
    ]


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        test_full_five_step_chain(Path(directory))
    print("五步调用链本地测试通过")
