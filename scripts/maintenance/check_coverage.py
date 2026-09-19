"""Fail closed when critical line/branch coverage drops below reviewed floors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(report: dict, policy: dict) -> list[str]:
    files = {key.replace('\\', '/'): value for key, value in report['files'].items()}
    errors = []
    for gate in policy['gates']:
        label = gate['file'] + ('::' + gate['function'] if 'function' in gate else '')
        data = files.get(gate['file'])
        if data is not None and 'function' in gate:
            data = data.get('functions', {}).get(gate['function'])
        if data is None:
            errors.append(f'{label}: missing coverage data')
            continue
        summary = data['summary']
        for name, covered, total in [('line', 'covered_lines', 'num_statements'),
                                     ('branch', 'covered_branches', 'num_branches')]:
            denominator = summary[total]
            percent = 100 * summary[covered] / denominator if denominator else 100.0
            minimum = gate[name]
            print(f'{label} {name}={percent:.1f}% (minimum {minimum}%)')
            if percent + 1e-9 < minimum:
                errors.append(f'{label}: {name} {percent:.1f}% < {minimum}%')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, default=ROOT/'coverage.json')
    parser.add_argument('--policy', type=Path, default=ROOT/'config/quality/coverage.json')
    args = parser.parse_args()
    errors = check(json.loads(args.report.read_text(encoding='utf-8')),
                   json.loads(args.policy.read_text(encoding='utf-8')))
    if errors:
        print('\n'.join(errors))
        return 1
    print('Critical coverage gates passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
