# Excel 财务工作簿与只读刷新

## 当前完成情况与 Windows 获取方式

源码现在直接包含 `excel/finance-template.xlsx`。它保留17张工作表、预算和账龄公式、统计图表，不包含真实公司的会计数据、账号或登录会话。Windows 获取这个提交后，直接用 Excel 打开此文件即可；无需安装 Python、Node，也无需运行生成脚本才能拿到模板。

- **已实现并在 Linux 验证**：账无忧只读接口采集、账套/分页/金额校验、财务快照、工作簿布局与计算。当前选择是公司编号 + 单个月份，利润趋势读取该年1月至所选月。
- **已有源码，尚未在 Windows Excel 实机验收**：VBA刷新按钮、`.xlsm`安装脚本、失败回滚与人工输入保留。
- **尚未实现**：在工作簿中输入用户名/密码并自动登录、按公司名选择、自定义起止月份区间、本地组件一键安装与自动启动。此前讨论的是目标方案，不能当成已经交付。
- **仍需人工输入**：预算、账龄所需的未核销单据及到期日。Linux WPS 不能直接运行现有 Windows 宏。

若需要安装现有的刷新按钮，在 Windows 桌面 Excel 所在电脑打开项目根目录的 PowerShell，执行：

```powershell
.\excel\Install-Finance.ps1
```

默认读取 `excel/finance-template.xlsx`，生成 `excel/finance.xlsm`，原模板不会被覆盖。若目标文件已经存在，脚本停止，避免覆盖已有数据。安装需要 Excel 允许“信任对 VBA 工程对象模型的访问”，实际运行宏也受组织宏安全策略约束。

`.xlsx`是普通工作簿，不含 VBA 按钮；`.xlsm`才包含可执行的刷新按钮。即使生成了`.xlsm`，实际刷新仍需要下面的一次性服务连接配置、有效账套会话和访问令牌，并非现在只填写账号密码就能运行。

此前 `outputs/finance_refresh_20260919/财务管理模板.xlsx` 是带公司实测数据的本地样本，outputs 不随 Git 提交。`scripts/finance/build_workbook.mjs` 是模板制作脚本，依赖制作环境的 `@oai/artifact-tool`，不是 Windows 用户的运行依赖。维护空白模板时使用 `config/finance_template_blank.json`，生成后更新仓库中的正式模板。

## 选型结果（2026-09-19）

已查看的模板覆盖情况：

