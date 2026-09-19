# 账无忧读取接口扫描报告

扫描日期：2026-09-18。目标：[vip4-kj.kdzwy.com](https://vip4-kj.kdzwy.com/)。

已通过当前配置账号登录并验证 18 个独立账套会话。读取接口实测使用已有“智轻云测试”账套，保存请求参数、返回字段和状态，不在报告中保存凭证明细、工资人员数据、Cookie、密码或令牌。

## 结果

| 项目 | 数量 |
|---|---:|
| 下载前端资源（HTML / JS，不含提取的内联脚本） | 300 |
| 静态发现的方法＋URL 模板组合 | 1,130 |
| GET 读取候选 | 372 |
| POST 查询候选，需逐项确认 | 45 |
| 动作接口或含义不明确、已排除 | 245 |
| 其他写入或方法/语义未知 | 468 |
| 实测业务成功的读取接口（方法＋路径＋m 动作去重） | 60 |
| 下载失败 / JavaScript 解析失败 | 0 / 0 |

“417 个读取候选”不是“417 个已验证只读接口”。实测和静态统计口径不同；同一路径可以包含多套查询参数，源码中的动态变量还可能形成不同模板，不能直接相加。

- [已验证接口表](api_inventory/VERIFIED_READS.md)：方法、路径、实际参数字段、返回结构。
- [已验证请求示例与返回结构 JSON](api_inventory/verified_reads.json)。
- [读取候选与源码证据 JSON](api_inventory/read_candidates.json)。
- [全部接口 CSV](api_inventory/all_interfaces.csv)：便于搜索、筛选和比较。
- [完整接口及证据 JSON](api_inventory/all_interfaces.json)：含写接口与不确定项，避免误用。
- [资源 URL、SHA-256 与大小](api_inventory/sources.json)，[统计摘要](api_inventory/summary.json)。

## 主要可读取数据

路径中的 `{dbId}` 必须来自当前会话，不能使用其他公司的 ID。

| 数据 | 接口示例 | 本次结果 |
|---|---|---|
| 当前公司、账期、权限、账套参数 | `GET /basedata/initParams?m=getSystemParams` | 登录阶段逐账套校验，查询阶段成功 |
| 科目树 | `GET /jdy-fi-bd/{dbId}/v1/account/` | 成功 |
| 科目分类 / 凭证字 / 币别 | `/jdy-fi/{dbId}/bs/v1/account-class`、`vch-group`、`currency` | 成功 |
| 客户、供应商等辅助核算 | `GET /jdy-fi/{dbId}/gl/v1/itemClass`、`item/page` | 成功；具体类别由远端返回 |
| 凭证列表 / 汇总 | `GET /jdy-fi/{dbId}/gl/v1/voucher/list`、`voucherTotal/list` | 成功 |
| 凭证详情 / 附件链接 | `GET /jdy-fi/{dbId}/gl/v1/voucher/{id}`、`att/v1/file/urls` | 静态发现，未构造未知业务 ID 实测 |
| 总账 | `POST /jdy-fi-rpt/{dbId}/v1/gl/general/query-total` | 成功 |
| 科目余额表 | `POST /jdy-fi-rpt/{dbId}/v1/gl/balance-report` | 成功 |
| 辅助核算余额表 | `POST /jdy-fi-rpt/{dbId}/v1/balance-item-report/query` | 成功 |
| 数量金额总账 | `POST /jdy-fi-rpt/{dbId}/v1/qtyTotalAccount/detail` | 成功 |
| 资产负债表 / 利润表 / 现金流量表 | `GET /jdy-fi/{dbId}/rpt/v1/balance`、`profit`、`cashflow` | 成功 |
| 费用明细 | `POST /jdy-fi-rpt/{dbId}/v1/cost-detail/list` | 成功 |
| 固定资产卡片 / 类别 | `POST /jdy-fi/{dbId}/fa/v1/card/list`、`GET .../fa/v1/type` | 成功 |
| 折旧明细 / 汇总 / 变动记录 | `GET .../fa/v1/report/depreciation-detail`、`depreciation-sum`、`.../fa/v1/change-record` | 成功 |
| 工资表 / 月度工资统计 | `POST /jdy-fi/{dbId}/pay/v1/sheet-list`、`sheet/statMonthPay` | 成功 |
| 发票列表 | `POST /jdy-fi/{dbId}/vat/v1/invoice-list` | 成功 |
| 税负、首页统计、出纳、库存、操作日志等 | 见完整目录中 `vat`、`index`、`ca`、`stock`、`log` 等路径 | 部分实测，其余保留候选或未知状态 |

成功表示 HTTP 成功且业务状态成功，允许返回空列表；并不表示测试账套拥有所有业务数据。字段是否必填、权限范围、日期边界与分页上限没有通过破坏性或穷举试验确定。

## 登录链路与接口约定

1. 从本地 `config/kdzwy.json` 读取启用账号，通过官方 `https://gj.kdzwy.com/` 页面登录。
2. 以实际管家域名读取 `/guanjia/acctflow/selfnode`，取得“服务管理”节点；分页查询 `/guanjia/acctflow/nodecustomer`，核对 `totalCount`。仅为已建账且拥有数据库 ID 的公司创建账套会话。
3. 调用 `/guanjia/customer/accounturl/before` 和 `/guanjia/customer/accounturl?companyId=...`，跟随官方账套 SSO 地址。
4. `POST /auth/exchangeToken` 完成认证，随后查询 `getSystemParams`，核对返回的 `companyId` 和 `DBID`，才保存会话。
5. 账套读取请求通常需要当前账套 Cookie 与 `app-token` 请求头。新接口主要分布在 `/jdy-fi/`、`/jdy-fi-bd/`、`/jdy-fi-rpt/`、`/jdy-fi-tp/`；旧接口使用 `/gl/`、`/bs/`、`/pay/` 等路径及 `m` 动作参数。

业务响应有 `status=200`、`code=200`、`errcode=0`、`errorcode=0` 等多种格式，HTTP 200 本身不代表成功。列表分页字段也不统一，以各接口的实测 JSON 为准。

## 扫描覆盖与限制

- 新版会计应用发布目录：`static_production_20260915152836`，主包 `main.606e9379.chunk.js`，入口和清单中的 125 个 JS 分块全部取得。
- 出纳 `/home/index.html` 的主包、vendor 和发布清单中的 48 个懒加载分块，以及会计主应用实际引用的旧版账簿 / 报表 / 设置页面与脚本，均纳入扫描。
- 使用 Acorn 解析 JavaScript AST，没有执行下载的源码；还原字符串拼接与 `.concat(...)`，保留无法求值的变量占位符、请求参数表达式及源码行列位置。
- 包装器跨模块传参、运行期配置、非当前版本的功能、权限隐藏模块和未被前端引用的服务端接口，可能无法恢复。这是当前可见部署前端的接口盘点，不能宣称服务端“所有接口”已穷尽。
- `GET /jdy-fi/{dbId}/gl/v1/itemClass/delete` 等 GET 写接口已排除。动态验证仅打开选定查询页面，POST 采用明确查询白名单；不自动重放扫描发现的 URL。
- 页面偏好参数 `saveUser`、导出任务、导入、重算、结账、审核等请求采取保守处理。一些旧版页面因此未完成查询；其源码候选仍保留在清单中，不冒充实测成功。
- 早期进入管家首页时，页面自身曾发出 `/guanjia/fusion/update/status` 和 `/guanjia/md` 状态/埋点请求。随后登录与查询验证加入请求拦截。本次未主动执行凭证上传、辅助核算创建、修改、删除、审核、结账或报税。

## 可重复集成测试

已增加独立白名单驱动的主要读取接口测试，以微誉 2026-07 为例验证数据、分页和凭证详情。运行方式及结果见 [READ_API_TESTS.md](READ_API_TESTS.md)。此测试与前述前端扫描快照分别保留。

## Linux 使用与复现

15 个 BAT 均有同名 `.sh`；安装与入口说明见 [commands/README.md](../commands/README.md)。Windows 原入口保留；当前工作区缺少它们引用的 PowerShell 文件，因此不能据此声称原 BAT 已恢复可用。

```sh
scripts/linux/start.sh
scripts/linux/start.sh discover
scripts/linux/start.sh login --accountbook-key company_23354453 --no-pause
```

静态扫描依赖 Python `requests`、Node.js 和 Acorn；页面验证另需 Playwright Chromium。Acorn 安装在忽略版本控制的运行目录：

```sh
npm install --prefix runtime/interface_scan/parser --no-audit --no-fund acorn@8.18.0
python3 scripts/maintenance/scan_read_interfaces.py \
  --session http_sessions/accounts/account_1/companies/company_23354453.accountbook.cookies.json \
  --output runtime/interface_scan/current \
  --acorn-module runtime/interface_scan/parser/node_modules/acorn \
  --page /home/index.html
```

账号 key 和公司 ID 是本次示例；其他环境应使用其注册表里的实际会话路径。静态扫描默认复用本地已有资源及其哈希；需要检查新发布版本时使用新的 `--output` 目录。`--reuse-assets` 完全跳过下载，仅重新解析既有快照。

```sh
python3 scripts/maintenance/probe_read_pages.py \
  --session http_sessions/accounts/account_1/companies/company_23354453.accountbook.cookies.json \
  --output runtime/interface_scan/verified
```

`--extended` 查询选定旧版账簿和出纳页面；`--only PATH` 限制在内置审核过的页面集合内。报告聚合入口为 `scripts/maintenance/write_interface_report.py`，接收 `--scan-dir`、多个 `--probe` JSON 以及 `--output`。

## 验证

- 15 个 `.sh` 的 Bash 语法与可执行权限通过。
- 新增 9 个单元测试通过，覆盖中文/空格参数、上传取消/EOF、失败退出码、公司分页、错账套拒存、会话权限，登录与后台写请求区分，以及 GET 写接口识别。
- 新增 Python 文件编译与 JavaScript 语法检查通过。
- Linux 公司发现已实际取得并验证 18 个账套；指定账套刷新和 `start.sh` 启动、进入菜单、退出均成功。
- 未运行凭证上传或整套 OCR/LLM 流水线；本次没有改变业务生成逻辑。
