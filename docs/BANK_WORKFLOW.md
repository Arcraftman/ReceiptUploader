# 银行流程与模块职责

## 阶段入口
在月度project.json的sources.bank.stage选择阶段，仍通过现有start控制台的bank/run命令启动。

| stage | 使用的输入 | 工作与输出 | 不执行的工作 |
| --- | --- | --- | --- |
| ocr | 原PDF、流水XLSX | 裁剪、OCR、流水匹配；兼容原命令 | LLM、凭证生成、上传 |
| match | 已有OCR报告、切割异常报告、原流水XLSX | 重新生成bank_map及匹配报告 | 裁剪、OCR、LLM、上传 |
| llm | 已有bank_map、OCR文本、目标账套实时目录 | 分析并保存template_analysis.json；继续校验已有分析缓存 | 裁剪、OCR、重新匹配、凭证生成、上传 |
| prepare | 已有映射、分析及账套凭证默认值 | 生成receipt，进入PDF verify | 裁剪、OCR、LLM、上传 |
| verify | 已生成的待上传receipt | PDF绑定检查 | 重新生成、模型调用、上传 |
| send | 已有receipt及映射 | PDF/凭证校验、上传、防重和回读 | 裁剪、OCR、LLM、重新生成 |
| all | 原始输入 | 按顺序执行完整链路；仍受校验与上传确认约束 | 不绕过任何上传检查 |

修改原流水或备注后，先运行match再运行llm。缺少前置阶段产物时，后续阶段报错停止，不自动重跑前置阶段。
客户/供应商预加载只在分析阶段按现有配置处理；单独ocr/match/prepare不触发这项远端写入。
match与verify仅适用于bank，其他业务保持原有阶段集合。

## 代码职责

- application/bank_pipeline.py：阶段分派及统一提交入口。
- application/bank_preprocessing.py：裁剪、OCR和匹配阶段。
- application/bank_analysis.py：实时科目、辅助目录、缓存复核和模板分析。
- application/bank_preparation.py：既有分析验证、凭证生成和PDF门禁衔接。
- bank_receipt_layout.py：目录分类与查找，兼容旧平铺结构。
- bank_rules.py：备注、交易方向和人员科目等确定性规则。
- bank_receipt_verifier.py：待上传PDF门禁及凭证验证。
- upload_journal.py：上传幂等记录；与流程阶段状态分开保存。

## 产物职责

manual/automatic仅是业务分类，不表示是否上传。四类人工备注优先进入manual；人工补齐后仍可留在manual，两边通过相同上传入口。旧平铺receipt仍可读取，重复receiptId会阻塞上传。
阶段checkpoint记录处理进度，PDF未齐为waiting_for_pdf_binding；上传是否成功仍以上传日志与回读结果为准，不能通过改目录或阶段状态覆盖它。

本轮重构保持项目配置位置、工作区位置、receiptId及历史上传日志不变。不会自动运行真实LLM或上传。
