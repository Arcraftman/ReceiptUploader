from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .api import KdzwyApi
from .map_lookup import InvoicePdfMap
from .models import ApiError, AttachmentFile, Receipt, ReceiptError
from .paths import ProjectPaths

MONEY = Decimal("0.01")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,119}$")
AUXILIARY_PREFIXES = ("customer", "supplier", "emp", "project", "inventory", "dept")


def money(value: Any, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReceiptError(f"{field} 不是有效金额") from exc
    # Negative amounts are valid accounting values, including red invoices.
    return amount


def load_snapshot(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"科目快照不可读取：{exc}") from exc
    return {
        (str(item.get("id")), str(item.get("number"))): item
        for item in payload.get("activeDetailSubjects", [])
        if isinstance(item, dict)
    }


def load_receipt(path: Path, snapshot: dict[tuple[str, str], dict[str, Any]], pdf_map: InvoicePdfMap | None = None) -> Receipt:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"无法读取 receipt：{exc}") from exc
    if not isinstance(root, dict) or root.get("schemaVersion") != "1.0":
        raise ReceiptError("schemaVersion 必须为 1.0")
    if root.get("draft") is True:
        raise ReceiptError("receipt 仍是草稿，请先补齐业务字段并移除 draft=true")
    receipt_id = root.get("receiptId")
    if not isinstance(receipt_id, str) or not ID_RE.fullmatch(receipt_id):
        raise ReceiptError("receiptId 格式无效")
    voucher = root.get("voucher")
    if not isinstance(voucher, dict):
        raise ReceiptError("voucher 必须是对象")
    raw_date = voucher.get("date")
    if not isinstance(raw_date, str) or not DATE_RE.fullmatch(raw_date):
        raise ReceiptError("voucher.date 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ReceiptError("voucher.date 不是有效日期") from exc
    for field in ("groupId", "summary", "userName"):
        if voucher.get(field) in (None, ""):
            raise ReceiptError(f"voucher.{field} 不能为空")
    attachments = voucher.get("attachments", 0)
    if not isinstance(attachments, int) or isinstance(attachments, bool) or attachments < 0:
        raise ReceiptError("voucher.attachments 必须是非负整数")
    entries = voucher.get("entries")
    if not isinstance(entries, list) or len(entries) < 2:
        raise ReceiptError("voucher.entries 至少需要两条分录")
    normalized_entries: list[dict[str, Any]] = []
    debit = Decimal("0.00")
    credit = Decimal("0.00")
    used_line_no: set[int] = set()
    for index, raw_entry in enumerate(entries, 1):
        if not isinstance(raw_entry, dict):
            raise ReceiptError(f"entries[{index}] 必须是对象")
        line_no = raw_entry.get("lineNo", index)
        if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1 or line_no in used_line_no:
            raise ReceiptError(f"entries[{index}].lineNo 无效或重复")
        used_line_no.add(line_no)
        for field in ("accountId", "accountNumber", "accountName"):
            if raw_entry.get(field) in (None, ""):
                raise ReceiptError(f"entries[{index}].{field} 不能为空")
        dc = raw_entry.get("dc")
        if dc not in (1, -1):
            raise ReceiptError(f"entries[{index}].dc 必须为 1 或 -1")
        amount = money(raw_entry.get("amount"), f"entries[{index}].amount")
        amount_for = money(raw_entry.get("amountFor", amount), f"entries[{index}].amountFor")
        key = (str(raw_entry["accountId"]), str(raw_entry["accountNumber"]))
        subject = snapshot.get(key)
        if snapshot and subject is None:
            raise ReceiptError(f"entries[{index}] 科目不在当前快照：{key[0]}/{key[1]}")
        if subject:
            auxiliary_prefix = next((prefix for prefix in AUXILIARY_PREFIXES if raw_entry.get(f"{prefix}Id") not in (None, "")), None)
            expected_aux = raw_entry.get("auxiliaryExpected")
            expected_subject_name = expected_aux.get("subjectAccountName") if isinstance(expected_aux, dict) else ""
            subject_name = raw_entry.get("subjectAccountName") or expected_subject_name
            if not subject_name:
                subject_name = subject.get("fullName") if auxiliary_prefix else raw_entry.get("accountName")
            if str(subject_name) != str(subject.get("fullName")):
                raise ReceiptError(f"entries[{index}].subjectAccountName 与科目快照不一致")
        item = dict(raw_entry)
        item.update({
            "lineNo": line_no,
            "entryId": raw_entry.get("entryId", index),
            "explanation": str(raw_entry.get("explanation") or voucher["summary"]),
            "dc": dc,
            "amount": amount,
            "amountFor": amount_for,
            "cur": str(raw_entry.get("cur") or "RMB"),
            "rate": raw_entry.get("rate", "1.0"),
            "qtyAux": bool(raw_entry.get("qtyAux", False)),
        })
        # Reconstruct local read-back expectations from the compact receipt and
        # the authoritative live subject snapshot. They are never persisted or
        # sent to the save API.
        for prefix, item_class in (("customer", "客户"), ("supplier", "供应商"), ("emp", "职员"), ("project", "项目"), ("inventory", "存货"), ("dept", "部门")):
            auxiliary_id = item.get(f"{prefix}Id")
            if auxiliary_id not in (None, "") and not isinstance(item.get("auxiliaryExpected"), dict):
                auxiliary_name = str(item.get(f"{prefix}Name") or item.get("accountName", ""))
                subject_name = str(subject.get("fullName", "")) if subject else str(item.get("subjectAccountName", ""))
                item["subjectAccountName"] = subject_name
                item["accountName"] = auxiliary_name
                item["auxiliaryExpected"] = {
                    "itemClass": item_class,
                    "id": str(auxiliary_id),
                    "number": str(item.get(f"{prefix}Number", "")),
                    "name": auxiliary_name,
                    "accountNumber": str(item.get("accountNumber", "")),
                    "subjectAccountName": subject_name,
                }
                break
        normalized_entries.append(item)
        if dc == 1:
            debit += amount
        else:
            credit += amount
    if debit != credit:
        raise ReceiptError(f"借贷不平衡：借方 {debit}，贷方 {credit}")

    invoice_codes = [str(item) for item in voucher.get("invoiceCodes", []) if item not in (None, "")]
    if invoice_codes and pdf_map is None:
        raise ReceiptError("receipt 使用 invoiceCodes 时必须提供 xlsx_pdf_map.json")
    if pdf_map and invoice_codes:
        attachments_raw = []
        for code in invoice_codes:
            pdf_path = pdf_map.resolve(code)
            if pdf_path is not None:
                attachments_raw.append({"path": str(pdf_path), "mapInvoiceCode": code})
    else:
        attachments_raw = voucher.get("attachmentFiles", []) or []
    if not isinstance(attachments_raw, list):
        raise ReceiptError("voucher.attachmentFiles 必须是数组")
    attachment_files: list[AttachmentFile] = []
    names: set[str] = set()
    total_size = 0
    base = path.parent.resolve()
    for index, raw_file in enumerate(attachments_raw, 1):
        if not isinstance(raw_file, dict) or not isinstance(raw_file.get("path"), str):
            raise ReceiptError(f"attachmentFiles[{index}].path 无效")
        relative = Path(raw_file["path"])
        if relative.is_absolute():
            attachment_path = relative.resolve()
            display_path = str(relative)
        else:
            if ".." in relative.parts:
                raise ReceiptError(f"附件路径不能包含 ..：{relative}")
            attachment_path = (base / relative).resolve()
            try:
                attachment_path.relative_to(base)
            except ValueError as exc:
                raise ReceiptError(f"附件路径越过 receipt 目录：{relative}") from exc
            display_path = str(relative)
        if not attachment_path.is_file() or attachment_path.suffix.lower() != ".pdf":
            raise ReceiptError(f"附件必须是存在的 PDF：{display_path}")
        if attachment_path.name in names:
            raise ReceiptError(f"附件文件名重复：{attachment_path.name}")
        names.add(attachment_path.name)
        size = attachment_path.stat().st_size
        if size <= 0:
            raise ReceiptError(f"附件为空：{relative}")
        total_size += size
        attachment_files.append(AttachmentFile(
            path=attachment_path,
            relative_path=display_path,
            bill_type_id=str(raw_file.get("billTypeId", "")),
            remark=str(raw_file.get("remark", "")),
            size=size,
        ))
    if total_size > 30 * 1024 * 1024:
        raise ReceiptError("单个 receipt 的 PDF 合计不能超过 30MB")
    if not invoice_codes and attachment_files and attachments != len(attachment_files):
        raise ReceiptError("attachments 与 attachmentFiles 数量不一致")

    normalized = dict(voucher)
    normalized.update({
        "groupId": str(voucher["groupId"]),
        "groupName": voucher.get("groupName", "记"),
        "attachments": attachments,
        "entries": normalized_entries,
        "debitTotal": debit,
        "creditTotal": credit,
        "userName": str(voucher["userName"]).strip(),
    })
    unresolved = [code for code in invoice_codes if pdf_map and not pdf_map.get(code)]
    return Receipt(receipt_id=receipt_id, voucher=normalized, source=root.get("source"), attachment_files=attachment_files, invoice_codes=invoice_codes, unresolved_invoice_codes=unresolved)


def find_receipts(input_dir: Path, snapshot: dict[tuple[str, str], dict[str, Any]], pdf_map: InvoicePdfMap | None = None) -> tuple[list[tuple[Path, Receipt]], list[dict[str, str]]]:
    valid: list[tuple[Path, Receipt]] = []
    invalid: list[dict[str, str]] = []
    for path in sorted(input_dir.rglob("*.json")):
        if path.name.endswith(".result.json") or path.name.endswith(".report.json") or path.name in {"xlsx_pdf_map.json", "xlsx_pdf_map.report.json"}:
            continue
        if "maps" in path.parts:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("draft") is True:
                continue
            if isinstance(raw, dict) and raw.get("uploaded") is True:
                continue
            valid.append((path, load_receipt(path, snapshot, pdf_map)))
        except ReceiptError as exc:
            invalid.append({"file": str(path), "error": str(exc)})
    return valid, invalid


def build_voucher(receipt: Receipt, number_data: dict[str, Any], dbid: str | None) -> dict[str, Any]:
    source = receipt.voucher
    number = int(number_data["vchNum"])
    year = str(number_data["year"])
    period = str(number_data["period"])
    entries = []
    for entry in source["entries"]:
        item = dict(entry)
        item.pop("lineNo", None)
        # auxiliaryExpected and subjectAccountName are local metadata used
        # for read-back validation; they must never be sent as save fields.
        item.pop("auxiliaryExpected", None)
        item.pop("subjectAccountName", None)
        # Current frontend builds the save payload with customerId/supplierId
        # (and the corresponding ID for other auxiliary classes). Number/name
        # are display/read-back values, not save fields.
        for prefix in AUXILIARY_PREFIXES:
            if f"{prefix}Id" in item:
                item.pop(f"{prefix}Number", None)
                item.pop(f"{prefix}Name", None)
        item["entryId"] = item.get("entryId") or len(entries) + 1
        def browser_number_text(value: Any) -> str:
            number = Decimal(str(value))
            text = format(number, "f").rstrip("0").rstrip(".")
            return text or "0"

        # Current accounting frontend calls .toString() for these fields
        # before POSTing /gl/v1/voucher/save. The API rejects JSON numbers.
        item["amount"] = browser_number_text(item["amount"])
        item["amountFor"] = browser_number_text(item["amountFor"])
        item["price"] = browser_number_text(item.get("price", 0))
        item["qty"] = browser_number_text(item.get("qty", 0))
        item["rate"] = browser_number_text(item.get("rate", 1))
        item["id"] = str(item.get("id") or "0")
        item["_index"] = len(entries)
        item["control"] = bool(item.get("control", True))
        item["acctCur"] = item.get("acctCur", "[]")
        item["attachments"] = int(item.get("attachments", 0) or 0)
        entries.append(item)
    voucher = {
        "id": "",
        "groupId": source["groupId"],
        "number": number,
        "voucherNo": f"{source.get('groupName') or '记'}-{number}",
        "attachments": source["attachments"],
        "date": source["date"],
        "year": year,
        "period": period,
        "yearPeriod": int(number_data.get("yearPeriod") or int(year) * 100 + int(period)),
        "entries": entries,
        "debitTotal": browser_number_text(source["debitTotal"]),
        "creditTotal": browser_number_text(source["creditTotal"]),
        "explanation": source["summary"],
        "internalind": "",
        "transType": "",
        "userName": source["userName"],
        "checked": 0,
        "checkName": "",
        "posted": False,
        "modifyTime": source["date"],
        "ownerId": source.get("ownerId", 0),
        "checkerId": source.get("checkerId", 1),
        "internalind": source.get("internalind", ""),
        "transType": source.get("transType"),
    }
    if dbid:
        voucher["sdbid"] = dbid
    return voucher


def _readback_entries(detail: Any) -> list[dict[str, Any]]:
    """Return voucher entry rows from the getVchById response."""
    if not isinstance(detail, dict):
        return []
    entries = detail.get("entries")
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, dict)]
    voucher = detail.get("voucher")
    if isinstance(voucher, dict) and isinstance(voucher.get("entries"), list):
        return [item for item in voucher["entries"] if isinstance(item, dict)]
    return []


