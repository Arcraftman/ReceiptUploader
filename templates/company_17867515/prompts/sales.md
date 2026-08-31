你正在连续分析同一个销售业务板块。以下规则优先级最高，不得违反：

1. 当前资料公司是“{{source_company}}”。sales目录下，该公司一定是seller（销售方），交易对方一定是buyer（购买方）。
2. 即使OCR将买卖方顺序识别颠倒，也必须按source规则纠正。
3. 程序已把候选限定为sales；候选只有一个时必须逐字复制decisionCode、templatePath和templateId，分类confidence为0.99。
4. 金额必须满足amountWithoutTax + taxAmount = totalAmountWithTax；金额或购销方字段失败可以blocked，但不得清空已经唯一的模板。
5. 不得臆造科目、辅助核算对象、金额或交易对方。
