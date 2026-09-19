# 主要读取接口集成测试

本测试基于 [接口扫描报告](KDZwy_READ_API_REPORT.md) 的已验证请求，使用独立只读白名单，直接验证 HTTP 接口，不依赖前端页面启动、OCR、LLM 或上传流程。

## 本次示例

公司：上海微誉信息技术有限公司（`company_17867515`）。月份：`2026-07`。

34 个检查项覆盖 29 个主要查询用例、查询前后身份验证、凭证跨页一致性、凭证详情及电子附件链接。结果为 **33 项通过，1 项因无电子附件样本跳过**，没有失败。

- 科目树返回 226 个顶层节点；客户目录共 214 条，供应商目录共 1,623 条，各读取第一页 20 条。
- 当月凭证共 586 张。前两页各 20 张，ID 不重叠；所选详情有 3 条分录，凭证 ID、月份及借贷合计检查通过。
- 总账、科目余额、辅助核算余额、资产负债表、利润表、现金流量表、费用明细等读取成功。
- 数量账、固定资产卡片、工资表、职员、发票等部分用例返回空结果，代表请求和返回结构可用，尚未覆盖这些接口的非空业务数据。
- 前两页及所选详情未找到 `fileIds`，附件链接用例明确为 `skipped`，不能据此宣称附件接口已验证，也不推断全公司没有电子附件。

可查看 [实际结果表](read_api_tests/weiyu_2026-07/RESULTS.md) 和 [机器可读报告](read_api_tests/weiyu_2026-07/results.json)。这些文件是单次执行快照，后续业务变化可能影响数量。

## 执行方式

Linux：

```sh
commands/test_read_apis.sh company_17867515 2026-07
```

Windows：

```bat
commands\test_read_apis.bat company_17867515 2026-07
```

公司必须是 `runtime/registry/accountbooks.json` 中明确启用的账套 key，月份必须明确提供。此测试不要求初始化当月业务工作区，也不会自动创建或修改公司/月度配置。

报告默认写入 `runtime/read_api_tests/<company>/<month>/results.json` 与 `RESULTS.md`。可以使用 `--output DIRECTORY` 指定其他目录。

- 退出码 `0`：没有失败或阻断；仍可能存在明确跳过项。
- 退出码 `1`：至少一项失败/阻断，或使用 `--strict-skips` 且存在跳过项。
- 退出码 `2`：公司、月份或本地配置不合法。

若会话已过期，先刷新所选公司的会话，再重跑：

```sh
commands/login_companies.sh --accountbook-key company_17867515 --no-pause
commands/test_read_apis.sh company_17867515 2026-07
```

集成测试也已接入 Python `unittest`：

```sh
KDZWY_READ_TEST_COMPANY=company_17867515 \
KDZWY_READ_TEST_MONTH=2026-07 \
python3 -m unittest discover -s tests -p test_read_api_live.py -v
```

Windows 可先用 `set` 设置这两个环境变量，再运行同一条 Python 命令。可选环境变量 `KDZWY_READ_TEST_OUTPUT` 指定报告目录。

**没有同时设置公司和月份时，联网测试全部跳过，常规测试发现不会自动访问真实账号。** 离线保护测试可直接运行：

```sh
python3 -m unittest discover -s tests -p test_read_api_checks.py -v
```

## 检查内容与边界

- 业务状态：同时检查 HTTP 与 `status/code/errcode/errorcode/success/ok`，不把错误 JSON 或缺少状态的 HTTP 200 算成功。
- 身份：会话必须与注册表公司匹配；前后查询 `getSystemParams` 严格核对 `companyId` 和 `DBID`；出现真实 ID 冲突立即阻断后续请求。
- 只读：GET 也必须位于固定名单，POST 仅允许已审核的查询接口。拒绝保存、删除、其他账套路径、内嵌动作参数以及外部 URL；不跟随重定向。
- 结构：校验必要字段、数组/分组类型与分页元数据。空列表与无数据导致的依赖跳过分开记录。
- 凭证：检查月份、ID 唯一性、分页无重叠及总数稳定；详情检查 ID/月份、分录科目、方向、有限金额和借贷合计。
- 附件：只有远端样本提供实际 `fileIds` 时才查询最多 3 个预览链接，不猜 ID，不下载，也不记录签名地址。
- 数据保护：业务数据只在内存中校验，结果仅保存结构、行数、状态和检查结论，不保存凭证明细、金额、人员信息、Cookie、密码或 token。

当前部署的几个已验证约定：

1. 总账 `data.item` 是“分组 → 行对象”的二维数组；报告同时记录分组数和展开行数。
2. 数量金额总账 `data.rows=null` 只在 `data.size=0` 时作为合法空结果接受，其他情况仍失败。
3. 凭证详情和空工资统计可能返回 `dbId="0"` 占位。此值不等于实际账套 ID；仅这两个响应允许占位，账套仍由请求路径和前后身份查询锁定。非零且不匹配的 ID 始终失败。
4. 凭证列表需保留前端的筛选参数，不能只传 `page/pageSize/fromPeriod/toPeriod`。

测试清单位于 `tests/fixtures/read_api_cases.json`；它是独立、可审阅的请求和结构契约，不会自动执行扫描目录中的全部候选接口。修改用例时，还需通过独立 HTTP 白名单与离线保护测试。
