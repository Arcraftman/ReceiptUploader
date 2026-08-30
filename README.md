# 企业凭证 OCR、模板匹配与账无忧上传

当前唯一操作说明已精简并迁移到 [docs/USAGE.md](docs/USAGE.md)。请以该文档为准；旧的 `datasets.json`、`month.conf`、`config/accountbooks.json` 和 `project.json` v5 及更早版本均已移除且不兼容。

<details>
<summary>已废弃的历史手册（仅供追溯，不可用于当前版本）</summary>

# 企业凭证自动处理与上传操作手册（已废弃）

本项目把企业原始财务资料转换为会计凭证，并通过账无忧 HTTP 接口串行保存凭证、上传附件和回读验证。

日常操作以 `commands/` 目录中的 `.bat` 文件为入口。Python 长命令仅保留给开发、排错和特殊维护，不作为普通用户的日常启动方式。

## 0. 快速启动与命令总表

### 0.1 启动项目

在 CMD 或 PowerShell 中进入项目目录并启动统一菜单：

```bat
cd /d D:\receipt-uploader
commands\start.bat
```

启动器会登录主账号、发现并登记全部公司、刷新各公司的 HTTP 会话，然后显示一次精简的可访问公司列表。列表只显示公司 ID 和名称，不展示历史配置、运行状态或当前月份。`month` 只创建月份项目；`bank` 严格按当月 `mode/analysis_stage` 执行银行 OCR、LLM 或 `prepare+existing`。真实上传仍不在菜单开放。

菜单命令：

```text
month dataset公司ID YYYY-MM target公司ID
bank dataset公司ID YYYY-MM
unmatched dataset公司ID YYYY-MM
verify dataset公司ID YYYY-MM
list
status
login
discover
help
quit
```

例如，为上海微誉创建 2026 年 8 月项目：

```text
month 17867515 2026-08 17867515
```

普通用户必须显式指定 dataset 公司、月份和 target 公司；同主体也要把同一个公司 ID 分别写入 dataset 与 target 参数。若 dataset 公司尚无内部模板记录，启动器会读取 `config/template_companies.json` 的 `default_base_template` 准备所需配置。每个月份都会独立生成 `project.json` v7，并固定创建 `sales`、`purchase`、`bank`、`misc` 四类资料目录。dataset、target 和四个业务各自的 `enabled/mode/analysis_stage/preload_items` 只配置在该月 `project.json`；公司 JSON 只保存跨月份共享的模板和资料身份。

### 0.2 创建月份后的资料目录

月份初始化后，原始资料放入：

```text
data\inbox\company_<公司ID>_<真实公司名>\<YYYY-MM>\input\
├─ sales\
├─ purchase\
├─ bank\
├─ misc\
├─ 收入成本表.xlsx
└─ 用途确认信息.xlsx
```

- 销项发票放入 `input/sales/`。
- 进项和费用发票放入 `input/purchase/`。
- 银行 PDF 和同名 Excel 放入 `input/bank/`；所有银行规则只配置在当月 `project.json.sources.bank.banks`。
- 两个 Excel 放在 `input/` 根目录。

### 0.3 从 OCR 到 dry-run 的最短流程

以下示例公司的配置名是 `company_17867515_上海微誉信息技术有限公司`。命令参数使用配置文件名，但不带 `.json`。所有月份敏感命令都必须显式传入 `YYYY-MM`；公司 JSON 本身不再保存月份。

1. 编辑 `data/inbox/company_17867515_上海微誉信息技术有限公司/2026-08/project.json`，在要运行的业务对象（例如 `sources.sales`）中设置：

   ```json
   "mode": "analysis-only",
   "analysis_stage": "ocr",
   "preload_items": false
   ```

2. 执行 OCR：

   ```bat
   commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
   ```

3. 第一次使用 Qwen 前，在 PowerShell 中设置百炼 API Key，然后重新打开终端：

   ```powershell
   [Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", (Read-Host "请输入阿里云百炼 API Key"), "User")
   ```

   当前默认模型是 `qwen3.7-flash`，配置位于 `config/pipeline.defaults.json`。

4. 将本月 `project.json` 中对应业务的 `analysis_stage` 改为 `llm`，再次执行分析：

   ```bat
   commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
   ```

5. 生成人工复核报告：

   ```bat
   commands\analysis_report.bat company_17867515_上海微誉信息技术有限公司 2026-08 sales
   ```

6. 人工复核通过后，将本月 `project.json` 中对应业务改为：

   ```json
   "mode": "dry-run",
   "analysis_stage": "existing"
   ```

7. 执行上传前演练；此命令不会真实上传：

   ```bat
   commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
   ```

