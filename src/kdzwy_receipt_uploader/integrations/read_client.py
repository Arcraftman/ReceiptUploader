"""Shared read-only accounting transport and request parameter helpers.

The explicit allowlist is independent of API discovery and test fixtures.
"""
from __future__ import annotations

import calendar
import json
import re
import ssl
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from ..api import KdzwyApi
from ..company_registry import normalize_month
from ..item_class import AUXILIARY_ITEM_CLASSES

GET_PATHS = frozenset({
    '/basedata/initParams', '/jdy-fi-bd/{dbId}/v1/account/',
    *('/jdy-fi/{dbId}/' + suffix for suffix in (
        'bs/v1/account-class', 'bs/v1/vch-group', 'bs/v1/currency',
        'bs/v1/dept', 'bs/v1/employee', 'gl/v1/itemClass', 'gl/v1/item/page',
        'gl/v1/voucher/list', 'gl/v1/voucherTotal/list', 'rpt/v1/balance',
        'rpt/v1/profit', 'rpt/v1/cashflow', 'fa/v1/type', 'fa/v1/change-record',
        'fa/v1/report/depreciation-detail', 'fa/v1/report/depreciation-sum',
        'vat/v1/tax-burden', 'ca/v1/cashier-account', 'att/v1/file/urls',
    )),
})

POST_PATHS = frozenset({
    *('/jdy-fi-rpt/{dbId}/' + suffix for suffix in (
        'v1/gl/general/query-total', 'v1/gl/balance-report',
        'v1/balance-item-report/query', 'v1/qtyTotalAccount/detail',
        'v1/cost-detail/list',
    )),
    *('/jdy-fi/{dbId}/' + suffix for suffix in (
        'fa/v1/card/list', 'pay/v1/sheet-list', 'pay/v1/sheet/statMonthPay',
        'vat/v1/invoice-list',
    )),
})

class CheckFailure(Exception):
    """Messages must describe contracts, not include response payloads."""

class IdentityMismatch(CheckFailure):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def read_envelope(payload: Any, http_status: int = 200) -> Any:
    if http_status != 200:
        raise CheckFailure(f'HTTP {http_status}')
    if not isinstance(payload, dict):
        raise CheckFailure('JSON 顶层不是对象')
    keys = ('status', 'code', 'errcode', 'errorcode', 'success', 'ok')
    statuses = {k: payload[k] for k in keys if k in payload}
    if not statuses:
        raise CheckFailure('缺少明确业务状态，不能仅凭 HTTP 200 判断成功')
    for key, value in statuses.items():
        if key in ('success', 'ok'):
            valid = value is True
        else:
            allowed = (0, '0') if key in ('errcode', 'errorcode') else (0, '0', 200, '200')
            valid = not isinstance(value, bool) and value in allowed
        if not valid:
            raise CheckFailure(f'业务状态 {key} 非成功值')
    if 'data' not in payload:
        raise CheckFailure('响应缺少 data')
    return payload['data']

def check_identity(value: Any, company_id: str, dbid: str) -> None:
    if not isinstance(value, dict) or str(value.get('companyId')) != company_id or str(value.get('DBID')) != dbid:
        raise IdentityMismatch('远端 companyId / DBID 与选定账套不一致，停止后续查询')

def validate_request(method: str, path: str, query: dict, body: Any, dbid: str) -> None:
    if not re.fullmatch(r'\d+', dbid):
        raise CheckFailure('会话 DBID 格式无效')
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '%' in path or '..' in path:
        raise CheckFailure('仅允许精确的本账套路径，不允许 URL、编码路径或内嵌查询参数')
    template = path.replace('/' + dbid + '/', '/{dbId}/', 1)
    voucher_detail = bool(re.fullmatch(r'/jdy-fi/' + re.escape(dbid) + r'/gl/v1/voucher/\d+', path))
    if method == 'GET':
        allowed = template in GET_PATHS or voucher_detail
        if body is not None:
            allowed = False
    elif method == 'POST':
        allowed = template in POST_PATHS and isinstance(body, dict)
    else:
        allowed = False
    if not allowed:
        raise CheckFailure('请求不在独立只读白名单中')
    if path == '/basedata/initParams':
        if query != {'m': 'getSystemParams'}:
            raise CheckFailure('系统参数接口只允许 getSystemParams')
    elif any(k.lower() in ('m', 'method', 'action', '_method') for k in query):
        raise CheckFailure('不允许通过参数切换操作')

class ReadClient:
    def __init__(self, session_file: Path, company_id: str, company_name: str, timeout: int = 25):
        origin, cookies, dbid, token, name, cid = KdzwyApi._load_session(session_file)
        if origin != 'https://vip4-kj.kdzwy.com':
            raise CheckFailure('本测试仅面向 vip4-kj.kdzwy.com')
        if cid != company_id or name != company_name or not dbid or not token:
            raise CheckFailure('会话身份与选定账套不一致或缺少 DBID / token')
        self.dbid = dbid
        self.company_id = company_id
        self.origin = origin
        self.timeout = timeout
        self.headers = {
            'Cookie': cookies, 'app-token': token,
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': origin + '/accounting/index.html',
            'User-Agent': 'KdzwyReadContractTests/1.0',
        }
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        self.last_response: dict[str, Any] = {}

    def request(self, method: str, path: str, query: dict | None = None, body: dict | None = None) -> Any:
        query = query or {}
        self.last_response = {}
        validate_request(method, path, query, body, self.dbid)
        url = self.origin + path + ('?' + urlencode(query, doseq=True) if query else '')
        request = Request(url, method=method, headers=self.headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(10 * 1024 * 1024 + 1)
        except HTTPError as exc:
            self.last_response = {'http_status': exc.code}
            raise CheckFailure(f'HTTP {exc.code}；不跟随登录重定向，也不保存错误正文') from None
        except (URLError, TimeoutError, OSError):
            raise CheckFailure('网络请求失败或超时') from None
        if len(raw) > 10 * 1024 * 1024:
            raise CheckFailure('响应超过 10 MiB，停止读取')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise CheckFailure('响应不是有效 JSON，可能需要刷新登录') from None
        # Do not persist arbitrary server error messages or values.
        self.last_response = {'http_status': status}
        if isinstance(payload, dict):
            self.last_response['business_status'] = {
                k: v if isinstance(v, (int, bool)) or v in ('0', '200') else 'non_success'
                for k, v in payload.items() if k in ('status', 'code', 'errcode', 'errorcode', 'success', 'ok')
            }
        return read_envelope(payload, status)

def month_values(month: str) -> dict:
    month = normalize_month(month)
    year, number = map(int, month.split('-'))
    start, end = month + '-01', f'{month}-{calendar.monthrange(year, number)[1]:02d}'
    period = month.replace('-', '')
    return {
        'period': period, 'periodInt': int(period),
        'dateFrom': start, 'dateTo': end,
        'dateFromInt': int(start.replace('-', '')), 'dateToInt': int(end.replace('-', '')),
        # These are built-in category codes, never remote customer/supplier IDs.
        'customerClassId': AUXILIARY_ITEM_CLASSES['客户'],
        'supplierClassId': AUXILIARY_ITEM_CLASSES['供应商'],
    }

def substitute(value: Any, variables: dict) -> Any:
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, str) and re.fullmatch(r'\{\w+\}', value):
        return variables[value[1:-1]]
    return value