def validate_auxiliary_readback(source_entries: list[dict[str, Any]], detail: Any) -> list[dict[str, Any]]:
    """Verify live auxiliary objects after save, before attachments/archive.

    The website may return a successful save while binding an account number as
    the auxiliary value if the request contains display fields. Treat any ID
    or name mismatch as a hard failure so the batch cannot continue silently.
    """
    expected_rows = [
        (index, entry) for index, entry in enumerate(source_entries)
        if isinstance(entry.get("auxiliaryExpected"), dict)
    ]
    if not expected_rows:
        return []
    actual_entries = _readback_entries(detail)
    if len(actual_entries) < len(source_entries):
        raise ApiError("凭证回读缺少分录，无法确认客户/供应商辅助核算")
    mismatches: list[dict[str, Any]] = []
    for index, source_entry in expected_rows:
        expected = source_entry["auxiliaryExpected"]
        actual = actual_entries[index]
        item_class = str(expected.get("itemClass", ""))
        prefix = {"客户": "customer", "供应商": "supplier", "职员": "emp", "项目": "project", "存货": "inventory", "部门": "dept"}.get(item_class)
        if not prefix:
            mismatches.append({"line": index + 1, "reason": "未知辅助核算类型", "expected": expected, "actual": actual})
            continue
        expected_id = str(expected.get("id", ""))
        actual_id = str(actual.get(f"{prefix}Id", ""))
        expected_name = str(expected.get("name", "")).strip()
        # The service may expose the auxiliary display name as customName,
        # supplierName, or the normalized auxiliaryName. accountName is the
        # subject/contact field used by the original website and is checked
        # separately as the account's display name.
        actual_aux_name = str(actual.get(f"{prefix}Name") or actual.get("auxiliaryName") or "").strip()
        actual_account_name = str(actual.get("accountName", "")).strip()
        expected_account_number = str(expected.get("accountNumber", "")).strip()
        expected_subject_name = str(expected.get("subjectAccountName", "")).strip()
        actual_account_number = str(actual.get("accountNumber", "")).strip()
        if not expected_id or actual_id != expected_id or not expected_name or actual_aux_name != expected_name or (expected_account_number and actual_account_number != expected_account_number) or (expected_subject_name and actual_account_name != expected_subject_name):
            mismatches.append({
                "line": index + 1,
                "itemClass": item_class,
                "expected": {"id": expected_id, "name": expected_name, "accountNumber": expected_account_number, "subjectAccountName": expected_subject_name},
                "actual": {"id": actual_id, "auxiliaryName": actual_aux_name, "accountNumber": actual_account_number, "accountName": actual_account_name},
            })
    if mismatches:
        raise ApiError(f"辅助核算回读不一致，已停止后续处理：{json.dumps(mismatches, ensure_ascii=False)}")
    return [{"line": index + 1, "itemClass": str(entry["auxiliaryExpected"].get("itemClass", "")), "id": str(entry["auxiliaryExpected"].get("id", "")), "name": str(entry["auxiliaryExpected"].get("name", ""))} for index, entry in expected_rows]


