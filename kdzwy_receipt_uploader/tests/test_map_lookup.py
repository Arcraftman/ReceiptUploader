from __future__ import annotations

import json
from pathlib import Path

from src.kdzwy_receipt_uploader.map_lookup import InvoicePdfMap


def test_map_lookup_empty_and_absolute(tmp_path: Path) -> None:
    map_path = tmp_path / "xlsx_pdf_map.json"
    pdf_path = tmp_path / "a.pdf"
    pdf_path.write_bytes(b"pdf")
    map_path.write_text(json.dumps({"1001": str(pdf_path), "1002": ""}), encoding="utf-8")
    lookup = InvoicePdfMap.load(map_path)
    assert lookup.resolve("1001") == pdf_path.resolve()
    assert lookup.resolve("1002") is None
    assert lookup.get("missing") == ""
