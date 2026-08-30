# 模板编辑说明

每个公司只有一套跨月份共享模板，位于 `templates/<template_company>/`。每个模板 JSON 自己保存分类规则和凭证分录；`index.json` 只说明目录布局，不再重复登记模板清单。

模板放在对应业务目录 `sales`、`purchase`、`bank` 或 `misc`。文件名统一使用：

```text
一级板块_票据类型_结算方式_业务类型_币种_template.json
```

例如：`销售_增值税发票_往来结算_销售商品收入_人民币_template.json`。

## 必填字段

- `id`：稳定且唯一的模板 ID。
- `name`：模板显示名，通常等于文件名去掉 `_template.json`。
- `enabled`：是否允许选择此模板。
- `documentBlock`、`documentType`、`settlementMethod`、`businessType`、`currency`：五段语义分类。
- `keywords`、`matchRules`：OCR 文本的确定性候选筛选规则。
- `summary`：凭证摘要规则。
- `entries`：本模板自己的动态分录数组。

程序会基于五段语义生成 `decisionCode`。模型只能在规则筛选后的候选中复制准确的 `templateId` 与 `templatePath`。

## 常用占位符

- `invoiceCode`：发票号码。
- `sales_map.amount`、`sales_map.taxAmount`、`sales_map.totalAmount`、`sales_map.date`：销售映射字段。
- `purchase_map.amount`、`purchase_map.taxAmount`、`purchase_map.totalAmount`、`purchase_map.date`、`purchase_map.supplierName`：采购映射字段。
- `source.businessType`、`source.customName`、`source.amount`、`source.totalAmount`：银行或杂项字段。

## 编辑原则

- 模板目录、`matchRules.sourceFolders` 和业务来源必须一致。
- 专用业务应补充明确的 `anyKeywords` 与 `excludeKeywords`；证据不足时保持禁用或阻断。
- 不在模板中写死客户、供应商等辅助对象 ID，必须从当前目标账套动态获取。
- 每个模板独立决定分录数量；科目必须存在且借贷金额必须平衡。
- 只有历史已入账样本或人工确认能证明分录结构时，才启用新模板。
- 正式上传前必须审查 `preupload_review.report.json`。
