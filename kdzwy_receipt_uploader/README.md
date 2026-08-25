# 企业凭证自动处理与上传

本项目用于串行处理多家公司、多个账套和多个月份的会计资料。主流程包括：Excel 映射、OCR、DeepSeek 模板分析、凭证草稿生成、上传前校验、凭证保存、附件上传和结果回读。

项目使用纯 HTTP 接口访问账套，不使用 Playwright 自动登录。登录会话按账套独立保存。

## 1. 当前能力

| 业务板块 | `--source` | 输入资料 | 当前状态 |
|---|---|---|---|
| 销项发票 | `sales` | 销售发票、收入成本表 | OCR、分析、凭证生成和真实上传已跑通 |
| 进项与费用 | `purchase` | 进项/费用发票、用途确认表 | 已接入 OCR 和分析；模板正在校准，尚未批准批量上传 |
| 银行 | `bank` | 每个银行账户的回单和流水 | 目录和映射位置已预留，真实上传保持阻断 |
| 杂项 | `misc` | 无法归入前三类的资料 | 目录和映射位置已预留，真实上传保持阻断 |

核心安全原则：不能确定模板、科目、辅助核算、金额或借贷方向时必须停止，不得猜测入账。

## 2. 三个独立的公司概念

| 概念 | 命令参数 | 配置文件 | 作用 |
|---|---|---|---|
| 资料公司 | `--dataset weiyu` | `config/datasets.json` | 决定读取哪家公司的 PDF、XLSX 和月份目录 |
| 目标账套 | `--accountbook xinghai` | `config/accountbooks.json` | 决定登录哪个账套、读取哪套动态科目、最后写入哪里 |
| 公司模板 | `--company-template weiyu` | `config/template_companies.json` | 决定使用哪家公司的业务规则、提示词和模板 |

例如下面的测试表示：读取微誉资料，使用微誉模板，在星海测试账套环境中分析：

