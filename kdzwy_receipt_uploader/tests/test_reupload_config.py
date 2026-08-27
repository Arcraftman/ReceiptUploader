from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kdzwy_receipt_uploader.cli import successful_receipt_ids


class Paths:
    def __init__(self, root: Path) -> None:
        self.logs = root / "logs"
        self.logs.mkdir()


def main() -> None:
    with TemporaryDirectory() as directory:
        paths = Paths(Path(directory))
        (paths.logs / "run.jsonl").write_text(
            json.dumps({"status": "submitted_and_verified", "receiptId": "r1"}) + "\n"
            + json.dumps({"status": "submitted_and_verified", "receiptId": "r2"}) + "\n",
            encoding="utf-8",
        )
        ids = successful_receipt_ids(paths)
        assert ids == {"r1", "r2"}
        print("单一批处理模式历史记录读取测试通过")


if __name__ == "__main__":
    main()
