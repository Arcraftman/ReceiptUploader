"""Existing exceptional-payables renderer must balance and resolve unique live IDs."""
import copy
from pathlib import Path

import pytest

from kdzwy_receipt_uploader.ocr.models import OcrArtifact, OcrPipelineError
from kdzwy_receipt_uploader.ocr.rendering import enforce_dynamic_supplier_payables_exception


@pytest.fixture
def example(tmp_path):
    artifact = OcrArtifact('r1', tmp_path/'a.pdf', 'bank', 'bank', tmp_path,
                           tmp_path/'ocr.txt', tmp_path/'ocr.json', '交易日期 2026-09-01', 'fake', 'success')
    record = {'id': 'dynamic', 'exception': {'allocationAccountNumber': '2202'}}
    context = {'businessMapValues': {'amount': '100', 'bankAccountNumber': '100203', 'exceptionConfig': {
        'handling': 'dynamic_supplier_payables', 'template_id': 'dynamic',
        'allocations': [{'supplier_name': '甲公司', 'amount': '40'}, {'supplier_name': '乙公司', 'amount': '60'}]}},
        'dynamicAccountCatalog': {'accounts': [
            {'number': '2202', 'id': 'p1', 'fullName': '应付账款'},
            {'number': '100203', 'id': 'b1', 'fullName': '银行存款'}]},
        'dynamicItemClassCatalog': {'itemClassId': 5, 'rows': [
            {'id': 's1', 'name': '甲公司', 'number': '001'}, {'id': 's2', 'name': '乙公司', 'number': '002'}]}}
    return artifact, record, context


def test_unique_supplier_split_matches_source_bank_and_amount(example):
    artifact, record, context = example
    result = {}
    assert enforce_dynamic_supplier_payables_exception(result, artifact, Path("unused"), context, record)
    entries = result['filledEntries']
    assert [(e['accountId'], e['amount']) for e in entries] == [('p1', 40), ('p1', 60), ('b1', 100)]
    assert [e['auxiliary']['id'] for e in entries[:2]] == ['s1', 's2']
    assert entries[-1]['explanation'].endswith('2026-09-01')


@pytest.mark.parametrize('case', ['missing-config','handling','template','empty','no-bank','no-date','bad-total',
    'nan-total','bad-row','bad-amount','nan-amount','no-name','negative','unbalanced','no-account','duplicate-bank','unknown-supplier','duplicate-supplier'])
def test_invalid_split_remains_pending(example, case):
    artifact, record, context = example
    values = context['businessMapValues']
    config = values['exceptionConfig']
    if case == 'missing-config': del values['exceptionConfig']
    elif case == 'handling': config['handling'] = 'other'
    elif case == 'template': config['template_id'] = 'other'
    elif case == 'empty': config['allocations'] = []
    elif case == 'no-bank': values['bankAccountNumber'] = ''
    elif case == 'no-date':
        from dataclasses import replace
        artifact = replace(artifact, text='无日期')
    elif case == 'bad-total': values['amount'] = 'invalid'
    elif case == 'nan-total': values['amount'] = 'NaN'
    elif case == 'bad-row': config['allocations'] = [None]
    elif case == 'bad-amount': config['allocations'][0]['amount'] = 'invalid'
    elif case == 'nan-amount': config['allocations'][0]['amount'] = 'NaN'
    elif case == 'no-name': config['allocations'][0]['supplier_name'] = ''
    elif case == 'negative': config['allocations'][0]['amount'] = '-1'
    elif case == 'unbalanced': config['allocations'][0]['amount'] = '41'
    elif case == 'no-account': context['dynamicAccountCatalog'] = {}
    elif case == 'duplicate-bank': context['dynamicAccountCatalog']['accounts'].append(copy.deepcopy(context['dynamicAccountCatalog']['accounts'][-1]))
    elif case == 'unknown-supplier': context['dynamicItemClassCatalog'] = {}
    elif case == 'duplicate-supplier': context['dynamicItemClassCatalog']['rows'].append({'id': 'duplicate', 'name': '甲公司'})
    result = {}
    assert not enforce_dynamic_supplier_payables_exception(result, artifact, Path("unused"), context, record)
    assert result['analysisStatus'] == 'exception_pending'
    assert result['filledEntries'] == []
    assert result['exceptionValidationErrors']


def test_missing_definition_rejected(example):
    artifact, _, context = example
    with pytest.raises(OcrPipelineError):
        enforce_dynamic_supplier_payables_exception({}, artifact, Path("unused"), context, {})
