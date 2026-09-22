from pathlib import Path

import json
from openpyxl import Workbook
import pytest

from kdzwy_receipt_uploader.bank_statement_matcher import (
    BankStatementMatchError,
    collect_person_name_exclusions,
    extract_invoice_like_numbers,
    is_person_name,
    match_bank_statements,
)


def write_statement(path: Path, rows: list[tuple[object, ...]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()


def artifact(root: Path, bank_key: str, filename: str) -> dict[str, object]:
    pdf = root / "receipts" / bank_key / filename
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-test")
    relative = Path(bank_key) / Path(filename).stem
    artifact_directory = root / "ocr" / relative
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / "ocr.txt").write_text("test", encoding="utf-8")
    metadata = artifact_directory / "ocr.json"
    metadata.write_text("{}", encoding="utf-8")
    return {
        "bankKey": bank_key,
        "sourcePdf": str(pdf),
        "artifactDirectory": relative.as_posix(),
        "metadata": str(metadata),
    }


def test_matches_multiple_banks_and_maps_bank_direction(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_statement(
        input_dir / "alpha.xlsx",
        [
            (None, "索引", None, None, None, "银行借方", "银行贷方", "对方名称"),
            (None, "A12345", None, None, None, 100, "文字", "甲供应商", "运费"),
            (None, "A23456", None, None, None, 0, 25, "乙客户"),
            (None, "A34567", None, None, None, 88, 0, "未匹配供应商"),
            (None, "A45678", None, None, None, 66, 0, "张三"),
        ],
    )
    write_statement(
        input_dir / "beta.xlsx",
        [
            (None, None, None, None, None, "银行借方", "银行贷方", None, None, "索引", "对方名称"),
            (None, None, None, None, None, "26312000003817427881", 300, None, None, "B12345", "丙客户"),
        ],
    )
    ocr_root = tmp_path / "ocr"
    artifacts = [
        artifact(tmp_path, "alpha", "A12345.pdf"),
        artifact(tmp_path, "alpha", "A23456.pdf"),
        artifact(tmp_path, "alpha", "bank_exception/alpha_page_0001_receipt_03.pdf"),
        artifact(tmp_path, "beta", "B12345.pdf"),
    ]
    configs = {
        "alpha": {
            "bank_account_number": "100201",
            "split": {"filename_index_length": 6, "filename_index_prefix": "A"},
            "statement_columns": {
                "index_column": "B",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
                "remark_column": "I",
            },
        },
        "beta": {
            "bank_account_number": "100204",
            "split": {"filename_index_length": 6, "filename_index_prefix": "B"},
            "statement_columns": {
                "index_column": "J",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "K",
                "remark_column": "I",
            },
        },
    }
    map_path = tmp_path / "maps" / "bank_map.json"
    report_path = tmp_path / "maps" / "bank_map.report.json"

    report = match_bank_statements(
        configs,
        input_dir,
        {"outputDirectory": str(ocr_root), "artifacts": artifacts},
        map_path,
        report_path,
        config_company="固定资料公司",
    )

    assert report["status"] == "ok_with_unmatched"
    assert report["summary"] == {
        "remarkExceptionCount": 0,
        "withoutPdfCount": 0,
        "skippedInternalTransferCount": 0,
        "bankCount": 2,
        "statementRowCount": 5,
        "recognizedReceiptCount": 3,
        "bankExceptionReceiptCount": 1,
        "matchedCount": 3,
        "exceptionFilteredStatementCount": 0,
        "unmatchedStatementCount": 2,
        "unmatchedReceiptCount": 1,
        "skippedPersonNameCount": 0,
        "duplicateIndexCount": 0,
        "directionErrorCount": 0,
    }
    result = json.loads(map_path.read_text(encoding="utf-8"))
    outflow = result["banks"]["alpha"]["entries"]["A12345"]
    assert outflow["flowDirection"] == "outflow"
    assert outflow["ourDebitAmount"] is None
    assert outflow["ourCreditAmount"] == "100.00"
    assert outflow["configCompany"] == "固定资料公司"
    assert outflow["bankAccountNumber"] == "100201"
    assert outflow["counterpartyName"] == "甲供应商"
    assert outflow["counterpartyType"] == "supplier"
    assert outflow["supplierName"] == "甲供应商"
    assert outflow["remark"] == "运费"
    assert "forcedTemplatePath" not in outflow
    assert "templateRouteSource" not in outflow
    inflow = result["banks"]["alpha"]["entries"]["A23456"]
    assert inflow["flowDirection"] == "inflow"
    assert inflow["ourDebitAmount"] == "25.00"
    assert inflow["counterpartyName"] == "乙客户"
    assert inflow["counterpartyType"] == "customer"
    assert inflow["customerName"] == "乙客户"
    numeric_identifier = result["banks"]["beta"]["entries"]["B12345"]
    assert numeric_identifier["flowDirection"] == "inflow"
    assert numeric_identifier["bankAccountNumber"] == "100204"
    assert numeric_identifier["bankDebitAmount"] is None
    assert numeric_identifier["ourDebitAmount"] == "300.00"
    assert numeric_identifier["invoiceNumbers"] == ["26312000003817427881"]
    marker = report["banks"]["alpha"]["unmatchedStatements"][0]
    assert marker["index"] == "A34567"
    assert marker["markerOnly"] is True
    assert marker["downstreamEligible"] is False
    assert marker["configCompany"] == "固定资料公司"
    assert marker["counterpartyType"] == "supplier"
    assert "A34567" not in result["banks"]["alpha"]["entries"]
    assert report["banks"]["alpha"]["skippedPersonNameStatements"] == []
    assert report["banks"]["alpha"]["unmatchedStatements"][1]["index"] == "A45678"
    assert collect_person_name_exclusions(configs, input_dir)["alpha"] == set()
    assert report_path.is_file()


def test_configured_exception_is_removed_before_person_and_normal_matching(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_statement(
        input_dir / "alpha.xlsx",
        [
            (None, "A12345", None, None, None, 88, 0, "张三"),
            (None, "A23456", None, None, None, 100, 0, "普通供应商"),
        ],
    )
    configs = {
        "alpha": {
            "bank_account_number": "100201",
            "split": {"filename_index_length": 6, "filename_index_prefix": "A"},
            "statement_columns": {
                "index_column": "B",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
                "remark_column": "I",
            },
        }
    }
    report = match_bank_statements(
        configs,
        input_dir,
        {
            "outputDirectory": str(tmp_path / "ocr"),
            "artifacts": [artifact(tmp_path, "alpha", "A23456.pdf")],
        },
        tmp_path / "bank_map.json",
        tmp_path / "bank_map.report.json",
        excluded_statement_indices={"alpha": {"A12345"}},
    )

    assert report["summary"]["exceptionFilteredStatementCount"] == 1
    assert report["summary"]["skippedPersonNameCount"] == 0
    assert report["summary"]["matchedCount"] == 1
    assert report["banks"]["alpha"]["exceptionFilteredStatements"][0]["index"] == "A12345"


def test_person_name_detection_is_conservative() -> None:
    assert is_person_name("张三") is True
    assert is_person_name("欧阳娜娜") is True
    assert is_person_name("甲供应商") is False
    assert is_person_name("乙客户") is False
    assert is_person_name("上海微誉") is False
    assert is_person_name("TIPS电子缴税") is False


def test_invoice_numbers_require_an_entirely_numeric_inactive_cell() -> None:
    assert extract_invoice_like_numbers(
        "26312000004664982496\n26312000004646763391"
    ) == ["26312000004664982496", "26312000004646763391"]
    assert extract_invoice_like_numbers("发票号 26312000004664982496") == []
    assert extract_invoice_like_numbers("普通文字") == []


def test_rejects_missing_statement_column_configuration(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_statement(input_dir / "alpha.xlsx", [(None, "A12345", None, None, None, 1, None)])
    configs = {
        "alpha": {
            "bank_account_number": "100201",
            "split": {"filename_index_length": 6, "filename_index_prefix": "A"},
            "statement_columns": {
                "index_column": "B",
                "bank_debit_column": None,
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
                "remark_column": "I",
            },
        }
    }
    with pytest.raises(BankStatementMatchError, match="bank_debit_column 尚未配置"):
        match_bank_statements(
            configs,
            input_dir,
            {"outputDirectory": str(tmp_path / "ocr"), "artifacts": []},
            tmp_path / "bank_map.json",
            tmp_path / "bank_map.report.json",
        )


def test_duplicate_statement_index_is_partial(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_statement(
        input_dir / "alpha.xlsx",
        [
            (None, "A12345", None, None, None, 1, None),
            (None, "A12345", None, None, None, 2, None),
        ],
    )
    one_artifact = artifact(tmp_path, "alpha", "A12345.pdf")
    configs = {
        "alpha": {
            "bank_account_number": "100201",
            "split": {"filename_index_length": 6, "filename_index_prefix": "A"},
            "statement_columns": {
                "index_column": "B",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
                "remark_column": "I",
            },
        }
    }
    report = match_bank_statements(
        configs,
        input_dir,
        {"outputDirectory": str(tmp_path / "ocr"), "artifacts": [one_artifact]},
        tmp_path / "bank_map.json",
        tmp_path / "bank_map.report.json",
    )
    assert report["status"] == "partial"
    assert report["summary"]["duplicateIndexCount"] == 1
    assert report["summary"]["matchedCount"] == 0

@pytest.mark.parametrize('remark,debit,credit,party,expected', [
    ('内部转账100204', 0, 200, '本公司', True),
    ('内部转账100209', 0, 200, '本公司', True),
    ('内部转账100204', 200, 0, '本公司', False),
    ('内部转账100204', 0, 0, '本公司', False),
    ('内部转账100204', 100, 200, '本公司', False),
    ('内部转账', 0, 200, '本公司', False),
    ('内部转账abc', 0, 200, '其他公司', False),
    ('往来款', 0, 200, '本公司', False),
    ('货款', 200, 0, '其他公司', True),
])
def test_internal_transfer_matching_gate(tmp_path, remark, debit, credit, party, expected):
    write_statement(tmp_path / 'alpha.xlsx', [
        (None, 'A12345', None, None, None, debit, credit, party, remark),
    ])
    config = {'alpha': {
        'bank_account_number': '100201',
        'split': {'filename_index_length': 6, 'filename_index_prefix': 'A'},
        'statement_columns': {'index_column': 'B', 'bank_debit_column': 'F',
                              'bank_credit_column': 'G', 'counterparty_name_column': 'H',
                              'remark_column': 'I'},
    }}
    report = match_bank_statements(config, tmp_path,
        {'outputDirectory': str(tmp_path / 'ocr'),
         'artifacts': [artifact(tmp_path, 'alpha', 'A12345.pdf')]},
        tmp_path / 'map.json', tmp_path / 'report.json', config_company='本公司')
    assert report['summary']['matchedCount'] == int(expected)
    assert report['summary']['skippedInternalTransferCount'] == int(not expected)
    assert report['summary']['unmatchedStatementCount'] == 0
    assert report['summary']['unmatchedReceiptCount'] == 0
    assert report['status'] == 'ok'
    entries = json.loads((tmp_path / 'map.json').read_text(encoding='utf-8'))['banks']['alpha']['entries']
    assert ('A12345' in entries) == expected


def test_statement_without_pdf_builds_truthful_evidence(tmp_path):
    from datetime import datetime
    from kdzwy_receipt_uploader.bank_final_receipts import build_bank_ocr_artifacts, load_bank_records
    write_statement(tmp_path / 'alpha.xlsx', [(None, 'A12345', datetime(2026,8,3), None, None, 18.62, 0, '银行供应商', '手续费')])
    config = {'alpha': {'bank_account_number': '100204',
        'split': {'filename_index_length': 6, 'filename_index_prefix': 'A'},
        'statement_columns': {'index_column': 'B', 'bank_debit_column': 'F', 'bank_credit_column': 'G',
                              'counterparty_name_column': 'H', 'remark_column': 'I'}}}
    report = match_bank_statements(config, tmp_path, {'artifacts': []}, tmp_path/'map.json', tmp_path/'report.json', allow_without_pdf=True)
    records, unmatched = load_bank_records(tmp_path/'map.json', tmp_path/'report.json')
    assert not unmatched and report['summary']['matchedCount'] == 1
    assert records['alpha__A12345']['receipt']['pdf'] == ''
    artifacts = build_bank_ocr_artifacts(records)
    assert artifacts[0].engine == 'bank-statement'
    assert '2026-08-03' in artifacts[0].text and '手续费' in artifacts[0].text


@pytest.mark.parametrize('remark', ['扣款（增值税缴税）', '扣款（社保缴税）', '本月公积 金扣款', '扣款（个税缴税）', '跳过', '手续费，跳过单独处理'])
def test_remark_exception_excludes_statement_and_pdf(tmp_path, remark):
    from kdzwy_receipt_uploader.bank_rules import DEFAULT_REMARK_EXCEPTIONS
    write_statement(tmp_path/'alpha.xlsx', [(None,'A12345',None,None,None,100,0,'单位',remark)])
    config={'alpha':{'bank_account_number':'100201',
        'split':{'filename_index_length':6,'filename_index_prefix':'A'},
        'statement_columns':{'index_column':'B','bank_debit_column':'F','bank_credit_column':'G',
                             'counterparty_name_column':'H','remark_column':'I'}}}
    report=match_bank_statements(config,tmp_path,
        {'outputDirectory':str(tmp_path/'ocr'),'artifacts':[artifact(tmp_path,'alpha','A12345.pdf')]},
        tmp_path/'map.json',tmp_path/'report.json',allow_without_pdf=True,remark_exception=["增值税缴税", "社保缴税", "公积金", "个税缴税", "跳过"])
    assert report['summary']['remarkExceptionCount']==1
    assert report['summary']['matchedCount']==0
    assert report['summary']['unmatchedReceiptCount']==0
    assert report['summary']['unmatchedStatementCount']==0


def test_explicit_skip_is_effective_without_configured_exceptions():
    from kdzwy_receipt_uploader.bank_rules import remark_exception_match
    assert remark_exception_match('此笔手续费跳过', []) == '跳过'
    assert remark_exception_match('正常手续费', []) == ''
