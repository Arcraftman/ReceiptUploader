# 项目业务决策记忆

更新日期：2026-08-30

## 当前配置决策

- 公司配置文件必须命名为 `company_<company_id>_<真实公司名>.json`，内容使用 version 3。
- `company_key` 固定为 `company_<company_id>`，不再允许 `xinghai` 等历史别名。
- 公司配置只保存资料公司身份和一个跨月份共享的 `template_company`。
- 数据目录由公司配置文件名确定，不再维护独立 dataset 注册表。
- 每个公司、每个月只有一个 `project.json` v7；它是该月 dataset、目标账套、输入和四个业务精确运行配置的唯一来源。
- `sales`、`purchase`、`bank`、`misc` 每个都必须显式包含 `enabled`、`mode`、`analysis_stage`、`preload_items`；不再从 `defaults` 继承这四项。
- `sales`、`purchase`、`bank`、`misc` 四类目录固定自带，但每月执行开关互相独立且默认关闭。
- 同一公司不同月份的所有运行、报告、确认和状态重置命令都必须明确传入 `YYYY-MM`。
- 资料公司必须完整写入当月 `project.json.dataset`，并与公司配置身份一致。
- 目标账套必须完整写入当月 `project.json.target`，不允许从资料公司或命令历史推断。
- `month` 命令必须同时显式接收 dataset 公司、月份和 target 公司；同主体也不得省略 target。
- 公司发现只生成 `runtime/registry/accountbooks.json` 和会话，不生成未配置公司的占位 JSON。
- 新资料公司首次执行 `month` 时，才创建公司配置和独立模板副本。
- 运行期账套注册表 version 2 只保存身份、账号、启用状态和会话路径，不保存流水线覆盖。
- 全局流水线配置 version 2 只保存 OCR/LLM 并发和模型接口等技术参数。
- 默认模型为百炼 `qwen3.7-flash`，API Key 只读取 `DASHSCOPE_API_KEY`。
- 模板 JSON 是分类规则、历史证据和会计分录的唯一真相源；模板索引只描述扫描布局。
- 原始资料只读；所有生成物、日志和状态写入目标账套隔离工作区。

## 银行回单

