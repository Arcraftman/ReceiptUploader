"""January–August replay plus company-specific routing regressions."""
import json
from test_august_company_templates import AugustCompanyTemplatesTest, ROOT


class HistoricalCompanyTemplatesTest(AugustCompanyTemplatesTest):
    fixtures = json.loads((ROOT / 'tests/fixtures/company_templates_2026_01_08.json').read_text())
    companies = {'company_20139879': (19, 2084), 'company_21726397': (26, 832)}
    evidence_period = None

    def test_new_recurring_business_routes(self):
        cases = [
            ('company_20139879', 'purchase', '房租发票', '房租发票'),
            ('company_20139879', 'purchase', '顺丰快递费', '运杂费'),
            ('company_20139879', 'misc', '工资社保公积金计提', '工资社保公积金计提'),
            ('company_21726397', 'sales', '钉钉AI办公小助理', '销售商品收入'),
            ('company_21726397', 'purchase', '销售商品成本', '销售商品成本'),
            ('company_21726397', 'purchase', '办公用品', '办公用品费'),
        ]
        for company, folder, text, business in cases:
            with self.subTest(text=text):
                self.assertEqual([r['businessType'] for r in self.route(company, folder, text)], [business])

    def test_zhiqing_social_security_no_longer_defaults_to_single_account(self):
        values = {'flowDirection': 'outflow'}
        for text, business in [('缴社保', '缴社保公司个人分拆'), ('仅公司部分社保', '缴社保')]:
            self.assertEqual([r['businessType'] for r in self.route('company_21726397', 'bank', text, values)], [business])

    def test_refunds_do_not_route_as_normal_receipts_or_payments(self):
        cases = [('company_20139879', '退客户款', 'outflow', 'customer'),
                 ('company_21726397', '供应商退款', 'inflow', 'supplier')]
        for company, text, direction, role in cases:
            values = {'flowDirection': direction, 'counterpartyType': role}
            self.assertEqual([r['businessType'] for r in self.route(company, 'bank', text, values)], [text])
            values['flowDirection'] = 'inflow' if direction == 'outflow' else 'outflow'
            self.assertEqual(self.route(company, 'bank', text, values), [])


if __name__ == '__main__':
    import unittest
    # Explicit suite avoids running imported base-class tests twice.
    unittest.main(defaultTest='HistoricalCompanyTemplatesTest')
