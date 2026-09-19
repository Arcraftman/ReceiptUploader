"""Run explicit live, read-only API contract checks for one registered company."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from kdzwy_receipt_uploader.read_api_checks import CheckFailure, run_company_checks, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('company', help='Registered company_<id>, not a guessed DBID')
    parser.add_argument('month', help='Explicit YYYY-MM')
    parser.add_argument('--output', type=Path, help='Defaults to runtime/read_api_tests/<company>/<month>')
    parser.add_argument('--strict-skips', action='store_true', help='Also return nonzero when data-dependent checks are skipped')
    args = parser.parse_args()
    try:
        report = run_company_checks(
            ROOT, args.company, args.month,
            lambda r: print(f'{r["status"]:7} {r["label"]}' + (f'：{r["reason"]}' if r.get('reason') else ''), flush=True),
        )
    except (CheckFailure, OSError, ValueError, RuntimeError) as exc:
        print('测试配置无效：' + type(exc).__name__, file=sys.stderr)
        return 2
    output = args.output or ROOT / 'runtime/read_api_tests' / args.company / args.month
    write_report(report, output)
    print('结果：' + ', '.join(f'{k}={v}' for k, v in report['summary'].items()))
    print('报告：' + str((output / 'RESULTS.md').resolve()))
    bad = sum(report['summary'].get(k, 0) for k in ('failed', 'blocked'))
    return 1 if bad or (args.strict_skips and report['summary'].get('skipped', 0)) else 0


if __name__ == '__main__':
    raise SystemExit(main())
