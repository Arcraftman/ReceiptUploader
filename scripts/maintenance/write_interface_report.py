"""Publish a credential-free interface inventory from local scan evidence."""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def key(method, url, query=None):
    query = query if query is not None else parse_qs(urlsplit(url).query)
    action = query.get('m', [])
    return method, urlsplit(url).path, action[0] if action else ''


def ok(row):
    status = row.get('business_status', {})
    return row['http_status'] == 200 and bool(status) and all(
        v is True if k == 'success' else not isinstance(v, bool) and v in (0, '0', 200, '200')
        for k, v in status.items()
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan-dir', type=Path, required=True)
    p.add_argument('--probe', type=Path, action='append', default=[])
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    static = json.loads((args.scan_dir / 'interfaces.json').read_text())
    observed = {}
    blocked = []
    for path in args.probe:
        data = json.loads(path.read_text())
        blocked.extend(data.get('blocked_requests', []))
        for row in data.get('responses', []):
            if row['path'].endswith('/sys/v1/init') or row['path'].startswith(('/auth/', '/vip-spi/')):
                continue
            k = key(row['method'], row['path'], row['query'])
            if k not in observed or (ok(row) and not ok(observed[k])):
                observed[k] = row
    def redact(value):
        if isinstance(value, dict):
            return {k: ('{dbId}' if k.lower() in ('dbid','databaseid') else redact(v)) for k,v in value.items() if not any(x in k.lower() for x in ('token','password','cookie','authcode'))}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value
    for row in static['interfaces']:
        match = observed.get(key(row['method'], row['url_template']))
        if match:
            row['verification'] = 'verified_business_success' if ok(match) else 'observed_not_confirmed'
            row['observed_request_and_schema'] = redact(match)
    counts = Counter(r['classification'] for r in static['interfaces'])
    verified = [redact(r) for r in observed.values() if ok(r)]
    verified.sort(key=lambda r:(r['path'],r['method']))
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    # Keep evidence source URLs and line/column; omit machine-specific cache paths.
    for row in static['interfaces']:
        for e in row['evidence']:
            e.pop('file', None)
    (output / 'all_interfaces.json').write_text(json.dumps(static, ensure_ascii=False, indent=2)+'\n')
    candidates = [r for r in static['interfaces'] if r['classification'] in ('read_candidate','post_query_candidate_needs_review') or r['verification']=='verified_business_success']
    (output / 'read_candidates.json').write_text(json.dumps(candidates, ensure_ascii=False, indent=2)+'\n')
    (output / 'verified_reads.json').write_text(json.dumps(verified, ensure_ascii=False, indent=2)+'\n')
    with (output / 'all_interfaces.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f);w.writerow(['method','url_template','classification','verification','query_keys','parameter_expression','source','line','column'])
        for r in static['interfaces']:
            e=r['evidence'][0]
            w.writerow([r['method'],r['url_template'],r['classification'],r['verification'],','.join(r['query_keys']),e['parameters_expression'],e['source'],e['line'],e['column']])
    assets=json.loads((args.scan_dir/'assets_manifest.json').read_text())
    pages=json.loads((args.scan_dir/'pages_manifest.json').read_text())
    sources=[{k:v for k,v in r.items() if k!='file'} for r in [*assets,*pages]]
    (output/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n')
    summary={'scan_date':static['scanned_at'],'static_unique_method_url_pairs':len(static['interfaces']),'static_classification_counts':dict(counts),'verified_unique_method_path_action':len(verified),'downloaded_resources':len([r for r in sources if '#inline-script-' not in r['url']]),'parse_failures':static['parse_failures'],'asset_failures':[r for r in sources if 'error' in r],'blocked_request_count':len(blocked)}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    md=['# 账无忧只读接口实测清单','','扫描日期：'+static['scanned_at'][:10]+'。域名：`https://vip4-kj.kdzwy.com`。', '',f'静态提取 {len(static["interfaces"])} 个请求方式与 URL 模板组合；GET 读取候选 {counts["read_candidate"]} 个，POST 查询候选（待逐项确认）{counts["post_query_candidate_needs_review"]} 个。实测业务成功的读取接口 {len(verified)} 个（按方法、路径、m 动作去重）。', '', '候选与实测口径不同，不能直接相加。GET 并不保证只读；例如 `GET /jdy-fi/{dbId}/gl/v1/itemClass/delete` 已排除。','', '参数表仅列实际观察到的字段，不表示字段均必填。完整请求示例、返回字段和成功状态见 [verified_reads.json](verified_reads.json)；源码证据见 [all_interfaces.json](all_interfaces.json)。', '', '| 方法 | 路径 / 动作 | 实测参数字段 | 返回数据字段 / 类型 |','|---|---|---|---|']
    for r in verified:
        action=r['query'].get('m',[]);url=r['path']+('?m='+action[0] if action else '')
        params=set(r['query']);params.update(r.get('body',{}).keys() if isinstance(r.get('body'),dict) else r.get('body_keys',[]))
        shape=', '.join(r.get('data_keys',[])[:16]) or r.get('data_type','')
        md.append(f"| {r['method']} | `{url}` | {', '.join(sorted(params)) or '无'} | {shape} |")
    (output/'VERIFIED_READS.md').write_text('\n'.join(md)+'\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    main()
