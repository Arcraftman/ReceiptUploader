"""Package a freshly installed, data-free local finance workbook."""
from pathlib import Path
import hashlib
import json
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
VERSION = '2026.09.20.2'
target = ROOT / 'dist' / f'KdzwyFinance-Local-{VERSION}'
assert (target / 'excel/Finance.xlsm').is_file(), 'Generate the blank XLSM with install-excel.ps1 first.'
for source in (ROOT / 'src').rglob('*'):
    if source.is_file() and '__pycache__' not in source.parts and source.suffix in ('.py', '.json'):
        dest = target / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
for name in (
    'pyproject.toml', 'README.md', 'scripts/start.py',
    'scripts/finance/start-local.ps1', 'scripts/finance/setup-local.ps1',
    'scripts/finance/install-excel.ps1', 'excel/FinanceRefresh.bas',
    'config/finance_read_sources.json', 'config/kdzwy.example.json',
):
    dest = target / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / name, dest)
(target / 'Install.cmd').write_text(
    '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\finance\\setup-local.ps1"\r\npause\r\n', encoding='ascii')
(target / 'VERSION.txt').write_text(VERSION + '\n', encoding='ascii')
(target / '使用说明.txt').write_text('''账无忧财务工作簿：本地测试分发包 2026.09.20.2

需要 Windows 桌面 Microsoft Excel、Python 3.10 或更新版本和网络。无需 py 启动器。
安装器会验证现有 Python，跳过失效的启动器路径。如无法自动找到，可设置 PYTHON_EXE 为有效 python.exe 的完整路径。
此包包含源码与环境安装器；不是免 Python 的独立 EXE 包。

1. 将压缩包完整解压到一个固定目录，不要仅复制 Excel 文件。
2. 双击 Install.cmd。首次安装会下载 Python 依赖与 Chromium 浏览器。
3. 编辑 config/kdzwy.json，将 CHANGE_ME 替换为你自己的账无忧账号密码。
4. 打开 excel/Finance.xlsm，按组织策略启用宏。
5. 点击“登录账无忧”。首次登录自动发现可访问账套，完成后启动本机只读服务。
6. 填写公司编号及 YYYY-MM 月份，点击“刷新财务数据”，完成后自行保存。

19 张工作表中有 1 张隐藏年度数据表；年度报表当前支持 2026 年 1—9 月。
利润、资产负债、收入科目逐月读取至所选月份，数据协议版本为 2。
“研发收入”“销售商品收入”仍沿用 500102/500103 映射，缺少对应科目时为空；尚未确定替代分类依据。
本包是空白模板，不含公司账务数据、密码、令牌或登录会话。
若 18765 端口被其他旧财务服务占用，关闭旧服务后再登录。
''', encoding='utf-8-sig')
manifest = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(target.rglob('*')) if p.is_file() and p.name != 'SHA256.json'}
(target / 'SHA256.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
archive = target.with_suffix('.zip')
# Keep the complete version in the archive filename.
archive = target.parent / (target.name + '.zip')
with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
    for p in sorted(target.rglob('*')):
        if p.is_file():
            z.write(p, target.name + '/' + p.relative_to(target).as_posix())
print(archive)
print('SHA256:', hashlib.sha256(archive.read_bytes()).hexdigest())