### 0.4 用户命令一览

| 命令 | 用途 | 是否会写入线上账套 |
|---|---|---|
| `commands\start.bat` | 推荐入口：登录、发现公司并进入安全菜单 | 否 |
| `commands\discover_companies.bat` | 重新发现全部公司并建立会话 | 否 |
| `commands\login_companies.bat` | 只刷新已登记公司的 HTTP 会话 | 否 |
| `commands\initialize_month.bat DATASET_CONFIG YYYY-MM TARGET_COMPANY_ID_OR_KEY` | 创建月份项目并显式记录 dataset 与 target | 否 |
| `commands\run_bank.bat DATASET_COMPANY_CONFIG_NAME YYYY-MM` | 只运行已启用的 bank；依次完成裁剪、特殊对象分流、剩余 OCR 和剩余流水匹配 | 否 |
| `commands\run_company.bat DATASET_COMPANY_CONFIG_NAME YYYY-MM` | 用 dataset 公司定位月份项目，再按该月显式 target、mode 和 stage 执行 | 否 |
| `commands\analysis_report.bat COMPANY_CONFIG_NAME YYYY-MM sales` | 生成指定月份的销项复核简表；业务也可为 `purchase`、`bank`、`misc` | 否 |
| `commands\status.bat` | 查看全部隔离任务状态 | 否 |
| `commands\confirm_one.bat COMPANY_CONFIG_NAME YYYY-MM` | 明确确认后上传指定月份的一张 | **是** |
| `commands\confirm_all.bat COMPANY_CONFIG_NAME YYYY-MM` | 明确确认后上传指定月份的全部有效凭证 | **是** |
| `commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM` | 清除指定月份的本地上传断点 | 否，但会修改本地状态 |
| `commands\create_company_template.bat --name "公司全名" --base-template KEY` | 维护人员为例外公司手工指定基础模板 | 否 |

直接重新发现公司：

```bat
commands\discover_companies.bat
```

只刷新现有公司会话：

```bat
commands\login_companies.bat
```

查看任务状态：

```bat
commands\status.bat
```

### 0.5 真实上传命令

只有 OCR、Qwen、人工复核和 `dry-run` 全部通过后，才允许执行真实上传。

单张验证：

```bat
commands\confirm_one.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

整批上传：

```bat
commands\confirm_all.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

两条命令都会要求输入屏幕显示的完整确认文字。`confirm_all` 可能在线上生成整批凭证，不能用于测试启动流程。

---

## 1. 开始前先确认

### 1.1 当前业务完成度

| 业务 | 配置名 | 当前可执行范围 | 是否允许批量上传 |
|---|---|---|---|
| 销项发票 | `sales` | 映射、OCR、Qwen、凭证、附件、回读 | 已跑通，但仍须先单张验证 |
| 进项与费用 | `purchase` | 映射、OCR、Qwen、凭证草稿、校验 | 暂未批准批量上传 |
| 银行 | `bank` | 裁剪、特殊对象分流、剩余 OCR/流水匹配、LLM 分析、prepare+existing 最终草稿 | 不允许上传 |
| 杂项 | `misc` | 已提炼6类月末模板；运行和上传尚未接入 | 不允许上传 |

### 1.2 绝对不能跳过的安全规则

1. 本月 `project.json` 中的 `sources.*.enabled` 只是后续执行的安全开关，不决定月份项目是否创建对应目录。只启用本月已经验证并准备执行的业务；`bank` 和 `misc` 不会因月份初始化而自动开启。
2. `confirm` 表示真实保存凭证，不是“确认一下配置”。
3. 正式批量上传前必须依次完成 OCR、Qwen、人工复核、dry-run、单张上传和网页回读。
4. `commands\confirm_all.bat` 不根据本地历史日志自动跳过；重复运行可能在线上生成重复凭证。
5. 如果上一批线上凭证没有清空，不得直接重新完整上传。
6. 不能确定模板、科目、辅助核算、金额或借贷方向时，程序必须阻断，不得猜测入账。
7. Cookie、登录密码、百炼 API Key 不得写入 README、公司 JSON 或模板文件。

---

## 2. 先理解三个独立概念

项目把“资料、模板、账套”分开管理：

| 概念 | 示例 | 决定什么 |
|---|---|---|
| 资料公司 `dataset` | `company_17867515` | 从哪个目录读取 PDF、XLSX 和月份资料 |
| 模板公司 `template_company` | `weiyu` | 使用哪家公司的业务规则、提示词和会计模板 |
| 目标账套 `accountbook` | `company_17867515` | 动态读取哪套科目和辅助核算，最后写入哪里 |

