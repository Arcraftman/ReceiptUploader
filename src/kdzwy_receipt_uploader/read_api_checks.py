"""Opt-in contract checks for reviewed, read-only accounting interfaces.

No crawler output is automatically executed. Reports contain schemas/counts,
never business rows, cookies, tokens or signed attachment URLs.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .company_registry import load_accountbooks, normalize_month
from .integrations.read_client import (
    GET_PATHS,
    POST_PATHS,
    CheckFailure,
    IdentityMismatch,
    NoRedirect,
    ReadClient,
    check_identity,
    month_values,
    read_envelope,
    substitute,
    validate_request,
)

DEPENDENT_IDS = ('voucher_pagination', 'voucher_detail', 'attachment_urls')


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    cases = payload['cases']
    if payload.get('version') != 1 or len({r['id'] for r in cases}) != len(cases):
        raise CheckFailure('测试清单版本或用例 ID 无效')
    return cases


def validate_shape(value: Any, case: dict) -> dict:
    expected = {'dict': dict, 'list': list}[case['data_type']]
    if not isinstance(value, expected):
        raise CheckFailure(f'data 类型错误，期望 {case["data_type"]}')
    if isinstance(value, list):
        if any(not isinstance(row, dict) for row in value):
            raise CheckFailure('data 列表包含非对象行')
        return {'data_type': 'list', 'row_counts': {'data': len(value)}, 'empty': not value}
    missing = set(case['required_keys']) - value.keys()
    if missing:
        raise CheckFailure('data 缺少约定字段：' + ', '.join(sorted(missing)))
    counts = {}
    groups = {}
    for field in case['list_fields']:
        rows = value[field]
        zero_field = case.get('nullable_lists', {}).get(field)
        if rows is None and zero_field and type(value.get(zero_field)) is int and value[zero_field] == 0:
            counts[field] = 0
            continue
        if not isinstance(rows, list):
            raise CheckFailure(f'data.{field} 不是列表')
        if case.get('row_layouts', {}).get(field) == 'groups':
            if any(not isinstance(group, list) for group in rows):
                raise CheckFailure(f'data.{field} 不是分组列表')
            groups[field] = len(rows)
            rows = [row for group in rows for row in group]
        if any(not isinstance(row, dict) for row in rows):
            raise CheckFailure(f'data.{field} 包含非对象行')
        counts[field] = len(rows)
    totals = {}
    for field in ('records', 'totalPage', 'totalCount', 'page'):
        if field in value:
            raw = value[field]
            if isinstance(raw, bool) or not re.fullmatch(r'\d+', str(raw)):
                raise CheckFailure(f'data.{field} 不是非负整数')
            totals[field] = int(raw)
    return {'data_type': 'dict', 'data_keys': sorted(value), 'row_counts': counts, 'group_counts': groups,
            'totals': totals, 'empty': not any(counts.values()) if counts else None}


def voucher_rows(value: dict, period: str, page_size: int = 20) -> set[str]:
    rows = value['rows']
    if len(rows) > page_size:
        raise CheckFailure('凭证列表超过请求 pageSize')
    if int(value.get('page', 1)) == 1 and len(rows) != min(page_size, int(value['records'])):
        raise CheckFailure('凭证第 1 页行数与总数不一致')
    ids = {str(row.get('id') or '') for row in rows}
    if '' in ids or len(ids) != len(rows):
        raise CheckFailure('凭证列表缺少 ID 或 ID 重复')
    if any(str(row.get('yearPeriod')) != period for row in rows):
        raise CheckFailure('凭证列表包含指定月份以外的数据')
    return ids


def verify_pagination(first: dict, second: dict, period: str) -> dict:
    ids1, ids2 = voucher_rows(first, period), voucher_rows(second, period)
    if ids1 & ids2:
        raise CheckFailure('凭证第 1/2 页存在重复 ID')
    if int(first['records']) != int(second['records']):
        raise CheckFailure('分页期间总数变化，无法断言本次分页一致')
    expected = min(20, max(0, int(first['records']) - 20))
    if len(ids2) != expected or int(second['page']) != 2:
        raise CheckFailure('第 2 页行数或页码与总数不一致')
    return {'first_page_rows': len(ids1), 'second_page_rows': len(ids2), 'disjoint_ids': True}


def verify_voucher_detail(value: Any, selected: dict, dbid: str, period: str) -> dict:
    if not isinstance(value, dict) or str(value.get('id')) != str(selected['id']):
        raise CheckFailure('凭证详情 ID 与列表选择不一致')
    if str(value.get('dbId')) not in ('0', dbid):
        raise IdentityMismatch('凭证详情返回了其他账套的 dbId')
    if str(value.get('yearPeriod')) != period:
        raise CheckFailure('凭证详情月份不一致')
    entries = value.get('entries')
    if not isinstance(entries, list) or len(entries) < 2:
        raise CheckFailure('凭证详情缺少有效分录列表')
    try:
        debit = sum((Decimal(str(e['amount'])) for e in entries if e['dc'] == 1), Decimal(0))
        credit = sum((Decimal(str(e['amount'])) for e in entries if e['dc'] == -1), Decimal(0))
        if any(e['dc'] not in (1, -1) or not e.get('accountId') for e in entries):
            raise CheckFailure('凭证分录缺少科目 ID 或借贷方向无效')
        values = [debit, credit, Decimal(str(value['debitTotal'])), Decimal(str(value['creditTotal']))]
        if any(not x.is_finite() for x in values) or len(set(values)) != 1:
            raise CheckFailure('凭证借贷合计与明细分录不一致')
    except (KeyError, TypeError, InvalidOperation):
        raise CheckFailure('凭证分录金额或合计结构无效') from None
    return {'entries': len(entries), 'id_matches': True, 'period_matches': True, 'balanced': True,
            'response_dbid_placeholder': str(value.get('dbId')) == '0'}


def check_attachment_links(value: Any, count: int) -> dict:
    # The read API returns either a list directly or an object containing urls.
    rows = value if isinstance(value, list) else value.get('urls') if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != count:
        raise CheckFailure('附件链接返回数量/结构不符合请求')
    for row in rows:
        url = row.get('url') if isinstance(row, dict) else row
        if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname:
            raise CheckFailure('附件未返回有效 HTTPS 链接')
    return {'url_count': len(rows), 'https_only': True, 'downloaded': False}


def run_checks(client: ReadClient, cases: list[dict], month: str, progress: Callable | None = None) -> list[dict]:
    values = month_values(month)
    results: list[dict] = []
    responses: dict[str, Any] = {}

    def record(id, label, method, endpoint, action):
        start = time.monotonic()
        client.last_response = {}
        result = {'id': id, 'label': label, 'method': method, 'endpoint': endpoint}
        try:
            result.update(action())
            result.setdefault('status', 'passed')
        except CheckFailure as exc:
            result.update(status='failed', reason=str(exc))
            if isinstance(exc, IdentityMismatch):
                result['abort_remaining'] = True
        except Exception as exc:
            result.update(status='failed', reason='测试执行异常：' + type(exc).__name__)
        result.update(client.last_response)
        result['elapsed_ms'] = round((time.monotonic() - start) * 1000)
        results.append(result)
        if progress:
            progress(result)
        return result

    def identity():
        value = client.request('GET', '/basedata/initParams', {'m': 'getSystemParams'})
        check_identity(value, client.company_id, client.dbid)
        return {'identity_matches': True}

    first = record('identity_before', '查询前账套身份', 'GET', '/basedata/initParams?m=getSystemParams', identity)
    if first['status'] != 'passed':
        for case in [*cases, *({'id': k, 'label': k} for k in DEPENDENT_IDS), {'id': 'identity_after', 'label': '查询后账套身份'}]:
            results.append({'id': case['id'], 'label': case['label'], 'status': 'blocked', 'reason': '身份预检未通过，未发送请求'})
        return results

    for index, case in enumerate(cases):
        def execute(case=case):
            path = case['path'].replace('{dbId}', client.dbid)
            value = client.request(case['method'], path, substitute(case.get('query', {}), values), substitute(case.get('body'), values))
            shape = validate_shape(value, case)
            if isinstance(value, dict) and value.get('dbId') is not None and str(value['dbId']) != client.dbid:
                if case['id'] == 'salary_stats' and str(value['dbId']) == '0':
                    shape['response_dbid_placeholder'] = True
                else:
                    raise IdentityMismatch('响应中的 dbId 与当前账套不一致')
            if case['id'] == 'voucher_list':
                voucher_rows(value, values['period'])
            responses[case['id']] = value
            return shape
        result = record(case['id'], case['label'], case['method'], case['path'], execute)
        if result.get('abort_remaining'):
            remaining = [(c['id'], c['label']) for c in cases[index + 1:]]
            remaining += [(k, k) for k in (*DEPENDENT_IDS, 'identity_after')]
            results.extend({'id': k, 'label': label, 'status': 'blocked', 'reason': '检测到身份冲突，未发送后续请求'} for k, label in remaining)
            return results

    voucher_case = next(c for c in cases if c['id'] == 'voucher_list')
    first_page = responses.get('voucher_list')
    sampled = list(first_page['rows']) if first_page else []

    def pagination():
        if first_page is None:
            return {'status': 'blocked', 'reason': '凭证列表测试失败，未发送第 2 页请求'}
        if int(first_page['records']) <= 20:
            return {'status': 'skipped', 'reason': '本月不足两页凭证，不能验证跨页一致性'}
        query = substitute(voucher_case['query'], values)
        query['page'] = 2
        second = client.request('GET', voucher_case['path'].replace('{dbId}', client.dbid), query)
        validate_shape(second, voucher_case)
        detail = verify_pagination(first_page, second, values['period'])
        sampled.extend(second['rows'])
        return detail
    record('voucher_pagination', '凭证跨页一致性', 'GET', voucher_case['path'], pagination)

    def detail():
        if not sampled:
            return {'status': 'skipped' if first_page is not None else 'blocked', 'reason': '没有可用的本月凭证列表样本'}
        selected = next((row for row in sampled if row.get('fileIds')), sampled[0])
        value = client.request('GET', f'/jdy-fi/{client.dbid}/gl/v1/voucher/{selected["id"]}')
        checked = verify_voucher_detail(value, selected, client.dbid, values['period'])
        responses['voucher_detail'] = value
        return checked
    detail_result = record('voucher_detail', '凭证详情与借贷一致性', 'GET', '/jdy-fi/{dbId}/gl/v1/voucher/{voucherId}', detail)
    if detail_result.get('abort_remaining'):
        results.extend({'id': k, 'label': k, 'status': 'blocked', 'reason': '检测到身份冲突，未发送后续请求'} for k in ('attachment_urls', 'identity_after'))
        return results

    def attachments():
        if 'voucher_detail' not in responses:
            return {'status': 'blocked' if sampled else 'skipped', 'reason': '没有验证通过的凭证详情'}
        ids = responses['voucher_detail'].get('fileIds')
        if not ids:
            return {'status': 'skipped', 'reason': '前两页及所选详情无 fileIds；纸质附件张数不代表已上传电子附件'}
        if not isinstance(ids, list) or any(not re.fullmatch(r'\d+', str(i)) for i in ids):
            raise CheckFailure('远端附件 ID 列表格式无效')
        selected = ids[:3]
        value = client.request('GET', f'/jdy-fi/{client.dbid}/att/v1/file/urls', {'ids': ','.join(map(str, selected)), 'dl': 'false'})
        return check_attachment_links(value, len(selected))
    record('attachment_urls', '电子附件预览链接', 'GET', '/jdy-fi/{dbId}/att/v1/file/urls', attachments)
    record('identity_after', '查询后账套身份', 'GET', '/basedata/initParams?m=getSystemParams', identity)
    return results


def run_company_checks(root: Path, company_key: str, month: str, progress: Callable | None = None) -> dict:
    normalize_month(month)
    books = load_accountbooks(root / 'runtime/registry/accountbooks.json')
    if company_key not in books or not books[company_key].enabled:
        raise CheckFailure('需要注册表中明确的、已启用的 company_<id>')
    book = books[company_key]
    cases = load_cases(root / 'tests/fixtures/read_api_cases.json')
    started = datetime.now(timezone.utc).isoformat()
    try:
        client = ReadClient(root / book.session_file, book.company_id, book.name)
        results = run_checks(client, cases, month, progress)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, CheckFailure) else '无法加载会话：' + type(exc).__name__
        results = [{'id': 'identity_before', 'label': '查询前账套身份', 'status': 'failed', 'reason': reason}]
        results.extend({'id': id, 'label': label, 'status': 'blocked', 'reason': '会话初始化失败，未发送请求'}
                       for id, label in [(c['id'], c['label']) for c in cases] + [(k, k) for k in (*DEPENDENT_IDS, 'identity_after')])
    return {'version': 1, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'company_key': company_key, 'company_name': book.name, 'month': month,
            'summary': dict(Counter(r['status'] for r in results)), 'results': results}


def write_report(report: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 主要读取接口集成测试', '',
             f'公司：{report["company_name"]}（{report["company_key"]}）；月份：{report["month"]}。',
             f'执行时间（UTC）：{report["started_at"]}。', '',
             '结果：' + '，'.join(f'{k}={v}' for k, v in report['summary'].items()) + '。', '',
             '只执行独立白名单内的读取请求；空列表表示接口可调用但未覆盖非空业务数据。跳过项不算通过。',
             '业务数据仅在内存中校验，报告不保存明细、金额、登录凭据或附件地址。', '',
             '| 测试 | 状态 | 返回行数 / 检查结果 |', '|---|---|---|']
    for result in report['results']:
        note = result.get('reason') or (json.dumps(result['row_counts'], ensure_ascii=False) if result.get('row_counts') else '结构/状态检查通过')
        if result.get('totals'):
            note += '；' + json.dumps(result['totals'], ensure_ascii=False)
        if result['id'] == 'voucher_pagination' and result['status'] == 'passed':
            note = f'第 1 页 {result["first_page_rows"]} 条，第 2 页 {result["second_page_rows"]} 条，ID 不重叠'
        if result['id'] == 'voucher_detail' and result['status'] == 'passed':
            note = f'{result["entries"]} 条分录；ID、月份与借贷合计一致'
        if result.get('response_dbid_placeholder'):
            note += '；返回 dbId=0 占位，账套由路径和前后身份检查锁定'
        lines.append(f'| {result["label"]} | {result["status"]} | {note} |')
    (directory / 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
