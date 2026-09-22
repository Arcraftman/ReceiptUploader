"""Self-contained Windows login and read-only finance service for the XLSM release."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from kdzwy_receipt_uploader.commands.login_companies import login_account, discover, book_session, write_private
from kdzwy_receipt_uploader.finance.serve import main as serve
from kdzwy_receipt_uploader.finance.service_lifecycle import ensure_service
from kdzwy_receipt_uploader.project_runtime import using_project
from kdzwy_receipt_uploader.upload_journal import exclusive_lock, UploadInProgress

PORT = 18767


def health(root):
    try:
        token = (root / 'runtime/finance/access.token').read_text(encoding='ascii').strip()
        req = urllib.request.Request(f'http://127.0.0.1:{PORT}/health', headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.load(response)
        return data.get('schema') == '2' and data.get('readOnly') is True
    except (OSError, ValueError):
        return False


def login(root):
    try:
        print('账无忧财务登录（密码输入时不回显，不保存到工作簿或配置文件）')
        user = input('账号：').strip()
        secret = getpass.getpass('密码：')
        if not user or not secret:
            raise ValueError('请填写账号和密码。')
        print('正在打开浏览器，请完成登录验证。')
        with exclusive_lock(root / 'runtime/locks/company_login.lock'):
            account = {'key': 'account_' + hashlib.sha256(user.encode()).hexdigest()[:16], 'username': user, 'password': secret}
            session, origin, _ = login_account(account, headed=True, reuse=False,
                                              progress=lambda message: print(message, flush=True))
            print('网页登录成功，正在读取可访问账套列表……', flush=True)
            rows = discover(session, origin)
            print(f'发现 {len(rows)} 个账套，正在逐个建立本机会话。账套较多时需要几分钟。', flush=True)
            records = []
            for index, row in enumerate(rows, 1):
                print(f"[{index}/{len(rows)}] 正在连接 company_{row['companyId']}：{row['companyName']}", flush=True)
                records.append(book_session(session, origin, row, account['key']))
            if not records:
                raise RuntimeError('没有发现可访问的账套。')
            write_private(root / 'runtime/registry/accountbooks.json', {'version': 2, 'accountbooks': records})
        if not health(root):
            print('账套连接完成，正在启动本机财务服务……', flush=True)
            ensure_service(root, health)
        print('登录完成。返回 Excel 填写公司编号和月份，点击刷新。')
        for record in records:
            print(record['key'], record['name'])
    except (Exception, KeyboardInterrupt) as exc:
        message = '已有登录正在进行，请先关闭该登录浏览器后重试。' if isinstance(exc, UploadInProgress) else str(exc) or '已取消登录。'
        print('登录失败：', message, flush=True)
        print('本次登录已结束，窗口将自动关闭，可以重新点击登录。', flush=True)
        time.sleep(2)
        return 1
    input('按回车关闭窗口。')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['login', 'serve', 'self-check', 'configure-deepseek'])
    args = parser.parse_args()
    root = Path(sys.executable).resolve().parent.parent
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(root / 'app/browsers')
    os.environ['KDZWY_PROJECT_ROOT'] = str(root)
    with using_project(root):
        if args.command == 'serve':
            try:
                serve(['--port', str(PORT)])
            except Exception as exc:
                print('财务服务启动失败：', exc, file=sys.stderr, flush=True)
                return 1
        elif args.command == 'login':
            return login(root)
        elif args.command == 'configure-deepseek':
            from kdzwy_receipt_uploader.finance.private_key import protect
            print('配置 DeepSeek：密钥仅加密保存在当前 Windows 用户的本机目录，不写入工作簿。')
            key = getpass.getpass('DeepSeek API Key（不回显）：').strip()
            if key:
                path = root / 'runtime/finance/deepseek.key'
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(protect(key.encode('utf-8')))
                print('已保存。返回 Excel 点击“生成图表解读”；如果配置了环境变量，将优先使用环境变量。')
            input('按回车关闭窗口。')
        else:
            from playwright.sync_api import sync_playwright
            json.loads((root / 'config/finance_read_sources.json').read_text(encoding='utf-8'))
            with sync_playwright() as p:
                browser_path = Path(p.chromium.executable_path)
                assert browser_path.is_file(), 'Bundled browser missing'
                browser = p.chromium.launch(executable_path=str(browser_path), headless=True)
                page = browser.new_page()
                page.set_content('<title>Finance self-check</title>')
                assert page.title() == 'Finance self-check'
                browser.close()
            print('OK: bundled Python, browser, config; schema 2')


if __name__ == '__main__':
    sys.exit(main())
