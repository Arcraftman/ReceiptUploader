"""Assemble the macro workbook with its frozen Windows runtime and browser."""
from pathlib import Path
import hashlib
import json
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
VERSION = '2026.09.21.7'
target = ROOT / 'dist' / f'KdzwyFinance-Windows-x64-{VERSION}'
payload = target / 'payload'
assert (payload / 'excel/Finance.xlsm').is_file()
shutil.copytree(ROOT / 'dist/finance-release-build/bin/KdzwyFinance', payload / 'app', dirs_exist_ok=True)
old = ROOT / 'dist/KdzwyFinance-Windows-x64-2026.09.19.1'
if not old.is_dir():
    old = ROOT / 'dist/KdzwyFinance-Windows-x64-2026.09.21.4'
shutil.copytree(old / 'payload/app/browsers', payload / 'app/browsers', dirs_exist_ok=True)
shutil.copytree(old / 'payload/licenses', payload / 'licenses', dirs_exist_ok=True)
(payload / 'config').mkdir(exist_ok=True)
shutil.copy2(ROOT / 'config/finance_read_sources.json', payload / 'config/finance_read_sources.json')
installer = (old / 'Install.ps1').read_text(encoding='utf-8-sig')
installer = installer.replace("Join-Path $Destination 'Finance.xlsm'", "Join-Path $Destination 'excel/Finance.xlsm'")
installer = installer.replace("'SHA256.json') -Raw", "'SHA256.json') -Encoding UTF8 -Raw")
(target / 'Install.ps1').write_text(installer, encoding='utf-8-sig')
shutil.copy2(old / 'Install.cmd', target / 'Install.cmd')
(target / 'VERSION.txt').write_text(VERSION, encoding='ascii')
(target / '使用说明.txt').write_text('''账无忧 Excel 宏发行版 2026.09.21.7

要求：Windows 10/11 64 位、桌面 Microsoft Excel、网络连接。
已内置运行组件和 Chromium 浏览器，不需要安装 Python、运行 pip 或配置 py 启动器。

1. 完整解压压缩包，双击 Install.cmd，安装到当前用户目录并创建桌面快捷方式。
2. 打开桌面快捷方式，按组织宏策略启用工作簿中的宏。
3. 点击“登录账无忧”，在弹出的登录窗口输入账号和密码（密码不回显），完成浏览器验证。
   窗口会显示会话确认、账套列表、逐个账套连接和服务启动进度。看到“登录完成”后再刷新。
4. 返回工作簿，在控制台填写公司编号和月份，再点击“刷新财务数据”。
5. 在财务分析看板点击“配置DeepSeek”，输入自己的 API Key。密钥使用 Windows 当前用户加密保存在本机，不写入工作簿。
   也可使用 DEEPSEEK_API_KEY 环境变量（优先）。配置后点击“生成图表解读”；以后刷新财务数据会自动生成。
   仅向 DeepSeek 发送月份及看板汇总数值，不发送账号、公司编号和凭证明细；调用使用接收方自己的 API 额度。
   相同数据使用本地解读缓存；接口失败会显示原因并保留已刷新财务数据。
6. 刷新完成后保存工作簿。

登录完成时自动清理同一 Windows 用户的旧版财务服务，保留登录会话后启动新版。
服务禁止重复绑定18767端口；启动失败会退出本次子进程，详细原因写入 runtime/finance/service-start.log。

也可以直接使用 payload/excel/Finance.xlsm，但必须保留相邻 app 和 config 目录。
不能只发单个 XLSM；需要将整个发行包发给接收方。
所有密码均不写入工作簿或配置文件；登录会话保存在安装目录，发行包不含会话。

新版包含22张表（1张隐藏年度数据表），利润和资产负债等逐月读取到所选月份。
新增“财务分析看板”，展示年初至所选月份的收入、成本、期间费用、净利润、利润率和6张分析图表。
六张图表各有独立的 DeepSeek 解读文本框，图表之间保留宽松留白。
“2026研发费用”和“2026年利润和负债”均已补齐1—12月模板。按所选月份截止，后续月份保留框架并留空。
研发费用页仅保留居中的明细表，整行按年初至所选月份合计金额降序排序。
新增“高新企业相关指标监测”，自动计算序号2、3，序号1保留指标原文和门槛，比值及比较结果留空。
指标沿用参考文件当年累计管理监测口径：研发费用/营业收入、研发收入/营业收入；不代表正式三年认定结果，正式申报需补历史数据及高新收入分类。
研发费用取43010101—43010108末级科目本期借方，保留红字冲销，不扣结转贷方；其他科目体系需先配置映射。
年度模板支持2026年1—12月，包含第四季度和全年累计；由12月切回早期月份后刷新，会清除后续月份旧数据。
“研发收入”“销售商品收入”仍依赖对应科目映射，未配置时不会自动拆分国内业务收入。
本包是空白工作簿，不含实测公司的账务数据。
服务仅监听本机127.0.0.1:18767；使用完毕可关闭后台KdzwyFinance进程。
''', encoding='utf-8-sig')
manifest = {p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(payload.rglob('*')) if p.is_file()}
(target / 'SHA256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
archive = target.parent / (target.name + '.zip')
with ZipFile(archive, 'w', ZIP_DEFLATED, compresslevel=5) as z:
    for p in sorted(target.rglob('*')):
        if p.is_file():
            z.write(p, target.name + '/' + p.relative_to(target).as_posix())
print(archive)
print('MiB:', round(archive.stat().st_size / 1024**2, 1))
