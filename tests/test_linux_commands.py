from __future__ import annotations
import os
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

from kdzwy_receipt_uploader import command_dispatch as commands
login = module('portable_login', 'src/kdzwy_receipt_uploader/commands/login_companies.py')
scan = module('scan_interfaces', 'scripts/maintenance/scan_read_interfaces.py')

class LinuxCommandTests(unittest.TestCase):
    def test_login_allowed_while_homepage_writes_blocked(self):
        self.assertFalse(login.block_login_page_request('POST', 'https://www.kdzwy.com/bs/guanjia/login'))
        self.assertFalse(login.block_login_page_request('GET', 'https://vip1-gj.kdzwy.com/guanjia/user/info'))
        self.assertTrue(login.block_login_page_request('POST', 'https://vip1-gj.kdzwy.com/guanjia/md'))
        self.assertTrue(login.block_login_page_request('GET', 'https://vip1-gj.kdzwy.com/guanjia/fusion/update/status'))

    @unittest.skipUnless(os.name == "posix", "POSIX executable bits and bash syntax; Windows exercises native bat instead")
    def test_linux_launcher_is_executable_and_syntax_valid(self):
        shell = ROOT / 'scripts/linux/start.sh'
        self.assertTrue(shell.is_file())
        self.assertTrue(shell.stat().st_mode & 0o111)
        subprocess.run(['bash', '-n', str(shell)], check=True)

    def test_chinese_and_space_arguments_preserved(self):
        with patch.object(sys, 'argv', ['start.py','initialize_month','company_123_测试 公司','2026-09','company_456']), patch.object(commands,'run') as run:
            self.assertEqual(commands.main(), 0)
            run.assert_called_once_with('initialize_company_month.py','company_123_测试 公司','2026-09','company_456')

    def test_cancel_and_eof_never_upload_or_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config/companies').mkdir(parents=True)
            (root/'config/companies/company_1_测试.json').write_text('{}')
            for mode in ('confirm_one','confirm_all'):
                for answer in ('no', EOFError()):
                    with patch.object(commands,'project_root',return_value=root), patch.object(sys,'argv',['start.py',mode,'company_1_测试','2026-09']), patch.object(commands,'run') as run, patch('builtins.input', side_effect=answer if isinstance(answer,Exception) else None, return_value=answer):
                        self.assertEqual(commands.main(),2)
                        run.assert_not_called()

    def test_failed_prepare_stops_pipeline(self):
        with patch('kdzwy_receipt_uploader.commands.prepare_company_workspace.main', return_value=17):
            with self.assertRaises(SystemExit) as error:
                commands.run('prepare_company_workspace.py')
            self.assertEqual(error.exception.code,17)

    def test_discovery_paginates_and_checks_total(self):
        session=Mock()
        with patch.object(login,'data',side_effect=[ [{'nodeName':'服务管理','id':9}], {'items':[{'companyId':1,'isCreateAccount':True,'databaseId':'100'}],'hasNextPage':True}, {'items':[{'companyId':2,'isCreateAccount':True,'databaseId':'200'}],'hasNextPage':False,'totalCount':2} ]):
            rows=login.discover(session,'https://vip1-gj.kdzwy.com')
            self.assertEqual(len(rows),2)
            self.assertEqual(session.get.call_args_list[-1].kwargs['params']['page'],2)

    def test_mismatched_identity_is_not_saved(self):
        session=Mock();session.cookies=[];session.headers={}
        response=Mock();response.url='https://vip4-kj.kdzwy.com/accounting/index.html'
        new=Mock();new.headers={};new.cookies.get.return_value='secret-auth';new.get.return_value=response
        with patch('requests.Session',return_value=new),patch.object(login,'data',side_effect=[{},'https://vip4-kj.kdzwy.com/zwy/start',{'access_token':'secret-token'},{'companyId':2,'DBID':'100'}]),patch.object(login,'write_private') as write:
            with self.assertRaisesRegex(RuntimeError,'mismatch'):
                login.book_session(session,'https://vip1-gj.kdzwy.com',{'companyId':1,'databaseId':'100','companyName':'测试'},'a')
            write.assert_not_called()

    def test_session_file_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'session.json'
            login.write_private(p,{'token':'test'})
            if os.name == 'posix':
                self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertEqual(json.loads(p.read_text()),{'token':'test'})
            self.assertFalse(p.with_suffix('.json.tmp').exists())

    def test_get_is_not_automatically_read_only(self):
        for method,url in [('GET','/jdy-fi/{dbId}/gl/v1/itemClass/delete'),('POST','/report/report?m=addRptItem'),('POST','/gl/balance/query?m=addInitBalance'),('POST','/jdy-fi/{dbId}/rpt/v1/profit/report/edit')]:
            self.assertEqual(scan.classify(method,url),'excluded_action_or_ambiguous')
        self.assertEqual(scan.classify('GET','/jdy-fi/{dbId}/fa/v1/type'),'read_candidate')
        self.assertEqual(scan.classify('POST','/jdy-fi/{dbId}/fa/v1/card/list'),'post_query_candidate_needs_review')

if __name__ == '__main__':
    unittest.main()
