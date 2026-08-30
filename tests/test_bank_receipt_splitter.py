from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from kdzwy_receipt_uploader import bank_receipt_splitter
from kdzwy_receipt_uploader.bank_receipt_splitter import BankReceiptSplitError
from kdzwy_receipt_uploader.company_registry import (
    CompanyRegistryError,
    validate_bank_configs,
)


def bank_config(
    *, parts: int = 2, length: int = 8, prefix: str = "T"
) -> dict[str, object]:
    return {
        "testbank": {
            "bank_account_number": "100201",
            "split": {
                "parts_per_page": parts,
                "filename_index_length": length,
                "filename_index_prefix": prefix,
            },
            "statement_columns": {
                "index_column": None,
                "bank_debit_column": None,
                "bank_credit_column": None,
                "counterparty_name_column": None,
            },
        }
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"上海银行": bank_config()["testbank"]},
        {"TestBank": bank_config()["testbank"]},
        {"testbank": {}},
        {
            "testbank": {
                "bank_account_number": "100201",
                "split": {
                    "parts_per_page": 0,
                    "filename_index_length": 8,
                    "filename_index_prefix": "T",
                },
                "statement_columns": bank_config()["testbank"]["statement_columns"],
            }
        },
        {
            "testbank": {
                "bank_account_number": "100201",
                "split": {
                    "parts_per_page": 2,
                    "filename_index_length": 5,
                    "filename_index_prefix": "T",
                },
                "statement_columns": bank_config()["testbank"]["statement_columns"],
            }
        },
        {
            "testbank": {
                "bank_account_number": "100201",
                "split": {
                    "parts_per_page": 2,
                    "filename_index_length": 8,
                    "filename_index_prefix": "1",
                },
                "statement_columns": bank_config()["testbank"]["statement_columns"],
            }
        },
        {
            "testbank": {
                "bank_account_number": "100201",
                "split": bank_config()["testbank"]["split"],
                "statement_columns": {
                    "index_column": None,
                    "bank_debit_column": None,
                },
            }
        },
    ],
)
def test_unified_bank_config_rejects_invalid_rules(payload: object) -> None:
    with pytest.raises(CompanyRegistryError):
        validate_bank_configs(payload, "sources.bank.banks")


def test_unified_bank_config_preserves_prefix_case_and_multiple_banks() -> None:
    payload = bank_config(prefix="c", length=15)
    payload["secondbank"] = {
        "bank_account_number": "100204",
        "split": {
            "parts_per_page": 3,
            "filename_index_length": 16,
            "filename_index_prefix": "V",
        },
        "statement_columns": {
            "index_column": "流水号",
            "bank_debit_column": "借方",
            "bank_credit_column": "贷方",
            "counterparty_name_column": "对方名称",
        },
    }
    normalized = validate_bank_configs(payload, "sources.bank.banks")
    assert set(normalized) == {"testbank", "secondbank"}
    assert normalized["testbank"]["split"]["filename_index_prefix"] == "c"
    assert normalized["secondbank"]["bank_account_number"] == "100204"


def test_filename_index_priority_and_configured_length() -> None:
    text = "回单编号：743B3T2387161\n交易流水号：C0347HE000ZBT4Z"
    assert bank_receipt_splitter._extract_filename_index(text, 15, "C") == (
        "C0347HE000ZBT4Z",
        "transaction_serial",
    )
    assert bank_receipt_splitter._extract_filename_index(
        "回单编号：T43B3T2387161", 13, "T"
    ) == ("T43B3T2387161", "receipt_number")
    assert bank_receipt_splitter._extract_filename_index(
        "无标签索引 C0347HE001CUWWZ", 15, "C"
    ) == ("C0347HE001CUWWZ", "configured_C_15")
    assert bank_receipt_splitter._extract_filename_index(
        "核心流水号：V026073100843134", 16, "V"
    ) == ("V026073100843134", "transaction_serial")


def test_filename_index_prefix_is_case_sensitive_and_preserved() -> None:
    assert bank_receipt_splitter._extract_filename_index(
        "交易流水：C0347HE000ZBT4Z", 15, "c"
    ) == ("", "bank_exception")
    assert bank_receipt_splitter._extract_filename_index(
        "无标签索引 c0347HE000ZBT4Z", 15, "C"
    ) == ("", "bank_exception")
    assert bank_receipt_splitter._extract_filename_index(
        "无标签索引 c0347HE000ZBT4Z", 15, "c"
    ) == ("c0347HE000ZBT4Z", "configured_c_15")


def test_empty_unified_bank_config_is_rejected(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    with pytest.raises(BankReceiptSplitError, match="至少配置一家银行"):
        bank_receipt_splitter.split_configured_bank_pdfs(
            {}, input_dir, tmp_path / "generated", tmp_path / "report.json"
        )


def test_bank_exception_receipts_are_normal_outputs_and_can_be_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source_pdf = input_dir / "testbank.pdf"
    with pymupdf.open() as document:
        document.new_page(width=100, height=200)
        document.save(source_pdf)

    monkeypatch.setattr(
        bank_receipt_splitter,
        "_recognize_filename_index",
        lambda page, configured_length, configured_prefix: (
            "", "test-no-number", "bank_exception"
        ),
    )
    output_root = tmp_path / "generated"
    report_path = output_root / "split.report.json"
    config = bank_config()
    report = bank_receipt_splitter.split_configured_bank_pdfs(
        config, input_dir, output_root, report_path
    )

    assert report["configSource"] == "project.json.sources.bank.banks"
    assert report["summary"]["receiptCount"] == 2
    assert report["summary"]["recognizedReceiptCount"] == 0
    assert report["summary"]["bankExceptionReceiptCount"] == 2
    assert len(list((output_root / "testbank" / "bank_exception").glob("*.pdf"))) == 2

    orphan = output_root / "testbank" / "legacy-name.pdf"
    orphan.write_bytes(b"stale generated output")
    reused = bank_receipt_splitter.split_configured_bank_pdfs(
        config, input_dir, output_root, report_path
    )
    assert reused["summary"]["reusedBankCount"] == 1
    assert reused["summary"]["bankExceptionReceiptCount"] == 2
    assert not orphan.exists()
