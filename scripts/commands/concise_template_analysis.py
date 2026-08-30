"""Command-line entry point for the human-readable Qwen analysis report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.company_registry import (
    dataset_from_company,
    load_accountbooks,
    load_company_jobs,
    load_company_profile,
    normalize_month,
    resolve_target_accountbook,
    workspace_relative_path,
)
from kdzwy_receipt_uploader.concise_template_analysis import write_concise_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="将 template_analysis.json 转成普通用户可读的标准记账简表")
    parser.add_argument("--month", default="", help="月份目录，例如 2026-08")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc"], default="", help="业务板块")
    parser.add_argument("--company", default="", help="读取 config/companies/<配置文件名>.json，可省略.json")
    parser.add_argument("--input", type=Path, default=None, help="直接指定 template_analysis.json")
    parser.add_argument("--output", type=Path, default=None, help="输出Markdown路径")
    args = parser.parse_args()
    if args.month:
        try:
            args.month = normalize_month(args.month)
        except ValueError as exc:
            parser.error(str(exc))

    if args.input is None:
        source_company_key, month, source = "", args.month.strip(), args.source.strip()
        workspace_input: Path | None = None
        if not args.company:
            parser.error("未传 --input 时必须提供 --company 和 --month")
        if args.company:
            selector = args.company.strip()
            if Path(selector).name != selector:
                parser.error("--company 只能是配置文件名，不能包含目录")
            config_name = selector[:-5] if selector.lower().endswith(".json") else selector
            company_path = ROOT / "config" / "companies" / f"{config_name}.json"
            if not company_path.is_file():
                parser.error(f"公司配置不存在：{company_path}")
            try:
                company = load_company_profile(company_path)
            except ValueError as exc:
                parser.error(str(exc))
            dataset_profile = dataset_from_company(company)
            source_company_key = dataset_profile.key
            if not month:
                parser.error("使用 --company 时必须显式指定 --month")
            project_path = ROOT / dataset_profile.data_root / month / "project.json"
            if not project_path.is_file():
                parser.error(f"月份配置不存在：{project_path}")
            try:
                jobs = load_company_jobs(project_path, company)
            except ValueError as exc:
                parser.error(str(exc))
            if not source:
                enabled = [job.source for job in jobs if job.enabled]
                if len(enabled) != 1:
                    parser.error("月份 project.json 必须只启用一个业务板块，或者显式指定 --source")
                source = enabled[0]
            selected_job = next((job for job in jobs if job.source == source), None)
            if selected_job is None:
                parser.error(f"月份配置不包含业务板块：{source}")
            source_company_key = selected_job.dataset
            try:
                accountbook = resolve_target_accountbook(
                    selected_job,
                    load_accountbooks(ROOT / "runtime" / "registry" / "accountbooks.json"),
                )
            except ValueError as exc:
                parser.error(str(exc))
            workspace_input = (
                ROOT
                / workspace_relative_path(accountbook.login_account or "default", accountbook.key, source_company_key, month)
                / "generated"
                / "ocr"
                / source
                / "template_analysis.json"
            )
        if not source_company_key or not month or not source:
            parser.error("未传 --input 时必须提供 --company、--month，并启用或指定一个 --source")
        if workspace_input is None:
            parser.error("隔离工作区必须通过 --company 定位；维护场景请直接传 --input")
        input_path = workspace_input
    else:
        input_path = args.input if args.input.is_absolute() else ROOT / args.input
    if not input_path.is_file():
        parser.error(f"分析文件不存在：{input_path}")
    output_path = args.output or input_path.with_name("concise_template_analysis.md")
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    report = write_concise_analysis(input_path, output_path)
    print(f"简表生成完成：发票 {report['invoiceCount']} 张")
    print(f"输出：{report['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
