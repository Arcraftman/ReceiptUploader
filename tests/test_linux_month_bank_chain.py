"""Native shell month initialization and bank preparation on Linux and Windows."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import openpyxl
import pymupdf

from kdzwy_receipt_uploader.company_registry import load_company_profile, load_company_jobs
from kdzwy_receipt_uploader.bank_receipt_splitter import split_configured_bank_pdfs
from kdzwy_receipt_uploader.bank_receipt_ocr import run_bank_receipt_ocr
from kdzwy_receipt_uploader.bank_statement_matcher import match_bank_statements
from kdzwy_receipt_uploader.bank_final_receipts import (
    load_bank_records, build_bank_ocr_artifacts, source_values, generate_bank_final_receipts,
)
from kdzwy_receipt_uploader.receipts_ocr import analyze_ocr_and_choose_template

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def test_linux_month_to_bank_prepare(tmp_path):
    # Run the shipped shell dispatcher and initializer, including its real
    # prepare_company_workspace subprocess, in a disposable project.
    shutil.copytree(ROOT / 'schema', tmp_path / 'schema')
    for folder in ['scripts/linux', 'scripts/win']:
        shutil.copytree(ROOT / folder, tmp_path / folder)
    shutil.copy2(ROOT / 'scripts/start.py', tmp_path / 'scripts/start.py')
    if os.name == 'nt':
        shutil.copytree(ROOT / 'src', tmp_path / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    else:
        (tmp_path / 'src').symlink_to(ROOT / 'src', target_is_directory=True)
    key = 'company_1'
    config_name = 'company_1_测试公司.json'
    config_path = tmp_path / 'config/companies' / config_name
    write(config_path, {'version': 3, 'company_key': key, 'company_id': '1',
                        'company_name': '测试公司', 'template_company': key})
    write(tmp_path / 'runtime/registry/accountbooks.json', {'version': 2, 'accountbooks': [
        {'key': key, 'name': '测试公司', 'company_id': '1', 'enabled': True,
         'login_account': 'account_1', 'session_file': 'unused.cookies.json'},
        {'key': 'company_2', 'name': '目标公司', 'company_id': '2', 'enabled': True,
         'login_account': 'account_2', 'session_file': 'unused2.cookies.json'},
        {'key': 'company_3', 'name': '新公司', 'company_id': '3', 'enabled': True,
         'login_account': 'account_1', 'session_file': 'unused3.cookies.json'},
    ]})
    write(tmp_path / 'config/bank_exception.defaults.json', {'version': 2, 'exceptions': [], 'pdf_keywords': {}})
    template_root = tmp_path / 'templates' / key
    shutil.copytree(ROOT / 'templates/company_17867515', template_root)
    command = ([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', r'scripts\win\start.bat']
               if os.name == 'nt' else ['bash', 'scripts/linux/start.sh'])
    command += ['month', config_name, '2026-09', 'company_2']
    env = {**os.environ, 'PYTHON_EXE': sys.executable, 'PYTHONUTF8': '1', 'PYTHONPATH': str(tmp_path / 'src'), 'KDZWY_PROJECT_ROOT': str(tmp_path),
           'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}
    write(tmp_path / 'config/template_companies.json', {
        'version': 2, 'default_base_template': key,
        'template_companies': [{'key': key, 'name': '测试公司', 'directory': key, 'enabled': True}],
    })
    created = subprocess.run([sys.executable, '-m', 'kdzwy_receipt_uploader.commands.create_company', '--name', '新公司'],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert created.returncode == 0, created.stdout + created.stderr
    assert json.loads((tmp_path / 'config/companies/company_3_新公司.json').read_text())['template_company'] == 'company_3'
    assert (tmp_path / 'templates/company_3/index.json').exists()
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    month = tmp_path / 'data/inbox/company_1_测试公司/2026-09'
    project_path = month / 'project.json'
    project = json.loads(project_path.read_text())
    assert 'upload_to_dataset_enabled' not in project
    profile = load_company_profile(config_path)
    jobs = load_company_jobs(project_path, profile)
    assert len(jobs) == 4 and all(not job.enabled for job in jobs)

    # The same command must produce a loadable cross-company month too.
    cross = subprocess.run(command[:-2] + ['2026-10', 'company_2'], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=30)
    assert cross.returncode == 0, cross.stdout + cross.stderr
    cross_path = month.parent / '2026-10/project.json'
    assert all(job.accountbook == 'company_2' for job in load_company_jobs(cross_path, profile))

    columns = {'index_column': 'A', 'bank_debit_column': 'B', 'bank_credit_column': 'C',
               'counterparty_name_column': 'D', 'remark_column': 'E'}
    bank = {'enabled': True, 'bank_account_number': '100203',
            'split': {'parts_per_page': 1, 'filename_index_length': 7, 'filename_index_prefix': 'A'}}
    project['sources']['bank'].update(enabled=True, banks={'alpha': bank}, statement_columns={'alpha': columns})
    write(project_path, project)
    bank_job = next(j for j in load_company_jobs(project_path, profile) if j.source == 'bank')
    configs = bank_job.overrides['banks']
    input_dir = month / 'input/bank'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((60, 60), 'A123456\nbank fee\n2026-09-01\n12.30')
        pdf.save(input_dir / 'alpha.pdf')
    wb = openpyxl.Workbook()
    wb.active.append(['流水号', '借方', '贷方', '对方', '备注'])
    wb.active.append(['A123456', 12.3, 0, '测试银行', '银行手续费'])
    wb.save(input_dir / 'alpha.xlsx')
    wb.close()
    generated = tmp_path / 'workspaces/account_1/company_1/2026-09/generated'
    split = split_configured_bank_pdfs(configs, input_dir, generated / 'bank_receipts', generated / 'split.json')
    ocr = run_bank_receipt_ocr(split, generated / 'ocr/bank', workers=1, company='测试公司',
                              ocr_runner=lambda path: ('流水号 A123456\n银行手续费\n记账日期：2026-09-01\n金额12.30', 'fake-ocr'))
    map_path = generated / 'maps/bank/bank_map.json'
    report_path = map_path.with_name('bank_map.report.json')
    report = match_bank_statements(configs, input_dir, ocr, map_path, report_path, config_company='测试公司')
    assert report['summary']['matchedCount'] == 1
    matched, _ = load_bank_records(map_path, report_path)
    artifact = build_bank_ocr_artifacts(matched)[0]
    record = matched[artifact.invoice_code]

    class Selector:
        called = False
        def choose(self, text, candidates, invoice_code, **kwargs):
            self.called = True
            assert len(candidates) == 1
            return {'templatePath': candidates[0]['path'], 'templateId': candidates[0]['id'],
                    'confidence': 0.99, 'selectionMode': 'llm', 'llmAttempted': True}

    selector = Selector()
    context = {'businessMapValues': source_values(record), 'dynamicAccountCatalog': {'accounts': [
        {'number': '220201', 'id': 'payable', 'fullName': '应付账款_人民币户'},
        {'number': '100203', 'id': 'bank', 'fullName': '银行存款_测试银行'},
    ]}, 'dynamicItemClassCatalog': {'classes': [{'itemClassId':5, 'items':[{'id':'supplier', 'number':'1', 'name':'测试银行'}]}]}}
    decision = analyze_ocr_and_choose_template(artifact, template_root, selector=selector, final_template_context=context)
    assert not selector.called and decision['analysisStatus'] == 'ready_for_review', decision
    out = generated / 'receipts/bank'
    result = generate_bank_final_receipts(matched, {artifact.invoice_code: decision}, out, key, '2026-09', {})
    assert result['summary']['generatedCount'] == 1
    receipt = json.loads(next(out.rglob('receipt.json')).read_text())
    assert receipt['draft'] is True
    assert receipt['voucher']['date'] == '2026-09-01'
    assert [entry['accountNumber'] for entry in receipt['voucher']['entries']] == ['220201', '100203']
    assert sum(entry['dc'] * float(entry['amount']) for entry in receipt['voucher']['entries']) == 0
    # Re-initialization retains explicit bank configuration and never re-enables other sources.
    again = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(project_path.read_text())['sources'] == project['sources']
