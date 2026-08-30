from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.cli import run_confirm_sequential
from kdzwy_receipt_uploader.models import ApiError, Receipt


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.count = 0


class FakePaths:
    def __init__(self, root: Path) -> None:
        self.logs = root / "logs"
        self.submitted = root / "submitted"
        self.failed = root / "failed"
        self.logs.mkdir()
        self.submitted.mkdir()
        self.failed.mkdir()


def make_receipt(receipt_id: str) -> Receipt:
    return Receipt(
        receipt_id=receipt_id,
        voucher={
            "date": "2026-07-01",
            "groupId": "g1",
            "groupName": "记",
            "summary": receipt_id,
            "attachments": 0,
            "userName": "用户",
            "entries": [
                {"lineNo": 1, "accountId": "a1", "accountNumber": "1122", "accountName": "应收账款", "dc": 1, "amount": 1, "amountFor": 1, "cur": "RMB", "rate": 1},
                {"lineNo": 2, "accountId": "a2", "accountNumber": "5001", "accountName": "销售收入", "dc": -1, "amount": 1, "amountFor": 1, "cur": "RMB", "rate": 1},
            ],
        },
        source=None,
        attachment_files=[],
    )


def main() -> None:
    import kdzwy_receipt_uploader.cli as cli

    original = cli.process_one
    calls: list[str] = []

    def fake_process(receipt: Receipt, api: FakeApi) -> dict[str, str]:
        calls.append(receipt.receipt_id)
        if receipt.receipt_id == "r2":
            raise ApiError("模拟失败")
        return {"status": "submitted_and_verified", "voucherNo": receipt.receipt_id, "voucherId": receipt.receipt_id}

    cli.process_one = fake_process
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = FakePaths(root)
            valid = [(root / "r1.json", make_receipt("r1")), (root / "r2.json", make_receipt("r2")), (root / "r3.json", make_receipt("r3"))]
            for path, _ in valid:
                path.write_text("{}", encoding="utf-8")
            failed, processed = run_confirm_sequential(valid, FakeApi(), paths)
            assert failed is True
            assert processed == 1
            assert calls == ["r1", "r2"]
            assert not (root / "submitted" / "r3.json").exists()
        print("串行上传失败即停测试通过")
    finally:
        cli.process_one = original


if __name__ == "__main__":
    main()
