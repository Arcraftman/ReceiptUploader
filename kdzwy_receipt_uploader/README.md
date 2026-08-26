# 企业凭证自动处理与上传操作手册

本项目把企业原始财务资料转换为会计凭证，并通过账无忧 HTTP 接口串行保存凭证、上传附件和回读验证。

日常操作以 `.bat` 文件为入口。Python 长命令仅保留给开发、排错和特殊维护，不作为普通用户的日常启动方式。

---

## 1. 开始前先确认

### 1.1 当前业务完成度

| 业务 | 配置名 | 当前可执行范围 | 是否允许批量上传 |
|---|---|---|---|
| 销项发票 | `sales` | 映射、OCR、DeepSeek、凭证、附件、回读 | 已跑通，但仍须先单张验证 |
| 进项与费用 | `purchase` | 映射、OCR、DeepSeek、凭证草稿、校验 | 暂未批准批量上传 |
| 银行 | `bank` | 合并 PDF 裁剪、回单号识别、单张 PDF 输出 | 不允许上传 |
| 杂项 | `misc` | 目录、提示词和模板位置占位 | 不允许上传 |

### 1.2 绝对不能跳过的安全规则

1. 一次只启用一个业务板块。不要第一次就同时运行 `sales` 和 `purchase`。
2. `confirm` 表示真实保存凭证，不是“确认一下配置”。
3. 正式批量上传前必须依次完成 OCR、DeepSeek、人工复核、dry-run、单张上传和网页回读。
4. `confirm_all.bat` 不根据本地历史日志自动跳过；重复运行可能在线上生成重复凭证。
5. 如果上一批线上凭证没有清空，不得直接重新完整上传。
6. 不能确定模板、科目、辅助核算、金额或借贷方向时，程序必须阻断，不得猜测入账。
7. Cookie、登录密码、DeepSeek API Key 不得写入 README、公司 JSON 或模板文件。

---

## 2. 先理解三个独立概念

项目把“资料、模板、账套”分开管理：

| 概念 | 示例 | 决定什么 |
|---|---|---|
| 资料公司 `dataset` | `weiyu` | 从哪个目录读取 PDF、XLSX 和月份资料 |
| 模板公司 `template_company` | `weiyu` | 使用哪家公司的业务规则、提示词和会计模板 |
| 目标账套 `accountbook` | `xinghai` | 动态读取哪套科目和辅助核算，最后写入哪里 |

当前测试关系是：

```text
资料：weiyu（上海微誉信息技术有限公司）
模板：weiyu（上海微誉信息技术有限公司）
账套：xinghai（星海公司）
```

这是跨主体测试。真实上传时必须通过专用确认 BAT 明确授权。

---

## 3. 项目目录

```text
kdzwy_receipt_uploader/
├─ config/                         # 账套、数据集、公司任务和流水线配置
│  ├─ accountbooks.json
│  ├─ datasets.json
│  ├─ template_companies.json
│  ├─ pipeline.defaults.json
│  ├─ app.json                    # 本机运行配置，不提交密钥
│  └─ companies/                  # 每家公司一个日常运行配置
├─ data/inbox/                    # 原始资料和生成结果
├─ templates/                     # 每家公司独立的四类模板和提示词
├─ runtime/                       # 任务状态、日志、成功和失败记录
├─ src/kdzwy_receipt_uploader/    # 核心代码
├─ run_company.bat                # 日常分析和 dry-run 入口
├─ confirm_one.bat                # 真实上传一张
├─ confirm_all.bat                # 真实批量上传
├─ generate_analysis_report.bat   # 生成普通用户可读分析简表
├─ pipeline_status.bat            # 查看全部任务状态
├─ start_http_login.bat           # 纯 HTTP 登录
└─ start_discover_companies.bat   # 选择公司、建立会话和公司配置
```

旧目录 `x1/x2/j1/j2/j3` 已废弃，不再兼容。

