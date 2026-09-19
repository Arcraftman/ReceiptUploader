# 用户命令

Windows 使用本目录中的 BAT；Linux 使用同名 `.sh`，16 个入口均有对应版本。完整流程见 [当前操作手册](../docs/USAGE.md)。

Linux 首次安装登录依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[discovery]'
.venv/bin/python -m playwright install chromium
commands/start.sh
```

如系统缺少 Chromium 动态库，按 Playwright 的安装提示补齐。入口依次选择 `PYTHON_EXE`、`.venv/bin/python`、`.auto/bin/python`、`python3`。中文及含空格的参数应加引号；执行失败会保留退出码，不自动暂停。

Linux 登录实现不依赖当前仓库缺失的 `scripts/windows/*.ps1`：通过官方登录页登录，随后以 HTTP 请求发现公司、分页读取列表并获取独立账套会话。每份会话保存前核对公司 ID 和 DBID，文件权限为 `0600`；运行期注册表仍为 v2。`--headed` 可显示浏览器以人工处理验证码，`--refresh` 强制刷新主账号登录。

```sh
commands/discover_companies.sh
commands/login_companies.sh --accountbook-key company_23354453 --no-pause
commands/initialize_month.sh COMPANY_CONFIG_NAME YYYY-MM TARGET_COMPANY_ID_OR_KEY
commands/run_company.sh COMPANY_CONFIG_NAME YYYY-MM
```

`start.sh` 登录并进入设置菜单，不自动运行业务。`confirm_one.sh` 和 `confirm_all.sh` 保留与 BAT 相同的真实上传二次确认；本次扫描没有执行这些上传操作。普通运行入口仍由现有 Python 上传安全门拦截未经确认的 `send/all`。

接口分析与复现方法见 [只读接口扫描报告](../docs/KDZwy_READ_API_REPORT.md)。

| 命令 | 用途 |
|---|---|
| `start.bat` | 推荐入口：发现公司、刷新会话并进入安全菜单 |
| `discover_companies.bat` | 重新发现全部可访问公司；可同时初始化指定月份 |
| `login_companies.bat` | 刷新运行期账套注册表中的 HTTP 会话 |
| `create_company_template.bat` | 为新资料公司创建跨月份共享模板和 v3 公司配置 |
| `initialize_month.bat` | 创建该公司、该月份的 `project.json` v8 和四类资料目录 |
| `run_bank.bat` | 只读取当月配置，并按 bank 的统一 stage 执行 |
| `list_bank_exceptions.bat` | 列出已从普通流程分离的特殊记录、裁剪原件和特殊副本，不执行后续业务 |
| `list_unmatched_bank.bat` | 只列出未被 exception 接管的普通未匹配记录 |
| `run_company.bat` | 执行明确的资料公司和月份 |
| `analysis_report.bat` | 生成指定月份、指定业务的人工复核简表 |
| `status.bat` | 查看隔离任务状态 |
| `test_read_apis.bat` | 只读验证指定账套、月份的主要数据接口 |
| `confirm_one.bat` | 二次确认后真实上传一张 |
| `confirm_all.bat` | 二次确认后真实上传全部有效凭证 |
| `reset_upload_state.bat` | 清除指定公司、指定月份的本地上传断点 |

统一菜单的月份命令：

```text
month dataset公司ID YYYY-MM target公司ID
bank dataset公司ID YYYY-MM
exceptions dataset公司ID YYYY-MM
unmatched dataset公司ID YYYY-MM
verify dataset公司ID YYYY-MM
```

所有月份敏感命令都必须显式传入 `YYYY-MM`：

统一阶段为 `ocr`、`llm`、`prepare`、`send`、`all`。`prepare` 生成待上传 receipt；`send` 正式上传；`all` 从 OCR 到正式上传一次完成。客户和供应商预加载固定为 auto，每次运行都会核对目标账套。

```bat
commands\run_company.bat COMPANY_CONFIG_NAME YYYY-MM
commands\run_bank.bat COMPANY_CONFIG_NAME YYYY-MM
commands\list_bank_exceptions.bat COMPANY_CONFIG_NAME YYYY-MM
commands\list_unmatched_bank.bat COMPANY_CONFIG_NAME YYYY-MM
commands\analysis_report.bat COMPANY_CONFIG_NAME YYYY-MM [sales|purchase|bank|misc]
commands\confirm_one.bat COMPANY_CONFIG_NAME YYYY-MM
commands\confirm_all.bat COMPANY_CONFIG_NAME YYYY-MM
commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM
```

`COMPANY_CONFIG_NAME` 是 dataset 公司的配置文件名，不含 `.json`。月份配置必须显式包含 `project.json.dataset` 和 `project.json.target`。


主要读取接口的可重复集成测试：

```sh
commands/test_read_apis.sh company_17867515 2026-07
```

Windows 使用同名 `.bat`。结果、参数和默认不联网的 unittest 入口见 [读取接口测试说明](../docs/READ_API_TESTS.md)。