| 来源 | 能覆盖的内容 | 本项目仍需完成的部分 |
| --- | --- | --- |
| [Vertex42 Financial Statements](https://www.vertex42.com/ExcelTemplates/financial-statements.html) | 利润、资产负债、现金流、预算、备用金等独立模板 | 中文统一工作簿、账无忧连接、刷新保护和账龄数据 |
| [Smartsheet Business Templates](https://www.smartsheet.com/content/free-business-templates) | 预算、业务报表和财务模板集合 | 按账套科目映射、实时只读连接、单据核销数据 |
| [Vertex42 Invoice Assistant](https://www.vertex42.com/ExcelTemplates/invoice-assistant.html) | 客户、未结余额和账龄报告 | 页面明确产品已于2022年停止销售，不作为新系统依赖 |

未在以上候选中找到覆盖全部需求且原生连接账无忧的单个工作簿。因此自行制作统一中文模板，不复制或分发第三方模板。报表采用账无忧本账套数据，不套用其他公司的科目结构。

## 已交付与边界

- 分发模板：`excel/finance-template.xlsx`。本地历史样本：`outputs/finance_refresh_20260919/财务管理模板.xlsx`（不随 Git 分发）。
- 工作表：控制台、经营统计、预算输入、预算对比、账龄输入、账龄统计，以及利润表、资产负债表、现金流量表、科目余额、凭证明细、出纳账、往来余额、月度趋势、刷新信息、公司列表、使用说明。
- Linux Python 服务通过固定只读白名单访问账无忧，不提供任意 URL 转发或写接口。
- Windows 安装脚本把模板转为 `.xlsm`，安装 VBA 和工作表上的真实刷新按钮。宏用 Windows 自带 WinHTTP 请求 Linux 服务，无需安装 Python。
- 当前 Linux 环境无法运行 Windows Excel COM/VBA。按钮源码和安装脚本已经提供，但未宣称已在 Windows Excel 点击验收。
- 当前不是“全部账龄自动接入”的最终版：账无忧单据核销、合同到期日接口尚未验证。按未核销单据输入表计算账龄/逾期；未分配净余额单列。不会用期初余额虚构账龄。
- 出纳账为已记账的1001/1002科目明细，不包括尚未制证的银行流水。仅纯现金银行互转凭证从外部收支中排除；含多类业务的复合凭证需复核。
- 现金流量表保留原接口金额字段名，尚未对全部会计制度的字段期间口径进行验证。
- 预算按公司、月份、利润项目编码人工输入，刷新不会覆盖。预置公式容量：预算200行、账龄500行、单张接口数据表6000行；超出接口表容量会阻止更新。
- 财务指标基于企业会计/小企业常见利润表项目名称；非营利、政府等其他制度尚未适配，不应直接套用本模板。

## 一次性连接配置

### Linux 后台

1. 保持项目 Python 依赖可用。使用项目现有 Python 环境。
2. 刷新一个账套登录（过期时再执行，不在 Excel 中存金蝶密码）：

```bash
bash commands/login_companies.sh --accountbook-key company_17867515 --no-pause
```

3. 启动服务：

```bash
bash commands/finance_server.sh
```

只监听 `127.0.0.1:18765`。第一次运行生成权限为0600的 `runtime/finance/access.token`；不要将令牌或会话文件放入工作簿、Git或聊天中。

### Windows Excel

复制模板和 `excel/` 目录到 Windows。通过 SSH 建立到 Linux 的本地转发，在使用期间保持窗口开启：

```powershell
ssh -N -L 18765:127.0.0.1:18765 steve@你的Linux主机
```

另开 PowerShell，将服务访问令牌安全复制到当前 Windows 用户目录：

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\KdzwyFinance"
scp steve@你的Linux主机:/home/steve/PythonC/ReceiptUploader/runtime/finance/access.token "$env:APPDATA\KdzwyFinance\access.token"
```

Excel 的 VBA 工程安装需要用户允许“信任对 VBA 工程对象模型的访问”。脚本不修改注册表或自动降低宏安全设置；组织策略禁止时由管理员部署。安装完成后可关闭该工程访问选项，运行已安装宏仍按组织宏信任规则处理。

```powershell
.\excel\Install-Finance.ps1 -Template .\财务管理模板.xlsx -Output .\财务管理.xlsm
```

打开生成的 `.xlsm`，在控制台填公司编号及 `YYYY-MM`，点击“刷新财务数据”。公司编号见“公司列表”。宏成功后不自动保存，用户自行保存工作簿。

## 刷新行为

按钮 → 本地SSH转发 → Linux只读服务 → 账无忧已登录会话 → 校验账套/分页/金额 → Excel更新原始表 → 重新计算报表。

请求每次重新读取，不使用旧业务缓存冒充实时。多个接口不是数据库原子快照；查询前后校验账套身份、分页总数与重复ID。期间账务可能被其他用户修改时建议刷新后再核对，未保证同一笔数据在整个读取过程中完全不变。

接口全部返回并验证后才发送工作簿快照；Excel先检查全部工作表，再保存旧数据并批量替换，写入失败尝试回滚。预算、账龄输入不会写回账无忧。公司或月份已改变但未刷新时，统计公式不显示上次选择的数据。

## 实测接口

- GET `/jdy-fi/{dbId}/rpt/v1/profit`：本月、累计、月度趋势。
- GET `/jdy-fi/{dbId}/rpt/v1/balance`：资产负债表。
- GET `/jdy-fi/{dbId}/rpt/v1/cashflow`：原系统现金流报表。
- POST `/jdy-fi-rpt/{dbId}/v1/gl/balance-report`：科目余额。
- POST `/jdy-fi-rpt/{dbId}/v1/balance-item-report/query`：客户/供应商余额，排除合计和重复层级；补入未启用辅助核算的往来明细科目，再与总账核对。
- GET `/jdy-fi/{dbId}/gl/v1/voucher/list`：完整分页凭证及分录，读取后逐凭证校验借贷与合计。

此前验证的 `queryItemAccount` → `queryDetailItem` 可用于按核算项目下钻。首版批量刷新使用凭证明细和余额查询，避免对每一个客户/供应商逐一发起请求。

## 后续 Windows 验收

1. 完成一次安装，确认生成的 `.xlsm` 上有可点击按钮。
2. 点击刷新，确认公司、月份、更新时间与后台一致。
3. 输入预算和一条未核销单据，重复刷新后确认人工输入保留。
4. 停止 SSH 转发再点击刷新，确认提示失败且旧数据保留。
5. 切换另一个账套，确认未刷新前统计页留空，刷新后公司数据隔离。

这些是尚需在实际 Windows Excel 上执行的验收，不计入 Linux 自动测试通过数量。
