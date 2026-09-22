import json
from pathlib import Path
import pytest
from kdzwy_receipt_uploader.bank_rules import is_supplier_refund
from kdzwy_receipt_uploader.bank_final_receipts import source_values, validate_bank_analysis_rules, BankFinalReceiptError
from kdzwy_receipt_uploader.receipts_ocr import OcrArtifact, analyze_ocr_and_choose_template, compact_analysis_for_storage, OcrPipelineError


@pytest.mark.parametrize('remark,expected', [('退款', True), (' 退款 ', True), ('供应商退款', True), ('货款', False)])
def test_contains_refund_remark(remark, expected):
    assert is_supplier_refund({'remark': remark}) is expected


def test_refund_preload_uses_supplier_class_instead_of_customer():
    from test_bank_preload_items import FakeApi
    from kdzwy_receipt_uploader.preload_items import preload_bank_counterparties
    api=FakeApi()
    result=preload_bank_counterparties(api, {'refund': {'remark':'银行业务退款结算', 'flowDirection':'inflow',
        'counterpartyName':'已有客户', 'counterpartyRoles':['customer']}}, create_missing=True)
    assert result.resolved[0]['itemClassIds']==[5]
    assert api.created[0][0]==5


@pytest.mark.parametrize('bank', ['100201', '100204'])
def test_refund_uses_fixed_template_and_supplier_even_on_inflow(tmp_path, bank):
    record = {'counterpartyName':'上海柯陈电子有限公司','remark':'银行业务退款结算','flowDirection':'inflow',
              'counterpartyType':'customer', 'bankAccountNumber':bank,
              'transactionAmount':'18880.00','statementAmount':'18880.00',
              'amountSource':'bank_statement.ourDebitAmount','amountValidated':True,
              'bankKey':'test','index':'A123456'}
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
            {'number':'220201','id':'payable','fullName':'应付账款_人民币户'},
            {'number':bank,'id':'bank','fullName':'银行存款_当前银行'}]},
            'dynamicItemClassCatalog':{'classes':[{'itemClassId':5,'items':[{'id':'supplier1614','number':'1614','name':'上海柯陈电子有限公司'}]}]}}
    root=Path(__file__).parents[1]/'templates/company_17867515'
    result=analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())
    assert result['analysisStatus']=='ready_for_review',result
    result=compact_analysis_for_storage(result)
    validate_bank_analysis_rules(record,result)
    assert [(e['accountNumber'], e['dc']) for e in result['filledEntries']]==[(bank,1),('220201',-1)]
    assert result['filledEntries'][0]['explanation']=='供应商付款退回 2026-08-14'
    assert result['filledEntries'][1]['explanation']=='供应商付款退回'
    with pytest.raises(BankFinalReceiptError,match='退款备注'):
        validate_bank_analysis_rules({**record,'remark':'货款'},result)
    record['flowDirection']='outflow'
    with pytest.raises(OcrPipelineError,match='收款方向'):
        analyze_ocr_and_choose_template(artifact,root,selector=NoModel(),final_template_context=context())
