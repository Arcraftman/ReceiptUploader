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
            (None, "A12345", None, None, None, 100, "文字", "甲供应商"),
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
        "bankCount": 2,
        "statementRowCount": 5,
        "recognizedReceiptCount": 3,
        "bankExceptionReceiptCount": 1,
        "matchedCount": 3,
        "exceptionFilteredStatementCount": 0,
        "unmatchedStatementCount": 1,
        "unmatchedReceiptCount": 1,
        "skippedPersonNameCount": 1,
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
    person_marker = report["banks"]["alpha"]["skippedPersonNameStatements"][0]
    assert person_marker["index"] == "A45678"
    assert person_marker["counterpartyName"] == "张三"
    assert person_marker["counterpartyType"] == "person"
    assert person_marker["markerReason"] == "person_name"
    assert person_marker["downstreamEligible"] is False
    assert "supplierName" not in person_marker
    assert "A45678" not in result["banks"]["alpha"]["entries"]
    assert collect_person_name_exclusions(configs, input_dir)["alpha"] == {"A45678"}
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