---

## 4. 第一次使用

以下步骤每台电脑只需完成一次。

### 步骤 1：安装 Python 依赖

在项目目录打开 PowerShell：

```powershell
python -m pip install -r requirements.txt
```

项目通常优先使用：

```text
D:\receipt-uploader\.auto\Scripts\python.exe
```

如果该环境不存在，BAT 会尝试使用系统 `python`。

### 步骤 2：创建本机运行配置

复制 `config/app.example.json` 为 `config/app.json`。

正常情况下保留示例中的超时和运行目录即可。真实 Cookie 文件会由公司任务动态写入运行快照，不需要手工把 Cookie 填进公司配置。

### 步骤 3：准备登录配置

登录配置位于：

```text
D:\receipt-uploader\config\kdzwy.json
```

只需要配置登录账号、密码和要登录的公司名称。该文件包含敏感信息，不得提交到版本库或发给无关人员。

### 步骤 4：建立公司登录会话

运行：

```bat
start_http_login.bat
```

成功后，每家公司生成独立会话：

```text
D:\receipt-uploader\http_sessions\companies\{公司全名}.accountbook.cookies.json
```

登录是纯 HTTP，不使用 Playwright。

如果还没有公司配置，可以先运行：

```bat
start_discover_companies.bat
```

该入口会：

1. 显示可选择公司。
2. 保存所选账套到 `config/accountbooks.json`。
3. 为新公司创建默认禁用的 `config/companies/{key}.json`。
4. 建立所选公司的 HTTP 会话。
5. 已完成配置且启用的公司会继续执行；新建但未配置的公司会安全跳过。

---

## 5. 新公司接入

### 步骤 1：确认账套已经发现并登录

先运行：

```bat
start_discover_companies.bat
```

