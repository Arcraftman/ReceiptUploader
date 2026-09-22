"""Inventory split bank PDFs without verified attachment upload evidence."""
from __future__ import annotations

from .bank_receipt_layout import receipt_paths
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def pdf_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_unuploaded_bank_pdfs(split_root: Path, receipt_root: Path, output_root: Path) -> dict:
    verified = set()
    ledger = receipt_root / "bank_attachment_uploads.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("attachmentStatus") == "uploaded_linked_and_verified":
                verified.update(item.get("attachmentSha256") or [])
    for path in receipt_paths(receipt_root):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        result = data.get("uploadResult") or {}
        if data.get("uploaded") is True and result.get("attachmentStatus") == "uploaded_linked_and_verified":
            verified.update(result.get("attachmentSha256") or [])
    output_root.mkdir(parents=True, exist_ok=True)
    previous = output_root / "bank_exception.json"
    if previous.is_file():
        old = json.loads(previous.read_text(encoding="utf-8"))
        for item in old.get("entries", []):
            copy = Path(item.get("copy", "")).resolve()
            if copy.is_relative_to((output_root / "pdf").resolve()) and copy.is_file() and pdf_hash(copy) == item.get("sha256"):
                copy.unlink()
    rows = []
    for pdf in sorted(split_root.rglob("*.pdf")):
        digest = pdf_hash(pdf)
        if digest in verified:
            continue
        relative = pdf.relative_to(split_root)
        target = output_root / "pdf" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, target)
        rows.append({"pdf": str(pdf.resolve()), "copy": str(target.resolve()),
                     "sha256": digest, "reason": "未找到附件上传并绑定成功的记录"})
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(),
              "unuploadedPdfCount": len(rows), "entries": rows}
    # The manifest is authoritative; stale copies are never listed as current exceptions.
    (output_root / "bank_exception.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "bank_exception.txt").write_text("未确认上传并绑定成功的PDF：" + str(len(rows)) + "\n" + "\n".join(row["pdf"] for row in rows), encoding="utf-8")
    return report
