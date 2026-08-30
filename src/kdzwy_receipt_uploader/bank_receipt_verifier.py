"""Verify bank receipt drafts before a future real upload."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Collection

from .models import ReceiptError
from .workflow import load_receipt


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verify_bank_receipts(
    receipt_root: Path,
    report_path: Path | None = None,
    *,
    allowed_record_keys: Collection[str] | None = None,
) -> dict[str, Any]:
    """List drafts and validate every draft=false receipt using upload rules."""
    drafts: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    paths = sorted(receipt_root.glob("receipt_*/receipt.json")) if receipt_root.is_dir() else []
    dedicated_bank_root = receipt_root.name.lower() == "bank"
    normalized_allowed_keys = (
        {str(key) for key in allowed_record_keys}
        if allowed_record_keys is not None
        else None
    )
    orphan_count = 0
    examined = 0
    for path in paths:
        try:
            root = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"receipt": str(path.resolve()), "error": f"JSON 无法读取：{exc}"})
            continue
        if not isinstance(root, dict):
            invalid.append({"receipt": str(path.resolve()), "error": "receipt 顶层必须是对象"})
            continue
        raw_receipt_id = str(root.get("receiptId") or "")
        if (
            not dedicated_bank_root
            and root.get("source") != "bank"
            and root.get("manualEntry") is not True
            and not raw_receipt_id.startswith("bank-")
        ):
            continue
        examined += 1
        receipt_id = raw_receipt_id or path.parent.name
        receipt_tail = receipt_id.rsplit("-", 1)[-1] if receipt_id.startswith("bank-") else ""
        record_key = path.parent.name.removeprefix("receipt_")
        item = {
            "receiptId": receipt_id,
            "recordKey": record_key,
            "statementIndex": receipt_tail.split("__", 1)[-1] if receipt_tail else "",
            "receipt": str(path.resolve()),
        }
        if normalized_allowed_keys is not None and record_key not in normalized_allowed_keys:
            orphan_count += 1
            invalid.append(
                {
                    **item,
                    "error": "receipt 不属于当前普通 bank_map；可能是已分流特殊对象或旧流程产物",
                }
            )
            continue
        if root.get("draft") is True:
            drafts.append(item)
            continue
        if root.get("draft") is not False:
            invalid.append({**item, "error": "draft 必须明确为 true 或 false"})
            continue
        try:
            load_receipt(path, {})
        except ReceiptError as exc:
            invalid.append({**item, "error": str(exc)})
        else:
            ready.append(item)

    if invalid:
        status = "invalid"
    elif drafts:
        status = "drafts_pending"
    elif ready:
        status = "ready"
    else:
        status = "empty"
    report = {
        "version": 1,
        "status": status,
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "receiptDirectory": str(receipt_root.resolve()),
        "summary": {
            "receiptCount": examined,
            "draftCount": len(drafts),
            "readyCount": len(ready),
            "invalidCount": len(invalid),
            **(
                {"orphanCount": orphan_count}
                if normalized_allowed_keys is not None
                else {}
            ),
        },
        "drafts": drafts,
        "ready": ready,
        "invalid": invalid,
    }
    _atomic_write_json(report_path or receipt_root / "bank_receipts.verify.report.json", report)
    return report
