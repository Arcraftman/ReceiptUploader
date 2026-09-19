# 主要读取接口集成测试

公司：上海微誉信息技术有限公司（company_17867515）；月份：2026-07。
执行时间（UTC）：2026-09-18T10:24:15.444325+00:00。

结果：passed=33，skipped=1。

只执行独立白名单内的读取请求；空列表表示接口可调用但未覆盖非空业务数据。跳过项不算通过。
业务数据仅在内存中校验，报告不保存明细、金额、登录凭据或附件地址。

| 测试 | 状态 | 返回行数 / 检查结果 |
|---|---|---|
| 查询前账套身份 | passed | 结构/状态检查通过 |
| 科目树 | passed | {"rows": 226} |
| 科目分类 | passed | {"rows": 5} |
| 凭证字 | passed | {"rows": 1} |
| 币别 | passed | {"rows": 2} |
| 自定义辅助核算类别 | passed | {"rows": 0} |
| 客户目录 | passed | {"rows": 20}；{"records": 214, "totalPage": 11, "page": 1} |
| 供应商目录 | passed | {"rows": 20}；{"records": 1623, "totalPage": 82, "page": 1} |
| 凭证列表 | passed | {"rows": 20}；{"records": 586, "totalPage": 30, "page": 1} |
| 凭证汇总 | passed | {"vchs": 17}；{"totalCount": 586} |
| 总账 | passed | {"item": 78}；{"records": 26} |
| 科目余额表 | passed | {"item": 27}；{"records": 1121} |
| 辅助核算余额表 | passed | {"data": 123} |
| 数量金额总账 | passed | {"rows": 0} |
| 资产负债表 | passed | {"rows": 32} |
| 利润表 | passed | {"rows": 32} |
| 现金流量表 | passed | 结构/状态检查通过 |
| 费用明细 | passed | {"data": 4} |
| 固定资产类别 | passed | {"rows": 6} |
| 固定资产卡片 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 折旧明细 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 折旧汇总 | passed | {"rows": 1}；{"records": 0, "totalPage": 0, "page": 1} |
| 资产变动记录 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 工资表 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 月度工资统计 | passed | {"items": 0}；返回 dbId=0 占位，账套由路径和前后身份检查锁定 |
| 部门目录 | passed | {"depts": 1} |
| 职员目录 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 发票列表 | passed | {"rows": 0}；{"records": 0, "totalPage": 0, "page": 1} |
| 税负数据 | passed | 结构/状态检查通过 |
| 出纳账户 | passed | {"rows": 5} |
| 凭证跨页一致性 | passed | 第 1 页 20 条，第 2 页 20 条，ID 不重叠 |
| 凭证详情与借贷一致性 | passed | 3 条分录；ID、月份与借贷合计一致；返回 dbId=0 占位，账套由路径和前后身份检查锁定 |
| 电子附件预览链接 | skipped | 前两页及所选详情无 fileIds；纸质附件张数不代表已上传电子附件 |
| 查询后账套身份 | passed | 结构/状态检查通过 |