当前已初始化关系是：

```text
资料：company_17867515（上海微誉信息技术有限公司）
模板：weiyu（上海微誉信息技术有限公司）
账套：company_17867515（上海微誉信息技术有限公司）
```

资料与目标账套是同一主体。若以后启用跨主体任务，仍必须显式设置 `allow_cross_entity=true`，真实上传时还要通过专用确认 BAT 授权。

---

## 3. 项目目录

```text
D:\receipt-uploader\
├─ commands\                       # 普通用户只从这里启动 BAT
├─ config\                         # 登录、账套、dataset 和公司任务配置
│  └─ companies\                  # company_<id>_<真实公司名>.json
├─ data\inbox\                    # 用户提供的公司/月度原始资料
├─ docs\                           # 架构与长期业务决策
├─ examples\                       # 脱敏输入输出示例
├─ http_sessions\                  # 本机会话，不提交版本库
├─ schema\                         # 配置和 receipt 的 JSON Schema
├─ scripts\
│  ├─ commands\                   # BAT 调用的 Python 命令实现
│  ├─ maintenance\                # 非日常维护脚本
│  └─ windows\                    # 登录与公司发现的 PowerShell 实现
├─ src\kdzwy_receipt_uploader\    # 可复用核心包
├─ templates\                      # 公司模板、提示词和动态目录
├─ tests\                          # 自动化测试
├─ runtime\                        # 全局锁和编排日志
└─ workspaces\                     # 按账号/账套/dataset/月隔离的生成物
```

项目根目录只保留工程元数据和一级业务目录；普通用户不直接运行 `scripts/` 中的文件。

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

### 步骤 4：发现公司并建立登录会话

第一次使用或还没有 `config/accountbooks.json` 时，运行：

```bat
commands\discover_companies.bat
```

该入口会遍历 `config/kdzwy.json` 中的全部启用账号，自动导入每个账号返回的全部可访问公司，不要求逐家公司选择。它会：

1. 把本次发现的账套写入并启用到 `config/accountbooks.json`；本次没有返回的旧账套会被禁用。
2. 为新公司创建默认 `enabled=false` 的 `config/companies/company_<company_id>_<真实公司名>.json`。
3. 为全部已发现公司建立独立 HTTP 会话。
4. 调用时必须同时指定 `--dataset`、`--month` 和 `--target`，建立会话后才初始化月份。
5. 未指定参数时只发现、列出、登记和登录公司；无论是否初始化目录，都不会自动运行 analysis、prepare、dry-run 或 confirm。

直接指定并初始化的示例：

```bat
commands\discover_companies.bat --dataset 17867515 --month 2026-09 --target 17867515
```

`--dataset` 和 `--target` 支持精确的 company ID、`company_key`、真实公司全名或标准配置文件名；日常优先使用稳定且无需中文转义的 company ID。其他情况请进入 `commands\start.bat`，使用 `month DATASET_COMPANY_ID YYYY-MM TARGET_COMPANY_ID`。新月份会创建独立 `project.json` v7，显式保存 dataset 和 target，四类 source 默认关闭且不继承其他月份。

成功后，每家公司会有独立会话：

```text
D:\receipt-uploader\http_sessions\accounts\{登录账号key}\companies\{公司全名}.accountbook.cookies.json
```

以后只需要刷新已登记公司的会话时，运行：

```bat
commands\login_companies.bat
```

发现和登录均为纯 HTTP，不使用 Playwright。

---

## 5. 创建公司月份项目

### 步骤 1：确认账套已经发现并登录

先运行：

```bat
commands\discover_companies.bat
```

确认 `config/accountbooks.json` 和 `http_sessions/accounts/<登录账号key>/companies/` 中已有该公司。

公司出现在可访问清单后，在统一菜单输入 dataset 公司 ID、月份和 target 公司 ID。两个公司都必须显式选择，不推断历史月份状态。

### 步骤 2：创建公司月份项目

```text
month 18458361 2026-09 20151038
```

公司发现阶段生成的内部身份记录会被安全复用。若缺少模板记录，系统根据 `config/template_companies.json` 的 `default_base_template` 准备公司独立模板；当前基础模板配置为 `weiyu`。随后创建指定月份的四类资料目录以及独立的 `project.json` v7。

创建完成后会得到：

```text
config/companies/company_<company_id>_<真实公司名>.json
templates/{company_key}/
```

`month DATASET_COMPANY_ID YYYY-MM TARGET_COMPANY_ID` 负责把两个完整身份分别写入 `project.json.dataset` 与 `project.json.target`。target 不允许省略；同主体时两个参数填写相同 ID。模板仍由 dataset 公司跨月份共享。

