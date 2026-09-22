"""Verify bank receipt drafts before a future real upload."""
from __future__ import annotations

from .bank_receipt_layout import receipt_paths

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Collection

from .models import ReceiptError
from .workflow import load_receipt


def verify_bank_pdf_links(receipt_root: Path, *, bank_only: bool = False) -> dict[str, Any]:
    """Check only PDF binding for pending receipts; never rewrite receipt data."""
    missing = []
    pending = 0
    paths = receipt_paths(receipt_root)
    if not paths and not bank_only:
        missing.append({"receipt": str(receipt_root), "error": "尚未生成receipt，不能进入下一步"})
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if bank_only and data.get("source") != "bank" and not str(data.get("receiptId") or "").startswith("bank-") and data.get("manualEntry") is not True:
                continue
            if data.get("uploaded") is True:
                continue
            pending += 1
            links = (data.get("voucher") or {}).get("attachmentFiles")
            valid = isinstance(links, list) and bool(links)
            for item in links if isinstance(links, list) else []:
                raw = str(item.get("path") or "").strip() if isinstance(item, dict) else ""
                pdf = Path(raw)
                if not pdf.is_absolute():
                    pdf = path.parent / pdf
                valid = valid and bool(raw) and pdf.suffix.lower() == ".pdf" and pdf.is_file()
            if not valid:
                missing.append({"receipt": str(path.resolve()), "receiptId": data.get("receiptId"),
                                "error": "PDF绑定为空或文件不存在；填写voucher.attachmentFiles[].path"})
        except (OSError, ValueError, AttributeError) as exc:
            missing.append({"receipt": str(path.resolve()), "error": str(exc)})
    report = {"status": "waiting_for_pdf_binding" if missing else "ready",
              "phase": "verify", "pendingReceiptCount": pending, "missingPdfCount": len(missing),
              "missing": missing, "verifiedAt": datetime.now(timezone.utc).isoformat()}
    _atomic_write_json(receipt_root / "bank_pdf_links.verify.report.json", report)
    return report


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
    paths = receipt_paths(receipt_root) if receipt_root.is_dir() else []
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
        voucher = root.get("voucher") if isinstance(root.get("voucher"), dict) else {}
        summary = str(voucher.get("summary") or "")
        if len(summary) > 255:
            invalid.append({**item, "error": "凭证摘要超过255字符"})
            continue
        overlong_lines = [
            index
            for index, entry in enumerate(voucher.get("entries") or [], 1)
            if isinstance(entry, dict)
            and len(str(entry.get("explanation") or "")) > 255
        ]
        if overlong_lines:
            invalid.append(
                {**item, "error": f"分录摘要超过255字符：第{overlong_lines}行"}
            )
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
