import json
from pathlib import Path

import pytest
from kdzwy_receipt_uploader.bank_rules import resolve_employee_payable
from kdzwy_receipt_uploader.bank_final_receipts import source_values, validate_bank_analysis_rules, BankFinalReceiptError
from kdzwy_receipt_uploader.receipts_ocr import OcrArtifact, analyze_ocr_and_choose_template, compact_analysis_for_storage


@pytest.mark.parametrize('name,account', [(name, code) for name in ['马李','徐芳','朱晓福','钱玉叶','陈耀','王勇','郑绍贵','张先锋','刘利青'] for code in ['224199', '22410203', '2241020304']])
def test_personal_reimbursement_four_lines(tmp_path, name, account):
    record = {'configCompany':'上海微誉信息技术有限公司','counterpartyName':name,
              'remark':'报销水费','flowDirection':'outflow','bankAccountNumber':'100201',
              'transactionAmount':'276.00','statementAmount':'276.00',
              'amountSource':'bank_statement.ourCreditAmount','amountValidated':True}
    meta=tmp_path/'ocr.json'; meta.write_text(json.dumps({'fields':{'allowedTemplateBlocks':['银行']}}))
    artifact=OcrArtifact(invoice_code='bank__A123456',source_pdf=tmp_path/'test.pdf',source_folder='bank',source_side='bank',
                        output_dir=tmp_path,text_path=tmp_path/'ocr.txt',metadata_path=meta,
                        text='记账日期：2026-08-28\n报销水费',engine='fixture',status='success')
    class NoModel:
        def choose(self,*args,**kwargs):
            pytest.fail('人员专用模板不应请求LLM')
    context={'businessMapValues':source_values(record),'dynamicAccountCatalog':{'accounts':[
        {'number':'560203','id':'expense','fullName':'管理费用_办公用品费'},
        {'number':account,'id':'person','fullName':'其他应付款_'+name},
        {'number':'100201','id':'bank','fullName':'银行存款_上海银行'}]},'dynamicItemClassCatalog':{'classes':[]}}
    root=Path(__file__).parents[1]/'templates/company_17867515'
    result=compact_analysis_for_storage(analyze_ocr_and_choose_template(artifact,root,NoModel(),context))
    assert result['analysisStatus']=='ready_for_review',result
    validate_bank_analysis_rules(record,result)
    assert [(e['accountNumber'],e['dc']) for e in result['filledEntries']]==[
        ('560203',1),(account,-1),(account,1),('100201',-1)]
    assert [e['explanation'] for e in result['filledEntries']]==[name+'报销水费']*3+[name+'报销水费 2026-08-28']
    changed_catalog = [dict(a, id="changed") if a['number'] == account else a for a in context['dynamicAccountCatalog']['accounts']]
    with pytest.raises(BankFinalReceiptError):
        validate_bank_analysis_rules(record, result, changed_catalog)
    result['filledEntries'][1]['accountNumber']='22418888' 
    with pytest.raises(BankFinalReceiptError):
        validate_bank_analysis_rules(record,result)



def test_nested_tree_requires_unique_exact_leaf():
    row = {"counterpartyName": "马李"}
    leaf = {"number": "22410203", "id": "person", "name": "马李"}
    rows = [{"number": "2241", "id": "root", "name": "其他应付款", "child": [
        {"number": "224102", "id": "parent", "name": "马李", "child": [leaf]},
        {"number": "224104", "id": "old", "name": "马李（2022钉钉168541）"}]}]
    assert resolve_employee_payable(row, rows)["accountNumber"] == "22410203"
    with pytest.raises(ValueError):
        resolve_employee_payable(row, rows + [{"number": "224109", "id": "duplicate", "name": "马李"}])
    with pytest.raises(ValueError):
        resolve_employee_payable(row, [{"number": "220201", "id": "supplier", "name": "马李"}])
    with pytest.raises(ValueError):
        resolve_employee_payable(row, [])


def test_removed_default_remark_exclusions():
    from kdzwy_receipt_uploader.bank_rules import DEFAULT_REMARK_EXCEPTIONS, remark_exception_match
    assert DEFAULT_REMARK_EXCEPTIONS == ["跳过"]
    for remark in ["增值税缴税", "社保缴税", "公积金", "个税缴税"]:
        assert not remark_exception_match(remark, DEFAULT_REMARK_EXCEPTIONS)
