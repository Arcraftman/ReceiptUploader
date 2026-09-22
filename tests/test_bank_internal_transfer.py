import json
from pathlib import Path
import pytest
from kdzwy_receipt_uploader.bank_rules import is_supplier_refund
from kdzwy_receipt_uploader.bank_final_receipts import source_values, validate_bank_analysis_rules, BankFinalReceiptError
from kdzwy_receipt_uploader.receipts_ocr import OcrArtifact, analyze_ocr_and_choose_template, compact_analysis_for_storage, OcrPipelineError


@pytest.mark.parametrize('bank', ['100201', '100209'])
def test_internal_transfer_dynamic_accounts(tmp_path, bank):
    record = {'counterpartyName':'上海柯陈电子有限公司','remark':'内部转账100204','flowDirection':'inflow',
              'counterpartyType':'customer', 'bankAccountNumber':bank,
              'transactionAmount':'18880.00','statementAmount':'18880.00',
              'amountSource':'bank_statement.ourDebitAmount','amountValidated':True,
              'bankCreditAmount':'18880.00','bankKey':'test','index':'A123456'}
    pdf=tmp_path/'demo.pdf';pdf.write_bytes(b'%PDF')
    metadata=tmp_path/'ocr.json';metadata.write_text(json.dumps({'fields':{'allowedTemplateBlocks':['银行']}}),encoding='utf-8')
    artifact=OcrArtifact(invoice_code='test__A123456',source_pdf=pdf,source_folder='bank',source_side='bank',
        output_dir=tmp_path,text_path=tmp_path/'ocr.txt',metadata_path=metadata,
        text='记账日期：2026-08-14\n无法入账，收款账户已销户',engine='fixture',status='success')
    class NoModel:
        def choose(self,*args,**kwargs):
            pytest.fail('退款规则不应请求LLM')
    def context():
        return {'businessMapValues':source_values(record),'dynamicAccountCatalog':{'accounts':[
            {'number':'100204','id':'other-bank','fullName':'银行存款_招商银行'},
            {'number':bank,'id':'bank','fullName':'银行存款_当前银行'}]},
            'dynamicItemClassCatalog':{'classes':[{'itemClassId':5,'items':[{'id':'supplier1614','number':'1614','name':'上海柯陈电子有限公司'}]}]}}
    root=Path(__file__).parents[1]/'templates/company_17867515'
    result=analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())
    assert result['analysisStatus']=='ready_for_review',result
    result=compact_analysis_for_storage(result)
    validate_bank_analysis_rules(record,result)
    assert [(e['accountNumber'], e['dc']) for e in result['filledEntries']]==[(bank,1),('100204',-1)]
    assert result['filledEntries'][0]['explanation']=='内部转账 2026-08-14'
    assert result['filledEntries'][1]['explanation']=='内部转账 2026-08-14'
    with pytest.raises(BankFinalReceiptError,match='内部转账备注'):
        validate_bank_analysis_rules({**record,'remark':'货款'},result)
    for remark,expected in [('内部转账'+bank,'不能与当前银行'),('内部转账100299','无法在当前账套唯一解析'),('内部转账abc','数字科目编码')]:
        record['remark']=remark
        with pytest.raises(OcrPipelineError,match=expected):
            analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())
    record['remark']='内部转账100204'
    record['bankCreditAmount']='0'
    with pytest.raises(OcrPipelineError,match='流水贷方'):
        analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())
    record['bankCreditAmount']='18880.00'
    record['flowDirection']='outflow'
    with pytest.raises(OcrPipelineError,match='流水贷方'):
        analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())

@pytest.mark.parametrize('remark,expected', [
    ('本月内部转账100204到账', '100204'),
    ('内部转账 100209 银行划转', '100209'),
    ('内部转账', ''),
    ('内部转账100204 内部转账100209', ''),
])
def test_transfer_remark_contains_dynamic_account(remark, expected):
    from kdzwy_receipt_uploader.bank_rules import internal_transfer_account
    assert internal_transfer_account({'remark': remark}) == expected
