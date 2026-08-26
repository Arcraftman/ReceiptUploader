"""Command-line entry point for the human-readable DeepSeek analysis report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kdzwy_receipt_uploader.concise_template_analysis import write_concise_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="将 template_analysis.json 转成普通用户可读的标准记账简表")
    parser.add_argument("--dataset", default="", help="资料集英文key，例如 weiyu")
    parser.add_argument("--month", default="", help="月份目录，例如 7月")
    parser.add_argument("--source", choices=["sales", "purchase", "bank", "misc"], default="", help="业务板块")
    parser.add_argument("--company", default="", help="读取 config/companies/<key>.json 中的数据集、月份和已启用板块")
    parser.add_argument("--input", type=Path, default=None, help="直接指定 template_analysis.json")
    parser.add_argument("--output", type=Path, default=None, help="输出Markdown路径")
    args = parser.parse_args()

    if args.input is None:
        dataset, month, source = args.dataset.strip(), args.month.strip(), args.source.strip()
        if args.company:
            company_path = ROOT / "config" / "companies" / f"{args.company.strip().lower()}.json"
            if not company_path.is_file():
                parser.error(f"公司配置不存在：{company_path}")
            company = json.loads(company_path.read_text(encoding="utf-8-sig"))
            dataset = dataset or str(company.get("dataset", "")).strip()
            month = month or str(company.get("month", "")).strip()
            if not source:
                sources = company.get("sources", {}) if isinstance(company.get("sources"), dict) else {}
                enabled = [key for key, value in sources.items() if value is True or isinstance(value, dict) and value.get("enabled") is True]
                if len(enabled) != 1:
                    parser.error("公司配置必须只启用一个业务板块，或者显式指定 --source")
                source = enabled[0]
        if not dataset or not month or not source:
            parser.error("未传 --input 时，必须提供 --company，或同时提供 --dataset、--month、--source")
        input_path = ROOT / "data" / "inbox" / dataset / month / "generated" / "ocr" / source / "template_analysis.json"
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
