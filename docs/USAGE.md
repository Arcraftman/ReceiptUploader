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
month dataset公司ID YYYY-MM target公司ID
```

同一主体记账：

```text
month 17867515 2026-09 17867515
```

把微誉 2026-09 的资料写入星海账套：

```text
month 17867515 2026-09 20151038
```

`dataset` 和 `target` 都必须明确指定；即使同一家公司，也要把同一个公司 ID 分别作为 dataset 和 target 输入。

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
  "version": 7,
  "month": "2026-09",
  "dataset": {
    "company_key": "company_17867515",
    "company_id": "17867515",
    "company_name": "上海微誉信息技术有限公司"
  },
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
    "analysis_validation": "strict",
    "purpose": "production",
    "allow_cross_entity": false,
    "only_mapped_invoices": true
  },
  "sources": {
    "sales": {"enabled": true, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false},
    "purchase": {"enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false},
    "bank": {
      "enabled": false,
      "mode": "analysis-only",
      "analysis_stage": "ocr",
      "preload_items": false,
      "banks": {
        "zhaoshangyinhang": {
          "bank_account_number": "100204",
          "split": {
            "parts_per_page": 3,
            "filename_index_length": 15,
            "filename_index_prefix": "C"
          },
          "statement_columns": {
            "index_column": null,
            "bank_debit_column": null,
            "bank_credit_column": null,
            "counterparty_name_column": null
          }
        }
      }
    },
    "misc": {"enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false}
  }
}
```

关键含义：

- `dataset`：本月资料来自哪家公司，三个身份字段必须与资料公司配置完全一致。
- `target`：本月最终写入哪家公司的账套，三个身份字段必须与运行期账套注册表完全一致。
- `input`：该月 Excel 文件名和用途列。
- `defaults`：只保存四个业务可共享的高级参数，不再保存业务运行开关。
- `sources.<业务>`：四个业务都必须精确写全 `enabled`、`mode`、`analysis_stage`、`preload_items`；只有 `enabled=true` 才会执行。
- `sources.bank.banks`：当月银行唯一配置源；每个 bank key 同时保存 `bank_account_number`、`split` 和 `statement_columns`。
- 某个 source 还可直接覆盖 `analysis_validation`、并发数、`purpose`、跨主体许可和 `only_mapped_invoices`；不再使用嵌套 `overrides`。

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

当月所有银行配置只写在 `project.json.sources.bank.banks`。不再生成、读取或同步 `bank_split.json`。每个 bank key 同时决定配置分组和输入文件名：

```json
"banks": {
  "zhaoshangyinhang": {
    "bank_account_number": "100204",
    "split": {
      "parts_per_page": 3,
      "filename_index_length": 15,
      "filename_index_prefix": "C"
    },
    "statement_columns": {
      "index_column": "J",
      "bank_debit_column": "F",
      "bank_credit_column": "G",
      "counterparty_name_column": "K"
    }
  }
}
```

银行数量没有写死；每增加一家银行，用户在 `banks` 中增加一个完整对象。`<bank_key>.pdf` 用于裁剪，`<bank_key>.xlsx` 留给流水匹配。`split` 中的三项均必填，前缀大小写敏感。`statement_columns` 必须恰好包含四项：

| 配置项 | 含义 |
|---|---|
| `bank_account_number` | 当前银行在目标账套中的固定银行存款科目号；模板选择和最终分录必须一致 |
| `index_column` | Excel 中与已切割 PDF 文件名索引相等的列 |
| `bank_debit_column` | 银行借方列；有金额时是我方贷方，即现金流出 |
| `bank_credit_column` | 银行贷方列；有金额时是我方借方，即现金流入 |
| `counterparty_name_column` | 对手方名称列；现金流出固定作为供应商，现金流入固定作为客户 |

银行借贷两列中只有一方是有效金额，另一方可以是 0、空值或文字。`bank` 启用时四列不允许为 `null`；未填写会在任务预检阶段停止。`configCompany` 固定等于本月 `dataset.company_name`；对手方名称和供应商/客户角色由流水表及上述方向规则决定，OCR 和模型不得覆盖。

现金流入时，如果 `bank_credit_column` 是有效金额，并且同一行的 `bank_debit_column` 完全由一串或多串数字组成（数字之间只能有空格、换行或常用分隔符，不能含普通文字），系统会按原顺序保存这些数字，并让它们直接成为 `explanation_body`。这条规则是替换，不会与模板原有 body 拼接；只要单元格中含有普通文字就不触发。

每个 bank 对象必须填写一个纯数字文本 `bank_account_number`。该值会进入 bank map、模型的模板选择上下文和固定业务规则；模板渲染时，名称包含“银行存款”的唯一分录会强制使用该科目。已有 `template_analysis.json` 的科目号不一致时，`prepare+existing` 也会阻断，不能沿用旧分析。

