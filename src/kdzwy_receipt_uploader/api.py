from __future__ import annotations

import copy
import hashlib
import json
import ssl
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .config import AppConfig
from .models import ApiError, AttachmentFile


class KdzwyApi:
    _shared_read_cache: dict[tuple[Any, ...], Any] = {}

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.origin, self.cookies, session_dbid, self.access_token, self.session_company = self._load_session(config.cookie_file)
        if config.expected_company and self.session_company != config.expected_company:
            raise ApiError(
                f"账簿会话公司不匹配：期望 {config.expected_company}，实际 {self.session_company or '未标明公司'}"
            )
        self.dbid = session_dbid
        self.accounting_origin = config.accounting_origin
        session_digest = hashlib.sha256(self.cookies.encode("utf-8")).hexdigest()
        self._cache_scope = (self.origin, self.accounting_origin, self.dbid, self.session_company, session_digest)

    def _cached_read(self, key: tuple[Any, ...], loader) -> Any:
        scoped_key = (self._cache_scope, *key)
        if scoped_key not in self._shared_read_cache:
            self._shared_read_cache[scoped_key] = copy.deepcopy(loader())
        return copy.deepcopy(self._shared_read_cache[scoped_key])

    def _invalidate_cached_reads(self, category: str) -> None:
        stale = [
            key for key in self._shared_read_cache
            if len(key) > 1 and key[0] == self._cache_scope and key[1] == category
        ]
        for key in stale:
            self._shared_read_cache.pop(key, None)

    @staticmethod
    def _load_session(path: Path) -> tuple[str, str, str | None, str | None, str | None]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError(f"无法读取账簿会话文件：{exc}") from exc
        target_url = payload.get("target_url")
        cookies = payload.get("cookies")
        if not isinstance(target_url, str) or not isinstance(cookies, list):
            raise ApiError("账簿会话文件结构无效")
        parsed = urlparse(target_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ApiError("账簿会话文件不是有效 HTTPS 地址")
        host = parsed.hostname
        path = parsed.path or "/"
        pairs: list[str] = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name, value = cookie.get("name"), cookie.get("value")
            domain = str(cookie.get("domain") or "").lstrip(".")
            cookie_path = str(cookie.get("path") or "/")
            if not name or value is None or not domain:
                continue
            if (host == domain or host.endswith("." + domain)) and path.startswith(cookie_path):
                pairs.append(f"{name}={value}")
        if not pairs:
            raise ApiError("账簿域没有可用 Cookie")
        return (
            f"{parsed.scheme}://{parsed.netloc}",
            "; ".join(pairs),
            parse_qs(parsed.query).get("dbId", parse_qs(parsed.query).get("dbid", [None]))[0],
            payload.get("access_token") if isinstance(payload.get("access_token"), str) else None,
            payload.get("company_name") if isinstance(payload.get("company_name"), str) else None,
        )

    def _request(self, method: str, endpoint: str, body: bytes | None, headers: dict[str, str], timeout: int) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Cookie": self.cookies,
            "Origin": self.origin,
            "Referer": self.origin.rstrip("/") + "/default.jsp",
            "User-Agent": self.config.user_agent,
            "X-Requested-With": "XMLHttpRequest",
            **headers,
        }
        if self.access_token:
            request_headers.setdefault("app-token", self.access_token)
        new_v1_prefixes = ("/jdy-fi/", "/jdy-fi-rpt/", "/basedata/", "/gl/", "/bs/")
        base = self.accounting_origin if endpoint.startswith(new_v1_prefixes) else self.origin
        request = Request(base.rstrip("/") + endpoint, data=body, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                raw = response.read().decode("utf-8", errors="replace")
                http_status = response.status
        except HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")[:2000]
            raise ApiError(f"{endpoint} 返回 HTTP {exc.code}：{response_text}") from exc
        except URLError as exc:
            raise ApiError(f"{endpoint} 访问失败：{exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"{endpoint} 未返回 JSON，登录态可能失效") from exc
        if not isinstance(payload, dict):
            raise ApiError(f"{endpoint} 返回结构异常")
        payload["_httpStatus"] = http_status
        return payload

    @staticmethod
    def data(endpoint: str, payload: dict[str, Any]) -> Any:
        status = payload.get("status", payload.get("code"))
        errcode = payload.get("errcode")
        if payload.get("success") is False or payload.get("ok") is False:
            raise ApiError(f"{endpoint} 业务失败：{payload.get('description') or payload.get('msg') or payload.get('message') or '未知错误'}")
        if errcode not in (None, 0, "0"):
            raise ApiError(f"{endpoint} 业务失败：{payload.get('msg') or payload.get('message') or '未知错误'} (errcode={errcode})")
        if status not in (None, 0, "0", 200, "200"):
            raise ApiError(f"{endpoint} 业务失败：{payload.get('msg') or payload.get('message') or '未知错误'} (status={status})")
        return payload.get("data")

    @staticmethod
    def unwrap_data(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = KdzwyApi.data(endpoint, payload)
        if isinstance(data, dict):
            return data
        if isinstance(payload, dict) and any(key in payload for key in ("id", "vchNum", "year", "period", "items")):
            return payload
        raise ApiError(f"{endpoint} 返回 data 不是对象")

    def post_form(self, endpoint: str, form: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            endpoint,
            urlencode({key: str(value) for key, value in form.items()}).encode("utf-8"),
            {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            self.config.timeout_seconds,
        )

    def post_json(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            endpoint,
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            {"Content-Type": "application/json; charset=UTF-8"},
            self.config.timeout_seconds,
        )

    def get_json(self, endpoint: str) -> dict[str, Any]:
        return self._request("GET", endpoint, None, {}, self.config.timeout_seconds)

    def get_current_user_context(self) -> dict[str, str]:
        from .user_context import resolve_current_user
        return resolve_current_user(self)

    def get_text(self, endpoint: str) -> str:
        request_headers = {
            "Accept": "text/html, application/xhtml+xml, */*",
            "Cookie": self.cookies,
            "Origin": self.origin,
            "Referer": self.origin.rstrip("/") + "/default.jsp",
            "User-Agent": self.config.user_agent,
        }
        request = Request(self.origin.rstrip("/") + endpoint, method="GET", headers=request_headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds, context=ssl.create_default_context()) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise ApiError(f"{endpoint} 返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ApiError(f"{endpoint} 访问失败：{exc.reason}") from exc

    def upload_pdf(self, endpoint: str, files: list[AttachmentFile]) -> dict[str, Any]:
        boundary = "----KdzwyReceiptUploader" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for item in files:
            filename = item.path.name.replace('"', "")
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
                b"Content-Type: application/pdf\r\n\r\n",
                item.path.read_bytes(),
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        return self._request(
            "POST",
            endpoint,
            b"".join(chunks),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            self.config.upload_timeout_seconds,
        )

    def get_dynamic_system_params(self) -> dict[str, Any]:
        endpoint = "/basedata/initParams?m=getSystemParams"
        result = self._cached_read(
            ("system-params",),
            lambda: self.unwrap_data(endpoint, self.get_json(endpoint)),
        )
        dynamic_dbid = result.get("DBID") or result.get("dbId")
        if dynamic_dbid:
            self.dbid = str(dynamic_dbid)
        return result

    def get_voucher_words(self) -> list[dict[str, Any]]:
        endpoint = "/gl/generatecode?m=findAll"
        data = self.data(endpoint, self.get_json(endpoint))
        return data if isinstance(data, list) else list((data or {}).get("items", [])) if isinstance(data, dict) else []

    def get_used_voucher_numbers(self) -> dict[str, Any]:
        endpoint = "/gl/voucher?m=findUsedVchNO"
        return self.get_json(endpoint)

    def get_voucher_number(self, date_value: str, group_id: str, voucher_id: str = "") -> dict[str, Any]:
        endpoint = f"/gl/voucher?m=getvchNum&groupId={quote(str(group_id))}&vchdate={quote(date_value)}&vchId={quote(voucher_id)}"
        return self.unwrap_data(endpoint, self.get_json(endpoint))

    def get_account_classes(self) -> list[dict[str, Any]]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/bs/v1/account-class"
        data = self.unwrap_data(endpoint, self.get_json(endpoint))
        return list(data.get("rows", [])) if isinstance(data, dict) else []

    def get_subject_tree(self, effective: int = 0, expand: bool = True) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi-bd/{quote(str(dbid))}/v1/account/?effective={effective}&expand={'true' if expand else 'false'}"
        return self._cached_read(
            ("subject-tree", int(effective), bool(expand)),
            lambda: self.unwrap_data(endpoint, self.get_json(endpoint)),
        )

    def get_subjects_by_class(self, class_id: int, effective: int = 0, expand: bool = False) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi-bd/{quote(str(dbid))}/v1/account/?classId={int(class_id)}&effective={effective}&matchCon=&expand={'true' if expand else 'false'}"
        return self.unwrap_data(endpoint, self.get_json(endpoint))

    def get_voucher_groups_v1(self) -> list[dict[str, Any]]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/bs/v1/vch-group"
        def load() -> list[dict[str, Any]]:
            data = self.unwrap_data(endpoint, self.get_json(endpoint))
            return list(data.get("rows", [])) if isinstance(data, dict) else []
        return self._cached_read(("voucher-groups",), load)

    def get_currencies(self, year_period: int | str | None = None) -> list[dict[str, Any]]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        period = year_period or self.get_dynamic_system_params().get("CURPERIOD", "")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/bs/v1/currency?name=&yearPeriod={quote(str(period))}"
        data = self.unwrap_data(endpoint, self.get_json(endpoint))
        return list(data.get("rows", [])) if isinstance(data, dict) else []

    def get_item_classes(self, show_collection: bool = False) -> list[dict[str, Any]]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        suffix = "?isShowCollection=1" if show_collection else ""
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/itemClass{suffix}"
        data = self.unwrap_data(endpoint, self.get_json(endpoint))
        return list(data.get("rows", [])) if isinstance(data, dict) else []

    def get_items_v1(self, item_class_id: int, match_con: str = "", page: int = 1, page_size: int = 500) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/item/page?itemClassId={int(item_class_id)}&matchCon={quote(match_con)}&page={int(page)}&pageSize={int(page_size)}"
        return self._cached_read(
            ("items", int(item_class_id), str(match_con), int(page), int(page_size)),
            lambda: self.unwrap_data(endpoint, self.get_json(endpoint)),
        )

    def get_next_item_number_v1(self, item_class_id: int) -> str:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/item/nextNum?itemClassId={int(item_class_id)}"
        data = self.data(endpoint, self.get_json(endpoint))
        value = data.get("number") if isinstance(data, dict) else data
        if value in (None, ""):
            raise ApiError(f"新版辅助对象取号未返回编码：itemClassId={item_class_id}")
        return str(value)

    def create_item_v1(self, item_class_id: int, number: str, name: str, remark: str = "") -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/item"
        normalized_name = str(name).strip()
        data = self.unwrap_data(endpoint, self.post_json(endpoint, {"itemClassId": str(item_class_id), "number": str(number), "name": normalized_name, "remark": remark}))
        self._invalidate_cached_reads("items")
        if isinstance(data, dict):
            item_id = data.get("id") or data.get("itemId") or data.get("itemID") or data.get("item_id")
            if item_id not in (None, "", 0, "0"):
                normalized = dict(data)
                normalized["id"] = item_id
                normalized.setdefault("name", normalized_name)
                normalized.setdefault("number", str(number))
                return normalized

        # Some deployments acknowledge a successful create without returning
        # the new item id.  Resolve only an exact, unique remote match instead
        # of treating the ambiguous response as failure or creating it again.
        lookup = self.get_items_v1(item_class_id, match_con=normalized_name, page_size=500)
        exact_matches = [
            dict(row)
            for row in (lookup.get("rows", []) if isinstance(lookup, dict) else [])
            if isinstance(row, dict) and str(row.get("name", "")).strip() == normalized_name
        ]
        if len(exact_matches) == 1:
            resolved = exact_matches[0]
            item_id = resolved.get("id") or resolved.get("itemId") or resolved.get("itemID") or resolved.get("item_id")
            if item_id not in (None, "", 0, "0"):
                resolved["id"] = item_id
                resolved.setdefault("number", str(number))
                return resolved
        if len(exact_matches) > 1:
            raise ApiError(
                f"新版辅助对象创建后精确回查不唯一：itemClassId={item_class_id}, "
                f"name={normalized_name}, matches={len(exact_matches)}"
            )
        raise ApiError(
            f"新版辅助对象创建未返回有效 itemId，且精确回查未找到："
            f"itemClassId={item_class_id}, name={normalized_name}, "
            f"response={json.dumps(data, ensure_ascii=False)[:500]}"
        )

    def get_all_items_v1(self, class_ids: tuple[int, ...] = (1, 2, 3, 4, 5, 6), page_size: int = 500) -> dict[str, dict[str, Any]]:
        labels = {1: "客户", 2: "职员", 3: "项目", 4: "存货", 5: "供应商", 6: "部门"}
        result: dict[str, dict[str, Any]] = {}
        for class_id in class_ids:
            data = self.get_items_v1(class_id, page_size=page_size)
            result[labels.get(class_id, str(class_id))] = {"itemClassId": class_id, "items": list(data.get("rows", [])), "records": data.get("records", 0), "totalPage": data.get("totalPage", 0)}
        return result

    def get_voucher_settings(self) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/bs/v1/user-setting?key=voucherAddSettings_v4&saveUser=1"
        return self._cached_read(
            ("voucher-settings",),
            lambda: self.unwrap_data(endpoint, self.get_json(endpoint)),
        )

    def get_supplier_items(self, page_size: int = 100000) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/item/page?itemClassId=5&matchCon=&effective=1&page=1&pageSize={page_size}"
        return self.unwrap_data(endpoint, self.get_json(endpoint))

    def find_voucher_balance(self, body: dict[str, Any]) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/voucher/find-balance"
        return self.unwrap_data(endpoint, self.post_json(endpoint, body))

    def save_voucher_v1(self, payload: dict[str, Any]) -> str:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/voucher/save"
        data = self.unwrap_data(endpoint, self.post_json(endpoint, payload))
        voucher_id = data.get("id") or data.get("voucherId")
        if voucher_id in (None, "", 0, "0", -1, "-1"):
            raise ApiError(f"新版凭证保存未返回有效 voucherId：{json.dumps(data, ensure_ascii=False)[:2000]}")
        return str(voucher_id)

    def get_voucher_v1(self, voucher_id: Any) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/voucher/{quote(str(voucher_id))}"
        return self.unwrap_data(endpoint, self.get_json(endpoint))

    def upload_invoice_pdf_v1(self, file: AttachmentFile, opt_type: str = "1") -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi-rpt/{quote(str(dbid))}/v1/invoice/discern"
        boundary = "----KdzwyReceiptUploader" + uuid.uuid4().hex
        filename = file.path.name.replace('"', "")
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/pdf\r\n\r\n".encode(),
            file.path.read_bytes(),
            b"\r\n",
        ]
        for name, value in (("dbId", str(dbid)), ("isCompress", "false"), ("isUnzip", "true"), ("optType", opt_type)):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        parts.append(f"--{boundary}--\r\n".encode())
        return self._request("POST", endpoint, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}, self.config.upload_timeout_seconds)

    def bind_voucher_files_v1(self, voucher_id: Any, file_ids: list[str]) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/att/v1/file/bind-vch"
        return self.post_json(endpoint, {"ids": file_ids, "vchIds": [str(voucher_id)] * len(file_ids), "vchId": str(voucher_id), "optType": "1", "notIncrement": True})

    def get_voucher_file_urls_v1(self, file_ids: list[str], download: bool = False) -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/att/v1/file/urls?ids={quote(','.join(file_ids))}&dl={'true' if download else 'false'}"
        return self.get_json(endpoint)

    def get_voucher_v1_list(self, query: str = "page=1&pageSize=100") -> dict[str, Any]:
        dbid = self.dbid or self.get_dynamic_system_params().get("DBID")
        endpoint = f"/jdy-fi/{quote(str(dbid))}/gl/v1/voucher/list?{query}"
        return self.get_json(endpoint)

    # Legacy /gl/ebook helpers intentionally removed from the default client.
    # The active upload path is upload_invoice_pdf_v1 -> bind_voucher_files_v1.