def preview(receipt: Receipt, voucher: dict[str, Any]) -> dict[str, Any]:
    return {
        "receiptId": receipt.receipt_id,
        "voucherNo": voucher.get("voucherNo"),
        "date": voucher.get("date"),
        "attachments": voucher.get("attachments"),
        "attachmentFiles": [{"path": x.relative_path, "size": x.size} for x in receipt.attachment_files],
        "entryIds": [x.get("entryId") for x in voucher.get("entries", [])],
        "debitTotal": voucher.get("debitTotal"),
        "creditTotal": voucher.get("creditTotal"),
    }


def build_v1_voucher_payload(receipt: Receipt, number_data: dict[str, Any], dbid: str | None) -> dict[str, Any]:
    voucher = build_voucher(receipt, number_data, dbid)
    voucher["id"] = "0"
    voucher["state"] = 1
    voucher["posted"] = False
    voucher["attachments"] = len(receipt.attachment_files)
    voucher["type"] = 0
    voucher.pop("year", None)
    voucher.pop("period", None)
    return {"type": 0, "vch": voucher}


def process_one(receipt: Receipt, api: KdzwyApi) -> dict[str, Any]:
    source = dict(receipt.voucher)
    system_params = api.get_dynamic_system_params()
    user_context = api.get_current_user_context()
    source["userName"] = user_context.get("userName") or source.get("userName", "")
    source["userNo"] = user_context.get("userNo", "")
    receipt_for_upload = Receipt(receipt.receipt_id, source, receipt.source, receipt.attachment_files, receipt.invoice_codes, receipt.unresolved_invoice_codes)
    number = api.get_voucher_number(source["date"], str(source["groupId"]))
    if not number.get("vchNum"):
        raise ApiError("新版取号接口未返回有效凭证号，未调用保存接口")
    voucher_payload = build_v1_voucher_payload(receipt_for_upload, number, api.dbid or str(system_params.get("DBID", "")))
    voucher_id = api.save_voucher_v1(voucher_payload)
    detail = api.get_voucher_v1(voucher_id)
    auxiliary_readback = validate_auxiliary_readback(source["entries"], detail)
    result: dict[str, Any] = {"status": "submitted_and_verified", "apiVersion": "vip4-v1", "receiptId": receipt.receipt_id, "voucherId": voucher_id, "voucherNo": voucher_payload["vch"]["voucherNo"], "voucherReadback": detail, "auxiliaryReadback": auxiliary_readback, "attachmentStatus": "not_requested", "attachmentFileIds": [], "unresolvedInvoiceCodes": receipt.unresolved_invoice_codes, "completedAt": datetime.now(timezone.utc).isoformat()}
    if receipt_for_upload.attachment_files:
        file_ids: list[str] = []
        for attachment in receipt_for_upload.attachment_files:
            uploaded = None
            retry_delays = (2, 5, 10)
            for attempt in range(len(retry_delays) + 1):
                try:
                    uploaded = api.upload_invoice_pdf_v1(attachment)
                    break
                except ApiError as exc:
                    message = str(exc)
                    tunnel_502 = "Tunnel connection failed: 502" in message or "返回 HTTP 502" in message
                    if not tunnel_502 or attempt >= len(retry_delays):
                        raise ApiError(
                            f"凭证已保存但附件上传失败：voucherId={voucher_id}，"
                            f"voucherNo={voucher_payload['vch']['voucherNo']}；{message}"
                        ) from exc
                    time.sleep(retry_delays[attempt])
            if uploaded is None:
                raise ApiError(
                    f"凭证已保存但附件上传无结果：voucherId={voucher_id}，"
                    f"voucherNo={voucher_payload['vch']['voucherNo']}"
                )
            data = uploaded.get("data", [])
            rows = data if isinstance(data, list) else []
            file_id = next((str(row.get("fileId")) for row in rows if isinstance(row, dict) and row.get("fileId") and row.get("uploadStatus") is not False), "")
            if not file_id:
                raise ApiError("新版 PDF 上传/识别未返回有效 fileId；凭证已保存，不重试保存")
            file_ids.append(file_id)
        bound = api.bind_voucher_files_v1(voucher_id, file_ids)
        api.data("新版附件绑定", bound)
        detail_after_bind = api.get_voucher_v1(voucher_id)
        detail_data = detail_after_bind if isinstance(detail_after_bind, dict) else {}
        if int(detail_data.get("attachments", 0) or 0) < len(file_ids) and int(detail_data.get("usedAttachments", 0) or 0) < len(file_ids):
            raise ApiError("新版附件绑定后凭证回读未确认附件数量；不重试保存")
        result.update({"attachmentStatus": "uploaded_linked_and_verified", "attachmentFileIds": file_ids, "voucherReadbackAfterAttachment": detail_after_bind})
    return result


def archive(path: Path, target_dir: Path, receipt: Receipt, result: dict[str, Any]) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}.{datetime.now().strftime('%Y%m%d%H%M%S')}{path.suffix}"
    for attachment in receipt.attachment_files:
        destination = target_dir / attachment.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(attachment.path, destination)
    shutil.move(str(path), target)
    (target_dir / f"{target.stem}.result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
