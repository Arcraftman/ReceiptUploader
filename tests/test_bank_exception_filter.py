from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from kdzwy_receipt_uploader import bank_exception_filter
from kdzwy_receipt_uploader.bank_exception_filter import filter_bank_exception_pdfs


def _write_statement(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append((None, "索引", None, None, None, "银行借方", "银行贷方", "对方名称"))
    sheet.append((None, "A02607010000001", None, None, None, 88, 0, "张三"))
    sheet.append(
        (
            None,
            "A02607020000002",
            None,
            None,
            None,
            100,
            0,
            "重庆京东盛际小额贷款有限公司",
        )
    )
    sheet.append(
        (
            None,
            "A02607030000003",
            None,
            None,
            None,
            1234.56,
            0,
            "TIPS电子缴税款业务待报解预算收入",
        )
    )
    workbook.save(path)
    workbook.close()


def test_special_pdfs_are_copied_and_excluded_before_ordinary_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_statement(input_root / "alpha.xlsx")

    split_root = tmp_path / "generated" / "bank_receipts" / "alpha"
    split_root.mkdir(parents=True)
    person_pdf = split_root / "A02607010000001.pdf"
    jd_pdf = split_root / "A02607020000002.pdf"
    ordinary_pdf = split_root / "A02607040000004.pdf"
    tax_pdf = split_root / "bank_exception" / "alpha_page_0001_receipt_01.pdf"
    tax_pdf.parent.mkdir()
    for pdf in (person_pdf, jd_pdf, ordinary_pdf, tax_pdf):
        pdf.write_bytes(b"%PDF-test")
    (split_root / "split.manifest.json").write_text(
        json.dumps(
            {
                "outputs": [
                    person_pdf.name,
                    jd_pdf.name,
                    ordinary_pdf.name,
                    tax_pdf.relative_to(split_root).as_posix(),
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        bank_exception_filter,
        "_pdf_text",
        lambda path: (
            "上海银行电子缴税付款凭证\n"
            "记账日期: 20260703\n"
            "小写(合计)金额:￥1,234.56"
            if path == tax_pdf.resolve()
            else "普通银行回单"
        ),
    )
    bank_configs = {
        "alpha": {
            "bank_account_number": "100201",
            "split": {
                "filename_index_length": 15,
                "filename_index_prefix": "A",
            },
            "statement_columns": {
                "index_column": "B",
                "bank_debit_column": "F",
                "bank_credit_column": "G",
                "counterparty_name_column": "H",
            },
        }
    }
    exceptions = [
        "TIPS电子缴税款业务待报解预算收入",
        "重庆京东盛际小额贷款有限公司",
        "张三",
    ]
    special_root = tmp_path / "generated" / "bank_exceptions"
    manifest_path = tmp_path / "generated" / "maps" / "bank" / "bank_exceptions.json"

    result = filter_bank_exception_pdfs(
        bank_configs,
        exceptions,
        input_root,
        {
            "banks": [
                {
                    "bankKey": "alpha",
                    "outputDirectory": str(split_root),
                }
            ]
        },
        special_root,
        manifest_path,
        config_company="固定资料公司",
        pdf_keyword_rules={
            "TIPS电子缴税款业务待报解预算收入": [
                "上海银行电子缴税付款凭证"
            ]
        },
    )

    assert result["summary"] == {
        "exceptionNameCount": 3,
        "exceptionStatementCount": 3,
        "splitExceptionPdfCount": 1,
        "exceptionPdfCount": 3,
        "copiedPdfCount": 3,
        "missingPdfCount": 0,
    }
    assert result["excludedStatementIndices"] == {
        "alpha": [
            "A02607010000001",
            "A02607020000002",
            "A02607030000003",
        ]
    }
    assert set(result["excludedPdfPaths"]) == {
        str(person_pdf.resolve()),
        str(jd_pdf.resolve()),
        str(tax_pdf.resolve()),
    }
    tax = result["entries"]["alpha__A02607030000003"]
    assert tax["matchMethod"] == "pdf_keyword_date_amount"
    assert Path(tax["copiedPdf"]).is_file()
    assert result["entries"]["alpha__A02607010000001"]["matchMethod"] == "statement_index"
    assert result["entries"]["alpha__A02607020000002"]["downstreamEligible"] is False
    assert all(path.is_file() for path in (person_pdf, jd_pdf, tax_pdf, ordinary_pdf))
    assert manifest_path.is_file()
