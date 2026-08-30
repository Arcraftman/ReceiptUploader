填充示范

示范文件：

examples/receipt_filled_demo.json

示范发票码：

26112000002695439356

映射文件位于隔离工作区：

workspaces/account_1/company_17867515/2026-08/generated/maps/purchase/xlsx_pdf_map.json

当前 map 对应 PDF：

data/inbox/company_17867515_上海微誉信息技术有限公司/2026-08/input/purchase/dzfp_26112000002695439356_北京鸿昌盛泰商贸有限公司_20260818175636.pdf

填充步骤：

1. `receiptId` 使用 `<company_key>-<YYYY-MM>-<invoiceCode>`，`source` 使用 `sales`、`purchase`、`bank` 或 `misc`；本示例是 `purchase`。
2. 保留自动生成的 `invoiceCodes`，不要手工写 PDF 绝对路径。
3. 把 `voucher.date` 替换为当前会计月份内的真实凭证日期。
4. 把 `voucher.groupId` 替换为当前账套真实凭证字 ID。
5. 把 `voucher.summary` 和每条 `explanation` 替换为真实业务摘要。
6. 把 `voucher.userName` 替换为真实制单人；未审核凭证的 `checkerId` 保持为 `0`。
7. 把每条分录的 `accountId`、`accountNumber`、`accountName` 替换为真实科目。
8. 根据真实业务填写 `amount`、`dc`、`cur`、`rate`、`amountFor`，并保证借方合计等于贷方合计。
9. 将完整文件复制到当前 workspace 的 `generated/receipts/purchase/`，并删除 `draft=true`（示范文件没有 `draft` 字段）。
10. 先将对应任务配置的 `mode` 设为 `dry-run`，校验通过后才考虑 `confirm`。

示范中的“请替换”文本不能提交到真实接口。示范金额 100.00 仅用于展示结构，不代表真实业务金额。