模板分录以科目编号为准；目标账套中同一编号显示的科目名称或明细名称不同，不作为阻断条件。银行存款分录仍以这个显式配置号码为准。

每张已匹配回单还会从 OCR 原文确定交易/记账日期。只有名称包含“银行存款”的那条分录在基础摘要末尾追加一个空格和 `YYYY-MM-DD`；另一条对方科目分录不追加日期。已有分析缺少该日期、摘要未按规则生成或数字 body 不一致时，`prepare+existing` 会停止并要求重新执行 LLM 分析。

每个银行模板还必须配置 `matchRules.flowDirections`。候选模板先按 Excel 流水确定的 `inflow/outflow` 硬筛选，再判断业务词；收款模板绝不允许处理付款，付款模板也不能处理收款。规则只剩一个候选时直接确定模板，不再调用 Qwen。外币只按完整的 `USD/US$/HKD/EUR` 或中文币种名称识别，`USB` 等普通单词不会再被误判。

bank 的 `preload_items` 现在同样支持 `false`、`"once"`、`"auto"`。设为 `"once"` 或 `"auto"` 时，系统根据 bank map 中固定的方向和对手方名称，在目标账套创建缺少的客户/供应商，再进行模板分析；这是远端写操作。`false` 时只读取现有辅助核算目录，缺少对象的分析会保持 blocked。

`sources.bank.exceptions` 是一个名称数组。每个名称都与 Excel 对手方列完整文本精确匹配；命中后统一与普通业务隔离。裁剪原件始终保留在 `generated/bank_receipts`，特殊 PDF 另复制到 `generated/bank_exceptions/<counterparty>/`；权威清单写入 `generated/maps/bank/bank_exceptions.json`。使用 `exceptions dataset公司ID YYYY-MM` 查看特殊对象、原件和副本；`unmatched` 只列出没有被 exception 接管的普通未匹配记录。

新公司、新月份首次创建时，`config/bank_exception.defaults.json` 中的名称会复制到该月 `sources.bank.exceptions`；现有月份绝不自动合并或覆盖。通用默认只预置 TIPS。配置只写 Excel 对手方列的完整名称：

```json
"exceptions": [
  "TIPS电子缴税款业务待报解预算收入",
  "重庆京东盛际小额贷款有限公司",
  "张三"
]
```

月度配置没有其他字段。TIPS 这类无文件名索引 PDF 的稳定关键词统一维护在 `config/bank_exception.defaults.json`，普通用户不用填写。

bank 入口只读取和校验当月 `project.json`，不会自动增加银行或改写运行字段。OCR 阶段严格按“全部银行裁剪完成 → `pdf_keywords` 分离特殊 PDF → exception 名称与流水索引补充分离 → OCR 其余单张回单 → 匹配其余流水”执行，只写特殊副本、`generated/ocr/bank` 和 `generated/maps/bank`，不生成 receipt。特殊 PDF 与对应流水不会进入普通 OCR、匹配、LLM 或凭证流程。

然后按 sales/purchase 相同生命周期运行：`analysis-only+llm` 写 `template_analysis.json`；人工复核后以 `prepare+existing` 生成最终 receipt，全部初始为 `draft=true`。补齐后手动改为 `false`，再运行 `verify dataset公司ID YYYY-MM`。verify 只能检查最终 receipts，并在 dry-run/未来真实提交前自动重跑；任何草稿或无效文件都会阻断整批。银行真实上传目前仍保持阻断。

## 配置边界

`generated/maps` 按实际运行按需创建：`sales/` 只保存销售 map，`purchase/` 只保存采购匹配和采购 map，`bank/` 只保存一套按 bank key 分组的 `bank_map` 和报告。初始化月份不会预建四个空 map 目录。

| 路径 | 作用 | 谁维护 |
|---|---|---|
| `config/kdzwy.json` | 登录账号和密码 | 用户，本地私密 |
| `config/app.json` | 账无忧接口超时、域名、User-Agent | 技术人员 |
| `config/pipeline.defaults.json` | OCR/LLM 并发和模型接口 | 技术人员 |
| `config/template_companies.json` | 可用模板公司及默认基础模板 | 模板维护者 |
| `config/companies/company_<id>_<公司名>.json` | 资料公司身份和共享模板 | 初始化命令 |
| `runtime/registry/accountbooks.json` | 本次发现的可访问账套与会话路径 | 系统自动生成 |
| `data/inbox/<公司>/<月>/project.json` | 该月目标、输入、业务开关和全部银行规则 | 用户每月维护 |

配置优先级只有：技术默认值 → 当月共享 `defaults` → 当月 source 精确运行字段及直接覆盖 → 命令行维护性临时覆盖。

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
