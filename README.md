# 企业凭证 OCR、模板匹配与账无忧上传

当前唯一操作说明已精简并迁移到 [docs/USAGE.md](docs/USAGE.md)。请以该文档为准；旧的 `datasets.json`、`month.conf`、`config/accountbooks.json` 和 `project.json` v4 均已移除且不兼容。

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

启动器会登录主账号、发现并登记全部公司、刷新各公司的 HTTP 会话，然后显示一次精简的可访问公司列表。列表只显示公司 ID 和名称，不展示历史配置、运行状态或当前月份。统一菜单只负责按本次指定的公司和月份创建项目，不会自动执行 OCR、Qwen、凭证生成或真实上传。

菜单命令：

```text
month 资料公司ID YYYY-MM [目标公司ID]
list
status
login
discover
help
quit
```

例如，为上海微誉创建 2026 年 8 月项目：

```text
month 17867515 2026-08
```

普通用户必须指定资料公司和月份，可选指定目标公司；省略目标时仍会把资料公司自己的账套明确写入 `project.json.target`。若资料公司尚无内部模板记录，启动器会读取 `config/template_companies.json` 的 `default_base_template` 准备所需配置。每个月份都会独立生成 `month.conf` 和 `project.json`，并固定创建 `sales`、`purchase`、`bank`、`misc` 四类资料目录。目标账套、`mode`、`analysis_stage` 和 `sources` 只配置在该月 `project.json`；公司 JSON 只保存跨月份共享的模板和资料身份。

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
- 银行合并 PDF 与 `bank_split.json` 放入 `input/bank/`。
- 两个 Excel 放在 `input/` 根目录。

### 0.3 从 OCR 到 dry-run 的最短流程

以下示例公司的配置名是 `company_17867515_上海微誉信息技术有限公司`。命令参数使用配置文件名，但不带 `.json`。所有月份敏感命令都必须显式传入 `YYYY-MM`；公司 JSON 本身不再保存月份。

1. 编辑 `data/inbox/company_17867515_上海微誉信息技术有限公司/2026-08/project.json`，只启用本月准备处理的业务，并设置：

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

   当前默认模型是 `qwen-turbo`，配置位于 `config/pipeline.defaults.json`。

4. 将本月 `project.json` 的 `analysis_stage` 改为 `llm`，再次执行分析：

   ```bat
   commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
   ```

5. 生成人工复核报告：

   ```bat
   commands\analysis_report.bat company_17867515_上海微誉信息技术有限公司 2026-08 sales
   ```

6. 人工复核通过后，将本月 `project.json` 改为：

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
| `commands\initialize_month.bat SOURCE_CONFIG YYYY-MM [TARGET_COMPANY_ID_OR_KEY]` | 创建月份项目并显式记录目标账套 | 否 |
| `commands\run_company.bat SOURCE_COMPANY_CONFIG_NAME YYYY-MM` | 用资料公司定位月份项目，再按该月显式 target、mode 和 stage 执行 | 否 |
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
| 银行 | `bank` | 合并 PDF 裁剪、回单号识别、单张 PDF 输出 | 不允许上传 |
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
4. 调用时若同时指定 `--company` 和 `--month`，建立会话后自动初始化资料公司月份；可用 `--target` 明确目标账套公司。
5. 未指定参数时只发现、列出、登记和登录公司；无论是否初始化目录，都不会自动运行 analysis、prepare、dry-run 或 confirm。

直接指定并初始化的示例：

```bat
commands\discover_companies.bat --company 17867515 --month 2026-09 --target 17867515
```

`--company` 和 `--target` 支持精确的 company ID、`company_key`、真实公司全名或标准配置文件名；日常优先使用稳定且无需中文转义的 company ID。这个命令行快捷方式只用于内部模板记录已经准备好的资料公司；其他情况请进入 `commands\start.bat`，使用 `month SOURCE_COMPANY_ID YYYY-MM [TARGET_COMPANY_ID]`。新月份会创建独立 `project.json`，显式保存目标账套，四类 source 默认关闭且不继承其他月份；发现、登记和登录仍覆盖本次发现的全部公司。

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

公司出现在可访问清单后，在统一菜单输入资料公司 ID、月份和可选目标公司 ID。清单同时用于选择资料公司和目标账套，不推断历史月份状态。

### 步骤 2：创建公司月份项目

```text
month 18458361 2026-09 20151038
```

公司发现阶段生成的内部身份记录会被安全复用。若缺少模板记录，系统根据 `config/template_companies.json` 的 `default_base_template` 准备公司独立模板；当前基础模板配置为 `weiyu`。随后创建指定月份的四类资料目录以及独立的 `month.conf` 和 `project.json`。

创建完成后会得到：

```text
config/companies/company_<company_id>_<真实公司名>.json
templates/{company_key}/
```

`month SOURCE_COMPANY_ID YYYY-MM [TARGET_COMPANY_ID]` 负责准备资料公司月份并把目标账套显式写入 `project.json.target`；省略目标时明确使用资料公司的同主体账套。dataset 从资料公司身份稳定推导，模板仍由资料公司跨月份共享。四类业务目录固定存在，但只有该月 `project.json` 中明确打开的 `sources.*.enabled` 才会进入运行计划。

## 6. 公司共享配置与月份运行配置

公司 JSON 只保存身份、dataset 和跨月份共享的模板：

```text
config/companies/company_<company_id>_<真实公司名>.json
```