确认 `config/accountbooks.json` 和 `D:\receipt-uploader\http_sessions\companies\` 中已有该公司。

### 步骤 2：创建公司配置和模板副本

```bat
create_company.bat --name "公司完整中文名称"
```

创建完成后会得到：

```text
config/companies/{company_key}.json
templates/{company_key}/
```

新公司配置默认不会直接运行，需要先填写 `dataset`、`month`，再启用一个业务板块。

### 步骤 3：动态刷新公司科目和辅助核算目录

这是公司初始化或账套科目发生大改时使用的维护操作，不是每日操作：

```powershell
python initialize_company_template.py --accountbook 公司英文key --company-template 公司英文key
```

程序会只读获取全部科目、凭证字、币别、客户、供应商、职员、项目、存货、部门和辅助核算要求。

输出位置：

```text
templates/{company}/catalog/accounts.json
templates/{company}/catalog/auxiliary_items.json
templates/{company}/workspace.json
```

重复执行会刷新动态目录，但不会覆盖用户已经编辑过的提示词。只有明确需要重建提示词时才使用 `--overwrite-prompts`。

---

## 6. 公司运行配置怎么填写

日常操作主要修改：

```text
config/companies/{company_key}.json
```

安全示例：

```json
{
  "version": 1,
  "company_key": "xinghai",
  "enabled": true,
  "dataset": "weiyu",
  "template_company": "weiyu",
  "month": "7月",
  "defaults": {
    "mode": "analysis-only",
    "analysis_stage": "ocr",
    "analysis_validation": "strict",
    "preload_items": false,
    "purpose": "test",
    "allow_cross_entity": true
  },
  "sources": {
    "sales": { "enabled": true },
    "purchase": { "enabled": false },
    "bank": { "enabled": false },
    "misc": { "enabled": false }
  }
}
```

### 关键字段

| 字段 | 说明 |
|---|---|
| `company_key` | 目标账套英文 key |
| `dataset` | 原始资料公司英文 key |
| `template_company` | 模板公司英文 key |
| `month` | 月份目录名称，必须和资料目录一致 |
| `mode` | 安全级别 |
| `analysis_stage` | OCR、DeepSeek 或复用已有分析 |
| `preload_items` | 是否检查并创建缺失客户/供应商 |
| `allow_cross_entity` | 是否允许资料主体和目标账套不同 |
| `sources` | 本次处理哪个业务板块 |

### mode 含义

| 值 | 行为 |
|---|---|
| `analysis-only` | 只处理映射、OCR 或 DeepSeek，不生成上传凭证 |
| `prepare` | 读取账套并生成准备数据，不上传 |
| `dry-run` | 生成正式结构的 receipt 并本地校验，不调用保存接口 |
| `confirm` | 真实保存凭证和上传附件 |

### analysis_stage 含义

| 值 | 行为 |
|---|---|
| `ocr` | 只做 OCR，不调用 DeepSeek，不需要账套会话 |
| `deepseek` | 读取已有 OCR，调用 DeepSeek，并读取动态账套科目和 Item |
| `existing` | 不做 OCR、不调用 DeepSeek，复用已经人工批准的分析 |
| `all` | OCR 后立刻执行 DeepSeek；首次运行不建议使用 |

### preload_items 特别说明

| 值 | 行为 |
|---|---|
| `false` | 不创建远端客户或供应商，最安全 |
| `once` | 输入 Excel 变化后检查一次，并创建缺失客户或供应商 |
| `auto` | 每次都检查并可能创建，日常不建议使用 |

`preload_items=once/auto` 是远端写操作，即使当前是分析流程也可能创建辅助核算对象。第一次 OCR 建议设置为 `false`；确认 Excel 中客户和供应商名称无误后再决定是否启用。

---

## 7. 每月资料怎么放

第一次运行 `run_company.bat` 时会根据公司配置自动创建目录。

标准结构：

```text
data/inbox/{dataset}/{month}/
├─ input/
│  ├─ sales/
│  ├─ purchase/
│  ├─ bank/
│  ├─ misc/
│  ├─ 收入成本表.xlsx
│  └─ 用途确认信息.xlsx
├─ generated/
└─ {dataset}_{month}.conf
```

用户只维护 `input/` 和月份 `.conf`。`generated/` 全部由程序生成。

### sales 资料

- 销项发票 PDF 放在 `input/sales/`。
- `收入成本表.xlsx` 用于生成金额和客户映射。
- 资料公司必须是发票销售方，购买方作为客户。

### purchase 资料

- 进项和费用发票 PDF 放在 `input/purchase/`。
- `用途确认信息.xlsx` 是特殊匹配依据，不能省略。
- 资料公司必须是购买方，销售方作为供应商。
- 不能只凭 OCR 商品名称猜测采购用途。

当前基础映射阶段会同时读取两张 XLSX，因此建议每个月份目录都保留 `收入成本表.xlsx` 和 `用途确认信息.xlsx`。

### bank 资料

银行原始合并 PDF 和裁剪配置放在：

```text
data/inbox/{dataset}/{month}/input/bank/
```

示例：

```text
input/bank/
├─ bank_split.json
├─ shanghaiyinhang.pdf
└─ shanghainongshangyinhang.pdf
```

`bank_split.json`：

```json
{
  "shanghaiyinhang": 2,
  "shanghainongshangyinhang": 3
}
```

含义是：上海银行 PDF 每页有 2 张回单，上海农商银行 PDF 每页有 3 张回单。

银行 key 必须使用英文小写、数字、下划线或连字符；原始 PDF 文件名必须和 key 完全一致。

---

## 8. sales 完整操作流程

以下示例使用 `xinghai` 公司配置。每一步完成后都先检查输出，再进入下一步。

### 第 1 步：只启用 sales

编辑 `config/companies/xinghai.json`：

```json
"sources": {
  "sales": { "enabled": true },
  "purchase": { "enabled": false },
  "bank": { "enabled": false },
  "misc": { "enabled": false }
}
```

### 第 2 步：执行 OCR

设置：

```json
"mode": "analysis-only",
"analysis_stage": "ocr",
"preload_items": false
```

运行：

```bat
run_company.bat xinghai analysis-only
```

检查：

```text
data/inbox/weiyu/7月/generated/ocr/sales/ocr_stage.report.json
data/inbox/weiyu/7月/generated/ocr/sales/
```

必须确认发票数量正确、发票号可识别、销售方是资料公司、购买方与金额没有大面积缺失，并且 `successTextCount` 不是 0。

### 第 3 步：执行 DeepSeek

先确保 DeepSeek API Key 已配置为用户环境变量。若尚未配置，可以在 PowerShell 中执行：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", (Read-Host "请输入 DeepSeek API Key"), "User")
```

