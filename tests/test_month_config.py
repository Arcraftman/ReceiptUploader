from __future__ import annotations

import pytest

from kdzwy_receipt_uploader.month_config import MonthConfig, MonthConfigError


def test_month_input_uses_project_mapping() -> None:
    config = MonthConfig.from_mapping("company_17867515", "2026-08", {
        "income_cost_filename": "收入成本表.xlsx",
        "usage_filename": "用途确认信息.xlsx",
        "usage_column": "E",
    })
    assert config.company == "company_17867515"
    assert config.month == "2026-08"
    assert config.usage_column == "E"


@pytest.mark.parametrize(
    "company,month,value",
    [
        ("company_17867515", "8月", {}),
        ("company_17867515", "2026-08", {"output_dirname": "maps"}),
        ("", "2026-08", {}),
    ],
)
def test_month_config_rejects_removed_shapes(company: str, month: str, value: object) -> None:
    with pytest.raises(MonthConfigError):
        MonthConfig.from_mapping(company, month, value)
