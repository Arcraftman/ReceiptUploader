填充示范

示范文件：

examples/receipt_filled_demo.json

示范发票码：

26112000002695439356

map 会从月份目录读取：

data/inbox/weiyu/7月/maps/xlsx_pdf_map.json

当前 map 对应 PDF：

data/inbox/weiyu/7月/purchase/dzfp_26112000002695439356_北京鸿昌盛泰商贸有限公司_20260818175636.pdf

填充步骤：

1. 保留自动生成的 invoiceCodes，不要手工写 PDF 绝对路径。
2. 把 voucher.date 替换为真实凭证日期。
3. 把 voucher.groupId 替换为当前账套真实凭证字 ID。
4. 把 voucher.summary 和每条 explanation 替换为真实业务摘要。
5. 把 voucher.userName 替换为真实制单人。
6. 把每条分录的 accountId、accountNumber、accountName 替换为真实科目。
7. 根据真实业务填写 amount、dc、cur、rate、amountFor。
8. 保证借方合计等于贷方合计。
9. 将完整文件复制到月份 receipts 下的一个待处理目录，并删除 draft=true（示范文件没有 draft 字段）。
10. 先将对应任务配置的 mode 设为 dry-run，校验通过后才考虑 confirm。

示范中的“请替换”文本不能提交到真实接口。示范金额 100.00 仅用于展示结构，不代表真实业务金额。