设置完成后重新打开终端。

把公司配置改为：

```json
"mode": "analysis-only",
"analysis_stage": "deepseek"
```

运行：

```bat
run_company.bat xinghai analysis-only
```

检查：

```text
data/inbox/weiyu/7月/generated/ocr/sales/template_analysis.json
```

DeepSeek 只能从 `templates/weiyu/sales/` 选择模板，不能访问 purchase、bank 或 misc 模板。

### 第 4 步：生成人工复核简表

运行：

```bat
generate_analysis_report.bat xinghai sales
```

输出：

```text
data/inbox/weiyu/7月/generated/ocr/sales/concise_template_analysis.md
```

逐张检查发票号、模板名称、借贷科目、金额、税额、客户辅助核算、统一摘要和 `blocked` 状态。

有任何错误就修改模板或提示词，然后重新运行 DeepSeek。不要带着错误进入 dry-run。

### 第 5 步：执行 dry-run

把公司配置改为：

```json
"mode": "dry-run",
"analysis_stage": "existing"
```

运行：

```bat
run_company.bat xinghai dry-run
```

检查：

```text
data/inbox/weiyu/7月/generated/receipts/sales/
data/inbox/weiyu/7月/generated/maps/sales/preupload_review.report.json
```

进入真实上传前必须满足：无效 receipt 为 0、预审警告为 0、借贷平衡、科目和客户 ID 来自当前账套、PDF 与发票号一致。

### 第 6 步：真实上传一张

公司配置必须保持：

```json
"analysis_stage": "existing"
```

运行：

```bat
confirm_one.bat xinghai
```

按提示输入 `xinghai` 才会继续。程序只处理一张。

随后立即到网页检查目标账套、日期、凭证字、制单人、统一摘要、科目、客户辅助核算、金额和发票附件。系统不会自动添加审核员。

单张验证结束后，如果下一步要从头执行整批，必须先在网页删除这张测试凭证，并再次确认它已经不存在。系统不会自动跳过这张单测凭证。

### 第 7 步：真实批量上传

只有单张网页回读完全正确，并且用于测试的单张凭证已经从线上删除后，才运行：

```bat
confirm_all.bat xinghai
```

按提示输入：

```text
UPLOAD ALL xinghai
```

任意一张保存、附件或回读不明确时，批次会立即停止，后续凭证不会继续。

---

## 9. purchase 操作流程

purchase 的执行顺序与 sales 一致：

1. 只启用 `purchase`。
2. 设置 `analysis-only + ocr` 并运行 `run_company.bat xinghai analysis-only`。
3. 检查 OCR 和用途确认匹配。
4. 设置 `analysis-only + deepseek` 并再次运行。
5. 运行 `generate_analysis_report.bat xinghai purchase`。
6. 人工检查模板、分录、供应商和税额。
7. 设置 `dry-run + existing` 并运行 `run_company.bat xinghai dry-run`。

purchase 必须额外检查：

- 资料公司是否为购买方。
- 发票销售方是否与用途确认表中的供应商一致。
- 发票号是否进入用途确认范围。
- 采购商品、原材料、固定资产和费用分类是否正确。
- 专票进项税、待抵扣税额和普票价税合计取值是否正确。
- 应付账款行是否使用供应商辅助核算。
- 同一凭证的摘要是否按模板统一生成并包含发票号。