## 6. 公司共享配置与月份运行配置

公司 JSON 只保存身份和跨月份共享的模板：

```text
config/companies/company_<company_id>_<真实公司名>.json
```

```json
{
  "version": 3,
  "company_key": "company_17867515",
  "company_id": "17867515",
  "company_name": "上海微誉信息技术有限公司",
  "template_company": "weiyu"
}
```

每个月份的运行配置只写在自己的 `project.json`：

```text
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/project.json
```

```json
{
  "version": 7,
  "month": "2026-08",
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
  "defaults": {
    "analysis_validation": "strict",
    "purpose": "production",
    "allow_cross_entity": false
  },
  "sources": {
    "sales": { "enabled": true, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false },
    "purchase": { "enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false },
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
      },
      "exceptions": [
        "TIPS电子缴税款业务待报解预算收入"
      ]
    },
    "misc": { "enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false }
  }
}
```

新月份不会继承上个月的业务配置。`sales`、`purchase`、`bank`、`misc` 四个 source key 固定存在，每个都必须精确填写 `enabled`、`mode`、`analysis_stage`、`preload_items`；新月份默认全部关闭。公司 JSON 中出现旧的 `month/defaults/sources` 会直接报错，不再兼容。

### 关键字段

| 所在文件 | 字段 | 说明 |
|---|---|---|
| 公司 JSON | `company_key/company_id/company_name` | 资料公司身份 |
| 公司 JSON | `template_company` | 唯一跨月份共享的业务模板 |
| 月份 `project.json` | `month` | 本配置所属月份，必须与目录名和命令一致 |
| 月份 `project.json` | `dataset.company_key/company_id/company_name` | 本月资料来源公司，三项必须与公司配置精确一致 |
| 月份 `project.json` | `target.accountbook_key/company_id/company_name` | 本月凭证最终写入的目标账套，三项必须与 `accountbooks.json` 精确一致 |
| 月份 `project.json` | `sources.<业务>.mode` | 该业务本月的安全级别 |
| 月份 `project.json` | `sources.<业务>.analysis_stage` | 该业务使用 OCR、Qwen 或已有分析 |
| 月份 `project.json` | `sources.<业务>.preload_items` | 该业务是否检查并创建缺失客户/供应商 |
| 月份 `project.json` | `sources.bank.banks` | 本月全部银行的裁剪与流水四列配置 |
| 月份 `project.json` | `sources.bank.exceptions` | 只填写流水表对手方列出现的完整名称；命中项在普通流程前统一隔离 |
| `config/bank_exception.defaults.json` | 新公司、新月份首次创建时复制的通用银行 exception 默认值；不覆盖已有月份 |
| 月份 `project.json` | `allow_cross_entity` | 本月是否允许资料主体和目标账套不同 |
| 月份 `project.json` | `sources` | 本月四类业务各自是否进入执行计划 |

配置文件名用于人工识别，`company_key` 用于程序内部稳定关联；二者不要求相同。运行 BAT 时传入资料公司的配置文件名并省略 `.json`，再显式传入 `YYYY-MM`。运行器据此读取对应月份的 `project.json`，并以其中的 `target` 决定最终账套；不会从命令参数猜目标，也不会回退到其他月份。

### mode 含义

| 值 | 行为 |
|---|---|
| `analysis-only` | 只处理映射、OCR 或 Qwen，不生成上传凭证 |
| `prepare` | 读取账套并生成准备数据，不上传 |
| `dry-run` | 生成正式结构的 receipt 并本地校验，不调用保存接口 |
| `confirm` | 真实保存凭证和上传附件 |

普通 `run_company.bat` 不接受 mode 参数，并拒绝执行 `confirm`；真实上传只能使用 `confirm_one.bat` 或 `confirm_all.bat`。

### analysis_stage 含义

| 值 | 行为 |
|---|---|
| `ocr` | 只做 OCR，不调用 Qwen，不需要账套会话 |
| `llm` | 读取已有 OCR，调用百炼 `qwen3.7-flash`，并读取动态账套科目和 Item |
| `existing` | 不做 OCR、不调用 Qwen，复用已经人工批准的分析 |
| `all` | OCR 后立刻执行 Qwen；首次运行不建议使用 |

### preload_items 特别说明

| 值 | 行为 |
|---|---|
| `false` | 不创建远端客户或供应商，最安全 |
| `once` | 输入 Excel 变化后检查一次，并创建缺失客户或供应商 |
| `auto` | 每次都检查并可能创建，日常不建议使用 |

