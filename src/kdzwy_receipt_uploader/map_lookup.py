"""Read the generated invoice-code to PDF map for voucher attachment upload."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ReceiptError


class InvoicePdfMap:
    """Immutable lookup over one month-local xlsx_pdf_map.json file."""

    def __init__(self, path: Path, values: dict[str, Any]) -> None:
        self.path = path.resolve()
        self.values = values

    @classmethod
    def load(cls, path: Path) -> "InvoicePdfMap":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReceiptError(f"无法读取 xlsx_pdf_map.json：{exc}") from exc
        if not isinstance(payload, dict):
            raise ReceiptError("xlsx_pdf_map.json 必须是 JSON 对象")
        return cls(path, payload)

    def get(self, invoice_code: str) -> str:
        value = self.values.get(str(invoice_code), "")
        if isinstance(value, list):
            return str(value[0]) if value else ""
        if value in (None, ""):
            return ""
        return str(value)

    def resolve(self, invoice_code: str) -> Path | None:
        value = self.get(invoice_code)
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = (self.path.parent / path).resolve()
        return path