```powershell
python run_companies.py --mode analysis-only --stage ocr --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

资料公司与账套不一致属于跨主体任务。配置中必须声明 `allow_cross_entity=true`；真实上传时还必须显式传入 `--allow-cross-entity-confirm`。

## 3. 项目目录

```text
kdzwy_receipt_uploader/
├─ config/                         # 公司、账套、任务和流水线配置
├─ data/inbox/                     # 原始资料及按月生成的结果
├─ templates/                      # 按公司、业务板块隔离的模板和提示词
├─ runtime/                        # 任务快照、日志和处理状态
├─ src/kdzwy_receipt_uploader/     # 核心业务代码
├─ scripts/maintenance/            # 非日常维护工具
├─ run_companies.py                # 推荐的日常主入口
├─ run_pipeline.py                 # 单任务流水线，由主入口调用
├─ batch_receipts.py               # 凭证校验和串行上传
├─ initialize_company_template.py  # 公司模板初始化器
└─ requirements.txt                # Python 依赖
```

旧目录名 `x1/x2/j1/j2/j3` 已废弃，不兼容旧结构。

## 4. 原始资料结构

```text
data/inbox/{dataset}/{month}/
├─ sales/
├─ purchase/
├─ bank/
│  ├─ {银行账户一}/
│  │  ├─ 回单/
│  │  └─ 流水/
│  └─ {银行账户二}/
├─ misc/
├─ 收入成本表.xlsx
├─ 用途确认信息.xlsx
└─ *.conf
```

用户资料统一放在月份目录的 `input/` 下；运行产生的 maps、OCR 和凭证草稿统一放在 `generated/` 下。

### sales

- PDF 放入 `sales/`，允许继续建立子目录。
- `收入成本表.xlsx` 用于建立 `sales_map`。
- `信息汇总表` H 列作为客户名称来源。
- 资料公司必须是发票销售方，购买方作为客户。

### purchase

- PDF 放入 `purchase/`，允许继续建立子目录。
- `用途确认信息.xlsx` 用于特殊匹配，不能只凭 OCR 商品名称判断用途。
- `发票` 工作表 J 列作为供应商名称来源。
- 资料公司必须是发票购买方，发票销售方作为供应商。

### bank

`bank` 下必须按银行账户建立一级子目录，每个账户生成独立映射和凭证草稿：

```text
maps/bank/{bank_account}/bank_map.json
maps/bank/{bank_account}/bank_map.report.json
receipts_bank_map/{bank_account}/
```

### misc

无法归入 sales、purchase 或 bank 的资料放入 `misc/`。规则未明确前不会真实上传。

## 5. 核心配置

### `config/datasets.json`：资料公司

```json
{
  "key": "weiyu",
  "entity_name": "上海微誉信息技术有限公司",
  "data_root": "data/inbox/weiyu",
  "enabled": true
}
```

### `config/accountbooks.json`：目标账套

```json
{
  "key": "xinghai",
  "name": "星海公司",
  "session_file": "../http_sessions/companies/星海公司.accountbook.cookies.json",
  "enabled": true
}
```

### `config/template_companies.json`：模板公司

```json
{
  "key": "weiyu",
  "name": "上海微誉信息技术有限公司",
  "directory": "weiyu",
  "enabled": true
}
```

模板键和目录必须以英文小写字母开头，只能包含英文小写、数字、下划线和连字符。

### `config/companies/<company_key>.json`：公司运行配置

```json
{
  "company_key": "xinghai",
  "enabled": true,
  "dataset": "weiyu",
  "template_company": "weiyu",
  "month": "7月",
  "defaults": {
    "mode": "analysis-only",
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

命令行参数可以覆盖任务的模式、阶段、板块和模板公司，但不会修改配置文件。

其他配置：

| 文件 | 作用 |
|---|---|
| `config/pipeline.defaults.json` | 全局默认流程、路径和开关 |
| `config/app.json` | HTTP 地址、会话和超时等基础配置 |
| `runtime/jobs/<账套>/<数据集>/<月份>/<source>/run.json` | 主入口为每个任务生成的实际运行快照 |

Cookie、DeepSeek API Key 和访问令牌不得写进 README、普通配置或版本库。

## 6. 初始化公司模板

每家公司的模板结构：

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

从任意已配置账套初始化或刷新模板工作区：

```powershell
python initialize_company_template.py --accountbook xinghai --company-template xinghai
```

该命令只读访问账套并完成：

- 动态读取全部科目及其辅助核算属性。
- 动态读取客户、职员、项目、存货、供应商和部门 Item。
- 创建四个业务模板目录和四份公司提示词。
- 自动注册新的模板公司。
- 重复运行时刷新动态目录，不覆盖用户修改过的提示词。

确实需要重建提示词时使用：

```powershell
python initialize_company_template.py --accountbook xinghai --company-template xinghai --overwrite-prompts
```

程序固定注入以下规则，公司提示词不能覆盖：

- `dc=1` 是借方，`dc=-1` 是贷方。
- 借方合计必须等于贷方合计。
- 科目必须来自本次运行的动态科目目录，且必须是可记账明细科目。
- 科目要求辅助核算时，Item 必须来自对应动态 ItemClass。
- 不确定时返回 `blocked`，不得臆造。
- 同一凭证全部分录必须使用相同摘要。

## 7. OCR 与 DeepSeek 分阶段运行

| `--stage` | 行为 | 调用 DeepSeek | 需要账套会话 |
|---|---|---:|---:|
| `ocr` | 只扫描指定板块并生成 OCR | 否 | 否 |
| `deepseek` | 复用 OCR，读取动态科目和 Item 后分析 | 是 | 是 |
| `existing` | 复用已经批准的分析结果 | 否 | 是 |
| `all` | 明确串行执行 OCR 和 DeepSeek | 是 | 是 |

推荐始终按板块、按阶段运行。第一次不要直接使用 `--source all --stage all`。

### Purchase 第一次测试流程

1. 只检查任务计划：

```powershell
python run_companies.py --mode analysis-only --stage ocr --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu --plan
```

2. 只执行 OCR：

```powershell
python run_companies.py --mode analysis-only --stage ocr --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

3. 检查 OCR：

```text
data/inbox/weiyu/7月/receipts_ocr/purchase/
data/inbox/weiyu/7月/receipts_ocr/purchase/ocr_stage.report.json
```

4. 确认 purchase 模板和提示词正确后，只执行 DeepSeek：

```powershell
python run_companies.py --mode analysis-only --stage deepseek --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

5. 生成普通用户可读的凭证简表：

```powershell
python concise_template_analysis.py --dataset weiyu --month 7月
```

输出：

```text
data/inbox/weiyu/7月/receipts_ocr/purchase/concise_template_analysis.md
```

## 8. 运行模式

| `--mode` | 行为 | 创建缺失 Item | 保存凭证 |
|---|---|---:|---:|
| `analysis-only` | Excel 映射、OCR/分析，不生成上传凭证 | 否 | 否 |
| `prepare` | 读取账套基础资料并生成准备数据 | 否 | 否 |
| `dry-run` | 生成凭证草稿并执行只读校验 | 否 | 否 |
| `confirm` | 串行保存凭证、上传附件并回读验证 | 是，可配置 | 是 |

`confirm` 就是真实上传许可，不是“确认一下配置”。

## 9. 客户和供应商预加载

在 `prepare`、`dry-run` 和 `confirm` 的实时账套流程中，系统会：

1. 从 `收入成本表.xlsx / 信息汇总表 / H列` 收集客户。
2. 从 `用途确认信息.xlsx / 发票 / J列` 收集供应商。
3. 与目标账套动态 Item 按名称比对。
4. 将真实 `customerId` 或 `supplierId` 写入映射和凭证草稿。
5. 仅在 `confirm` 且 `preload_create_missing_items=true` 时创建缺失 Item。

报告位置：

```text
data/inbox/{dataset}/{month}/maps/item_preload.report.json
```

`analysis-only` 不创建客户或供应商。

## 10. 摘要和分录规则

每个模板应显式定义：

```json
{
  "explanation_header": "销售商品收入",
  "explanation_body": "{invoiceCode}",
  "explanation_separator": " "
}
```

最终摘要示例：

```text
销售商品收入 26312000004167256876
```

同一凭证全部分录使用完全相同的摘要。DeepSeek 只能选择模板并填充模板允许的动态内容，不能逐行改写摘要或擅自增删分录。

purchase 还必须验证用途确认匹配、购买方、销售方、供应商、发票号、价款、税额、价税合计、科目、辅助核算、借贷平衡和附件映射。任何一项不确定都进入 `blocked`。

## 11. 正式上传安全规则

正式上传前至少完成：

1. `analysis-only --stage ocr`
2. 人工抽查 OCR
3. `analysis-only --stage deepseek`
4. 人工检查 `concise_template_analysis.md`
5. `dry-run`
6. 核对预上传报告警告为 0
7. 先用 `--limit 1` 上传一张
8. 网页回读确认凭证和附件正确后再批量上传

跨主体单张测试示例：

```powershell
python run_companies.py --mode confirm --stage existing --source sales --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu --allow-cross-entity-confirm --limit 1
```

安全约束：

- `confirm` 不根据本地历史成功日志自动跳过，重复执行可能重复入账。
- 用户中断并清空线上凭证后可以重新执行，但必须先确认线上确实已清空。
- 凭证之间默认间隔 1 秒。
- 附件识别出现明确的 Tunnel 502 时，只重试附件，间隔 2、5、10 秒，不重复保存凭证。
- 任一保存、附件上传或回读结果不明确时，立即停止后续任务。
- 当前 bank 和 misc 不允许真实上传。
- 当前 purchase 尚未批准批量真实上传。

## 12. 主要输出

```text
data/inbox/{dataset}/{month}/
├─ maps/
│  ├─ xlsx_pdf_map.json
│  ├─ sales_map.json
│  ├─ purchase_map.json
│  ├─ item_preload.report.json
│  ├─ upload_pdf_map.json
│  └─ preupload_review.report.json
├─ receipts_ocr/
│  ├─ sales/
│  ├─ purchase/
│  ├─ template_analysis.json
│  └─ concise_template_analysis.md
└─ receipts_sales_map/
```

```text
runtime/
├─ logs/
├─ jobs/{accountbook}/{dataset}/{month}/
├─ processing/
├─ failed/
└─ submitted/
```

原始 PDF、XLSX、失败记录和成功记录都不是普通缓存，不应随意删除。

## 13. 常用命令

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

查看任务计划：

```powershell
python run_companies.py --plan
```

销售 OCR：

```powershell
python run_companies.py --mode analysis-only --stage ocr --source sales --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

purchase OCR：

```powershell
python run_companies.py --mode analysis-only --stage ocr --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

purchase DeepSeek：

```powershell
python run_companies.py --mode analysis-only --stage deepseek --source purchase --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

只读校验：

```powershell
python run_companies.py --mode dry-run --stage existing --source sales --accountbook xinghai --dataset weiyu --month 7月 --company-template weiyu
```

## 14. 参数速查

| 参数 | 可选值/示例 | 说明 |
|---|---|---|
| `--accountbook` | `xinghai` | 目标账套，可重复指定 |
| `--dataset` | `weiyu` | 资料公司，可重复指定 |
| `--month` | `7月` | 月份目录，可重复指定 |
| `--company-template` | `weiyu` | 公司模板目录 |
| `--source` | `sales/purchase/bank/misc/all` | 业务板块 |
| `--stage` | `ocr/deepseek/existing/all` | 分析阶段 |
| `--mode` | `analysis-only/prepare/dry-run/confirm` | 运行安全级别 |
| `--plan` | 无值 | 只检查计划，不执行流水线 |
| `--limit` | `1` | confirm 时限制处理数量 |
| `--receipt-id` | receipt ID | confirm 时只处理指定凭证 |
| `--allow-cross-entity-confirm` | 无值 | 明确允许跨主体真实上传 |

## 15. 常见问题

### 为什么 OCR 不需要登录？

OCR 只读取本地 PDF。DeepSeek 阶段为了严格校验动态科目和 Item，需要有效账套会话。

### 为什么 analysis-only 没有创建客户或供应商？

这是安全设计。缺失 Item 只有在真实 `confirm` 且配置允许时才创建。

### 为什么一张失败后不继续？

保存结果或附件结果不明确时继续运行可能产生重复凭证，因此流水线必须停止并人工核对。

### 为什么模板、资料公司和账套要分开？

它们分别代表业务规则、原始资料和写入目标。分离后可以使用生产资料和生产模板在测试账套验证，也可以让不同公司拥有独立科目和提示词。

### 为什么账期不同仍能分析？

测试阶段允许资料月份与账套当前期间不同，差异只提示。正式上传前必须人工确认凭证日期和目标会计期间。