`preload_items=once/auto` 是远端写操作，即使当前是分析流程也可能创建辅助核算对象。第一次 OCR 建议设置为 `false`；确认 Excel 中客户和供应商名称无误后再决定是否启用。

---

## 7. 每月资料怎么放

每次收到一家公司的新月份资料，运行统一启动器并在菜单中指定公司和月份。月份必须带年份，统一使用 `YYYY-MM`，避免不同年份的“7月”写入同一目录：

```text
month 17867515 2026-08 17867515
```

菜单月份命令接收 dataset 公司 ID、`YYYY-MM` 和 target 公司 ID，三项缺一不可。系统把两个身份完整写入新月份的 `project.json` v7；四类 source 默认关闭且不继承其他月份。

标准结构：

```text
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/
├─ project.json                   # 本月唯一运行配置：dataset、target、mode、stage、sources
├─ input/
│  ├─ sales/
│  ├─ purchase/
│  ├─ bank/
│  ├─ misc/
│  ├─ 收入成本表.xlsx
│  └─ 用途确认信息.xlsx
└─ （生成物不放在 data；统一进入隔离 workspaces）
```

用户按月份只维护 `project.json` 和 `input/`。同主体生成物位于 `workspaces/<login_account>/<target_accountbook_key>/<YYYY-MM>/generated/`；跨主体资料位于 `workspaces/<login_account>/<target_accountbook_key>/from_<dataset>/<YYYY-MM>/generated/`。

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

### bank 资料与唯一配置

每家银行的 PDF 和 Excel 使用同一个小写 bank key：

```text
input/bank/
├─ shanghaiyinhang.pdf
├─ shanghaiyinhang.xlsx
├─ zhaoshangyinhang.pdf
└─ zhaoshangyinhang.xlsx
```

银行存款科目号、裁剪和流水列配置都写在当月 `project.json.sources.bank.banks.<bank_key>`。`bank_account_number` 是该银行在目标账套中的固定银行存款科目号，例如上海银行 `100201`、招商银行 `100204`。项目不再生成或读取 `bank_split.json`，bank 入口也不会自动改写 `project.json`。

---

## 8. sales 完整操作流程

以下示例使用上海微誉 `2026-08/project.json`。每一步完成后都先检查输出，再进入下一步。

### 第 1 步：只启用 sales

编辑 `data/inbox/company_17867515_上海微誉信息技术有限公司/2026-08/project.json`：

```json
"sources": {
  "sales": { "enabled": true, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false },
  "purchase": { "enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false },
  "bank": {
    "enabled": false,
    "mode": "analysis-only",
    "analysis_stage": "ocr",
    "preload_items": false,
    "banks": {},
    "exceptions": ["TIPS电子缴税款业务待报解预算收入"]
  },
  "misc": { "enabled": false, "mode": "analysis-only", "analysis_stage": "ocr", "preload_items": false }
}
```

### 第 2 步：执行 OCR

确认 `sources.sales` 中设置：

```json
"mode": "analysis-only",
"analysis_stage": "ocr",
"preload_items": false
```

运行：

