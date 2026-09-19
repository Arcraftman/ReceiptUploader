"""Offline regressions for the read-only integration test runner."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kdzwy_receipt_uploader.read_api_checks import (
    CheckFailure, IdentityMismatch, NoRedirect, ReadClient, check_identity,
    check_attachment_links, load_cases, month_values, read_envelope, run_checks,
    substitute, validate_request, validate_shape, verify_pagination,
    verify_voucher_detail, write_report,
)

CASES = load_cases(ROOT / 'tests/fixtures/read_api_cases.json')


class ReadAPIContractTests(unittest.TestCase):
    def test_command_exit_codes_preserve_failure_and_strict_skip(self):
        spec = importlib.util.spec_from_file_location('read_api_command', ROOT / 'src/kdzwy_receipt_uploader/commands/test_read_apis.py')
        command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(command)
        for summary, strict, expected in [({'passed': 33, 'skipped': 1}, False, 0),
                                          ({'passed': 33, 'skipped': 1}, True, 1),
                                          ({'failed': 1, 'blocked': 33}, False, 1)]:
            argv = ['test_read_apis.py', 'company_1', '2026-07'] + (['--strict-skips'] if strict else [])
            with patch.object(sys, 'argv', argv), patch.object(command, 'run_company_checks', return_value={'summary': summary}), patch.object(command, 'write_report'), patch('builtins.print'):
                self.assertEqual(command.main(), expected)

    def test_business_failure_is_not_http_success(self):
        for payload in ({'errcode': 1, 'data': {}}, {'errorcode': 1, 'data': {}},
                        {'errcode': False, 'data': {}}, {'code': True, 'data': {}},
                        {'success': False, 'data': {}}, {'data': {}},
                        {'errcode': 0, 'status': 500, 'data': {}}, []):
            with self.subTest(payload=payload), self.assertRaises(CheckFailure):
                read_envelope(payload)
        self.assertEqual(read_envelope({'errcode': 0, 'data': []}), [])
        self.assertEqual(read_envelope({'code': '0', 'success': True, 'data': {}}), {})
        with self.assertRaises(CheckFailure):
            read_envelope({'errcode': 0, 'data': {}}, 302)

    def test_fixed_allowlist_rejects_writes_and_other_tenants(self):
        for method, path, query, body in [
            ('GET', '/jdy-fi/123/gl/v1/itemClass/delete', {}, None),
            ('POST', '/jdy-fi/123/gl/v1/voucher/save', {}, {}),
            ('GET', '/jdy-fi/456/gl/v1/voucher/list', {}, None),
            ('GET', 'https://other.example/jdy-fi/123/gl/v1/voucher/list', {}, None),
            ('GET', '/basedata/initParams', {'m': 'updateCommonFunctions'}, None),
            ('GET', '/jdy-fi/123/gl/v1/voucher/list', {'m': 'delete'}, None),
            ('GET', '/jdy-fi/123/gl/v1/voucher/../save', {}, None),
            ('GET', '/jdy-fi/123/gl/v1/voucher/list?m=delete', {}, None),
            ('DELETE', '/jdy-fi/123/gl/v1/voucher/1', {}, None),
        ]:
            with self.subTest(path=path), self.assertRaises(CheckFailure):
                validate_request(method, path, query, body, '123')
        validate_request('GET', '/jdy-fi/123/gl/v1/voucher/999', {}, None, '123')
        validate_request('POST', '/jdy-fi/123/fa/v1/card/list', {}, {}, '123')

    def test_every_fixture_is_in_independent_allowlist(self):
        variables = month_values('2026-07')
        for case in CASES:
            with self.subTest(case=case['id']):
                validate_request(case['method'], case['path'].replace('{dbId}', '123'),
                                 substitute(case['query'], variables),
                                 substitute(case.get('body'), variables), '123')

    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.example'))

    def test_request_policy_runs_before_network(self):
        with patch.object(ReadClient, '__init__', return_value=None):
            client = ReadClient(None, '', '')
        client.dbid = '123'
        client.opener = Mock()
        with self.assertRaises(CheckFailure):
            client.request('POST', '/jdy-fi/123/gl/v1/voucher/save', body={})
        client.opener.open.assert_not_called()

    def test_month_substitution_handles_leap_year_and_types(self):
        variables = month_values('2024-02')
        self.assertEqual(variables['dateTo'], '2024-02-29')
        self.assertEqual(substitute({'from': '{period}', 'to': '{dateToInt}'}, variables),
                         {'from': '202402', 'to': 20240229})
        self.assertEqual(month_values('2026-07')['supplierClassId'], 5)

    def test_identity_requires_both_company_and_dbid(self):
        check_identity({'companyId': 1, 'DBID': '123'}, '1', '123')
        for data in ({'companyId': 2, 'DBID': '123'}, {'companyId': 1, 'DBID': '456'}, {'companyId': 1}):
            with self.assertRaises(IdentityMismatch):
                check_identity(data, '1', '123')

    def test_wrong_identity_prevents_all_business_calls(self):
        client = Mock(dbid='123', company_id='1', last_response={})
        client.request.return_value = {'companyId': '2', 'DBID': '123'}
        result = run_checks(client, CASES, '2026-07')
        client.request.assert_called_once_with('GET', '/basedata/initParams', {'m': 'getSystemParams'})
        self.assertEqual(result[0]['status'], 'failed')
        self.assertTrue(all(r['status'] == 'blocked' for r in result[1:]))
        self.assertEqual(len(result), len(CASES) + 5)

    def test_later_identity_conflict_stops_remaining_calls(self):
        case = copy.deepcopy(next(c for c in CASES if c['id'] == 'salary_stats'))
        value = {'dbId': '456', 'yearPeriod': 202607, 'items': [], 'colName': []}
        client = Mock(dbid='123', company_id='1', last_response={})
        client.request.side_effect = [{'companyId': '1', 'DBID': '123'}, value]
        result = run_checks(client, [case, CASES[0]], '2026-07')
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(result[1]['status'], 'failed')
        self.assertTrue(all(r['status'] == 'blocked' for r in result[2:]))

    def test_grouped_general_ledger_is_a_real_contract(self):
        case = next(c for c in CASES if c['id'] == 'general_ledger')
        value = {'item': [[{'id': 'one'}, {'id': 'two'}], [{'id': 'three'}]], 'records': 3}
        result = validate_shape(value, case)
        self.assertEqual(result['row_counts'], {'item': 3})
        self.assertEqual(result['group_counts'], {'item': 2})
        with self.assertRaises(CheckFailure):
            validate_shape({'item': [['unexpected text']], 'records': 1}, case)

    def test_null_rows_only_allowed_for_zero_sized_quantity_ledger(self):
        case = next(c for c in CASES if c['id'] == 'quantity_ledger')
        self.assertTrue(validate_shape({'rows': None, 'size': 0}, case)['empty'])
        for size in (1, False, None):
            with self.assertRaises(CheckFailure):
                validate_shape({'rows': None, 'size': size}, case)

    def test_other_list_contracts_do_not_accept_null(self):
        case = {'data_type': 'dict', 'required_keys': ['rows'], 'list_fields': ['rows']}
        for value in ({}, {'rows': None}, {'rows': ['text']}):
            with self.assertRaises(CheckFailure):
                validate_shape(value, case)
        self.assertTrue(validate_shape({'rows': []}, case)['empty'])

    def test_pagination_detects_duplicates_period_and_count_drift(self):
        def page(n, ids):
            return {'rows': [{'id': str(i), 'yearPeriod': 202607} for i in ids], 'records': 21, 'page': n}
        first, second = page(1, range(1, 21)), page(2, [21])
        self.assertTrue(verify_pagination(first, second, '202607')['disjoint_ids'])
        for broken in (page(2, [1]), dict(second, records=22), page(1, [21]),
                       dict(second, rows=[{'id': '21', 'yearPeriod': 202608}])):
            with self.assertRaises(CheckFailure):
                verify_pagination(first, broken, '202607')

    def test_detail_balance_identity_and_zero_placeholder(self):
        value = {'id': '99', 'dbId': '0', 'yearPeriod': 202607,
                 'entries': [{'dc': 1, 'amount': '12.30', 'accountId': '11'},
                             {'dc': -1, 'amount': '12.30', 'accountId': '22'}],
                 'debitTotal': '12.30', 'creditTotal': '12.30'}
        result = verify_voucher_detail(value, {'id': '99'}, '123', '202607')
        self.assertTrue(result['balanced'])
        self.assertTrue(result['response_dbid_placeholder'])
        for change in ({'dbId': '456'}, {'id': '98'}, {'creditTotal': '12.29'},
                       {'yearPeriod': 202608}, {'debitTotal': 'NaN'}):
            with self.subTest(change=change), self.assertRaises(CheckFailure):
                verify_voucher_detail(dict(value, **change), {'id': '99'}, '123', '202607')

    def test_attachment_urls_checked_but_not_returned_in_report(self):
        result = check_attachment_links([{'url': 'https://files.example/a?token=secret'}], 1)
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(result['downloaded'])
        for value in ([], [{'url': 'http://files.example/a'}], [{'url': ''}]):
            with self.assertRaises(CheckFailure):
                check_attachment_links(value, 1)

    def test_empty_month_skips_dependencies_without_fabricated_ids(self):
        case = copy.deepcopy(next(c for c in CASES if c['id'] == 'voucher_list'))
        empty = {key: 0 for key in case['required_keys']}
        empty.update(rows=[], page=1)
        identity = {'companyId': '1', 'DBID': '123'}
        client = Mock(dbid='123', company_id='1', last_response={})
        client.request.side_effect = [identity, empty, identity]
        result = run_checks(client, [case], '2026-07')
        self.assertEqual(client.request.call_count, 3)
        self.assertEqual([r['status'] for r in result], ['passed', 'passed', 'skipped', 'skipped', 'skipped', 'passed'])

    def test_report_distinguishes_empty_and_skipped(self):
        report = {'company_name': '测试', 'company_key': 'company_1', 'month': '2026-07',
                  'started_at': '2026-09-18T00:00:00Z', 'summary': {'passed': 1, 'skipped': 1},
                  'results': [{'id': 'customers', 'label': '客户', 'status': 'passed', 'row_counts': {'rows': 0}},
                              {'id': 'attachment_urls', 'label': '附件', 'status': 'skipped', 'reason': '没有样本'}]}
        with tempfile.TemporaryDirectory() as tmp:
            write_report(report, Path(tmp))
            data = json.loads((Path(tmp) / 'results.json').read_text())
            self.assertEqual(data['summary'], {'passed': 1, 'skipped': 1})
            self.assertIn('skipped', (Path(tmp) / 'RESULTS.md').read_text())


if __name__ == '__main__':
    unittest.main()
