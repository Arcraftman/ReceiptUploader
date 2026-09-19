"""Shared command dispatch for source-checkout launchers on all platforms."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from .project_runtime import project_root, using_project

ALIASES = {
    'login': 'login_companies', 'discover': 'discover_companies',
    'month': 'initialize_month', 'bank': 'run_bank', 'run': 'run_company',
    'verify': 'verify_bank', 'exceptions': 'list_bank_exceptions',
    'unmatched': 'list_unmatched_bank', 'report': 'analysis_report',
    'confirm-one': 'confirm_one', 'confirm-all': 'confirm_all',
    'reset-upload-state': 'reset_upload_state', 'create-company': 'create_company_template',
    'test-read-apis': 'test_read_apis',
}


def run(script: str, *args: str) -> None:
    """Invoke a packaged command with explicit arguments; no subprocess for dispatch."""
    import importlib
    module_name = ('finance.' + script.split('/')[-1].removesuffix('.py')
                   if script.startswith('finance/') else 'commands.' + script.removesuffix('.py'))
    module = importlib.import_module('kdzwy_receipt_uploader.' + module_name)
    handler = module.entrypoint if module_name == 'commands.login_companies' else module.main
    try:
        result = handler(list(args))
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
        return
    if result:
        raise SystemExit(result)


def dispatch(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog='kdzwy-receipts',
        description='Kdzwy project console. All platforms use the same commands.',
        epilog='Set KDZWY_PROJECT_ROOT when running an installed command outside the checkout.',
    )
    parser.add_argument('command', nargs='?', help='start, login, discover, month, bank, run, verify, exceptions, unmatched, status, report, confirm-one, confirm-all, reset-upload-state, create-company, test-read-apis, finance, receipts')
    if argv and argv[0] in ('--help', '-h'):
        parser.print_help()
        return 0
    command, *argv = argv or ['start']
    command = ALIASES.get(command, command)
    allowed = set(ALIASES.values()) | {'start', 'status', 'finance', 'finance_server', 'receipts'}
    # Preserve the original installed receipt CLI for existing option-based calls.
    if command.startswith('-') or command == 'receipts':
        from .cli import main as receipt_main
        return receipt_main(([command] + argv) if command != 'receipts' else argv)
    if command not in allowed:
        parser.error(f'Unknown command: {command}')
    if command == 'finance':
        if argv in ([], ['--help'], ['-h']):
            print('Usage: kdzwy-receipts finance {serve|build-template} [options]')
            return 0
        if argv[0] == 'build-template':
            run('finance/build_template.py', *argv[1:])
            return 0
        if argv[0] != 'serve':
            parser.error('Expected: finance serve or finance build-template')
        argv = argv[1:]
        command = 'finance_server'
    root = project_root()
    if command == 'finance_server':
        run('finance/serve.py', *argv)
        return 0
    if command == 'test_read_apis':
        run('test_read_apis.py', *argv)
        return 0
    if command in ('start', 'discover_companies', 'login_companies'):
        run('login_companies.py', '--mode', command, *argv)
        return 0
    if command == 'create_company_template':
        run('create_company.py', *argv)
        return 0
    p = argparse.ArgumentParser(prog=f'kdzwy-receipts {command}')
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
        p.error('company must be a config filename without directories')
    config = root / 'config/companies' / (name + '.json')
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
        print(f'WARNING: This will upload real vouchers: {name} / {args.month}.')
        try:
            answer = input(f'Type {expected} to continue: ')
        except EOFError:
            return 2
        if answer.casefold() != expected.casefold():
            print('Cancelled.')
            return 2
    run('prepare_company_workspace.py', *common, *(['--quiet'] if command == 'run_bank' else []))
    if command == 'run_company':
        run('login_companies.py', '--mode', 'login_companies', '--project-config', str(root / 'data/inbox' / name / args.month / 'project.json'), '--no-pause')
    extra = {
        'run_company': [], 'run_bank': ['--source', 'bank', '--concise'],
        'confirm_one': ['--stage', 'send', '--limit', '1', '--allow-confirm', '--allow-cross-entity-confirm'],
        'confirm_all': ['--allow-confirm', '--allow-cross-entity-confirm'],
    }
    run('run_companies.py', '--jobs-config', str(config), '--month', args.month, *extra[command])
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ('--help', '-h'):
        return dispatch(arguments)
    with using_project(project_root()):
        return dispatch(arguments)


if __name__ == '__main__':
    raise SystemExit(main())
