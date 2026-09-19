"""Opt-in live checks. Default test discovery never logs in or accesses the API.

KDZWY_READ_TEST_COMPANY=company_17867515 KDZWY_READ_TEST_MONTH=2026-07 \
    python3 -m unittest discover -s tests -p test_read_api_live.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kdzwy_receipt_uploader.read_api_checks import (
    DEPENDENT_IDS, load_cases, run_company_checks, write_report,
)

COMPANY = os.environ.get('KDZWY_READ_TEST_COMPANY')
MONTH = os.environ.get('KDZWY_READ_TEST_MONTH')
CASES = load_cases(ROOT / 'tests/fixtures/read_api_cases.json')


@unittest.skipUnless(COMPANY and MONTH, '真实接口测试需显式设置公司与月份；默认不联网')
class ReadAPILiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report = run_company_checks(ROOT, COMPANY, MONTH)
        out = Path(os.environ.get('KDZWY_READ_TEST_OUTPUT') or ROOT / 'runtime/read_api_tests' / COMPANY / MONTH)
        write_report(report, out)
        cls.results = {row['id']: row for row in report['results']}


def make_test(id, label):
    def test(self):
        result = self.results[id]
        if result['status'] == 'skipped':
            self.skipTest(result['reason'])
        self.assertEqual(result['status'], 'passed', result.get('reason', label))
    test.__doc__ = label
    return test


for id, label in [('identity_before', '查询前身份'), *[(c['id'], c['label']) for c in CASES],
                  *[(k, k) for k in DEPENDENT_IDS], ('identity_after', '查询后身份')]:
    setattr(ReadAPILiveTests, 'test_' + id, make_test(id, label))


if __name__ == '__main__':
    unittest.main()
