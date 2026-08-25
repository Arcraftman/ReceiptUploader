"""Command-line entry point for the human-readable DeepSeek analysis report."""
from __future__ import annotations

import argparse
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
    parser.add_argument("--input", type=Path, default=None, help="直接指定 template_analysis.json")
    parser.add_argument("--output", type=Path, default=None, help="输出Markdown路径")
    args = parser.parse_args()

    if args.input is None:
        if not args.dataset or not args.month:
            parser.error("未传 --input 时，必须同时传 --dataset 和 --month")
        input_path = ROOT / "data" / "inbox" / args.dataset / args.month / "receipts_ocr" / "template_analysis.json"
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
