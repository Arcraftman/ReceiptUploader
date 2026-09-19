"""Download published frontend assets and statically inventory API calls.

Only HTML/JS assets are requested by this command. Business API probes are
separately reviewed and recorded; discovered URLs are never blindly replayed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.inline = []
        self.in_script = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.in_script = not attrs.get('src')
            if attrs.get('src'):
                self.urls.append(attrs['src'])
    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False
    def handle_data(self, text):
        if self.in_script:
            self.inline.append(text)


WRITE = re.compile(r'(?i)(?:^|[/=?&_-])(?:save|delete|remove|update|insert|create|upload|import|restore|recover|reset|unbind|bind(?:$|[/-])|logout|recalculate|rebuild|refresh|generate|gen-voucher|close(?:$|[/?-])|audit|submit|send|enable|disable|clean|cancel|commit|withdraw|copy|sync|adjust|repair|check-out|mark(?:$|[/-])|edit|add|auto-match|automatch|reclassify|reclasssify|set(?:$|[/-])|(?-i:set[A-Z]))')
READ_POST = re.compile(r'(?i)(?:/(?:list|page|query|search|find-balance|find|detail|details|summary|balance|statistic|report)(?:$|[/?-])|[?&]m=(?:query|list|find|findAll|get))')


def classify(method, url):
    # A GET verb is not a guarantee of read-only behavior.
    if WRITE.search(url):
        return 'excluded_action_or_ambiguous'
    if method == 'GET':
        return 'read_candidate'
    if method == 'POST' and READ_POST.search(url):
        return 'post_query_candidate_needs_review'
    return 'unknown_or_write'


def download(session_path, out, extra_pages=()):
    import requests
    payload = json.loads(session_path.read_text(encoding='utf-8-sig'))
    origin = 'https://' + urlsplit(payload['target_url']).netloc
    client = requests.Session()
    for c in payload['cookies']:
        client.cookies.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'))
    client.headers['app-token'] = payload['access_token']
    pending = deque([origin + '/accounting/index.html', *[urljoin(origin, x) for x in extra_pages]])
    cached = {}
    for mf in ('assets_manifest.json', 'pages_manifest.json'):
        if (out / mf).exists():
            cached.update({r['url']: r for r in json.loads((out / mf).read_text()) if r.get('file') and '#inline-script-' not in r['url']})
    seen = set()
    manifest = []
    pages = []
    out.mkdir(parents=True, exist_ok=True)
    asset_dir = out / 'assets'
    asset_dir.mkdir(exist_ok=True)
    static_origin = 'https://static.kdzwy.com'
    def enqueue(url, base):
        u = urljoin(base, url)
        p = urlsplit(u)
        if p.scheme == 'https' and p.netloc in (urlsplit(origin).netloc, 'static.kdzwy.com'):
            if u not in seen:
                pending.append(u)
    while pending:
        url = pending.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            # Never forward app-token to the asset CDN.
            old = cached.get(url)
            if old and Path(old['file']).exists():
                body = Path(old['file']).read_bytes()
                if hashlib.sha256(body).hexdigest() != old['sha256']:
                    raise ValueError('cached asset hash mismatch')
                status = old.get('status', 200)
            else:
                response = client.get(url, timeout=30, allow_redirects=False) if url.startswith(origin + '/') else requests.get(url, timeout=30, allow_redirects=False)
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError('asset redirect refused; refresh session or inspect published asset URL')
                if urlsplit(response.url).netloc != urlsplit(url).netloc:
                    raise ValueError('redirected away from asset host (session expired?)')
                body, status = response.content, response.status_code
            text = body.decode('utf-8', errors='replace')
            if urlsplit(url).path.endswith('.js') and text.lstrip().startswith('<'):
                raise ValueError('JS returned HTML')
            local = asset_dir / (hashlib.sha256(url.encode()).hexdigest()[:12] + '_' + Path(urlsplit(url).path).name)
            local.write_bytes(body)
            record = {'url': url, 'file': str(local.resolve()), 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'status': status}
            if urlsplit(url).path.endswith('.js'):
                manifest.append(record)
            else:
                pages.append(record)
                html = Scripts()
                html.feed(text)
                for js in html.urls:
                    enqueue(js, url)
                for index, inline in enumerate(html.inline):
                    f = local.with_name(local.name + f'.inline{index}.js')
                    f.write_text(inline)
                    manifest.append({'url': url + f'#inline-script-{index}', 'file': str(f.resolve()), 'bytes': len(inline.encode()), 'sha256': hashlib.sha256(inline.encode()).hexdigest()})
            # Expand precisely the published Webpack chunk table, not guessed URLs.
            for match in re.finditer(r'(static_production_\d+)/js/.*?\.chunk\.js', text):
                version = match.group(1)
                for number, digest in re.findall(r'(\d+):"([a-f0-9]+)"', match.group()):
                    enqueue(f'/saas/vip1/accounting/{version}/js/{number}.{digest}.chunk.js', static_origin)
            # Vue/Webpack's named lazy chunks in the observed cashier application.
            if '/home/static/js/app.' in url:
                table = re.search(r'static/js/.*?\.js', text)
                if table:
                    for name, digest in re.findall(r'"(chunk-[a-f0-9]+)":"([a-f0-9]+)"', table.group()):
                        enqueue('/home/static/js/' + name + '.' + digest + '.js', origin)
            # Read-only legacy page shells referenced by this deployed frontend.
            for page in re.findall(r'["\'](/(?:books|reports|settings|voucher)/[^"\'<>\s?]+\.(?:jsp|html))["\']', text):
                if not re.search(r'(?i)backup|restore|import|reset|start|logout|add|edit', page):
                    enqueue(page, origin)
        except Exception as exc:
            manifest.append({'url': url, 'error': type(exc).__name__ + ': ' + str(exc)[:160]})
        if len(seen) % 25 == 0:
            print(f'前端资源：已处理 {len(seen)}，待处理 {len(pending)}', flush=True)
    (out / 'assets_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (out / 'pages_manifest.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    return manifest, pages


def inventory(out):
    extracted = json.loads((out / 'static_calls.json').read_text())
    grouped = {}
    for call in extracted['calls']:
        key = (call['method'], call['url'])
        if key not in grouped:
            grouped[key] = {'method': key[0], 'url_template': key[1], 'classification': classify(*key), 'verification': 'not_probed', 'query_keys': sorted({k for k, _ in parse_qsl(urlsplit(key[1]).query)}), 'evidence': []}
        if len(grouped[key]['evidence']) < 5:
            grouped[key]['evidence'].append({k: call[k] for k in ('source', 'file', 'line', 'column', 'parameters_expression', 'evidence')})
    rows = sorted(grouped.values(), key=lambda r:(r['classification'], r['url_template'], r['method']))
    result = {'scanned_at': datetime.now(timezone.utc).isoformat(), 'scope': 'Published JS and referenced legacy page scripts; static candidates are not proof of permission or read-only semantics.', 'counts': dict(Counter(r['classification'] for r in rows)), 'parse_failures': extracted['parse_failures'], 'interfaces': rows}
    (out / 'interfaces.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'runtime/interface_scan/current')
    p.add_argument('--acorn-module', required=True, help='Path to installed acorn Node module')
    p.add_argument('--page', action='append', default=[], help='Additional observed same-origin HTML entry point')
    p.add_argument('--reuse-assets', action='store_true')
    args = p.parse_args()
    if not args.reuse_assets:
        download(args.session, args.output, args.page)
    subprocess.run(['node', str(ROOT / 'scripts/maintenance/extract_frontend_apis.cjs'), str(Path(args.acorn_module).resolve()), str(args.output / 'assets_manifest.json'), str(args.output / 'static_calls.json')], check=True)
    result = inventory(args.output)
    print(json.dumps(result['counts'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
