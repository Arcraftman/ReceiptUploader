"""Offline replay of August exports; no credentials or network calls."""
from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kdzwy_receipt_uploader.template_catalog import TemplateCatalog
from kdzwy_receipt_uploader.receipts_ocr import (
    OcrArtifact, OcrPipelineError, _rule_candidates, enforce_template_explanation,
)
from kdzwy_receipt_uploader.final_template_sample import validate_filled_entries
from kdzwy_receipt_uploader.company_registry import load_template_companies, resolve_company_template

COMPANIES = {'company_20139879': (19, 244), 'company_21726397': (26, 118)}
FIXTURES = json.loads((ROOT / 'tests/fixtures/company_templates_2026_08.json').read_text())


class AugustCompanyTemplatesTest(unittest.TestCase):
    fixtures = FIXTURES
    companies = COMPANIES
    evidence_period = "2026-08"

    def test_companies_resolve_to_separate_registered_templates(self):
        registry = load_template_companies(ROOT / 'config/template_companies.json')
        for company in self.companies:
            profile = resolve_company_template(ROOT, company, registry[company].name)
            self.assertEqual(profile.directory, company)

    def artifact(self, folder, text='交易日期 2026-08-11'):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        p = Path(directory.name)
        metadata = p / 'ocr.json'
        metadata.write_text('{"fields": {}}')
        return OcrArtifact(invoice_code='sample', source_pdf=p / 'sample.pdf',
                           source_folder=folder, source_side=folder, output_dir=p,
                           text_path=p / 'ocr.txt', metadata_path=metadata,
                           text=text, engine='fixture', status='success')

    def route(self, company, folder, text, values=None):
        catalog = TemplateCatalog.load(ROOT / 'templates' / company)
        records = [dict(catalog.load_template(r), path=r['path']) for r in catalog.records]
        return _rule_candidates(records, self.artifact(folder, text), values)[0]

    def context(self, case):
        values = dict(case['values'])
        values.update(customName='测试对手方', supplierName='测试对手方', counterpartyName='测试对手方')
        template = json.loads((ROOT / 'templates' / case['company'] / case['templatePath']).read_text())
        if case['templatePath'].startswith('bank/'):
            values.update(transactionAmount=values['amount'], statementAmount=values['amount'],
                          amountSource='fixture', amountValidated=True,
                          flowDirection=template['matchRules']['flowDirections'][0])
        accounts = {e['number']: {'number': e['number'], 'id': 'fake-account-' + e['number'],
                                'fullName': '测试科目'} for e in case['expected']}
        classes = [{'itemClassId': i, 'items': [{'id': 'fake-item-' + str(i), 'number': 'TEST',
                                                'name': '测试对手方'}]} for i in (1, 5)]
        return {'businessMapValues': values, 'dynamicAccountCatalog': {'accounts': list(accounts.values())},
                'dynamicItemClassCatalog': {'classes': classes}}

    def test_all_historical_vouchers_replay_through_production_renderer(self):
        for case in self.fixtures:
            with self.subTest(company=case['company'], voucher=case['voucher']):
                context = self.context(case)
                decision = {'templatePath': case['templatePath']}
                enforce_template_explanation(decision, self.artifact(case['templatePath'].split('/')[0]),
                                             ROOT / 'templates' / case['company'], context)
                actual = [(e['accountNumber'], e['dc'], Decimal(str(e['amount']))) for e in decision['filledEntries']]
                expected = [(e['number'], e['dc'], Decimal(str(e['amount']))) for e in case['expected']]
                self.assertEqual(actual, expected)
                self.assertEqual(validate_filled_entries(decision, context), [])
                self.assertNotEqual(decision.get('status'), 'blocked')

    def test_catalog_contract_and_evidence_coverage(self):
        for company, (count, covered) in self.companies.items():
            catalog = TemplateCatalog.load(ROOT / 'templates' / company)
            self.assertEqual(len(catalog.records), count)
            cases = [c for c in self.fixtures if c['company'] == company]
            self.assertEqual(len(cases), covered)
            self.assertEqual(len({(c.get('period', '2026-08'), c['voucher']) for c in cases}), covered)
            codes = []
            for r in catalog.records:
                t = catalog.load_template(r)
                codes.append(t['decisionCode'])
                refs = t.get('historicalEvidence', {}).get('voucherRefs', [])
                if not refs:
                    self.assertEqual(t['businessType'], '费用报销')
                    self.assertIn('用户确认', t['usageNotes'])
                evidence_count = sum(self.evidence_period is None or v['period'] == self.evidence_period for v in refs)
                case_count = sum(c['templatePath'] == r['path'] for c in cases)
                # The original August snapshot predates newly added templates.
                if self.evidence_period is None or case_count:
                    self.assertEqual(evidence_count, case_count)
                for e in t['entries']:
                    self.assertNotIn('id', e.get('auxiliary', {}))
                    self.assertNotIn('_', e['accountSelector'].get('number', ''))
                if r['path'].startswith('bank/'):
                    self.assertEqual(sum(e['accountSelector'].get('numberFrom') == 'source.bankAccountNumber' for e in t['entries']), 1)
            self.assertEqual(len(set(codes)), count)
            prompt = (catalog.root / 'prompts/invoice_classifier_prompt.txt').read_text()
            for token in ('<<BUSINESS_RULES>>', '<<OCR_TEXT>>', '<<TEMPLATE_CATALOG>>', '<<FINAL_CONTEXT>>'):
                self.assertIn(token, prompt)
            self.assertNotIn('微誉', prompt)

    def test_bank_direction_and_role_are_required_for_customer_match(self):
        for company in self.companies:
            for values in ({'flowDirection': 'outflow', 'counterpartyType': 'customer'},
                           {'flowDirection': 'inflow', 'counterpartyType': 'supplier'},
                           {'flowDirection': 'inflow'}):
                self.assertEqual(self.route(company, 'bank', '收客户款', values), [])
            result = self.route(company, 'bank', '收客户款', {'flowDirection': 'inflow', 'counterpartyType': 'customer'})
            self.assertEqual([r['businessType'] for r in result], ['收客户款'])

    def test_specific_business_routes_and_ambiguous_or_exception_text_blocks(self):
        for company in self.companies:
            self.assertEqual([r['businessType'] for r in self.route(company, 'bank', '银行手续费', {'flowDirection': 'outflow'})], ['银行手续费'])
            for text in ('银行账户管理费', '收到银行手续费发票', '固定资产清理', '混合费用报销'):
                self.assertEqual(self.route(company, 'bank', text, {'flowDirection': 'outflow'}), [])
            self.assertEqual(self.route(company, 'sales', '硬件设备销售收入'), [])
        z = 'company_21726397'
        self.assertEqual(self.route(z, 'sales', '增值税发票 软件服务费'), [])
        for business in ('项目收入', '研发收入'):
            self.assertEqual([r['businessType'] for r in self.route(z, 'sales', business)], [business])
        self.assertEqual(self.route(z, 'bank', '三方品冻结', {'flowDirection': 'outflow', 'counterpartyType': 'supplier'}), [])

    def test_missing_split_amount_is_rejected_and_imbalance_detected(self):
        for company in self.companies:
            case = next(c for c in self.fixtures if c['company'] == company and '缴公积金' in c['templatePath'])
            context = self.context(case)
            del context['businessMapValues']['employeeHousingFund']
            with self.assertRaises(OcrPipelineError):
                enforce_template_explanation({'templatePath': case['templatePath']}, self.artifact('bank'), ROOT / 'templates' / company, context)
            context = self.context(case)
            context['businessMapValues']['employeeHousingFund'] += 1
            decision = {'templatePath': case['templatePath']}
            enforce_template_explanation(decision, self.artifact('bank'), ROOT / 'templates' / company, context)
            self.assertTrue(any('借贷不平衡' in e for e in validate_filled_entries(decision, context)))

    def test_unresolved_auxiliary_blocks(self):
        case = next(c for c in self.fixtures if c['templatePath'].startswith('sales/'))
        context = self.context(case)
        context['dynamicItemClassCatalog']['classes'] = []
        decision = {'templatePath': case['templatePath']}
        enforce_template_explanation(decision, self.artifact('sales'), ROOT / 'templates' / case['company'], context)
        self.assertEqual(decision['status'], 'blocked')

    def test_company_specific_accounts_and_red_research_entries(self):
        for company, city_tax in [('company_20139879', '222108'), ('company_21726397', '222118')]:
            case = next(c for c in self.fixtures if c['company'] == company and '税金及附加' in c['templatePath'])
            self.assertIn(city_tax, [e['number'] for e in case['expected']])
        case = next(c for c in self.fixtures if '研发费用结转' in c['templatePath'] and c['values']['researchTravel'] < 0)
        self.assertLess(case['values']['researchTravel'], 0)
        self.assertLess(case['values']['researchSoftware'], 0)


if __name__ == '__main__':
    unittest.main()
