import argparse,json,re,sys
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scan_read_interfaces import WRITE
parser=argparse.ArgumentParser(description='Open reviewed read pages with mutation requests blocked; record response schemas only.')
parser.add_argument('--session',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--only',action='append',help='Restrict to a reviewed page path; repeatable')
parser.add_argument('--extended',action='store_true',help='Also inspect legacy ledgers and cashier read pages')
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=True)
session=json.loads(args.session.read_text(encoding='utf-8-sig'))
origin='https://'+urlsplit(session['target_url']).netloc
db=session['dbid']

paths=['/voucher/voucherList','/voucher/voucherSummary','/books/general-ledger','/books/subject-balance','/books/accounting-balance','/books/multicol-acct','/books/qty-amount-detail','/books/qtyAmount-general','/report/balance-sheets','/report/profit-sheets','/report/item-profit-sheets','/report/cash-flow-std-sheets','/report/fee-detail-report','/assets/fixed-assets-cards','/assets/depreciation-detail-sheets','/assets/depreciation-summary-sheets','/assets/fixed-assets-change-record','/assets/fixed-assets-class','/pay/salary-list','/pay/salary-report','/pay/employee','/settings/subject-list','/settings/voucher-word','/settings/currency','/settings/assist-acc','/settings/fin-initial','/invoice/manage','/invoice/tax-burden']
if args.extended:
 paths=['/books/subsidiary-ledger.jsp','/books/accounting-ledger.jsp','/books/accounting-combination.jsp','/settings/operation-log-new.jsp','/home/index.html#/cashier/journal?keepRoute=true','/home/index.html#/cashier/incomeExpenses?keepRoute=true','/home/index.html#/cashier/checkTotal?keepRoute=true','/home/index.html#/cashier/bank-reconcile?keepRoute=true','/home/index.html#/cashier/account?keepRoute=true']
if args.only:
 unknown=set(args.only)-set(paths)
 if unknown:parser.error('Unreviewed page path: '+str(sorted(unknown)))
 paths=args.only
# Explicit read query allowlist; no wildcard POST authorization.
posts=['/sys/v1/init','/v1/balance-item-report/query','/v1/cost-detail/list','/v1/gl/balance-report','/v1/gl/general/query-total','/v1/gl/multicolaccount/query','/v1/qtyDetailAccount/detail','/v1/qtyTotalAccount/detail','/fa/v1/card/list','/gl/v1/finance/balance','/gl/v1/finance/balance-item','/gl/v1/voucher/find-balance','/ca/v1/journal-list','/pay/v1/sheet-list','/pay/v1/sheet/statMonthPay','/vat/v1/invoice-list']
rows=[];blocked=[];page_results=[];current='bootstrap'
def safe_url(u):
 p=urlsplit(u);q=parse_qs(p.query);return p.path,sorted(q)
with sync_playwright() as p:
 b=p.chromium.launch(headless=True);c=b.new_context(locale='zh-CN')
 c.add_cookies([{k:v for k,v in ck.items() if k in ('name','value','domain','path','secure')} for ck in session['cookies'] if ck['domain'].lstrip('.').endswith('kdzwy.com') and ck['name'] != 'authCode'])
 c.add_init_script('if(location.origin === '+json.dumps(origin)+') localStorage.setItem("accessToken",'+json.dumps(session['access_token'])+');')
 def guard(route):
  r=route.request;u=urlsplit(r.url);path=u.path
  if r.resource_type in ('image','font','media'):
   route.abort();return
  if r.resource_type not in ('xhr','fetch','websocket'):
   route.continue_();return
  if u.netloc!=urlsplit(origin).netloc:route.abort();return
  allowed=False
  if r.method in ('GET','HEAD'):
   allowed=not WRITE.search(path+'?'+u.query) and not re.search(r'(?i)nextnum|getvchnum|token|/ws/|reportview',path+'?'+u.query)
   if path=='/gl/generatecode' and parse_qs(u.query).get('m')==['findAll']:allowed=True
  elif r.method=='POST':allowed=any(path.endswith(x) for x in posts)
  if allowed:route.continue_()
  else:
   blocked.append({'method':r.method,'path':path,'page':current});route.abort()
 c.route('**/*',guard)
 def capture(response):
  req=response.request;u=urlsplit(response.url)
  if u.netloc!=urlsplit(origin).netloc or req.resource_type not in ('xhr','fetch'):return
  try:d=response.json()
  except:return
  if not isinstance(d,(dict,list)):return
  # Record shape and status only, never accounting rows, tokens or personal details.
  record={'method':req.method,'path':u.path.replace(db,'{dbId}'),'query':parse_qs(u.query),'page':current,'http_status':response.status}
  if req.post_data:
   try:record['body']=json.loads(req.post_data)
   except:record['body_keys']=sorted(parse_qs(req.post_data))
  if isinstance(d,dict):
   record['response_keys']=list(d)
   record['business_status']={k:d[k] for k in ('status','code','errcode','errorcode','success') if k in d}
   record['message']=str(d.get('msg') or d.get('message') or d.get('description') or '')[:160]
   value=d.get('data');record['data_type']=type(value).__name__
   if isinstance(value,dict):
    record['data_keys']=list(value)
    record['counts']={k:len(v) for k,v in value.items() if isinstance(v,list)}
   elif isinstance(value,list):record['count']=len(value)
  rows.append(record)
 c.on('response',capture)
 page=c.new_page();page.set_default_timeout(8000)
 page.goto(origin+'/accounting/index.html?dbId='+db,wait_until='domcontentloaded',timeout=30000)
 page.wait_for_function('!!(window.tab && window.tab.addTabItem)',timeout=30000)
 for routepath in paths:
  current=routepath
  try:
   page.evaluate('(p)=>window.tab.addTabItem({tabid:p,text:p,url:p,isIframe:p.includes(".jsp")||p.startsWith("/home/")})',routepath)
   page.wait_for_timeout(3000)
   page_results.append({'path':routepath,'url_path':urlsplit(page.url).path,'title':page.title()})
  except Exception as e:page_results.append({'path':routepath,'error':type(e).__name__})
  print('查询页面',routepath,'responses',len(rows),flush=True)
  out.joinpath('live_page_probes.json').write_text(json.dumps({'responses':rows,'blocked_requests':blocked,'pages':page_results},ensure_ascii=False,indent=2))
 b.close()
