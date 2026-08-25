from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MonthConfig:
    company: str
    month: str
    enabled: bool = True
    income_cost_filename: str = "收入成本表.xlsx"
    usage_filename: str = "用途确认信息.xlsx"
    usage_column: str = "E"
    output_dirname: str = "maps"

    @classmethod
    def load(cls, path: Path) -> "MonthConfig":
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        section = parser["processing"] if parser.has_section("processing") else {}
        return cls(
            company=section.get("company", path.parent.parent.name),
            month=section.get("month", path.parent.name),
            enabled=section.getboolean("enabled", fallback=True),
            income_cost_filename=section.get("income_cost_filename", "收入成本表.xlsx"),
            usage_filename=section.get("usage_filename", "用途确认信息.xlsx"),
            usage_column=section.get("usage_column", "E"),
            output_dirname=section.get("output_dirname", "maps"),
        )


def discover_month_configs(inbox_root: Path) -> list[tuple[Path, MonthConfig]]:
    result: list[tuple[Path, MonthConfig]] = []
    for company_dir in sorted(path for path in inbox_root.iterdir() if path.is_dir()):
        for month_dir in sorted(path for path in company_dir.iterdir() if path.is_dir()):
            configs = sorted(month_dir.glob("*.conf"))
            if configs:
                result.append((month_dir, MonthConfig.load(configs[0])))
    return result
