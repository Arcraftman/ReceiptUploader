import pytest

from kdzwy_receipt_uploader.bank_final_receipts import (
    BankFinalReceiptError, source_values, validate_bank_analysis_rules,
)
from kdzwy_receipt_uploader.receipts_ocr import bank_amount_snapshot
from kdzwy_receipt_uploader.preload_items import preload_bank_counterparties
from test_bank_preload_items import FakeApi


def record(**overrides):
    return {"configCompany": "千云（上海）信息科技有限公司",
            "transactionAmount": "100.00", "statementAmount": "100.00",
            "amountSource": "bank_statement.ourCreditAmount", "amountValidated": True,
            "bankAccountNumber": "100203", **overrides}


@pytest.mark.parametrize('company', ['千云（上海）信息科技有限公司', '智轻云(上海)科技有限公司', '未知公司'])
def test_missing_split_never_inherits_another_company_policy(company):
    values = source_values(record(configCompany=company))
    assert values['companyHousingFund'] is None
    assert values['employeeHousingFund'] is None


@pytest.mark.parametrize('fields', [('companyHousingFund', 'employeeHousingFund'), ('companySocialSecurity', 'employeeSocialSecurity')])
def test_explicit_unequal_split_survives_for_every_company(fields):
    for company in ['千云（上海）信息科技有限公司', '智轻云(上海)科技有限公司', '上海微誉信息技术有限公司']:
        values = source_values(record(configCompany=company, **{fields[0]: '30.01', fields[1]: '69.99'}))
        assert values[fields[0]] == '30.01'
        assert values[fields[1]] == '69.99'


@pytest.mark.parametrize('overrides', [
    {'companyHousingFund': '30'},
    {'companyHousingFund': '30', 'employeeHousingFund': '60'},
    {'companyHousingFund': '-1', 'employeeHousingFund': '101'},
    {'companyHousingFund': 'NaN', 'employeeHousingFund': '100'},
    {'companyHousingFund': '30.001', 'employeeHousingFund': '69.999'},
    {'transactionAmount': 'NaN'}, {'transactionAmount': 'Infinity'},
    {'statementAmount': '99'}, {'statementAmount': None},
])
def test_invalid_amount_evidence_blocks(overrides):
    with pytest.raises(BankFinalReceiptError):
        source_values(record(**overrides))


def test_same_total_but_changed_split_invalidates_cached_analysis():
    original = record(companyHousingFund='50', employeeHousingFund='50')
    analysis = {'bankSourceAmounts': bank_amount_snapshot(source_values(original))}
    changed = {**original, 'companyHousingFund': '30', 'employeeHousingFund': '70'}
    with pytest.raises(BankFinalReceiptError, match='金额依据已变化'):
        validate_bank_analysis_rules(changed, analysis)
    with pytest.raises(BankFinalReceiptError, match='金额依据已变化'):
        validate_bank_analysis_rules(original, {})


def test_money_direction_alone_cannot_create_supplier():
    api = FakeApi()
    result = preload_bank_counterparties(api, {
        'unknown': {'counterpartyName': '未知贸易有限公司', 'bankDebitAmount': '100', 'flowDirection': 'outflow'},
        'customer_refund': {'counterpartyName': '已有客户', 'bankDebitAmount': '100', 'flowDirection': 'outflow'},
    })
    assert api.created == []
    assert result.resolve(1, '已有客户')['id'] == 'customer-1'
    assert any(r['recordKey'] == 'unknown' for r in result.unresolved)