当前 purchase 尚未批准批量真实上传。即使 dry-run 通过，也只能在明确批准后进行单张测试。

---

## 10. bank 当前操作流程

银行目前只开放预处理，不会生成或上传凭证。

### 第 1 步：准备银行 PDF 和配置

将 `bank_split.json` 和各银行合并 PDF 放进 `input/bank/`。

### 第 2 步：只启用 bank

```json
"sources": {
  "sales": { "enabled": false },
  "purchase": { "enabled": false },
  "bank": { "enabled": true },
  "misc": { "enabled": false }
}
```

设置：

```json
"mode": "analysis-only",
"analysis_stage": "ocr",
"preload_items": false
```

### 第 3 步：运行银行预处理

```bat
run_company.bat xinghai analysis-only
```

输出：

```text
data/inbox/weiyu/7月/generated/bank_receipts/{bank_key}/
data/inbox/weiyu/7月/generated/bank_receipts/{bank_key}/split.manifest.json
data/inbox/weiyu/7月/generated/bank_receipts/split.report.json
```

程序会按配置等高裁剪，优先读取 PDF 原生文字，必要时使用 RapidOCR，并用回单号、流水号、凭证号等唯一号码命名单张 PDF。输入和配置未变化时会复用已有裁剪结果。

如果号码无法识别或发生重复，文件进入：

```text
generated/bank_receipts/{bank_key}/unrecognized/
```

同时任务失败并停止。必须人工修正，不能直接进入后续银行流程。

当前裁剪完成后程序会明确结束。银行专用 OCR、银行流水匹配、每账户独立 `bank_map`、receipt 和上传将在下一阶段接入。

---

## 11. 模板和提示词

每家公司模板结构：

```text
templates/{company}/
├─ sales/
├─ purchase/
├─ bank/
├─ misc/
├─ prompts/
│  ├─ _fixed_rules.md
│  ├─ sales.md
│  ├─ purchase.md
│  ├─ bank.md
│  └─ misc.md
├─ catalog/
│  ├─ accounts.json
│  └─ auxiliary_items.json
├─ final_template_sample.json
├─ index.json
└─ workspace.json
```

固定规则由程序注入，公司提示词不能覆盖：

- `dc=1` 是借方，`dc=-1` 是贷方。
- 借方合计必须等于贷方合计。
- 科目必须来自本次目标账套动态目录，并且是可记账明细科目。
- 科目要求辅助核算时，Item 必须来自对应 ItemClass。
- 同一凭证所有分录摘要必须完全一致。
- 不确定时必须返回 `blocked`。

模板应明确配置摘要：

```json
{
  "explanation_header": "销售商品收入",
  "explanation_body": "{invoiceCode}",
  "explanation_separator": " "
}
```

最终结果为 `销售商品收入 26312000004167256876`。DeepSeek 可以选择模板和填充模板允许的字段，但不能擅自改变模板分录数量、借贷方向或每行摘要。

---

## 12. 状态和日志怎么查看

运行：

```bat
pipeline_status.bat
```

每个任务的状态目录：

```text
runtime/jobs/{accountbook}/{dataset}/{month}/{source}/
├─ run.json
├─ app.json
├─ state.json
├─ events.jsonl
└─ job.lock
```

`state.json` 是当前任务的唯一任务级状态，记录账套、资料、月份、业务板块、模式、阶段、运行编号、当前步骤、产物、计数、退出码和错误。

`events.jsonl` 是只追加的事件历史，重新执行不会删除以前的任务过程。

