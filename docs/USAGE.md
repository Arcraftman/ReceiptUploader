# 企业凭证 OCR、模板匹配与账无忧上传

本项目按“资料公司 + 会计月份”处理 `sales`、`purchase`、`bank`、`misc` 四类资料，并把结果写入该月份明确指定的目标账套。

当前配置只有三层：

1. 公司配置：只保存资料公司身份和跨月份共享模板。
2. 月份配置：保存该月目标账套、输入文件、运行模式和四类业务开关。
3. 技术配置：保存并发数和模型接口等全局技术参数。

旧的 `datasets.json`、`month.conf` 和 `config/accountbooks.json` 已移除，不再兼容。

## 从零启动

在项目根目录 `D:\receipt-uploader` 执行：

```bat
py -m venv .auto
.auto\Scripts\python.exe -m pip install -r requirements.txt
copy config\app.example.json config\app.json
copy config\kdzwy.example.json config\kdzwy.json
```

创建 `config\kdzwy.json`，填写账无忧登录账号：

```json
{
  "version": 1,
  "accounts": [
    {
      "key": "account_1",
      "name": "账号1",
      "enabled": true,
      "username": "登录用户名",
      "password": "登录密码"
    }
  ]
}
```

在当前终端设置百炼密钥，然后启动统一菜单：

```bat
set DASHSCOPE_API_KEY=你的百炼API_KEY
commands\start.bat
```

`start.bat` 会依次完成：登录主账号、发现可访问公司、生成运行期账套注册表、刷新各公司 HTTP 会话，然后进入命令循环。它不会自动上传凭证。

## 菜单操作

查看公司后，输入：

```text
month 资料公司ID YYYY-MM [目标公司ID]
```

同一主体记账：

```text
month 17867515 2026-09
```

把微誉 2026-09 的资料写入星海账套：

```text
month 17867515 2026-09 20151038
```

省略目标公司时，系统仍会把资料公司自己的账套完整写入 `project.json.target`，不会在运行时猜测目标。

若资料公司尚未配置，`month` 命令会基于 `config/template_companies.json` 中的默认基础模板，自动创建：

```text
config/companies/company_<company_id>_<真实公司名>.json
templates/company_<company_id>/
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/
```

其他菜单命令：

```text
list       重新显示可访问公司
login      刷新已有公司 HTTP 会话
discover   重新发现公司并刷新会话
status     查看本地任务状态
help       查看帮助
quit       退出
```

## 每月唯一配置：project.json

月份初始化后编辑：

```text
data/inbox/company_<id>_<公司名>/<YYYY-MM>/project.json
```

标准结构：

```json
{
  "version": 5,
  "company_key": "company_17867515",
  "company_id": "17867515",
  "company_name": "上海微誉信息技术有限公司",
  "month": "2026-09",
  "target": {
    "accountbook_key": "company_17867515",
    "company_id": "17867515",
    "company_name": "上海微誉信息技术有限公司"
  },
  "input": {
    "income_cost_filename": "收入成本表.xlsx",
    "usage_filename": "用途确认信息.xlsx",
    "usage_column": "E"
  },
  "defaults": {
    "mode": "analysis-only",
    "analysis_stage": "ocr",
    "analysis_validation": "strict",
    "preload_items": false,
    "purpose": "production",
    "allow_cross_entity": false,
    "only_mapped_invoices": true
  },
  "sources": {
    "sales": {"enabled": true},
    "purchase": {"enabled": false},
    "bank": {"enabled": false},
    "misc": {"enabled": false}
  }
}
```

关键含义：

- `company_*`：本月资料来自哪家公司。
- `target`：本月最终写入哪家公司的账套，三个身份字段必须与运行期账套注册表完全一致。
- `input`：该月 Excel 文件名和用途列。
- `defaults`：该月所有业务的默认运行参数。
- `sources.<业务>.enabled`：只有设为 `true` 的业务才会执行。
- 某个 source 可直接覆盖 `mode`、`analysis_stage`、`analysis_validation`、并发数、`preload_items`、`purpose`、跨主体许可和 `only_mapped_invoices`；不再使用嵌套 `overrides`。

