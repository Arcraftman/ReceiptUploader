"""Portable authorized login, company discovery and safe setup console.

Install optional login dependencies with: pip install '.[discovery]'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    temp.chmod(0o600)
    temp.replace(path)


def check_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or not (p.hostname == 'kdzwy.com' or p.hostname.endswith('.kdzwy.com')):
        raise RuntimeError('服务器返回了非账无忧 HTTPS 地址，停止登录')
    return p


def session_from_state(state):
    import requests
    s = requests.Session()
    for c in state['cookies']:
        if c['domain'].lstrip('.').endswith('kdzwy.com'):
            s.cookies.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'))
    s.headers.update({'User-Agent': 'Mozilla/5.0 KdzwyReceiptUploader/0.1', 'X-Requested-With': 'XMLHttpRequest'})
    return s


def block_login_page_request(method, url):
    path = urlsplit(url).path.lower()
    return path.startswith('/guanjia/') and (
        method not in ('GET', 'HEAD')
        or any(x in path for x in ('/update', '/insert', '/save', '/delete', '/logout', '/record'))
    )


def data(response):
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError('登录已失效或服务器没有返回 JSON') from None
    if not isinstance(result, dict):
        raise RuntimeError('服务器响应结构异常')
    for key in ('code', 'status', 'errorcode', 'errcode'):
        if result.get(key) not in (None, 0, 200, '0', '200'):
            raise RuntimeError(f'接口业务失败：{key}={result[key]}，请在官方页面检查登录状态或验证码')
    if result.get('success') is False:
        raise RuntimeError('接口返回 success=false')
    return result.get('data')


def login_account(account, headed=False, reuse=True):
    key = account['key']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
        raise RuntimeError('登录账号 key 只能包含字母、数字、下划线和连字符')
    path = ROOT / 'http_sessions/accounts' / key / 'browser.session.json'
    if reuse and path.exists():
        state = json.loads(path.read_text())
        origin = state.get('guanjia_origin')
        if origin:
            check_url(origin)
            s = session_from_state(state)
            try:
                data(s.get(origin + '/guanjia/user/info', timeout=30))
                return s, origin, state
            except Exception:
                pass
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not headed)
        try:
            c = b.new_context(locale='zh-CN')
            # Homepage telemetry/preferences are not needed for login.
            def guard(route):
                r = route.request
                if block_login_page_request(r.method, r.url):
                    route.abort()
                else:
                    route.continue_()
            c.route('**/*', guard)
            page = c.new_page()
            page.goto('https://gj.kdzwy.com/', wait_until='domcontentloaded', timeout=60000)
            page.locator('#log-account').fill(account['username'])
            page.locator('#log-pwd').fill(account['password'])
            page.locator('#sub-btn').click()
            try:
                page.wait_for_url('**/acct-web/guanjia/**', timeout=180000 if headed else 45000)
            except Exception:
                raise RuntimeError('登录未完成；如需验证码，请运行 commands/login_companies.sh --headed 在浏览器完成验证') from None
            origin = 'https://' + check_url(page.url).netloc
            state = c.storage_state()
            state['guanjia_origin'] = origin
            write_private(path, state)
            return session_from_state(state), origin, state
        finally:
            b.close()


def discover(s, origin):
    nodes = data(s.get(origin + '/guanjia/acctflow/selfnode', timeout=30))
    service = [n for n in nodes if n.get('nodeName') == '服务管理']
    if len(service) != 1:
        raise RuntimeError('无法唯一确定服务管理节点')
    rows = []
    page = 1
    while True:
        d = data(s.get(origin + '/guanjia/acctflow/nodecustomer', params={'nodeId': service[0]['id'], 'page': page, 'limit': 100, 'orderProperty': 'acctCreateDate', 'orderDirection': 'desc'}, timeout=30))
        rows.extend(d['items'])
        if not d.get('hasNextPage'):
            if len(rows) != int(d['totalCount']):
                raise RuntimeError('公司分页数量与 totalCount 不一致')
            break
        page += 1
        if page > 1000:
            raise RuntimeError('公司分页异常')
    return [r for r in rows if r.get('isCreateAccount') and str(r.get('databaseId') or '0') != '0']


def book_session(master, origin, row, account_key):
    import requests
    # Separate cookie jars prevent one company's login contaminating another.
    s = requests.Session()
    s.headers.update(master.headers)
    for cookie in master.cookies:
        if '-kj.' not in cookie.domain:
            s.cookies.set_cookie(__import__('copy').copy(cookie))
    cid = str(row['companyId'])
    data(s.get(origin + '/guanjia/customer/accounturl/before', params={'recycle': 0, 'companyId': cid}, timeout=30))
    url = data(s.get(origin + '/guanjia/customer/accounturl', params={'companyId': cid}, timeout=30))
    host = check_url(url).hostname
    response = s.get(url, timeout=30)
    response.raise_for_status()
    parsed = check_url(response.url)
    if parsed.hostname != host:
        raise RuntimeError('账套登录跳转到了其他域')
    book_origin = 'https://' + parsed.netloc
    code = s.cookies.get('authCode', domain=host)
    if not code:
        raise RuntimeError('账套登录未返回 authCode')
    token = data(s.post(book_origin + '/auth/exchangeToken', json={'authCode': code}, timeout=30))['access_token']
    s.headers['app-token'] = token
    system = data(s.get(book_origin + '/basedata/initParams?m=getSystemParams', timeout=30))
    if str(system.get('companyId')) != cid or str(system.get('DBID')) != str(row['databaseId']):
        raise RuntimeError('登录结果公司 ID / DBID 不一致，拒绝保存')
    cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path, 'secure': c.secure} for c in s.cookies]
    path = ROOT / 'http_sessions/accounts' / account_key / 'companies' / f'company_{cid}.accountbook.cookies.json'
    payload = {'target_url': response.url, 'cookies': cookies, 'dbid': str(system['DBID']), 'access_token': token, 'company_name': row['companyName'], 'company_id': cid}
    write_private(path, payload)
    return {'key': f'company_{cid}', 'name': row['companyName'], 'company_id': cid, 'login_account': account_key, 'enabled': True, 'session_file': path.relative_to(ROOT).as_posix()}


def resolve_selector(rows, selector):
    selector = selector.removesuffix('.json')
    matches = [r for r in rows if selector in (r['key'], r['company_id'], r['name'], r['key'] + '_' + r['name'])]
    if len(matches) != 1:
        raise RuntimeError(f'无法唯一识别公司：{selector}')
    return matches[0]


def initialize(rows, dataset, month, target):
    source = resolve_selector(rows, dataset)
    dest = resolve_selector(rows, target)
    from kdzwy_receipt_uploader.company_registry import company_config_filename
    filename = company_config_filename(source['company_id'], source['name']).removesuffix('.json')
    config = ROOT / 'config/companies' / (filename + '.json')
    if not config.exists():
        subprocess.run([sys.executable, str(ROOT / 'scripts/commands/create_company.py'), '--name', source['name']], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/commands/initialize_company_month.py'), filename, month, dest['key']], check=True)


def console(rows):
    print('安全菜单：list | login | discover | month DATASET YYYY-MM TARGET | status | help | quit')
    while True:
        try:
            words = shlex.split(input('kdzwy> '))
        except EOFError:
            return
        if not words:
            continue
        cmd, *args = words
        try:
            if cmd in ('quit', 'exit'):
                return
            if cmd == 'list' and not args:
                for row in rows:
                    print(row['key'], row['name'])
            elif cmd == 'month' and len(args) == 3:
                initialize(rows, *args)
            elif cmd in ('login', 'discover') and not args:
                subprocess.run([sys.executable, __file__, '--mode', cmd + '_companies'], check=True)
                rows = json.loads((ROOT / 'runtime/registry/accountbooks.json').read_text())['accountbooks']
            elif cmd == 'status' and not args:
                subprocess.run([sys.executable, str(ROOT / 'scripts/commands/pipeline_status.py')], check=True)
            elif cmd in ('bank', 'exceptions', 'unmatched', 'verify') and len(args) == 2:
                row = resolve_selector(rows, args[0])
                mapping = {'bank': 'run_bank', 'exceptions': 'list_bank_exceptions', 'unmatched': 'list_unmatched_bank', 'verify': 'verify_bank'}
                from kdzwy_receipt_uploader.company_registry import company_config_filename
                config_name = company_config_filename(row['company_id'], row['name']).removesuffix('.json')
                subprocess.run([sys.executable, str(ROOT / 'scripts/commands/linux_command.py'), mapping[cmd], config_name, args[1]], check=True)
            else:
                print('list | login | discover | month DATASET YYYY-MM TARGET | bank/exceptions/unmatched/verify DATASET YYYY-MM | status | quit')
        except (RuntimeError, subprocess.CalledProcessError) as e:
            print(str(e))


def main(lock_file=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['start', 'discover_companies', 'login_companies'], default='login_companies')
    p.add_argument('--accountbook-key')
    p.add_argument('--project-config', type=Path)
    p.add_argument('--dataset', '-Dataset')
    p.add_argument('--month', '-Month')
    p.add_argument('--target', '-Target')
    p.add_argument('--no-pause', action='store_true')
    p.add_argument('--quiet', action='store_true')
    p.add_argument('--headed', action='store_true', help='显示浏览器，允许手动完成验证码')
    p.add_argument('--refresh', action='store_true', help='重新登录主账号，不复用会话')
    args = p.parse_args()
    if any((args.dataset, args.month, args.target)) and not all((args.dataset, args.month, args.target)):
        p.error('--dataset、--month、--target 必须一起指定')
    if args.project_config:
        from kdzwy_receipt_uploader.company_registry import load_company_profile, load_company_jobs
        project_path = args.project_config.resolve()
        company = load_company_profile(ROOT / 'config/companies' / (project_path.parent.parent.name + '.json'))
        jobs = load_company_jobs(project_path, company)
        keys = {job.accountbook for job in jobs}
        if len(keys) != 1:
            raise RuntimeError('月份配置没有唯一目标账套')
        resolved_key = keys.pop()
        if args.accountbook_key and args.accountbook_key != resolved_key:
            raise RuntimeError('--accountbook-key 与月份目标不一致')
        args.accountbook_key = resolved_key
    registry = ROOT / 'runtime/registry/accountbooks.json'
    previous = json.loads(registry.read_text())['accountbooks'] if registry.exists() else []
    if args.mode == 'login_companies' and not previous:
        raise RuntimeError('没有账套注册表，请先运行 commands/discover_companies.sh')
    config = json.loads((ROOT / 'config/kdzwy.json').read_text(encoding='utf-8-sig'))
    result = []
    for account in config['accounts']:
        if not account.get('enabled', True):
            continue
        s, origin, state = login_account(account, args.headed, not args.refresh)
        rows = discover(s, origin)
        for row in rows:
            key = 'company_' + str(row['companyId'])
            if args.accountbook_key and key != args.accountbook_key:
                continue
            if args.mode == 'login_companies' and not any(r['key'] == key and r['login_account'] == account['key'] and r.get('enabled', True) for r in previous):
                continue
            record = book_session(s, origin, row, account['key'])
            result.append(record)
            if not args.quiet:
                print('会话已验证：', key, row['companyName'], flush=True)
    if not result:
        raise RuntimeError('未发现可刷新会话的账套')
    if args.mode == 'login_companies' or args.accountbook_key:
        replacements = {r['key']: r for r in result}
        result = [dict(r, session_file=replacements[r['key']]['session_file']) if r['key'] in replacements else r for r in previous]
    if len({r['key'] for r in result}) != len(result):
        raise RuntimeError('多个登录账号返回重复公司 ID，拒绝覆盖注册表')
    write_private(registry, {'version': 2, 'accountbooks': result})
    print(f'已保存 {len(result)} 个账套；未执行 OCR、凭证生成或上传。')
    if args.dataset:
        initialize(result, args.dataset, args.month, args.target)
    if args.mode == 'start':
        if lock_file is not None:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        console(result)
    return 0


if __name__ == '__main__':
    try:
        # Shared lock covers start, discover and login; fail rather than overlap.
        import fcntl
        lock = ROOT / 'runtime/locks/company_login.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise SystemExit(main(f))
    except (RuntimeError, OSError, ValueError, ImportError) as exc:
        message = type(exc).__name__ if type(exc).__module__.startswith(('requests', 'urllib3')) else str(exc)
        print(f'登录未完成：{message}', file=sys.stderr)
        raise SystemExit(1)