| 状态 | 含义 |
|---|---|
| `running` | 任务正在运行，进程锁有效 |
| `succeeded` | 本次任务正常结束 |
| `failed` | 本次任务失败 |
| `cancelled` | 用户按 Ctrl+C 中断 |
| `interrupted` | 进程被强制关闭，未正常收尾 |
| `abandoned` | 再次启动时，上一未完成尝试被登记为遗留任务 |

同一个账套、资料、月份和业务板块不能同时运行两个进程。

其他日志：

```text
runtime/logs/run_companies*.log
runtime/logs/run_pipeline*.log
runtime/logs/batch_receipts*.log
runtime/logs/run.jsonl
runtime/submitted/
runtime/failed/
```

任务状态只用于观察和故障定位，不会根据本地状态自动跳过真实上传。

---

## 13. 失败和中断怎么处理

### OCR 看起来卡住

先查看日志中的当前文件编号，再运行 `pipeline_status.bat`。RapidOCR 处理图片型 PDF 时可能较慢，只要日志编号仍在前进就不是死锁。

### DeepSeek 返回 blocked

不要进入 dry-run。检查 OCR、当前板块提示词、模板目录、动态科目、辅助核算、金额和借贷平衡。修改后只重新执行 DeepSeek，不需要重新 OCR。

### Tunnel 502

附件接口出现明确 Tunnel 502 时，程序只重试附件，等待 2、5、10 秒，不会重复保存凭证。

如果最终仍失败，日志会显示已经保存的 `voucherId` 和 `voucherNo`。此时不要直接完整重跑 confirm；先到网页确认凭证是否存在，再决定单独恢复附件或清空线上记录后重跑。

### 用户中途停止

- Ctrl+C 会记录为 `cancelled`。
- 强制关闭窗口会显示 `interrupted`。
- 任意失败都会停止后续凭证。
- 已成功保存的线上凭证不会因为本地停止而自动删除。

### 用户已经清空线上凭证

确认线上相关凭证确实全部清空后，可以重新运行完整 confirm。当前系统不会根据旧的本地成功日志自动跳过。

如需清理旧 receipt 中可能存在的上传标记和对应审计记录，可运行：

```bat
reset_upload_state.bat xinghai
```

该工具会先备份审计日志，但不会删除线上凭证。没有确认线上状态前不要使用。

### 会话失效或公司不匹配

重新运行 `start_http_login.bat`。程序会严格检查当前会话公司名是否等于目标账套公司名，名称不一致时不会继续上传。

---

## 14. 正式上传验收清单

- [ ] 公司配置只启用了一个业务板块。
- [ ] dataset、template company、accountbook 和 month 正确。
- [ ] 跨主体任务已经明确允许。
- [ ] OCR 数量和原始 PDF 数量一致。
- [ ] DeepSeek 分析不存在 blocked。
- [ ] 简明报告已逐张检查。
- [ ] 所有科目来自当前目标账套。
- [ ] 客户或供应商 ID 来自当前目标账套。
- [ ] 全部分录摘要符合模板且保持一致。
- [ ] 借方合计等于贷方合计。
- [ ] 预上传审查警告为 0。
- [ ] dry-run 无效 receipt 为 0。
- [ ] `confirm_one.bat` 单张上传已通过网页检查。
- [ ] 单张附件能够打开。
- [ ] 已确认本次批量不会和线上已有凭证重复。

---

## 15. 核心实现边界

```text
BAT入口
  → 公司任务编排 run_companies.py
  → 单任务流水线 pipeline_runner.py
  → XLSX/PDF映射
  → OCR与DeepSeek
  → 模板渲染和receipt生成
  → 本地校验与预审
  → 串行上传cli.py
  → HTTP保存、附件绑定和回读workflow.py
```

关键原则：HTTP 客户端只负责请求；公司注册表只负责资料、模板和账套关系；模板负责会计结构；动态 ID 只能来自当前账套；状态管理只负责观察；任一不明确结果立即停止。

更详细的代码架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，长期业务决策见 [PROJECT_MEMORY.md](PROJECT_MEMORY.md)。
