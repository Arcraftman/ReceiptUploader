# 项目业务决策记忆

更新日期：2026-08-30

## 当前配置决策

- 公司配置文件必须命名为 `company_<company_id>_<真实公司名>.json`，内容使用 version 3。
- `company_key` 固定为 `company_<company_id>`，不再允许 `xinghai` 等历史别名。
- 公司配置只保存资料公司身份和一个跨月份共享的 `template_company`。
- 数据目录由公司配置文件名确定，不再维护独立 dataset 注册表。
- 每个公司、每个月只有一个 `project.json` v5；它是该月目标账套、输入、模式、阶段和业务开关的唯一配置源。
- `sales`、`purchase`、`bank`、`misc` 四类目录固定自带，但每月执行开关互相独立且默认关闭。
- 同一公司不同月份的所有运行、报告、确认和状态重置命令都必须明确传入 `YYYY-MM`。
- 目标账套必须写入当月 `project.json.target`，不允许从资料公司或命令历史推断。
- 公司发现只生成 `runtime/registry/accountbooks.json` 和会话，不生成未配置公司的占位 JSON。
- 新资料公司首次执行 `month` 时，才创建公司配置和独立模板副本。
- 运行期账套注册表 version 2 只保存身份、账号、启用状态和会话路径，不保存流水线覆盖。
- 全局流水线配置 version 2 只保存 OCR/LLM 并发和模型接口等技术参数。
- 默认模型为百炼 `qwen3.7-flash`，API Key 只读取 `DASHSCOPE_API_KEY`。
- 模板 JSON 是分类规则、历史证据和会计分录的唯一真相源；模板索引只描述扫描布局。
- 原始资料只读；所有生成物、日志和状态写入目标账套隔离工作区。

## 银行回单

- 每个公司、每个月在 `input/bank/bank_split.json` 独立配置银行键名到每页回单数量的直接映射。
- 银行键名必须小写，原始 PDF 命名为 `<bank_key>.pdf`。
- 原 PDF 保留不动，拆分结果写入工作区 `generated/bank_receipts/<bank_key>/`。
- 未识别或重复的回单号进入 `unrecognized` 并阻断银行后续流程。

## 安全决策

- 动态科目、客户、供应商等 ID 只能从当前目标账套获取。
- `confirm` 只能从 `confirm_one.bat` 或 `confirm_all.bat` 进入，并要求二次确认。
- 跨主体运行必须由当月配置显式允许；跨主体真实上传还必须经过命令行安全门。
- 上传后必须回读凭证和附件；失败或结果不明确时立即停止。
- 状态文件只用于观察和恢复判断，不能自动授权或跳过真实上传。
- purchase、bank、misc 在没有完成业务验证前不得扩大真实上传范围。

完整操作步骤见 [USAGE.md](USAGE.md)，技术分层见 [ARCHITECTURE.md](ARCHITECTURE.md)。