```json
{
  "version": 2,
  "company_key": "company_17867515",
  "company_id": "17867515",
  "company_name": "上海微誉信息技术有限公司",
  "enabled": true,
  "dataset": "company_17867515",
  "template_company": "weiyu"
}
```

每个月份的运行配置只写在自己的 `project.json`：

```text
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/project.json
```

```json
{
  "version": 4,
  "company_key": "company_17867515",
  "company_id": "17867515",
  "company_name": "上海微誉信息技术有限公司",
  "dataset": "company_17867515",
  "month": "2026-08",
  "target": {
    "accountbook_key": "company_17867515",
    "company_id": "17867515",
    "company_name": "上海微誉信息技术有限公司"
  },
  "defaults": {
    "mode": "analysis-only",
    "analysis_stage": "ocr",
    "analysis_validation": "strict",
    "preload_items": false,
    "purpose": "production",
    "allow_cross_entity": false
  },
  "sources": {
    "sales": { "enabled": true },
    "purchase": { "enabled": false },
    "bank": { "enabled": false },
    "misc": { "enabled": false }
  }
}
```

新月份不会继承上个月的 `mode`、`analysis_stage` 或 `sources`。`sales`、`purchase`、`bank`、`misc` 四个 source key 固定存在，新月份默认全部关闭；按该月实际资料明确开启。公司 JSON 中出现旧的 `month/defaults/sources` 会直接报错，不再兼容。

### 关键字段

| 所在文件 | 字段 | 说明 |
|---|---|---|
| 公司 JSON | `company_key/company_id/company_name` | 资料公司身份 |
| 公司 JSON | `dataset` | 该公司的资料根标识 |
| 公司 JSON | `template_company` | 唯一跨月份共享的业务模板 |
| 月份 `project.json` | `month` | 本配置所属月份，必须与目录名和命令一致 |
| 月份 `project.json` | `target.accountbook_key/company_id/company_name` | 本月凭证最终写入的目标账套，三项必须与 `accountbooks.json` 精确一致 |
| 月份 `project.json` | `mode` | 本月安全级别 |
| 月份 `project.json` | `analysis_stage` | 本月使用 OCR、Qwen 或已有分析 |
| 月份 `project.json` | `preload_items` | 本月是否检查并创建缺失客户/供应商 |
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
| `llm` | 读取已有 OCR，调用百炼 `qwen-turbo`，并读取动态账套科目和 Item |
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
month 17867515 2026-08
```

菜单月份命令接收资料公司 ID、`YYYY-MM` 和可选的目标公司 ID；不接受模板、来源或 dataset 覆盖参数。省略目标公司时，系统仍会把资料公司自己的账套明确写入 `project.json.target`。若内部缺少模板记录，系统按 `default_base_template` 准备；dataset 从资料公司身份推导，已有显式 dataset 保持不变。新月份生成独立 `project.json`，四类 source 默认关闭且不继承其他月份；资料必须由用户放入新生成的标准 `input` 目录。

标准结构：

```text
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/
├─ project.json                   # 本月唯一运行配置：target、mode、stage、sources 与工作区关联
├─ month.conf                     # 月份处理配置
├─ input/
│  ├─ sales/
│  ├─ purchase/
│  ├─ bank/
│  ├─ misc/
│  ├─ 收入成本表.xlsx
│  └─ 用途确认信息.xlsx
└─ （生成物不放在 data；统一进入隔离 workspaces）
```

用户按月份维护 `project.json`、`input/` 和固定文件名 `month.conf`；月份目录不接受其他 `.conf` 文件。同主体生成物位于 `workspaces/<login_account>/<target_accountbook_key>/<YYYY-MM>/generated/`；跨主体资料位于 `workspaces/<login_account>/<target_accountbook_key>/from_<dataset>/<YYYY-MM>/generated/`。

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

以下示例使用上海微誉 `2026-08/project.json`。每一步完成后都先检查输出，再进入下一步。

### 第 1 步：只启用 sales

编辑 `data/inbox/company_17867515_上海微誉信息技术有限公司/2026-08/project.json`：

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

默认模型与接口配置位于 `config/pipeline.defaults.json` 的 `llm` 节点：模型固定为 `qwen-turbo`，使用百炼 OpenAI 兼容接口，关闭思考模式并要求模型返回 JSON。配置文件只保存环境变量名称，不保存 API Key。

把 `2026-08/project.json` 改为：

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

把 `2026-08/project.json` 改为：

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

`2026-08/project.json` 必须保持：

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

银行目前只开放预处理，不会生成或上传凭证。

### 第 1 步：准备银行 PDF 和配置

将 `bank_split.json` 和各银行合并 PDF 放进 `input/bank/`。

### 第 2 步：只启用 bank

编辑本月 `project.json`：

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
commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
```

输出：

```text
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/{bank_key}/
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/{bank_key}/split.manifest.json
workspaces/account_1/company_17867515/2026-08/generated/bank_receipts/split.report.json
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

`weiyu` 当前只保留 24 个经 2026-07 已入账凭证验证、且分录结构可安全固定的模板：sales 1、purchase 5、bank 12、misc 6。复杂报销、内部转账、退款、汇兑损益以及无样本的推测模板不会作为候选，必须进入 `blocked`。模板不得写死动态科目 ID 或辅助核算 ID，它们必须来自本次运行的目标账套。

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