```bat
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

检查：

```text
workspaces/account_1/company_17867515/2026-08/generated/ocr/sales/ocr_stage.report.json
workspaces/account_1/company_17867515/2026-08/generated/ocr/sales/
```

必须确认发票数量正确、发票号可识别、销售方是资料公司、购买方与金额没有大面积缺失，并且 `successTextCount` 不是 0。

### 第 3 步：执行 Qwen

先确保阿里云百炼 API Key 已配置为用户环境变量。若尚未配置，可以在 PowerShell 中执行：

```powershell
[Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", (Read-Host "请输入阿里云百炼 API Key"), "User")
```

设置完成后重新打开终端。

默认模型与接口配置位于 `config/pipeline.defaults.json` 的 `llm` 节点：模型固定为 `qwen3.7-flash`，使用百炼 OpenAI 兼容接口，关闭思考模式并要求模型返回 JSON。配置文件只保存环境变量名称，不保存 API Key。

把 `2026-08/project.json` 的 `sources.sales` 改为：

```json
"mode": "analysis-only",
"analysis_stage": "llm"
```

运行：

```bat
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

检查：

```text
workspaces/account_1/company_17867515/2026-08/generated/ocr/sales/template_analysis.json
```

Qwen 只能从 `templates/weiyu/sales/` 选择模板，不能访问 purchase、bank 或 misc 模板。

### 第 4 步：生成人工复核简表

运行：

```bat
commands\analysis_report.bat company_17867515_上海微誉信息技术有限公司 2026-08 sales
```

输出：

```text
workspaces/account_1/company_17867515/2026-08/generated/ocr/sales/concise_template_analysis.md
```

逐张检查发票号、模板名称、借贷科目、金额、税额、客户辅助核算、统一摘要和 `blocked` 状态。

有任何错误就修改模板或提示词，然后重新运行 Qwen。不要带着错误进入 dry-run。

### 第 5 步：执行 dry-run

把 `2026-08/project.json` 的 `sources.sales` 改为：

```json
"mode": "dry-run",
"analysis_stage": "existing"
```

运行：

```bat
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

检查：

```text
workspaces/account_1/company_17867515/2026-08/generated/receipts/sales/
workspaces/account_1/company_17867515/2026-08/generated/maps/sales/preupload_review.report.json
```

进入真实上传前必须满足：无效 receipt 为 0、预审警告为 0、借贷平衡、科目和客户 ID 来自当前账套、PDF 与发票号一致。

### 第 6 步：真实上传一张

`2026-08/project.json` 的 `sources.sales` 必须保持：

```json
"analysis_stage": "existing"
```

运行：

```bat
commands\confirm_one.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

按提示完整输入 `company_17867515_上海微誉信息技术有限公司 2026-08` 才会继续。程序只处理这一月份的一张。

随后立即到网页检查目标账套、日期、凭证字、制单人、统一摘要、科目、客户辅助核算、金额和发票附件。系统不会自动添加审核员。

单张验证结束后，如果下一步要从头执行整批，必须先在网页删除这张测试凭证，并再次确认它已经不存在。系统不会自动跳过这张单测凭证。

### 第 7 步：真实批量上传

只有单张网页回读完全正确，并且用于测试的单张凭证已经从线上删除后，才运行：

```bat
commands\confirm_all.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

按提示输入：

```text
UPLOAD ALL company_17867515_上海微誉信息技术有限公司 2026-08
```

任意一张保存、附件或回读不明确时，批次会立即停止，后续凭证不会继续。

---

## 9. purchase 操作流程

purchase 的执行顺序与 sales 一致：

1. 只启用 `purchase`。
2. 在本月 `project.json` 设置 `analysis-only + ocr`，并运行 `commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08`。
3. 检查 OCR 和用途确认匹配。
4. 设置 `analysis-only + llm` 并再次运行。
5. 运行 `commands\analysis_report.bat company_17867515_上海微誉信息技术有限公司 2026-08 purchase`。
6. 人工检查模板、分录、供应商和税额。
7. 在本月 `project.json` 设置 `dry-run + existing`，并运行 `commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08`。

purchase 必须额外检查：

- 资料公司是否为购买方。
- 发票销售方是否与用途确认表中的供应商一致。
- 发票号是否进入用途确认范围。
- 采购商品与已验证的运杂费、手续费、通讯费、物业水电费分类是否正确。
- 原材料、固定资产、办公、差旅、维修等当前无已验证固定模板的业务是否正确进入 `blocked`。
- 专票进项税和普票价税合计取值是否正确。
- 应付账款行是否使用供应商辅助核算。
- 同一凭证的摘要是否按模板统一生成并包含发票号。

当前 purchase 尚未批准批量真实上传。即使 dry-run 通过，也只能在明确批准后进行单张测试。

---

## 10. bank 当前操作流程

银行按阶段执行：OCR 只生成 OCR/map；LLM 只生成分析；`prepare+existing` 才生成最终 `receipt.json` 草稿。任何真实上传仍未开放。

### 第 1 步：准备银行文件和当月配置

将每家银行的同名 `<bank_key>.pdf` 和 `<bank_key>.xlsx` 放入 `input/bank/`，然后在当月 `project.json.sources.bank.banks` 中完整配置每家银行的 `bank_account_number`、`split` 和 `statement_columns`。

### 第 2 步：显式启用 bank

当月 `sources.bank` 必须显式设置 `enabled=true`、`mode=analysis-only`、`analysis_stage=ocr`、`preload_items=false`。bank 入口只读取和校验配置，不会修改 `project.json`。

### 第 3 步：运行银行裁剪、特殊对象分流、剩余 OCR 和流水匹配

```bat
commands\run_bank.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

统一菜单中也可以输入：

```text
bank 17867515 2026-08
```

输出：

```text
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/{bank_key}/
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/{bank_key}/split.manifest.json
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/split.report.json
workspaces/account_1/company_17867515/2026-08/generated/bank_exceptions/{counterparty}/
workspaces/account_1/company_17867515/2026-08/generated/ocr/bank/{bank_key}/{receipt}/ocr.txt
workspaces/account_1/company_17867515/2026-08/generated/ocr/bank/{bank_key}/{receipt}/ocr.json
workspaces/account_1/company_17867515/2026-08/generated/ocr/bank/ocr_stage.report.json
workspaces/account_1/company_17867515/2026-08/generated/maps/bank/bank_map.json
workspaces/account_1/company_17867515/2026-08/generated/maps/bank/bank_map.report.json
workspaces/account_1/company_17867515/2026-08/generated/maps/bank/bank_exceptions.json
```

程序会按配置等高裁剪，优先读取 PDF 原生文字，必要时使用 RapidOCR。文件名优先级固定为：交易流水号（含“交易流水”“核心流水号”）→ 回单编号 → 独立字母数字索引；每一级候选都必须符合该银行配置的长度和起始字母。起始字母大小写敏感，文件名保留识别结果的原始大小写。输入和配置未变化时会复用已有裁剪结果。

如果号码无法识别或发生重复，文件进入：

```text
generated/bank_receipts/{bank_key}/bank_exception/
```

这是正常裁剪结果：没有唯一有效命名索引或出现重复索引的切片直接成为 bank exception，不再进入普通 OCR。系统会把它们与名单命中的特殊流水统一写入 `bank_exceptions.json`；能由技术关键词关联到名称的 PDF 放入对应名称目录，其余放入 `_无命名索引` 目录等待人工查看。

OCR 阶段固定执行“全部银行裁剪 → 按 PDF 关键词分离特殊回单 → 按 exception 名称和流水索引分离其余特殊回单 → 只对剩余回单 OCR → 只对剩余流水匹配”，并生成 `bank_map.json`、`bank_map.report.json` 和唯一的 `bank_exceptions.json`。这个阶段绝不生成 `receipt.json`。`configCompany` 永远固定为 `dataset.company_name`，模型无权选择或修改。银行借方有有效金额时是我方现金流出，`counterparty_name_column` 的单元格固定作为供应商；银行贷方有有效金额时是我方现金流入，该单元格固定作为客户。另一金额列允许为 0、空值或文字。

`sources.bank.exceptions` 现在只是名称数组。用户只填写银行流水 Excel 对手方/索引行中出现的完整名称，系统按大小写敏感精确匹配，不再区分客户、供应商或人名，也不再填写 `handling`、`template_id`、`records`。命中的流水与 PDF 一律从普通 OCR、`bank_map`、LLM、模板和普通凭证中排除。裁剪 PDF 原件保留在 `generated/bank_receipts`，特殊副本放入 `generated/bank_exceptions/<counterparty>/`。

以后创建任何公司月份时，初始化器会把 `config/bank_exception.defaults.json` 中的默认名称复制进本月 `sources.bank.exceptions`。目前只预置 TIPS；不会把微誉的京东名称或具体人名复制给其他公司，也不会覆盖已经存在的月份配置。

本月新增特殊对象时直接把名字加进数组，例如：`"exceptions": ["TIPS电子缴税款业务待报解预算收入", "重庆京东盛际小额贷款有限公司", "张三"]`。TIPS 这类无文件名索引 PDF 的技术关键词由全局默认规则维护，月度项目不用填写。

现金流入时，如贷方列为有效金额、借方列又完全由一串或多串数字组成，系统将这些数字按顺序直接设为 `explanation_body`；不会附加模板原 body，含普通文字时也不会触发。每张已匹配回单的交易/记账日期由 OCR 原文确定，只有“银行存款”分录的摘要末尾追加一个空格和 `YYYY-MM-DD`，对方科目分录保留不带日期的基础摘要。

银行模板使用 `flowDirections` 将收款和付款在模型调用前硬隔离。规则唯一时系统直接选择模板，不消耗 Qwen 调用；只有仍存在多个合法候选时才调用模型。内部转账（对手方等于资料公司）保持 blocked。若明确把本月 bank 的 `preload_items` 设为 `"once"` 或 `"auto"`，系统会按流水方向在目标账套创建缺少的客户/供应商；该配置属于远端写操作，默认 `false` 不创建。

模板选择时会注入当前 bank key 的 `bank_account_number`。模板中名为“银行存款”的分录会在运行时强制解析为这个科目号；分析文件和最终 receipt 生成前还会再次校验。配置科目不存在、模板没有且仅有一条银行存款分录，或已有分析使用了其他银行科目时，任务立即阻断。

数组中的完整名称优先于借贷方向分类，会在普通 OCR 前把对应 PDF 复制到特殊目录并排除；未配置姓名仍使用保守中文姓名规则作为兜底跳过。使用 `exceptions dataset公司ID YYYY-MM` 查看全部特殊对象、裁剪原件和特殊副本；`unmatched` 只显示未被 exception 接管的普通未匹配记录。

下一步把 bank 设置为 `mode=analysis-only、analysis_stage=llm` 生成并复核 `generated/ocr/bank/template_analysis.json`。复核后改为 `mode=prepare、analysis_stage=existing`，这时才生成最终 `generated/receipts/bank/receipt_*/receipt.json`，并全部保持 `draft=true`。用户补齐并复核后手动改为 `draft=false`。

只有在 `prepare+existing` 之后才能运行 `verify dataset公司ID YYYY-MM`。它逐条列出全部 `draft=true` 的号码、receiptId 和最终路径，并对 `draft=false` 文件执行与上传相同的字段、借贷平衡和附件校验。dry-run 和未来真实提交都会在执行前自动重复该检查；只要仍有草稿或无效 receipt，整批停止。

验证还会以当前普通 `bank_map` 为白名单。旧流程生成、但后来已被 exception 分流的 receipt 会显示为“旧/特殊产物”并阻断，不能因手工改成 `draft=false` 而混入提交。

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
│  ├─ invoice_classifier_prompt.txt
│  ├─ sales.md
│  ├─ purchase.md
│  ├─ bank.md
│  └─ misc.md
├─ classification_rules.json
├─ final_template_sample.json
├─ index.json
└─ 模板编辑说明.json
```

`weiyu` 当前保留 25 个模板：sales 1、purchase 5、bank 13、misc 6。其中京东供应链是按月 exception 配置启用的动态应付专用模板；其余模板来自 2026-07 已入账凭证验证。复杂报销、内部转账、退款、汇兑损益以及无样本的推测模板不会作为候选，必须进入 `blocked`。模板不得写死动态科目 ID 或辅助核算 ID，它们必须来自本次运行的目标账套。

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

最终结果为 `销售商品收入 26312000004167256876`。Qwen 可以选择模板和填充模板允许的字段，但不能擅自改变模板分录数量、借贷方向或每行摘要。

---

## 12. 状态和日志怎么查看

运行：

```bat
commands\status.bat
```

每个任务的状态目录：

```text
workspaces/{login_account}/{accountbook}/{YYYY-MM}/state/{source}/
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

先查看日志中的当前文件编号，再运行 `commands\status.bat`。RapidOCR 处理图片型 PDF 时可能较慢，只要日志编号仍在前进就不是死锁。

### Qwen 返回 blocked

不要进入 dry-run。检查 OCR、当前板块提示词、模板目录、动态科目、辅助核算、金额和借贷平衡。修改后只重新执行 Qwen，不需要重新 OCR。

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
commands\reset_upload_state.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

该工具会先备份审计日志，但不会删除线上凭证。没有确认线上状态前不要使用。

### 会话失效或公司不匹配

重新运行 `commands\login_companies.bat`。程序会严格检查当前会话公司名是否等于目标账套公司名，名称不一致时不会继续上传。

---

## 14. 正式上传验收清单

- [ ] 本月 `project.json` 只启用了本月已验证、准备执行的业务板块；其他月份配置不受影响。
- [ ] dataset、template company、accountbook 和 month 正确。
- [ ] 跨主体任务已经明确允许。
- [ ] OCR 数量和原始 PDF 数量一致。
- [ ] Qwen 分析不存在 blocked。
- [ ] 简明报告已逐张检查。
- [ ] 所有科目来自当前目标账套。
- [ ] 客户或供应商 ID 来自当前目标账套。
- [ ] 全部分录摘要符合模板且保持一致。
- [ ] 借方合计等于贷方合计。
- [ ] 预上传审查警告为 0。
- [ ] dry-run 无效 receipt 为 0。
- [ ] `commands\confirm_one.bat` 单张上传已通过网页检查。
- [ ] 单张附件能够打开。
- [ ] 已确认本次批量不会和线上已有凭证重复。

---

## 15. 核心实现边界

```text
commands/*.bat
  → scripts/commands/run_companies.py
  → src/kdzwy_receipt_uploader/pipeline_runner.py
  → XLSX/PDF映射
  → OCR与Qwen
  → 模板渲染和receipt生成
  → 本地校验与预审
  → src/kdzwy_receipt_uploader/cli.py
  → src/kdzwy_receipt_uploader/workflow.py
```

关键原则：HTTP 客户端只负责请求；公司注册表只负责资料、模板和账套关系；模板负责会计结构；动态 ID 只能来自当前账套；状态管理只负责观察；任一不明确结果立即停止。

更详细的代码架构见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，长期业务决策见 [PROJECT_MEMORY.md](docs/PROJECT_MEMORY.md)。

</details>
