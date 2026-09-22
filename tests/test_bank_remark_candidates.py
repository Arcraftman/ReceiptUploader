import json
from types import SimpleNamespace
import pytest
from kdzwy_receipt_uploader.ocr.rules import _rule_candidates


@pytest.mark.parametrize('name', ['张三', 'TIPS电子缴税款业务待报解预算收入', '测试公司'])
def test_statement_remark_selects_candidate_without_pdf_keyword(tmp_path, name):
    metadata = tmp_path / 'ocr.json'
    metadata.write_text(json.dumps({'fields': {'allowedTemplateBlocks': ['银行']}}), encoding='utf-8')
    artifact = SimpleNamespace(metadata_path=metadata, text='银行转账回单', source_folder='bank')
    candidate = {'path': 'bank/tax.json', 'documentBlock': '银行', 'businessType': '缴税',
                 'matchRules': {'sourceFolders': ['bank'], 'flowDirections': ['outflow'], 'anyKeywords': ['税款']}}
    selected, _ = _rule_candidates([candidate], artifact,
        {'counterpartyName': name, 'configCompany': '测试公司', 'remark': '缴纳税款', 'flowDirection': 'outflow'})
    assert selected == [candidate]
    selected, _ = _rule_candidates([candidate], artifact,
        {'counterpartyName': name, 'remark': '', 'flowDirection': 'outflow'})
    assert selected == []
