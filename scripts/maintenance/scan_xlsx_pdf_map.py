"""CLI for the reusable XLSX/PDF invoice-number matcher."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.matching import match_company_directory, match_month_directory  # noqa: E402
from kdzwy_receipt_uploader.company_registry import load_accountbooks, workspace_relative_path  # noqa: E402
from kdzwy_receipt_uploader.month_config import MonthConfig, MonthConfigError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按解压目录匹配 XLSX A 列发票号与 PDF 文件名")
    parser.add_argument("--company-dir", type=Path, default=None, help="公司资料目录；公司模式下必须显式指定")
    parser.add_argument("--output-dir", type=Path, default=None, help="映射输出目录；必须位于项目 workspaces 内")
    parser.add_argument("--month-dir", type=Path, default=None, help="单个月份目录；读取该目录下 project.json")
    args = parser.parse_args()
    project_root = PROJECT_ROOT
    month_dir = args.month_dir
    if month_dir is not None:
        if not month_dir.is_absolute():
            month_dir = project_root / month_dir
        month_dir = month_dir.resolve()
        project_config_path = month_dir / "project.json"
        if not project_config_path.is_file():
            print(f"月份目录没有标准 project.json：{month_dir}")
            return 2
        try:
            project_config = json.loads(project_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"project.json 无法读取：{exc}")
            return 2
        try:
            company_key = str(project_config["company_key"])
            month = str(project_config["month"])
            target_key = str(project_config["target"]["accountbook_key"])
            accountbook = load_accountbooks(
                project_root / "runtime" / "registry" / "accountbooks.json"
            )[target_key]
            config = MonthConfig.from_mapping(company_key, month, project_config.get("input"))
        except (KeyError, TypeError, MonthConfigError, ValueError) as exc:
            print(f"project.json 配置错误：{exc}")
            return 2
        default_output = (
            project_root
            / workspace_relative_path(
                accountbook.login_account or "default",
                accountbook.key,
                company_key,
                month,
            )
            / "generated"
            / "maps"
            / "maintenance"
        )
        output_dir = args.output_dir or default_output
        output_dir = output_dir if output_dir.is_absolute() else project_root / output_dir
        output_dir = output_dir.resolve()
        workspaces_root = (project_root / "workspaces").resolve()
        try:
            output_dir.relative_to(workspaces_root)
        except ValueError:
            print(f"映射输出目录必须位于 workspaces：{output_dir}")
            return 2
        input_dir = month_dir / "input"
        report = match_month_directory(input_dir, config, output_dir)
        summary = report["summary"]
        print("月份扫描完成")
        print(f"月份目录：{month_dir}")
        print(f"用途确认信息 {config.usage_column} 列发票号数量：{summary['usageConfirmNumberCount']}")
        print(f"purchase 文件夹 XLSX A 列发票号数量：{summary['purchaseFolderNumberCount']}")
        print(f"匹配数字数量：{summary['matchedCount']}")
        print(f"空值数量：{summary['emptyCount']}")
        print(f"map 输出：{output_dir / 'xlsx_pdf_map.json'}")
        return 0
    if args.company_dir is None or args.output_dir is None:
        parser.error("公司模式必须同时指定 --company-dir 和 --output-dir；单月模式请使用 --month-dir")
    company_dir = args.company_dir if args.company_dir.is_absolute() else project_root / args.company_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_root / args.output_dir
    company_dir = company_dir.resolve()
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to((project_root / "workspaces").resolve())
    except ValueError:
        print(f"映射输出目录必须位于 workspaces：{output_dir}")
        return 2
    if not company_dir.is_dir():
        print(f"公司目录不存在：{company_dir}")
        return 2
    report = match_company_directory(company_dir, output_dir)
    summary = report["summary"]
    print("扫描完成")
    print(f"公司目录：{company_dir}")
    print(f"XLSX 数字键数量：{summary['xlsxNumberCount']}")
    print(f"匹配数字数量：{summary['matchedCount']}")
    print(f"未匹配数字数量：{summary['unmatchedCount']}")
    print(f"目录内重复 PDF 匹配数量：{summary['duplicateCount']}")
    print(f"跨目录重复数字数量：{summary['crossDirectoryDuplicateCount']}")
    print(f"map 输出：{output_dir / 'xlsx_pdf_map.json'}")
    print(f"报告输出：{output_dir / 'xlsx_pdf_map.report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