- 当月 `project.json.sources.bank.banks` 是银行唯一配置源；不再生成或读取 `bank_split.json`。
- 当月 `project.json.sources.bank.exceptions` 是唯一的特殊对象名称数组。用户只写 Excel 对手方列出现的完整名称；不分客户、供应商、人名，不含 `handling/template_id/records/pdf_keywords`。所有命中名称统一隔离普通下游。
- `config/bank_exception.defaults.json` 是新公司、新月份的一次性初始化种子，目前只保存跨公司通用 TIPS 名称及无索引 PDF 的系统关键词规则；初始化后以本月 `project.json` 为唯一运行配置。全局默认不得覆盖或自动合并到已有月份，具体人名和京东等特殊对象只作为名称写入当月数组。
- 多银行数量不写死；每个 bank key 对应同名 `<bank_key>.pdf` 和 `<bank_key>.xlsx`，并同时包含 `bank_account_number`、`split` 与 `statement_columns`。
- 银行键名必须小写，原始 PDF 命名为 `<bank_key>.pdf`。
- 每个银行规则必须包含 `parts_per_page`、`filename_index_length`、`filename_index_prefix`；旧的银行键直接映射整数格式不再接受。
- 单张回单文件名优先使用交易流水号/交易流水/核心流水号，其次回单编号，最后使用独立字母数字索引；所有候选都必须符合该银行配置的长度和起始字母。起始字母大小写敏感，生成文件名保留识别文本中的原始大小写。
- `generated/maps` 只按实际业务创建：sales 不生成 purchase/xlsx map，purchase 不生成 sales map；bank 生成一套按 bank key 分组的 `bank_map.json`、报告和唯一的特殊对象清单 `bank_exceptions.json`，禁止重复的 per-bank map。
- bank 生命周期与 sales/purchase 对齐：OCR 阶段依次做全部裁剪、特殊对象物理分流、剩余 OCR 和剩余流水匹配；LLM 阶段只分析普通匹配并生成 `template_analysis.json`；人工复核后仅 `mode=prepare + analysis_stage=existing` 生成最终 receipt。不得在 OCR/match/LLM 阶段提前生成草稿。
- bank 专用入口只读取和校验当月 `project.json`，不增加银行、不改写开关、不覆盖列值。
- 每家银行的 `split` 必须恰好包含 `parts_per_page`、`filename_index_length`、`filename_index_prefix`。
- 每家银行的 `statement_columns` 必须恰好包含 `index_column`、`bank_debit_column`、`bank_credit_column`、`counterparty_name_column`；bank 未启用时可使用 `null`，启用后必须全部填写。
- `configCompany` 永远固定为月份项目中的 `dataset.company_name`。方向及辅助核算角色固定为：银行借方有有效金额 = 我方贷方/现金流出，指定对手方单元格是供应商；银行贷方有有效金额 = 我方借方/现金流入，指定对手方单元格是客户。OCR/LLM 不得覆盖这些字段。
- 每家银行必须配置目标账套中的固定 `bank_account_number`。模板选择上下文、固定提示词、模板渲染、已有分析复用和最终 receipt 必须使用同一科目号；银行模板必须恰好有一条名称包含“银行存款”的分录，运行时用配置科目替换模板历史样例中的银行科目。任何缺失或不一致都阻断。
- 模板科目以科目编号为准；目标账套中同一编号显示的科目名称或明细名称不同，不作为阻断条件。银行存款分录仍必须使用显式 `bank_account_number`。
- 现金流入（银行贷方列为有效金额）时，如果非金额侧的银行借方单元格完全由一串或多串 8–20 位数字组成，按原顺序保存为 `invoiceNumbers`，并直接替换 `explanation_body` 为以空格连接的这些数字；不得追加模板原 body，含任何普通文字时不得触发。
- 每张已匹配银行回单必须从 OCR 原文确定交易/记账日期。仅名称包含“银行存款”的唯一分录在基础摘要后追加一个空格和 `YYYY-MM-DD`；其他分录不追加日期。分析文件需保留逐分录摘要，旧分析缺少日期或摘要/body 不一致时不得进入 prepare+existing。
- 银行模板必须声明 `matchRules.flowDirections`，候选先按 bank map 的确定方向硬筛选；规则唯一时直接确定模板，不调用 Qwen。银行校验固定使用 `documentBlock=银行`、`amountSource=source`、source folder 和资金方向，不再套用发票 OCR 的 folder/map 元数据。
- 外币标记必须完整匹配，`USB` 不得因 `US$` 规则被误判。本公司内部转账固定保持 blocked。
- bank 的 `preload_items="once"/"auto"` 会按 bank map 的流入客户、流出供应商在目标账套创建缺少的辅助核算对象；这是显式远端写操作，`false` 时禁止创建并让缺失对象保持 blocked。
- 微誉历史凭证中公积金银行付款按公司和个人各 50% 结清；bank source 从总额确定性生成两个字段，分角差额由个人部分承接，禁止模型猜测。
- `bank_map.json` 只保存排除特殊对象后、唯一索引匹配且方向有效的普通记录，按 bank key 隔离；普通未匹配流水固定标记 `markerOnly=true`、`downstreamEligible=false`。配置命中的特殊对象统一进入 `bank_exceptions.json`，由 `exceptions` 命令查看；`unmatched` 只显示没有被 exception 接管的普通未匹配记录。
- exceptions 数组中的完整名称是权威分类，优先于借贷方向产生的供应商/客户初始分类；它们在普通 OCR 前连同 PDF 一起分流。未配置姓名继续使用“2–4 个纯中文字符且以常见单姓或复姓开头”的保守规则兜底跳过。
- 特殊对象的切割 PDF 原件不移动、不删除，另复制到 `generated/bank_exceptions/<counterparty>/`；`bank_exceptions.json` 同时保存原始和副本绝对路径、排除流水索引及排除 PDF 路径。无合格流水号的 TIPS 回单使用全局技术关键词识别，再仅允许用“记账日期 + 金额”唯一关联。
- `verify` 和提交前检查必须以当前普通 `bank_map` 为白名单；旧流程遗留或后来被分流的 receipt 即使已改成 `draft=false` 也按无效孤儿产物阻断，不得进入提交。
- 微誉的京东、TIPS 和指定人名只需作为名称列入 exceptions，统一不进入普通模板、LLM 或凭证生成；京东专用模板保留给未来单独特殊业务流程，不再由 exceptions 承载分摊配置。
- 原 PDF 保留不动，拆分结果写入工作区 `generated/bank_receipts/<bank_key>/`。
- 没有唯一有效命名索引或重复索引的切片进入 `bank_exception`；它们属于正常裁剪结果，统一写入银行 exception 清单并在普通 OCR 前排除，不得让银行预处理失败。
- prepare+existing 生成的最终 receipts 初始全部 `draft=true` 且不得覆盖用户修改；用户补齐后改为 false。verify 只能在此阶段之后执行，并列出剩余 draft 号码、receiptId 和路径；dry-run/真实上传前自动重复同一检查。真实银行上传仍需责任链复核后另行开放。

## 安全决策

- 动态科目、客户、供应商等 ID 只能从当前目标账套获取。
- `confirm` 只能从 `confirm_one.bat` 或 `confirm_all.bat` 进入，并要求二次确认。
- 跨主体运行必须由当月配置显式允许；跨主体真实上传还必须经过命令行安全门。
- 上传后必须回读凭证和附件；失败或结果不明确时立即停止。
- 状态文件只用于观察和恢复判断，不能自动授权或跳过真实上传。
- purchase、bank、misc 在没有完成业务验证前不得扩大真实上传范围。

完整操作步骤见 [USAGE.md](USAGE.md)，技术分层见 [ARCHITECTURE.md](ARCHITECTURE.md)。
