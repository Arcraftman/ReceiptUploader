import json
from pathlib import Path

import openpyxl
import pytest

from kdzwy_receipt_uploader.bank_statement_matcher import employee_payment_kind, collect_person_name_exclusions, match_bank_statements
from kdzwy_receipt_uploader.bank_final_receipts import source_values, validate_bank_analysis_rules, BankFinalReceiptError, generate_bank_final_receipts
from kdzwy_receipt_uploader.receipts_ocr import OcrArtifact, analyze_ocr_and_choose_template, compact_analysis_for_storage

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('name,remark,direction,expected', [
    ('张三','8月工资','outflow','salary'), ('欧阳娜娜','费用报销款','outflow','reimbursement'),
    ('张三','工资及费用报销','outflow',''), ('张三','费用报销','inflow',''),
    ('张三','借款','outflow',''), ('测试有限公司','费用报销','outflow',''),
])
def test_employee_routing(name, remark, direction, expected):
    assert employee_payment_kind({'counterpartyName':name,'remark':remark,'flowDirection':direction}) == expected

@pytest.mark.parametrize('company', ['company_17867515','company_20139879','company_21726397'])
@pytest.mark.parametrize('kind,remark,debit', [('salary','8月工资','221101'),('reimbursement','8月费用报销','560106')])
@pytest.mark.parametrize('bank', ['100201','100209'])
def test_employee_payment_render_and_prepare(tmp_path, company, kind, remark, debit, bank):
    record = {'counterpartyName':'张三','remark':remark,'flowDirection':'outflow',
              'bankAccountNumber':bank,'transactionAmount':'123.45','statementAmount':'123.45',
              'amountSource':'bank_statement.ourCreditAmount','amountValidated':True,
              'bankKey':'alpha','index':'A123456'}
    pdf = tmp_path/'demo.pdf'; pdf.write_bytes(b'%PDF-1.4')
    record['receipt']={'pdf':str(pdf)}
    metadata=tmp_path/'ocr.json'; metadata.write_text(json.dumps({'fields':{'allowedTemplateBlocks':['银行']}}))
    artifact=OcrArtifact(invoice_code='alpha__A123456',source_pdf=pdf,source_folder='bank',source_side='bank',
                         output_dir=tmp_path,text_path=tmp_path/'ocr.txt',metadata_path=metadata,
                         text='记账日期：2026-08-31\n转账',engine='fixture',status='success')
    class NoModel:
        def choose(self,*args,**kwargs):
            pytest.fail('员工备注规则不应调用模型')
    context={'businessMapValues':source_values(record),'dynamicAccountCatalog':{'accounts':[
        {'number':debit,'id':'expense','fullName':'销售费用_差旅费' if kind=='reimbursement' else '应付职工薪酬_工资'},
        {'number':bank,'id':'bank','fullName':'银行存款_当前银行'},
    ]},'dynamicItemClassCatalog':{'classes':[]}}
    decision=analyze_ocr_and_choose_template(artifact,ROOT/'templates'/company,selector=NoModel(),final_template_context=context)
    assert decision['analysisStatus']=='ready_for_review', decision
    decision=compact_analysis_for_storage(decision)
    validate_bank_analysis_rules(record,decision)
    assert [e['accountNumber'] for e in decision['filledEntries']]==[debit,bank]
    result=generate_bank_final_receipts({'demo':record},{'demo':decision},tmp_path/'receipts',company,'2026-08',{},draft=False)
    assert result['summary']['generatedCount']==1
    assert json.loads(next((tmp_path/'receipts').rglob('receipt.json')).read_text())['draft'] is False
    with pytest.raises(BankFinalReceiptError):
        validate_bank_analysis_rules({**record,'bankAccountNumber':'100299'},decision)
    with pytest.raises(BankFinalReceiptError,match='员工付款规则'):
        validate_bank_analysis_rules({**record,'remark':'借款'},decision)


def test_employee_rows_reach_matching_without_person_exclusion(tmp_path):
    wb=openpyxl.Workbook();ws=wb.active
    ws.append(['流水','借方','贷方','名称','备注'])
    for i,remark in enumerate(['工资','费用报销','借款'],1):
        ws.append([f'A12345{i}',100,0,'张三',remark])
    wb.save(tmp_path/'alpha.xlsx');wb.close()
    configs={'alpha':{'bank_account_number':'100209','split':{'filename_index_length':7,'filename_index_prefix':'A','parts_per_page':1},
                     'statement_columns':{'index_column':'A','bank_debit_column':'B','bank_credit_column':'C','counterparty_name_column':'D','remark_column':'E'}}}
    assert collect_person_name_exclusions(configs,tmp_path)=={'alpha':set()}

    from test_bank_statement_matcher import artifact
    artifacts = [artifact(tmp_path, 'alpha', f'A12345{i}.pdf') for i in (1, 2, 3)]
    report = match_bank_statements(configs, tmp_path, {'outputDirectory': str(tmp_path/'ocr'), 'artifacts': artifacts},
                                  tmp_path/'map.json', tmp_path/'report.json')
    assert report['summary']['matchedCount'] == 3
    assert report['summary']['skippedPersonNameCount'] == 0
    mapped = json.loads((tmp_path/'map.json').read_text())
    assert all(r['bankAccountNumber'] == '100209' for r in mapped['banks']['alpha']['entries'].values())


def test_employee_not_preloaded_as_supplier():
    from kdzwy_receipt_uploader.preload_items import preload_bank_counterparties
    from test_bank_preload_items import FakeApi
    api = FakeApi()
    result = preload_bank_counterparties(api, {'employee': {'counterpartyName':'张三','remark':'费用报销',
                                                          'flowDirection':'outflow','bankDebitAmount':'100'}})
    assert not api.created
    assert not result.unresolved
