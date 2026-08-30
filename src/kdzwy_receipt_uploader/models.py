from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass
class AttachmentFile:
    path: Path
    relative_path: str
    bill_type_id: str = ""
    remark: str = ""
    size: int = 0


@dataclass
class Receipt:
    receipt_id: str
    voucher: dict[str, Any]
    source: str | None = None
    attachment_files: list[AttachmentFile] = field(default_factory=list)
    invoice_codes: list[str] = field(default_factory=list)
    unresolved_invoice_codes: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    status: str
    receipt_id: str
    payload: dict[str, Any] = field(default_factory=dict)


class ReceiptError(ValueError):
    pass


class ApiError(RuntimeError):
    pass