四个资料目录始终自带：

```text
input/sales/
input/purchase/
input/bank/
input/misc/
```

目录存在不代表会运行；执行权只看对应的 `enabled`。

## 运行指定公司和月份

同一家公司不同月份必须分别写明月份：

```bat
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-07
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

第一个参数是 `config/companies` 下的文件名，不含 `.json`；第二个参数始终是 `YYYY-MM`。因此不会误用该公司的其他月份。

常用模式：

| `mode` | 用途 |
|---|---|
| `analysis-only` | 只进行 OCR/模型分析，不生成或上传凭证 |
| `prepare` | 生成待审核凭证，不上传 |
| `dry-run` | 校验已有凭证，不上传 |
| `confirm` | 真实上传；只允许通过确认 BAT 入口执行 |

分析阶段：

| `analysis_stage` | 用途 |
|---|---|
| `ocr` | PDF OCR 并按模板规则分析 |
| `llm` | 使用已有 OCR 调用模型 |
| `existing` | 使用已有模型分析结果 |
| `all` | 执行完整分析链路 |

当前默认模型为 `qwen3.7-flash`，位于 `config/pipeline.defaults.json`。API Key 只从环境变量 `DASHSCOPE_API_KEY` 读取。

## 审核与上传

生成简明分析报告：

```bat
commands\analysis_report.bat company_17867515_上海微誉信息技术有限公司 2026-08 sales
```

查看任务状态：

```bat
commands\status.bat
```

真实上传前必须检查工作区中的 `preupload_review.report.json`。确认单张和全部上传分别使用：

```bat
commands\confirm_one.bat company_17867515_上海微誉信息技术有限公司 2026-08
commands\confirm_all.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

两个入口都会要求再次输入确认文本。跨主体目标也必须由当月 `project.json` 显式许可。

清除指定公司、指定月份的本地上传断点：

```bat
commands\reset_upload_state.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

## 银行回单拆分

若一个银行 PDF 含多张回单，在该月创建：

```text
data/inbox/<公司>/<月份>/input/bank/bank_split.json
```

内容必须是小写银行键名到回单数量的直接映射：

```json
{
  "shanghaiyinhang": 2,
  "shanghainongshangyinhang": 3
}
```

参考 `examples/bank_split.json`。原始 PDF 保留不动，拆分结果写入隔离工作区的 `generated/bank_receipts/`。

## 配置边界

| 路径 | 作用 | 谁维护 |
|---|---|---|
| `config/kdzwy.json` | 登录账号和密码 | 用户，本地私密 |
| `config/app.json` | 账无忧接口超时、域名、User-Agent | 技术人员 |
| `config/pipeline.defaults.json` | OCR/LLM 并发和模型接口 | 技术人员 |
| `config/template_companies.json` | 可用模板公司及默认基础模板 | 模板维护者 |
| `config/companies/company_<id>_<公司名>.json` | 资料公司身份和共享模板 | 初始化命令 |
| `runtime/registry/accountbooks.json` | 本次发现的可访问账套与会话路径 | 系统自动生成 |
| `data/inbox/<公司>/<月>/project.json` | 该月目标、输入、模式和业务开关 | 用户每月维护 |

配置优先级只有：技术默认值 → 当月 `defaults` → 当月 source 直接覆盖 → 命令行维护性临时覆盖。

## 目录结构

```text
commands/                       用户 BAT 入口
config/                         稳定配置与本地私密配置
data/inbox/<公司>/<月>/         原始资料和该月 project.json
templates/<模板公司>/           跨月份共享模板与提示词
runtime/registry/               自动发现的账套注册表
http_sessions/                  登录和公司会话
workspaces/<账号>/<目标账套>/   隔离生成物、状态和日志
src/                            核心代码
scripts/                        Python 和 PowerShell 实现
tests/                          自动化测试
```

模板 JSON 是分类规则和凭证分录的唯一真相源；`templates/<公司>/index.json` 不重复列举模板。编辑规则见 `docs/TEMPLATE_EDITING.md`。

## 开发验证

```bat
.auto\Scripts\python.exe -m pytest -q
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\start_console.ps1 -ValidateOnly
```
