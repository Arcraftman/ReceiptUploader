"""Atomic persistence for prior analysis decisions."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

_ANALYSIS_MEMORY_LOCK = threading.Lock()


def _load_analysis_memory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "processed": [], "verifiedDecisions": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"version": 1, "processed": [], "verifiedDecisions": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "processed": [], "verifiedDecisions": []}


def _save_analysis_memory(path: Path, memory: dict[str, Any], invoice_code: str, decision: Mapping[str, Any]) -> None:
    with _ANALYSIS_MEMORY_LOCK:
        # Each worker starts from a snapshot. Reload inside the lock so one
        # worker cannot overwrite decisions saved by another worker.
        current = _load_analysis_memory(path)
        processed = [item for item in current.get("processed", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
        processed.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "analysisStatus": decision.get("analysisStatus", ""), "confidence": decision.get("confidence", 0)})
        current["processed"] = processed
        verified = [item for item in current.get("verifiedDecisions", []) if isinstance(item, dict) and item.get("invoiceCode") != invoice_code]
        if decision.get("analysisStatus") == "ready_for_review":
            fields = decision.get("extractedFields", {}) if isinstance(decision.get("extractedFields"), Mapping) else {}
            verified.append({"invoiceCode": invoice_code, "templatePath": decision.get("templatePath", ""), "businessType": decision.get("businessType", ""), "sellerName": fields.get("sellerName", ""), "buyerName": fields.get("buyerName", ""), "confidence": decision.get("confidence", 0)})
        current["verifiedDecisions"] = verified
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = False
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                temporary.replace(path)
                saved = True
                break
            except OSError as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(0.05 * (attempt + 1))
        if not saved:
            logging.getLogger(__name__).warning(
                "分析记忆写入失败但不阻断当前记录：%s：%s",
                path,
                last_error,
            )
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        memory.clear()
        memory.update(copy.deepcopy(current))
