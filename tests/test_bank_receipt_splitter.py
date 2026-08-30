from __future__ import annotations

import json
from pathlib import Path

import pytest

from kdzwy_receipt_uploader.bank_receipt_splitter import BankReceiptSplitError, _load_rules


def write_config(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_bank_split_rules_require_direct_mapping(tmp_path: Path) -> None:
    config = tmp_path / "bank_split.json"
    write_config(config, {"shanghaiyinhang": 2, "shanghainongshangyinhang": 3})
    assert _load_rules(config) == {"shanghaiyinhang": 2, "shanghainongshangyinhang": 3}

    write_config(config, {"banks": {"shanghaiyinhang": 2}})
    with pytest.raises(BankReceiptSplitError):
        _load_rules(config)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"上海银行": 2},
        {"ShanghaiYinhang": 2},
        {"shanghaiyinhang ": 2},
        {"shanghaiyinhang": True},
        {"shanghaiyinhang": "2"},
        {"shanghaiyinhang": 0},
        {"shanghaiyinhang": 11},
    ],
)
def test_bank_split_rules_reject_invalid_keys_and_counts(tmp_path: Path, payload: object) -> None:
    config = tmp_path / "bank_split.json"
    write_config(config, payload)
    with pytest.raises(BankReceiptSplitError):
        _load_rules(config)
