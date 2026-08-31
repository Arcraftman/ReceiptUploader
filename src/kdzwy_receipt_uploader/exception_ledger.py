"""Persistent, per-document exception ledger for fail-closed workflows."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "exceptions": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "exceptions": []}
    if not isinstance(value, dict) or not isinstance(value.get("exceptions"), list):
        return {"version": 1, "exceptions": []}
    return value


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _identity(source: str, stage: str, document_id: str, error_type: str, message: str) -> str:
    raw = "\x1f".join((source, stage, document_id, error_type, message))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def replace_stage_exceptions(
    path: Path,
    source: str,
    stage: str,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replace active exceptions for one full stage while retaining resolved history."""
    payload = _load(path)
    now = _now()
    existing = [dict(item) for item in payload.get("exceptions", []) if isinstance(item, Mapping)]
    current_rows: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    previous_by_id = {str(item.get("exceptionId")): item for item in existing}
    for row in rows:
        document_id = str(row.get("documentId") or row.get("invoiceCode") or row.get("receiptId") or row.get("file") or "*")
        error_type = str(row.get("errorType") or row.get("type") or row.get("status") or "stage_exception")
        message = str(row.get("message") or row.get("reason") or row.get("error") or error_type)
        exception_id = _identity(source, stage, document_id, error_type, message)
        previous = previous_by_id.get(exception_id, {})
        current_ids.add(exception_id)
        current_rows.append({
            "exceptionId": exception_id,
            "source": source,
            "stage": stage,
            "documentId": document_id,
            "errorType": error_type,
            "message": message,
            "blocking": bool(row.get("blocking", True)),
            "active": True,
            "firstSeenAt": previous.get("firstSeenAt") or now,
            "lastSeenAt": now,
            "resolvedAt": None,
            "details": dict(row.get("details") or {}),
        })
    retained: list[dict[str, Any]] = []
    for item in existing:
        if item.get("source") == source and item.get("stage") == stage and item.get("active") is True:
            if item.get("exceptionId") not in current_ids:
                item["active"] = False
                item["resolvedAt"] = now
                item["lastSeenAt"] = now
            else:
                continue
        retained.append(item)
    payload.update({
        "version": 1,
        "source": source,
        "updatedAt": now,
        "activeBlockingCount": sum(1 for item in retained + current_rows if item.get("active") and item.get("blocking")),
        "exceptions": retained + current_rows,
    })
    _write(path, payload)
    return payload


def append_exception(
    path: Path,
    source: str,
    stage: str,
    document_id: str,
    error_type: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _load(path)
    rows = [dict(item) for item in payload.get("exceptions", []) if isinstance(item, Mapping)]
    now = _now()
    exception_id = _identity(source, stage, document_id, error_type, message)
    for item in rows:
        if item.get("exceptionId") == exception_id:
            item.update({"active": True, "blocking": True, "lastSeenAt": now, "resolvedAt": None, "details": dict(details or {})})
            break
    else:
        rows.append({
            "exceptionId": exception_id,
            "source": source,
            "stage": stage,
            "documentId": document_id,
            "errorType": error_type,
            "message": message,
            "blocking": True,
            "active": True,
            "firstSeenAt": now,
            "lastSeenAt": now,
            "resolvedAt": None,
            "details": dict(details or {}),
        })
    payload.update({"version": 1, "source": source, "updatedAt": now, "exceptions": rows})
    payload["activeBlockingCount"] = sum(1 for item in rows if item.get("active") and item.get("blocking"))
    _write(path, payload)
    return payload


def resolve_document_stage(path: Path, source: str, stage: str, document_ids: Iterable[str]) -> None:
    payload = _load(path)
    targets = {str(value) for value in document_ids if str(value)}
    if not targets:
        return
    now = _now()
    changed = False
    for item in payload.get("exceptions", []):
        if item.get("source") == source and item.get("stage") == stage and item.get("documentId") in targets and item.get("active"):
            item["active"] = False
            item["resolvedAt"] = now
            item["lastSeenAt"] = now
            changed = True
    if changed:
        payload["updatedAt"] = now
        payload["activeBlockingCount"] = sum(1 for item in payload.get("exceptions", []) if item.get("active") and item.get("blocking"))
        _write(path, payload)


def blocking_document_ids(path: Path, source: str) -> set[str]:
    payload = _load(path)
    return {
        str(item.get("documentId"))
        for item in payload.get("exceptions", [])
        if isinstance(item, Mapping)
        and item.get("source") == source
        and item.get("active") is True
        and item.get("blocking") is True
        and item.get("documentId") not in (None, "")
    }


def replace_analysis_exception_stages(
    path: Path,
    source: str,
    analyses: Mapping[str, Any],
    scope_document_ids: Iterable[str] | None = None,
) -> set[str]:
    """Classify analysis output by the stage that actually failed."""
    scope = {str(value) for value in scope_document_ids} if scope_document_ids is not None else {str(value) for value in analyses}
    blocked_codes: set[str] = set()
    ocr_rows: list[dict[str, Any]] = []
    master_data_rows: list[dict[str, Any]] = []
    template_rows: list[dict[str, Any]] = []
    final_validation_rows: list[dict[str, Any]] = []
    for code in sorted(scope):
        analysis = analyses.get(code)
        if not isinstance(analysis, Mapping):
            blocked_codes.add(code)
            template_rows.append({
                "documentId": code,
                "errorType": "analysis_missing",
                "message": "缺少Qwen分析结果",
            })
            continue
        details = dict(analysis)
        ocr_fields = analysis.get("ocrFields") if isinstance(analysis.get("ocrFields"), Mapping) else {}
        if ocr_fields.get("criticalFieldsReady") is False:
            confidence = ocr_fields.get("fieldConfidence") if isinstance(ocr_fields.get("fieldConfidence"), Mapping) else {}
            weak_fields = [str(name) for name, value in confidence.items() if float(value or 0) < 0.5]
            ocr_rows.append({
                "documentId": code,
                "errorType": "critical_fields_not_ready",
                "message": "OCR关键字段未完整识别" + (f"：{', '.join(weak_fields)}" if weak_fields else ""),
                "details": details,
            })
        if analysis.get("analysisStatus") == "ready_for_review":
            continue
        blocked_codes.add(code)
        diagnostics = analysis.get("diagnostics") if isinstance(analysis.get("diagnostics"), Mapping) else {}
        block_reason = str(diagnostics.get("blockReason") or "")
        exception_type = str(analysis.get("exceptionType") or "")
        template_path = str(analysis.get("templatePath") or "")
        if "动态辅助核算目录无法唯一解析" in block_reason:
            master_data_rows.append({
                "documentId": code,
                "errorType": "supplier_or_customer_unresolved",
                "message": block_reason,
                "details": details,
            })
        elif exception_type == "template_analysis_error" or not template_path:
            template_rows.append({
                "documentId": code,
                "errorType": exception_type or "template_not_selected",
                "message": str(analysis.get("reason") or analysis.get("error") or block_reason or "模板无法唯一匹配"),
                "details": details,
            })
        else:
            final_validation_rows.append({
                "documentId": code,
                "errorType": "analysis_final_validation_failed",
                "message": block_reason or str(analysis.get("reason") or "分析强校验未通过"),
                "details": details,
            })
    replace_stage_exceptions(path, source, "ocr_field_validation", ocr_rows)
    replace_stage_exceptions(path, source, "master_data", master_data_rows)
    replace_stage_exceptions(path, source, "template_analysis", template_rows)
    replace_stage_exceptions(path, source, "final_validation", final_validation_rows)
    replace_stage_exceptions(path, source, "analysis", [])
    return blocked_codes
