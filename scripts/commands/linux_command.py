"""Linux equivalents of the commands/*.bat entry points."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script: str, *args: str) -> None:
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/commands' / script), *args], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    command, *argv = sys.argv[1:]
    if command == 'test_read_apis':
        run('test_read_apis.py', *argv)
        return 0
    if command in ('start', 'discover_companies', 'login_companies'):
        run('login_companies.py', '--mode', command, *argv)
        return 0
    if command == 'create_company_template':
        run('create_company.py', *argv)
        return 0
    p = argparse.ArgumentParser(prog=f'commands/{command}.sh')
    if command != 'status':
        p.add_argument('company')
        p.add_argument('month')
    if command == 'initialize_month':
        p.add_argument('target')
    if command in ('analysis_report', 'reset_upload_state'):
        p.add_argument('source', nargs='?', choices=['sales', 'purchase', 'bank', 'misc'] + (['all'] if command == 'reset_upload_state' else []))
    args = p.parse_args(argv)
    if command == 'status':
        run('pipeline_status.py')
        return 0
    if command == 'initialize_month':
        run('initialize_company_month.py', args.company, args.month, args.target)
        return 0
    if command == 'analysis_report':
        run('concise_template_analysis.py', '--company', args.company, '--month', args.month, *(['--source', args.source] if args.source else []))
        return 0
    if command == 'reset_upload_state':
        run('reset_upload_state.py', args.company, args.month, *(['--source', args.source] if args.source else []))
        return 0
    name = args.company.removesuffix('.json')
    if Path(name).name != name:
        p.error('company 必须是配置文件名，不能包含路径')
    config = ROOT / 'config/companies' / (name + '.json')
    if not config.is_file():
        p.error(f'Company config not found: {config}')
    common = ['--config', str(config), '--month', args.month]
    reads = {'list_bank_exceptions': 'list_bank_exceptions.py', 'list_unmatched_bank': 'list_unmatched_bank_records.py', 'verify_bank': 'verify_bank_receipts.py'}
    if command in reads:
        run(reads[command], *common)
        return 0
    if command in ('confirm_one', 'confirm_all'):
        expected = f'{name} {args.month}'
        if command == 'confirm_all':
            expected = 'UPLOAD ALL ' + expected
        print(f'将真实上传凭证：{name} / {args.month}。')
        try:
            answer = input(f'Type {expected} to continue: ')
        except EOFError:
            return 2
        if answer.casefold() != expected.casefold():
            print('Cancelled.')
            return 2
    run('prepare_company_workspace.py', *common, *(['--quiet'] if command == 'run_bank' else []))
    if command == 'run_company':
        run('login_companies.py', '--mode', 'login_companies', '--project-config', str(ROOT / 'data/inbox' / name / args.month / 'project.json'), '--no-pause')
    extra = {
        'run_company': [], 'run_bank': ['--source', 'bank', '--concise'],
        'confirm_one': ['--stage', 'send', '--limit', '1', '--allow-confirm', '--allow-cross-entity-confirm'],
        'confirm_all': ['--allow-confirm', '--allow-cross-entity-confirm'],
    }
    run('run_companies.py', '--jobs-config', str(config), '--month', args.month, *extra[command])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
