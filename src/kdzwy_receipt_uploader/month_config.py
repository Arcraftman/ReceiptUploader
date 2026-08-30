from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


MONTH_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
INPUT_KEYS = {
    "income_cost_filename",
    "usage_filename",
    "usage_column",
}


class MonthConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MonthConfig:
    """Validated month input settings embedded in project.json/run.json."""

    company: str
    month: str
    income_cost_filename: str = "收入成本表.xlsx"
    usage_filename: str = "用途确认信息.xlsx"
    usage_column: str = "E"

    @classmethod
    def from_mapping(
        cls,
        company: str,
        month: str,
        value: Mapping[str, Any] | None,
    ) -> "MonthConfig":
        company_key = str(company or "").strip()
        normalized_month = str(month or "").strip()
        if not company_key:
            raise MonthConfigError("月份输入配置缺少资料公司")
        if not MONTH_PATTERN.fullmatch(normalized_month):
            raise MonthConfigError("月份必须严格使用 YYYY-MM")
        if not isinstance(value, Mapping):
            raise MonthConfigError("project.input 必须是对象")
        unexpected = sorted(set(value) - INPUT_KEYS)
        if unexpected:
            raise MonthConfigError("project.input 包含不支持的字段：" + ", ".join(unexpected))
        fields = {
            "income_cost_filename": str(value.get("income_cost_filename") or "").strip(),
            "usage_filename": str(value.get("usage_filename") or "").strip(),
            "usage_column": str(value.get("usage_column") or "").strip(),
        }
        missing = [key for key, field_value in fields.items() if not field_value]
        if missing:
            raise MonthConfigError("project.input 缺少字段：" + ", ".join(missing))
        return cls(company=company_key, month=normalized_month, **fields)
