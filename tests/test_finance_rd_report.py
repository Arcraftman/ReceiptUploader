from openpyxl import load_workbook
from kdzwy_receipt_uploader.finance.build_template import save_template


def test_rd_only_visible_detail_and_hightech_formulas(tmp_path):
    book = load_workbook(save_template(tmp_path/'report.xlsx'))
    sheet = book['2026研发费用']
    assert sheet['B6'].value == '研发费用明细'
    assert sheet['N6'].value == '12月'
    assert sheet['O6'].value == '合计金额'
    assert sheet['A1'].value is None and sheet['A16'].value is None
    assert not sheet._charts
    assert sheet.column_dimensions['AR'].hidden
    assert sheet['AS4'].value == '43010103'
    assert 'subject_debit_leaf' in sheet['AT4'].value
    assert 'RANK(' in sheet['BG4'].value
    tech = book['高新企业相关指标监测']
    assert [tech['B7'].value,tech['B8'].value,tech['B9'].value] == [1,2,3]
    assert "'2026研发费用'!O15" in tech['E8'].value
    assert "'2026年利润和负债'!AI47" in tech['E9'].value
    assert '50000000' in tech['D8'].value and '200000000' in tech['D8'].value
    assert tech['D7'].value == 0.1
    assert all(tech[c].value is None for c in ('E7','F7','G7'))
    book.close()
