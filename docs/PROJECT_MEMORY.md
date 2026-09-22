# 项目业务决策记忆

更新日期：2026-09-21

## 2026-09-22 银行日志修正

- 内部转账规则：remark必须完整匹配“内部转账”+数字科目编码（如内部转账100204），仅流水贷方金额为正且等于交易金额、方向为inflow时，固定选择内部转账模板。借project当前银行科目，贷备注指定科目；两行摘要“内部转账 YYYY-MM-DD”。科目需在目标账套唯一解析，不能借贷相同；不经LLM改选，不预加载客户/供应商。付款侧不适用此收款模板，未实现自动配对或自动跳过付款侧。

- 最新用户明确规则：流水remark去除首尾空白后精确等于“退款”，固定选择供应商退款模板，不调用LLM改判。分录借当前配置银行存款、贷220201应付账款，摘要统一“供应商付款退回”，银行分录末尾加交易日期。供应商身份按该备注确定并从目标辅助核算目录解析，不因流入被改为客户。收款方向/金额/供应商辅助核算仍验证；非收款方向报错，不倒置银行余额。

- 银行OCR增加开始/逐张完成进度及并行等待15秒心跳，显示并发、耗时、成功、失败、缓存复用；按实际完成的future报告，再按单据目录排序落盘。
- 取消名单后的阶段名称改为切割技术异常整理；预加载日志区分账套目录总数与本批解析计数，逐条显示未解析对象、索引、方向、实际原因；增加科目读取及LLM开始提示。
- 11:55批次实际OCR耗时249.693秒，2进程，196张有文本；14张切割技术异常+5条未匹配流水是两类记录，不代表19笔独立交易。未解析对象为公积金中心付款及徐芳收款，原因no_reliable_organization_role_evidence。未修改业务身份判断或运行中的任务。

## start 控制台 run 入口

- `run company_ID YYYY-MM` 调用与PowerShell run相同的调度链：准备工作区、验证登录、按project中启用来源和stage执行。run显式传入--allow-confirm以支持用户配置的send/all；不覆盖stage/source，不跳过草稿及凭证校验。
- 当前17867515 -> 23354445、2026-08的三个任务已通过--plan预检查，没有执行OCR或上传。控制台入口和完整流程许可4项测试通过。

## 最新目标切换规则

- `month DATASET_ID YYYY-MM TARGET_ID` 要求资料公司与目标账套 company_id 不同，同一个账套使用不同别名也拒绝，且拒绝发生在配置写入前。dataset参数支持数字ID、company_ID或原配置文件名。
- 新字段 `defaults.upload_to_dataset_enabled`：true为dataset -> dataset；false为dataset -> target。新配置默认false，用户生成后手动打开。重复month保留显式开关值。旧cross_company_upload_enabled不再接受，避免反向语义混用。
- 切换只改变有效上传账套，不改project.target，不复制资料目录，仍读取dataset当月input。每个业务和输出工作区使用有效目标。
- 当前2026-08配置仅迁移字段，仍保留原dataset=target=17867515；未擅自指定另一家公司。按新规则重新month时需要用户指定不同target。

## 最新银行规则：取消对象排除名单

- 用户要求删除所有按对象名称配置的 exceptions，依靠流水 remark 参与模板选择。此决定覆盖下文旧名单和个人姓名跳过规则。
- 当前月 project.json 已删除 sources.bank.exceptions，新月份不再生成或从默认文件注入该字段；旧配置字段兼容读取但不会传入运行设置。默认名单和PDF关键词为空。
- 主银行流程不再按名单或短人名排除OCR、匹配、分析、凭证生成。已有工资/费用报销明确备注规则仍有效；其他人名交易进入正常模板分析。
- 银行候选模板关键词读取回单正文与 statement remark；备注不等于无条件登账，仍需满足模板方向、金额、科目等规则。
- 无法命名回单、重复流水、金额异常等技术异常仍报告和隔离。历史生成文件保留，下次 all 会重建过滤结果及银行map；未执行实际登账。

## 最新修正 2026.09.21.7

- 实机发现 .4与.5/.6多个serve进程可同时监听18767（Windows HTTPServer地址复用），令牌认证随机落到旧服务。已清理旧服务，本机已安装.6已恢复健康，保留会话无需重新登录。
- FinanceHTTPServer 使用 SO_EXCLUSIVEADDRUSE 和禁止地址复用。service_lifecycle.py在健康失败后按用户SID、可执行路径、serve参数及发行目录标记检查并结束旧财务服务，跨版本启动锁防并发。记录启动日志，退出/超时清理新子进程。
- 18项回归及本机旧服务自动替换测试通过；新版包2026.09.21.7，工作簿内容沿用.6。

## 最新修正 2026.09.21.6

- 用户澄清：高新企业指标序号1必须保留内容，只是不填充。已恢复第7行原文与10%门槛，E7:G7为空；序号2、3移至8、9行。
- 登录识别明确的账号/密码错误即抛错，经 finally 关闭本次浏览器，退出锁上下文；冻结程序失败不再等待回车，2秒后以退出码1结束。重试测试确认锁可重新获取，不启动服务。
- 最新发行包 dist/KdzwyFinance-Windows-x64-2026.09.21.6.zip。

## 最新财务宏发行版 2026.09.21.5

- Windows 宏发行包共22张表，内置 Python/Chromium；最新路径 dist/KdzwyFinance-Windows-x64-2026.09.21.5.zip。
- 用户要求研发费用页删除左侧两表，仅保留居中明细。现为 B6:P15，C:N 对应12个月，O 合计，P 占比，明细行7—14按合计降序，O15总计，隐藏AR:BG计算区。
- 看板六图各一个 DeepSeek 文本框，AJ2:AJ7绑定显示、AI2:AI7存文本，AJ1/AK1绑定公司月份。刷新成功后自动生成，另有生成/配置按钮。只提交汇总数字；请求失败不破坏财务数据。
- interpretation.py 使用 DeepSeek JSON输出与已核算事实，校验6段文本并按模型+数据缓存。密钥优先环境变量，或当前用户 DPAPI加密文件；发行包不含密钥、登录会话或测试数据。
- 高新企业指标序号1不处理。序号2、3按参考文件的当年累计代理比值公式计算，并明确非正式三年/完整年度认定。正式口径还缺历史年度、高新收入分类和总收入确认，不得把研发收入直接宣称为认定高新收入。
- 实际 DeepSeek API 已测试；Excel COM 回放真实解读验证6文本框、切公司隐藏旧值、失败后财务数据保留、比值15%/72%和收入分档边界。测试数据只在 outputs/finance-ai-monitor。

## 当前配置决策

- `sales` 的业务范围只以当月 `input/sales` 下实际存在的 PDF 为准；`project.json.input` 不再配置 `income_cost_filename`，收入成本表不再参与发票筛选、匹配、数量核对、销售 map 或辅助核算预加载。每张有效命名的销售 PDF 直接进入精确 OCR，客户、日期、金额和税额均由 OCR 证据补入销售 map；OCR 不完整的单据进入异常。
- purchase 使用 `sources.purchase.usage_confirmation_enabled` 明确纳税人路径，默认 `true`。`true` 表示一般纳税人，必须使用 `用途确认信息.xlsx`；`false` 表示小规模纳税人，不要求该表，采购范围只按 `input/purchase` 实际 PDF，OCR 票面不含税额和税额保留为审计证据，但业务成本/费用按价税合计入账，`taxAmount=0` 且凭证不得生成 `22210101` 进项税分录。
- 所有业务来源 `sales`、`purchase`、`bank`、`misc` 的正式 OCR 一律使用项目可用的最高精度模式，不得因为单据数量、耗时、缓存或业务类型自动降级。银行裁剪时用于空白检测和稳定命名的快速识别只能作为预处理，不能替代正式 OCR，也不能直接作为 LLM 的最终输入。
- 税率必须优先使用 OCR 明文；明文缺失时，只能从 OCR 货币金额中以 `不含税金额 + 税额 = 价税合计` 反算，并且唯一命中标准税率、反向分币校验通过后才接受。反算结果必须保存金额证据和 `amount_equation` 方法；多税率、证据冲突或无法唯一证明时进入异常，禁止交给 LLM 猜测。
- `preload_items` 已从 `project.json` 删除，运行时固定为 `auto`。每次执行都必须依据实际生成的业务 map 核对当前登录账号、当前目标账套的远端 ItemClass；已有项不修改，只有缺失客户或供应商才创建。
- 公司配置文件必须命名为 `company_<company_id>_<真实公司名>.json`，内容使用 version 3。
- `company_key` 固定为 `company_<company_id>`，不再允许 `xinghai` 等历史别名。
- 公司配置只保存资料公司身份和一个跨月份共享的 `template_company`。
- 数据目录由公司配置文件名确定，不再维护独立 dataset 注册表。
- 每个公司、每个月只有一个 `project.json` v8；它是该月 dataset、目标账套、输入和四个业务精确运行配置的唯一来源。
- `project.json` 版本为 v8。`sales`、`purchase`、`bank`、`misc` 每个显式包含 `enabled` 和统一 `stage`；purchase 可另配 `usage_confirmation_enabled`。`stage` 只支持 `ocr`、`llm`、`prepare`、`send`、`all`。旧的 `mode`、`analysis_stage`、`preload_items` 不再兼容。
- `sales`、`purchase`、`bank`、`misc` 四类目录固定自带，但每月执行开关互相独立且默认关闭。
- 同一公司不同月份的所有运行、报告、确认和状态重置命令都必须明确传入 `YYYY-MM`。
- 资料公司必须完整写入当月 `project.json.dataset`，并与公司配置身份一致。
- 目标账套必须完整写入当月 `project.json.target`，不允许从资料公司或命令历史推断。
- 目标开关以本文件顶部“最新目标切换规则”为准；资料目录始终属于dataset，不创建target的第二份资料目录。
- `month` 命令必须同时显式接收 dataset 公司、月份和 target 公司；同主体也不得省略 target。
- 公司发现只生成 `runtime/registry/accountbooks.json` 和会话，不生成未配置公司的占位 JSON。
- 新资料公司首次执行 `month` 时，才创建公司配置和独立模板副本。
- 运行期账套注册表 version 2 只保存身份、账号、启用状态和会话路径，不保存流水线覆盖。
- 全局流水线配置 version 2 只保存 OCR/LLM 并发和模型接口等技术参数。
- 默认模型为百炼 `qwen3.7-flash`，API Key 只读取 `DASHSCOPE_API_KEY`。
- `stage=llm` 和 `stage=all` 必须对每个有模板候选的 OCR 产物真实调用 LLM；不得因只剩一个候选而本地伪装成 Qwen 分析。
- 模板候选为空或 LLM 分析失败时必须写入 `template_analysis.json`，状态为 `blocked/exception_pending`，然后继续下一张。
- 模板 JSON 是分类规则、历史证据和会计分录的唯一真相源；模板索引只描述扫描布局。
- 原始资料只读；所有生成物、日志和状态写入目标账套隔离工作区。
- 全流程日志采用结构化日志与控制台 transcript 双记录。调度日志写入 `runtime/logs`，每个业务的 OCR、LLM、receipt 生成、异常和上传输出写入对应隔离工作区的 `logs/<source>`，每次运行使用独立时间戳文件。

## 银行回单

- `sources.bank.enabled` 是银行业务总开关；`sources.bank.banks.<bank_key>.enabled` 是单家银行开关。运行时只把双重启用的银行带入裁剪、OCR、匹配、辅助核算、模板分析、receipt 和上传；禁用银行不要求当月 PDF/XLSX。
- 银行流水列配置已从单家银行主体抽离到 `sources.bank.statement_columns.<bank_key>`。它必须与 `banks` 使用相同 bank key，并精确包含流水号、银行借方、银行贷方、对手方名称、备注五列；启用银行五列均必填，禁用银行可为 `null`。
- 当月 `project.json.sources.bank.banks` 是银行唯一配置源；不再生成或读取 `bank_split.json`。
- 银行交易对象身份由现有进销项 Excel 名称列和目标账套客户/供应商目录共同判断；资金方向只决定借贷方向，不得用于创建客户或供应商。
- 当月 `project.json.sources.bank.exceptions` 是特殊对象名称的唯一配置；所有命中名称统一隔离普通下游。
- 京东重庆供应链和各种缴税业务统一进入 `bank_exceptions` 单独处理，不保留普通银行模板，不进入常规 LLM、receipt 和自动上传链。
- `config/bank_exception.defaults.json` 只保存跨公司通用的系统 PDF 关键词规则。
- 对手方证据不足或客户、供应商证据冲突时必须进入异常处理，不允许按银行流入、流出猜测身份。
- 多银行数量不写死；每个 bank key 对应同名 `<bank_key>.pdf` 和 `<bank_key>.xlsx`。银行主体包含 `enabled`、`bank_account_number`、`split`，列定义放在独立的 `sources.bank.statement_columns`。
- 银行键名必须小写，原始 PDF 命名为 `<bank_key>.pdf`。
- 每个银行规则必须包含 `parts_per_page`、`filename_index_length`、`filename_index_prefix`；旧的银行键直接映射整数格式不再接受。
- 单张回单文件名优先使用交易流水号/交易流水/核心流水号，其次回单编号，最后使用独立字母数字索引；所有候选都必须符合该银行配置的长度和起始字母。起始字母大小写敏感，生成文件名保留识别文本中的原始大小写。
- `generated/maps` 只按实际业务创建：sales 不生成 purchase/xlsx map，purchase 不生成 sales map；bank 生成一套按 bank key 分组的 `bank_map.json`、报告和唯一的特殊对象清单 `bank_exceptions.json`，禁止重复的 per-bank map。
- bank 生命周期与 sales/purchase 对齐：`ocr` 依次做全部裁剪、特殊对象物理分流、剩余 OCR 和剩余流水匹配；`llm` 只分析普通匹配并生成 `template_analysis.json`；`prepare` 生成待上传 receipt；`send` 正式上传；`all` 连续完成全部流程。
- bank 专用入口只读取和校验当月 `project.json`，不增加银行、不改写开关、不覆盖列值。
- 每家银行的 `split` 必须恰好包含 `parts_per_page`、`filename_index_length`、`filename_index_prefix`。
- 每家银行的独立列配置必须恰好包含 `index_column`、`bank_debit_column`、`bank_credit_column`、`counterparty_name_column`、`remark_column`；单家银行未启用时可使用 `null`，启用后必须全部填写。
- `configCompany` 永远固定为月份项目中的 `dataset.company_name`。银行借方有有效金额 = 我方贷方/现金流出，银行贷方有有效金额 = 我方借方/现金流入；方向是硬约束。按方向产生的客户/供应商只能作为初始提示，最终 `counterpartyRoles` 必须由当前目标账套的客户和供应商目录解析；同一名称允许同时具有客户和供应商身份。
- 每家银行必须配置目标账套中的固定 `bank_account_number`。模板选择上下文、固定提示词、模板渲染、已有分析复用和最终 receipt 必须使用同一科目号；银行模板必须恰好有一条名称包含“银行存款”的分录，运行时用配置科目替换模板历史样例中的银行科目。任何缺失或不一致都阻断。
- 模板科目以科目编号为准；目标账套中同一编号显示的科目名称或明细名称不同，不作为阻断条件。银行存款分录仍必须使用显式 `bank_account_number`。
- 现金流入（银行贷方列为有效金额）时，如果非金额侧的银行借方单元格完全由一串或多串 8–20 位数字组成，按原顺序保存为 `invoiceNumbers`，并直接替换 `explanation_body` 为以空格连接的这些数字；不得追加模板原 body，含任何普通文字时不得触发。
- 每张已匹配银行回单必须从 OCR 原文确定交易/记账日期。仅名称包含“银行存款”的唯一分录在基础摘要后追加一个空格和 `YYYY-MM-DD`；其他分录不追加日期。分析文件需保留逐分录摘要，旧分析缺少日期或摘要/body 不一致时不得进入 prepare+existing。
- 银行模板必须声明 `matchRules.flowDirections`，候选先按 bank map 的确定方向硬筛选，再按目标账套目录解析的 `counterpartyRoles` 筛选。每条普通银行记录都必须调用 Qwen 核对候选，即使候选唯一也不跳过。银行校验固定使用 `documentBlock=银行`、`amountSource=source`、source folder 和资金方向，不再套用发票 OCR 的 folder/map 元数据。
- 外币标记必须完整匹配，`USB` 不得因 `US$` 规则被误判。本公司内部转账固定保持 blocked。
- bank 每次运行固定自动按 bank map 核对目标账套客户和供应商目录，只创建远端缺失的辅助核算对象。
- 微誉历史凭证中公积金银行付款按公司和个人各 50% 结清；bank source 从总额确定性生成两个字段，分角差额由个人部分承接，禁止模型猜测。
- 供应商付款不强制要求 `invoiceNumbers`；现金流出且目标账套目录确认对方为供应商时，服务费、培训费、运费、通讯费和水电费等用途仍按借应付账款、贷银行存款结算。`invoiceNumbers` 只作为增强证据。
- 供应商向本公司退款时使用独立“供应商退款”模板，分录为借银行存款、贷应付账款；不得因为现金流入就强制使用收客户款。微誉已验证对象“上海方顺医疗器械有限公司”纳入该模板的精确交易对象证据。
- `bank_map.json` 只保存排除特殊对象后、唯一索引匹配且方向有效的普通记录，按 bank key 隔离；普通未匹配流水固定标记 `markerOnly=true`、`downstreamEligible=false`。配置命中的特殊对象统一进入 `bank_exceptions.json`，由 `exceptions` 命令查看；`unmatched` 只显示没有被 exception 接管的普通未匹配记录。
- exceptions 数组中的完整名称是权威分类，优先于借贷方向产生的供应商/客户初始分类；它们在普通 OCR 前连同 PDF 一起分流。未配置姓名继续使用“2–4 个纯中文字符且以常见单姓或复姓开头”的保守规则兜底跳过。
- 特殊对象的切割 PDF 原件不移动、不删除，另复制到 `generated/bank_exceptions/<counterparty>/`；`bank_exceptions.json` 同时保存原始和副本绝对路径、排除流水索引及排除 PDF 路径。无合格流水号的 TIPS 回单使用全局技术关键词识别，再仅允许用“记账日期 + 金额”唯一关联。
- `verify` 和提交前检查必须以当前普通 `bank_map` 为白名单；旧流程遗留或后来被分流的 receipt 即使已改成 `draft=false` 也按无效孤儿产物阻断，不得进入提交。
- 微誉的京东、TIPS 和指定人名只需作为名称列入 exceptions，统一不进入普通模板、LLM 或凭证生成；京东专用模板保留给未来单独特殊业务流程，不再由 exceptions 承载分摊配置。
- 原 PDF 保留不动，拆分结果写入工作区 `generated/bank_receipts/<bank_key>/`。
- 空白银行切片必须在裁剪阶段直接丢弃，不生成 PDF、不进入 `bank_exception`、不进入 OCR/LLM，仅在裁剪报告中累计 `blankSliceCount`。严格空白条件固定为：没有 PDF 文本、快速 OCR 没有文字且图像有效墨迹比例低于阈值。非空但没有唯一有效命名索引或索引重复的切片才进入 `bank_exception`，不得让银行预处理失败。
- `prepare` 生成结构完整、可提交但尚未上传的 receipt；用户复核后进入 `send`。`all` 生成可提交 receipt 并在全部固定校验通过后连续上传；任何异常仍会阻断或分流。

## 安全决策

- 动态科目、客户、供应商等 ID 只能从当前目标账套获取。
- `confirm` 只能从 `confirm_one.bat`、`confirm_all.bat` 或对应 Linux `.sh` 入口进入，并要求二次确认。
- 跨主体运行必须由当月配置显式允许；跨主体真实上传还必须经过命令行安全门。
- 上传后必须回读凭证和附件；失败或结果不明确时立即停止。
- 状态文件只用于观察和恢复判断，不能自动授权或跳过真实上传。
- purchase、bank、misc 在没有完成业务验证前不得扩大真实上传范围。

完整操作步骤见 [USAGE.md](USAGE.md)，技术分层见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## Linux 与接口扫描

- `commands` 的 16 个 BAT 均有同名 Linux `.sh`。Linux 登录、发现与安全菜单由 `scripts/commands/login_companies.py` 实现，不依赖未随仓库提供的 PowerShell 文件。
- 官方登录入口为 `https://gj.kdzwy.com/`；管家域名由实际登录结果取得。账套跳转地址从当前账号公司列表对应的 `customer/accounturl` 获取，不猜测公司 ID。
- 每个账套使用独立 Cookie jar，在保存会话前校验远端公司 ID 和 DBID。会话保存在 `http_sessions/accounts/<account>/companies/`，权限 `0600`，注册表位于 `runtime/registry/accountbooks.json`。
- 2026-09-18 已完成当前账号 18 个账套的登录验证和当前发布前端的读取接口扫描，结果见 [KDZwy_READ_API_REPORT.md](KDZwy_READ_API_REPORT.md)。扫描结果是部署版本快照，不是服务端全部接口或全部账号权限的保证。
- 接口清单区分静态候选、实测业务成功、动作/含义不明。GET 也可能删除数据，发现的接口不得自动批量重放。正式业务仍沿用原有上传和校验流程。

- 主要读取接口的固定集成测试入口为 `commands/test_read_apis.sh` / `.bat`，必须指定公司 key 和月份。`unittest` 联网用例默认跳过，仅在显式配置公司及月份环境变量时启用。
- 2026-09-18 以微誉 `company_17867515`、`2026-07` 实测，33 项通过，电子附件链接因采样无 `fileIds` 跳过；结果见 [READ_API_TESTS.md](READ_API_TESTS.md)。只读测试不生成或上传凭证。

## 2026-09-18 两家公司8月历史模板

- 用户要求暂搁接口探测，改按提供的2026年8月凭证分析常用做账范式；不要建立生僻模板。
- 千云 company_20139879 独立13模板（覆盖244/277张历史凭证），智轻云 company_21726397 独立17模板（覆盖118/171张）。配置已分别绑定同名模板目录，不复制微誉业务科目。
- 分析与未纳入清单：`docs/template_analysis/2026-08_两家公司模板分析.md`。千云导出全部未审核；智轻云导出填写审核人。覆盖率只是分录结构回放，不代表自动分类成功率。
- 两家应收/应付使用1122/2202及运行时客户/供应商辅助核算；导出下划线后缀不是应复制的固定科目号。千云城建税222108，智轻云222118；千云物业560219，智轻云水电560211。
- 智轻云项目收入500101和研发收入500102必须有明确归属依据，不能只根据软件服务费决定；研发结转保留负数红字。
- 公积金必须有当期公司/个人金额，不固定各半；复杂工资折旧按各公司当期分项表取数。千云8月没有工资计提完整样本，不新增推测模板。
- 历史纠错、重分类、资产处置、内部划转、混合报销、偶发硬件和费用转进项等未建普通模板。按原有规则，银行缴税仍走例外流程。
- 新增测试`tests/test_august_company_templates.py`回放362张历史凭证并测试路由/阻断；源文件未修改，没有上传。

## 2026-09-18 以1—8月历史重建（替代8月初版选型）

- 新增1—7月两份凭证，结合原8月重建：千云18模板、完整结构覆盖2084/2261张；智轻云25模板、覆盖832/1309张。总3570张逐张借贷平衡。
- 千云新增房租、运杂费、管理服务费、退客户款、工资社保公积金计提；智轻云新增商品销售及成本、销售差旅、办公、汽车费用、房租、供应商退款、社保公司个人分拆。
- 智轻云单科目社保不是默认，必须明确仅公司部分；普通社保需分拆金额，否则阻断。研发福利4月和6月科目归属不同，不自动推广。
- 不合并Excel按月份+凭证号归组；不得按每行重复凭证号新建凭证，也不得跨月合并同号凭证。
- `docs/template_analysis/2026-01_08_两家公司模板重建.md`为当前依据，8月报告留作历史。模板version 2.0，有按月历史证据。
- 新历史回放2916张，原8月362张回归仍保留。混合凭证、内部转账、纠错和生僻业务不为覆盖率而硬套模板。未改原Excel或上传。

- 用户已删除银行备注强制模板功能；`remark_column`保留为业务证据，银行主体仅包含enabled、bank_account_number、split。旧备注强制分析必须重新执行llm，不得复用。

## 2026-09-18 健壮性修复

- month初始化不再输出顶层cross_company_upload_enabled，仅保留defaults中的正式字段；新建公司按模板注册表default_base_template选择基础模板，不再硬编码weiyu旧目录。
- 银行金额必须有限、正数，transactionAmount与statementAmount一致。公积金/社保显式分项必须非负、分币精度且合计等于流水。显式金额优先；仅微誉允许沿用已有公积金各半规则，千云/智轻云缺明细阻断。
- 分析保存bankSourceAmounts快照。LLM复用前重新校验全部银行分析规则；旧分析或金额来源改变必须重跑，prepare同样拦截。
- 预加载客户/供应商不再按借贷金额方向创建角色，必须依据实际业务资料或当前账套目录；未知企业也不得自动当供应商。
- 微誉采购模板排除明确自用业务，防止办公费用被默认计库存；requests列入运行依赖以支持Linux登录。
- 已更新过期测试：公司模板路径、已验证银行金额、动态银行科目、单候选仍调用模型、缴税/京东例外隔离、附件先于凭证保存等。
- 新增Linux实际sh初始化→PDF拆分→模拟OCR→流水匹配→模拟LLM→prepare回归，包括跨账套月份和重复初始化保留配置。测试未使用真实线上写接口。


## 2026-09-19 员工工资与费用报销

用户确认：以 `counterparty_name_column` 识别人名，再读取 `remark_column`。
仅付款方向且备注包含“工资”或“费用报销”其中一种时，直接使用该公司的工资/报销模板，不调用模型。两种同时出现、收款或未命中备注仍按人员待处理；显式 exceptions 名单继续优先。
微誉、千云、智轻云均已设置：工资借记 221101，费用报销全额暂记 560106 销售费用—差旅费，贷记当前银行配置的 bank_account_number。金额、日期及目标科目正常校验；人员不预建为客户/供应商。
通过校验后沿用 `stage=all` / `send` 提交流程，无需逐张人工确认；上传后用户自行调整费用科目。`ocr` / `llm` 不上传。备注、人员、方向或银行科目变化时禁止复用旧分析。


## 2026-09-19 Windows Excel 财务工作簿

用户新增目标：联网寻找全面财务模板，最终在 Excel 内点击刷新按钮读取账无忧只读接口；明确最终客户端为 Windows 桌面 Microsoft Excel，后台仍在 Linux。
已比较 Vertex42、Smartsheet；采用自建中文工作簿和 Windows VBA 刷新按钮。实现 `finance_snapshot.py`、`scripts/finance/serve.py`、`commands/finance_server.sh/.bat`、`excel/FinanceRefresh.bas`、`excel/Install-Finance.ps1`。Windows 使用 SSH 本地转发访问 Linux loopback 服务，独立访问令牌不嵌入工作簿，金蝶会话只在后台。
17张表的样本位于 `outputs/finance_refresh_20260919/财务管理模板.xlsx`。财务读取使用独立只读白名单和 `config/finance_read_sources.json`。实测微誉2026-08：524张凭证、1443分录、201现金银行分录；获取1—8月利润趋势。往来去重合计/子行，并补入无辅助核算往来科目，与总账勾稽。
边界：账龄尚缺核销和到期日已验证接口，首版人工补充未核销单据，未分配余额单列；出纳为总账1001/1002口径；现金流字段期间含义保留原名称；Windows按钮安装/实际点击还未在Windows环境验收。不得宣称全自动账龄或一键刷新终端已完成验收。
详情见 `docs/finance/README.md`，HTTP实测 `docs/finance/live_smoke.json`。


## 2026-09-19 框架整理（保持功能）

用户暂停 Excel 后续工作，要求优化整个 Python 项目的框架和模块划分，保持现有功能。OCR 拆为 `ocr/` 下的 models、fields、engine、selector、rules、rendering、memory、service；`receipts_ocr.py` 保留兼容导出。银行与发票流程拆入 `application/`，通过冻结字段的 PipelineContext 明确传递根目录和运行输入，原 pipeline_runner 保留命令和配置入口。
只读白名单、身份校验与传输移入 `integrations/read_client.py`，财务功能不再导入测试模块；姓名识别和工资/报销分类移入纯规则 `bank_rules.py`，原导入继续兼容。配置、模板、产物格式、缓存版本、业务规则和提交顺序未改变。
新增包依赖 / 开发与 OCR extras、Ruff 基础检查、Python 3.10/3.13 离线 CI 配置及架构测试。Linux/Python3.13 本地208测试、3697历史分录子测试通过，34真实接口测试跳过；wheel构建和安装导入通过，远端CI与Windows尚未运行。没有真实上传。详见 `docs/ARCHITECTURE.md`。银行/发票流程内部仍较长，后续按阶段契约逐步细化，不引入复杂工作流框架。


## 2026-09-19 四项工程保障落地

用户要求补齐故障恢复、覆盖率、可复现/跨平台安装和类型契约。新增 UploadJournal：正常 CLI 在 runtime/processing/uploads 按账套+receiptId 存储内容哈希、已上传fileIds、保存意图、凭证ID和完成结果；使用内核互斥锁、fsync、原子替换。保存结果未知禁止重提；已知ID的回读/绑定故障允许原命令继续核验，不能再保存凭证。恢复绑定时不把声明attachments当绑定证据；要求usedAttachments，否则留待核对。并发竞争不移动原文件或写阻断台账。
补充保存超时/中断/磁盘失败/损坏日志/跨账套/重复执行等测试，修复非有限金额、整数尾零序列化及空会话JSON异常处理；正常会计规则不变。两份旧main式测试已纳入pytest。
新增覆盖率JSON/HTML和config/quality/coverage.json门槛、scripts/maintenance/check_project.py统一检查入口。严格mypy覆盖workflow、提交日志及结果、应用context/options、app/month配置、银行纯规则8个模块，其余模块逐步收紧。
uv.lock是版本及哈希来源，requirements.txt与requirements-dev.txt从锁导出。干净安装发现onnxruntime新版本无Python3.10 wheel，增加py310的<1.24约束。CI为Linux/Windows×Python3.10/3.13，包含原生sh/bat初始化与完整OCR推理任务；Windows尚未实际执行，不能宣称已验收。
本地Linux：干净Python3.10.21与3.13.15均342测试通过、3697历史子测试通过；默认35跳过（34真实接口+1单独运行的OCR冒烟）。真实OCR两个版本均单独1项通过；mypy/Ruff/覆盖率门槛/sdist与wheel构建通过。未调用真实上传接口。说明见docs/QUALITY_AND_RECOVERY.md。


## 2026-09-19 财务模板源码分发与提交

用户要求提交全部更改，并明确Windows拿到源码后如何得到xlsx。源码新增不含公司数据的excel/finance-template.xlsx，17张表和公式保留，无需运行依赖制作环境的Node脚本。原带实测数据样本仍只在outputs本地。Install-Finance.ps1现在有默认路径，无参数运行生成excel/finance.xlsm，不覆盖已有文件。
明确当前边界：只读采集、单月快照/年初至当月趋势和模板已实现；Windows宏与安装源码存在但尚未实机验收。账号密码表单登录、公司名选择、任意起止月份、本地组件一键部署尚未实现。生成xlsm仍需已登录会话、服务和令牌，不能宣称简化登录刷新已完成。文档docs/finance/README.md已更新。

## 2026-09-19 Unified cross-platform launchers

- Canonical launchers: scripts/linux/start.sh, scripts/win/start.bat, scripts/win/start.ps1. ASCII wrappers, English command UI, UTF-8 Python I/O; business data remains unchanged.
- Shared Python command_dispatch; installed CLI uses the same dispatch. Old option-based receipt calls and commands/* forwarders remain supported.
- Login no longer depends solely on fcntl; uses the shared Windows/POSIX lock and releases it before the interactive console.
- Excel installer moved to scripts/finance/install-excel.ps1; excel/Install-Finance.ps1 forwards with unchanged default workbook paths.
- See docs/COMMANDS.md. Windows native launchers are covered by CI tests but have not been executed on this Linux host.
- Validation: Linux Python 3.13 full quality check passed (367 tests, 37 skips, 3697 historical subtests; Ruff, mypy, coverage gates and build). Focused launcher/login/month tests also passed on Python 3.10 (33 passed, 2 Windows-only skips). No real login, upload or Excel COM action was performed during this refactor.

## Latest launcher cleanup

The user requested removal of the old command launchers. Removed the root
commands/ directory and scripts/commands/linux_command.py. Previous notes about
compatibility forwarders are superseded. User-facing entry points are now
scripts/linux/start.sh and scripts/win/start.bat or start.ps1. Internal Python
implementations in scripts/commands/ remain required by shared dispatch.
Updated active documentation and native/month integration tests to use only
the new launchers. The obsolete historical README manual was removed.

## Packaged command implementation

Moved all scripts/commands implementations into src/kdzwy_receipt_uploader/commands
and removed scripts/commands. Dispatch calls packaged functions directly; worker
processes use Python -m modules. project_runtime provides scoped workspace context
and worker environment propagation without a mutable global ROOT. Finance runtime
(service, export and snapshots) now lives in the package finance/ directory.
Runtime no longer requires a source scripts directory; configs/templates/sessions
remain external workspace resources. Earlier source-checkout-only notes are superseded.
Removed the stale scripts/windows PowerShell ignore rule.

Validation: Python 3.13 full quality checks passed: 371 tests, 37 skips, 3697 historical subtests; Ruff, mypy, coverage gates and wheel/sdist build passed. Python 3.10 focused package/runtime/month/finance tests: 37 passed, 2 Windows-only skips. Installed the built wheel into a fresh temporary virtual environment; from an unrelated directory and a workspace with no scripts/, verified CLI help/status/login help/finance help, real offline v8 month initialization (including its worker), and pipeline/upload worker help. No real login, upload or Excel COM invocation occurred.

## Portable XLSX generation

Implemented `finance build-template [--output FILE] [--overwrite]` in the shared
CLI. Uses openpyxl and the packaged finance/template_layout.json resource; does
not require an existing XLSX, Node.js, account sessions or network. Default output
is WORKSPACE/excel/finance-template.xlsx; existing files require explicit overwrite.
17 sheets and all 1769 formulas match the prior template; updated the workbook's
installer path. Excel recalculates formulas on open. Removed the obsolete Node
builder and config/finance_template_blank.json. Refreshed the distributed XLSX.

Validation: 376 tests passed, 37 skipped, 3697 historical subtests; static/type checks, coverage gates and build passed. Python 3.10 builder/distribution checks: 7 passed. The installed wheel generated the 17-sheet workbook in an empty temporary workspace without Node or an existing workbook. Artifact verification found no formula errors, checked all sheet previews, and verified budget variance, aging/overdue amounts and stale-period suppression. Windows native Excel has not been exercised on this Linux host.

The former launcher commands `build-template` and `finance build-template` were
removed at the user's request. Template generation now has one user-facing
entry: enter `finance` at the interactive `kdzwy>` setup console. It rebuilds
the default workbook with overwrite enabled. The packaged builder remains an
internal implementation and test boundary.

## 2026-09-20 Annual finance report generation

Fixed the mismatch between the VBA refresh contract and the portable workbook
generator. The generator now creates an eighteenth, data-free worksheet named
`2026年利润和负债`, which is the exact sheet expected by `FinanceRefresh.bas`.
It includes the fixed month columns currently supported by the VBA code (January
through September), quarterly and year-to-date formulas, expense aggregation,
gross margin, debt ratio and income-category sections. Refresh source cells start
blank; no company identifiers, account data, sessions or tokens are embedded.

The tracked binary `excel/finance-template.xlsx` had already been removed from
the repository, so active documentation now tells users to generate it through
the single `kdzwy> finance` entry before running the Excel installer. Builder,
distribution and snapshot regression tests use the generated workbook as their
source of truth. Windows text reads now explicitly use UTF-8. Focused validation:
13 tests passed; the generated 18-sheet workbook rendered successfully and its
formula scan found no spreadsheet errors. Native Excel/VBA refresh still requires
Windows acceptance testing.

## 2026-09-20 Local XLSM login workflow

The Windows installer now places two controls on the workbook console: `登录账无忧`
and `刷新财务数据`. The login macro opens the repository-local PowerShell helper,
which runs the authorized headed login, starts the loopback-only finance service in
a hidden background process, and copies the generated service token to the current
Windows user's AppData directory. Credentials and Kingdee session data remain outside
the workbook. Refresh resolves the AppData token first and the repository runtime token
second; when neither exists, it reports that login/service startup is required instead
of exposing the raw VBA `路径未找到` error. This is a local checkout workflow, not a
standalone single-file XLSM distribution.

## 2026-09-20 Complete annual report history

Live diagnosis for `company_17867515` / `2026-08` confirmed that the raw snapshot
was complete (1,443 voucher entries, 201 cash/bank entries, 1,008 counterparty rows
and 256 monthly-profit rows). The incomplete cells were caused by the annual-report
macro: profit was populated from January through the selected month, while balance
sheet items and revenue-account categories were written only for the selected month.

The snapshot now returns a hidden managed sheet named `年度分析数据` containing
monthly profit, balance-sheet and subject-credit values from January through the
selected month. The macro reads that sheet and fills every supported month for all
three sections. The generated workbook therefore has 19 sheets, with the annual
report still last and the transport sheet hidden. Quarterly and YTD calculations
remain formula-driven in the visible report.

The transport protocol was raised to schema 2 so an old running service cannot be
silently consumed by the new workbook. A live read-only refresh for 微誉 2026-08
returned 1,543 annual transport rows and populated January through August for every
available mapped profit, balance and revenue item. The workbook saved with no formula
errors. R&D expense remained blank because the source report returned no value;
`500102` and `500103` remained blank because those accounts do not exist in this
company's chart of accounts. The existing `500101 国内业务收入` populated all eight
months. No missing source amounts were replaced with invented zeroes.

## 2026-09-20 Windows macro release (2026.09.20.3)

User clarified that distribution must retain the XLSM macro workflow and must not
require the recipient to install Python. Rebuilt the frozen KdzwyFinance component
with the current schema-2 service and bundled Chromium. Distribution is now
`dist/KdzwyFinance-Windows-x64-2026.09.20.3.zip`, with a blank workbook in
`payload/excel/Finance.xlsm`, `app/` runtime, and public read-source config.
Install.cmd copies the complete payload and creates an Excel shortcut.

The workbook detects app/KdzwyFinance.exe and invokes its login command, which asks
for credentials in a console (password hidden), completes browser login, discovers
accountbooks and starts the loopback service on port 18767. Passwords are not saved
to config files. The macro uses this installation's runtime token; source checkout
mode keeps its existing 18765 workflow. Runtime/browser self-check, installer hash
verification, installed service schema-2 health and blank-workbook/package checks
passed. A fresh real-account login on another user's PC has not been tested.
Income category mapping remains unresolved; the release does not invent values.

## 2026-09-21 Financial dashboard macro release

Added exactly one visible sheet `财务分析看板`, after 控制台, preserving existing sheet order and the last annual report. `finance/dashboard.py` generates six native Excel charts, six KPI values and monthly detail from the existing 月度趋势 snapshot. Selection 2026-08 displays January–August using sheet-scoped dynamic chart ranges. Expense totals exclude duplicate R&D subitems; net profit comes from the source report. Missing values remain blank, incomplete YTD KPIs remain blank, and chart-only hidden NA helpers prevent false zero points. No new service endpoint or schema change.

Desktop Excel synthetic checks verified expected YTD revenue 360000, cost 216000, expenses 11200, net profit 68000, weighted gross margin 40%, negative profit, missing source expense, zero revenue, stale company and chart lengths 1/3/8. PDF previews inspected. The reusable destructive-to-test-copy-only verifier is scripts/finance/verify-dashboard.ps1.

Latest standalone macro release: dist/KdzwyFinance-Windows-x64-2026.09.21.1.zip, blank 20-sheet Finance.xlsm with login/refresh buttons, same schema-2 frozen runtime/browser; recipient does not need Python. Existing 2026 Jan–Sep macro validation remains. No new company login/data fetch performed for this dashboard change.

## 2026-09-21 Login progress / session detection hotfix (2026.09.21.2)

User reported browser appeared logged in while console remained at the opening-browser message. Exact live cause not yet confirmed. Replaced fixed success-URL wait with bounded authenticated `/guanjia/user/info` checks on observed official browser origins, including popup pages. Rejects expired, empty and failed login envelopes. Console now reports browser/page/session stages and per-company connection progress, followed by local service startup. Login still requires user-completed verification and never treats a URL alone as successful authentication. Workbook/dashboard unchanged. Release 2026.09.21.2 rebuilds the frozen runtime. Login tests: 19 passed, 1 skipped; fresh real-account verification remains user-operated.

## 2026-09-21 Dashboard spacing and 2026研发费用 (2026.09.21.3)

User supplied 智轻云财务数据统计报告 - 8月(1).xlsx from their WeChat documents folder and requested replication of 2026研发费用. Added one visible sheet, retaining left classification grid, right details/totals/share table, lower summary and pie position. Right table sorts whole rows descending by signed total, stable ties and missing last. Formula summary replaces the original private pivot cache. Blank visual resource rd_template.xlsx is packaged via pyproject package-data; extraction script removes all numeric business amounts and chart caches.

Reference detail confirmed 43010101–08 (travel, transport, salary, housing fund, social insurance, software, welfare, depreciation) and monthly debit including red reversals. Snapshot adds subject_debit_leaf records from existing monthly subject-balance responses, excluding parent duplication. Code and terminal name must both match. Never net out month-end credit transfers. Reference layout intentionally remains Jan–Aug, gated by selected month, and explains that scope. New/mismatched account mappings are not invented.

Dashboard now has six per-chart explanations, wider horizontal gaps and vertical sections; detail starts row83. Desktop Excel verified reference's 64 monthly cells and total 757024.17, full-row descending sort and shares, stale company/period gating; additional tie, negative, missing sort cases included in verify-rd-report.ps1. QA files with reference amounts remain only in outputs/finance-dashboard-rd; distributed workbook is blank. Latest release dist/KdzwyFinance-Windows-x64-2026.09.21.3.zip has21 sheets, refreshed runtime and prior login fix. No fresh real-account login/API refresh has been performed for this change.

## 2026-09-21 Full twelve-month annual templates (2026.09.21.4)

Expanded 2026年利润和负债 to Jan–Dec, Q1–Q4 and YTD. Month amount columns are C,E,G,K,M,O,S,U,W,AA,AC,AE; Q4 is AG and YTD AI. Internal identity moved from AC to AK, backup/rollback ranges expanded. Refresh macro no longer rejects Oct–Dec and always clears all 12 monthly input columns before filling Jan through selection, so December -> March cannot retain future values. Annual transport capacity increased to 20000 rows, matching R&D formulas; other raw sheets retain their prior limits.

Expanded all three 2026研发费用 grids to 12 months: left B:M, sorted right P:AA with labels O, total AB, share AC; helpers AK:AN; chart moved right. Source reference max-column width span clamped to Excel's 16384-column limit. The year's scope remains 2026; detail sheets retain selected-month semantics.

Desktop Excel verify-twelve-months.ps1 on a test-only XLSM exercised all 12 selections plus December -> March with production update routine. Verified monthly values, Q4 flow sum, December balance for Q4, YTD, R&D ordering and dashboard chart point count. Injected test wrapper exists only in outputs test copy, never release. Snapshot tests also exercise full 12-month data collection. Latest package dist/KdzwyFinance-Windows-x64-2026.09.21.4.zip retains blank 21-sheet workbook and prior frozen runtime/login fix.

### 2026-09-22 内部转账匹配限制
- 内部转账记录仅在 remark 精确符合“内部转账+动态数字科目编码”，且银行贷方金额为正、方向唯一为流入时参与匹配。
- 内部转账前缀备注不合规、借方出款、金额无效或双向金额，以及交易方等于本公司但缺少合规备注的记录，均跳过匹配；普通收付款不受此限制。
- 同索引回单同步跳过，不计入未匹配异常，不进入后续分析和凭证生成。bank_map.report.json 保留 skippedInternalTransferStatements/Receipts 及原因，控制台显示跳过数量。
- 未修改原始流水，也未执行实际上传；重新运行匹配后生效。

### 2026-09-22 手续费备注直接选模
- remark 去除首尾空格后等于“手续费”、流水为付款时，直接选择银行手续费模板，不请求 LLM，也不要求额外“应付账款/发票款”关键词。
- counterpart 为供应商（用户确认招商银行股份有限公司上海自贸试验区分行为供应商）；预加载按供应商类别 5 解析，供应商辅助核算 ID 来自目标账套目录，不固定截图中的 646。
- 借220201应付账款、贷本条流水配置的银行科目；摘要银行手续费，银行分录追加交易日期。最终校验拒绝旧备注、错误方向或错误分录。
- 原始XLSX未改，未执行实际上传。手续费、退款、内部转账、预加载和最终校验相关22项测试通过。

### 2026-09-22 remark 统一包含匹配（覆盖此前精确匹配要求）
- 退款、手续费使用关键词包含匹配；工资、费用报销及普通模板关键词已使用包含匹配。
- 内部转账允许前后说明文字和关键词后空格，提取其后动态数字科目编码，必须唯一；仍要求银行贷方正金额且唯一流入，否则匹配阶段跳过。
- 一条备注命中多个直接选模业务规则时报告歧义，不任意选择模板。缓存仍比较原始备注防止沿用修改前的分析。
- 原始工作簿未修改，未执行实际上传。

### 2026-09-22 银行PDF可选与上传后未上传PDF汇总
- 用户要求重新生成；本次获得明确授权将需重分析的交易信息发送至配置的 DashScope/Qwen，使用 mode=prepare、analysis_stage=all，不进行凭证上传。
- 银行主流程启用 allow_without_pdf：流水无对应普通PDF也可进入bank_map，采用原始Excel的唯一日期、金额、交易方和remark生成标明 bank-statement 来源的分析证据；不伪造PDF。日期/金额/模板/科目校验仍保留。
- 支持整家银行未提供PDF（split allow_missing_pdf）；仅影响附件要求，不放宽业务规则。生成时实际无PDF则 attachments=0、attachmentFiles=[]。
- 上传并回查附件绑定成功后记录SHA256到receipt检查点及独立 bank_attachment_uploads.jsonl，避免重新生成receipt丢失附件成功依据。
- 主流程上传子进程结束（包括部分失败）后，对 generated/bank_receipts 全部PDF进行内容核对，将尚无成功附件绑定证据的PDF复制到 generated/bank_exception/pdf，并写 bank_exception.json 和 bank_exception.txt。原切割技术异常目录 bank_exceptions 保留，新的汇总覆盖所有未确认上传PDF，包括跳过和失败记录。
- 本次重生成：201流水，199进入分析（其中5条无PDF），2内部转账因备注/方向条件跳过；193凭证生成并通过本地verify，6分析blocked，未上传。
- 6条：上海银行7.20/13.50手续费备注仍货款；18903.64社保、1883.57个税、71049.47增值税未命中模板；18880退款备注仍货款。详细位置/原因见 generated/maps/bank/regeneration_blocked.json。
- 45项相关回归测试通过。不能把匹配成功视为全部模板分析或实际上传成功。

### 2026-09-22 内部转账跳过数量重新核对
- 用户指出应只跳过1条。首次读取上海银行testReport!L105仍为货款；检查期间用户文件已保存为内部转账100204，助手未改写XLSX。
- 使用最新文件重新prepare生成：201条流水，200条进入分析，仅招商银行C0347I6000DYL3Z转出200000跳过；上海银行V026081801060586转入200000生成借100201、贷100204。
- 本次195张凭证生成并通过verify，5条分析blocked（两笔手续费、三笔税费）。未上传凭证，regeneration_blocked.json已同步更新。

### 2026-09-22 恢复 remark_exception 单独处理
- sources.bank.remark_exception 为用户可编辑字符串数组，当前为增值税缴税、社保缴税、公积金、个税缴税。包含匹配时忽略空白；不恢复旧的交易方exceptions名单。
- 当前project.json、当前运行配置和月配置初始化/校验/schema已同步支持。新月份默认带这四项，可自行调整。
- 匹配阶段优先跳过命中备注的流水及同索引PDF，不进入bank_map、辅助核算、选模、凭证或上传；报告保留remarkExceptionStatements/Receipts和计数。上传后这些未绑定PDF仍进入bank_exception汇总。
- 之前生成且命中备注例外的receipt移入generated/receipts/remark_exception_receipts时间戳目录，避免旧凭证留在bank待上传目录。
- 实际重新匹配：201流水，4备注例外，1内部转账跳过，196进入分析；194已有凭证通过verify，2手续费未通过（7.20/13.50）。公积金7140对应旧receipt已移出待上传目录。未上传，未发起新模型调用。

### 2026-09-22 remark 包含“跳过”固定排除规则
- remark 包含“跳过”（如手续费，跳过单独处理）即按remark_exception排除，优先于手续费、退款、内部转账等业务规则。即使自定义remark_exception为空仍生效。
- 默认remark_exception及当前月份配置加入“跳过”。与其他备注例外共用报告、旧receipt移出待上传目录和未上传PDF汇总流程。
- 用户保存原始Excel后，下次bank运行自动应用。此次只更新规则和配置，未改原始Excel、未重新上传。

### 2026-09-22 最新重跑全部生成通过
- 读取用户最新保存的流水再次prepare运行：201流水，备注例外4、内部转账跳过1，196进入分析，196凭证生成，blocked=0。
- 196张通过本地提交前verify（无draft/invalid/orphan）；其中2张零PDF附件。徐芳仍有预加载目录证据提示，但对应分析与最终凭证校验通过。
- regeneration_blocked.json已清空。本次未上传凭证，上传后PDF异常汇总尚未执行。

### 2026-09-22 批量上传与project生成器候选项
- 用户明确授权196张银行凭证批量上传至company_23354445微誉测试；本次实际上传正在进行，不再只是prepare。
- 第11张久程信息科技(上海)有限公司保存为记-309、ID57439113922448后，因接口名称全角括号触发辅助回读误报。修复为仅括号全/半角等价比较，辅助ID及科目仍严格一致；38项辅助/输入校验测试、39项上传恢复测试通过。续跑复用原voucherId，无重复保存。
- project生成器增加完整来源配置字段展开；并发值继承技术默认值，已有来源覆盖值保留。默认来源仍关闭，目标开关仍false。
- month同时生成project.options.md，按schema列出全部字段路径、可选值/格式、用途、defaults覆盖关系；模型接口配置仍在pipeline.defaults.json。当前月份说明已生成。
- 17项生成器/配置集成测试通过。上传最终数量待完成后更新。

### 2026-09-22 本次银行批量上传最终完成
- 目标company_23354445微誉测试，2026-08；196张全部submitted_and_verified，196个唯一voucherId，凭证号连续记-299至记-494。
- 194张PDF附件uploaded_linked_and_verified，2张零附件not_requested。第11张通过原已保存ID恢复，没有重复创建。
- 上传后bank_exception共16份PDF：14份切割技术异常、上海银行公积金V026081301192364、招商内部转账转出C0347I6000DYL3Z。TXT/JSON清单及PDF副本位于generated/bank_exception。
- 最终证明摘要generated/maps/bank/upload_complete.report.json。此次批量上传和project生成器候选项说明两项任务均完成。


## 2026-09-22 银行PDF verify与人员科目动态模板（覆盖旧规则）
- 默认remark_exception仅保留“跳过”，移除增值税缴税、社保缴税、公积金、个税缴税四项；项目中的其他人工排除配置保留。
- 银行前期允许无PDF；prepare/all生成后进入verify，逐张检查尚未uploaded的receipt的voucher.attachmentFiles[].path非空且PDF存在。缺少绑定返回4并记录waiting_for_pdf_binding；人工绑定后verify通过才允许提交。verify不改receipt，不跑OCR/LLM、不上传；send直接检查已有产物。原有金额/分录验证仍在提交前执行。
- 重新生成保留已有attachmentFiles；银行上传时附件数量从实际绑定列表计算。
- 上海微誉报销人员专用模板四条分录：借560203，贷人员2241明细，借同人员明细，贷当前银行。560203沿用用户截图，摘要以姓名开头，仅银行行末加日期。
- 人员2241科目不固定截图编号：从目标账套实时科目树按人员名精确匹配唯一叶子，支持二级、三级及更深层级，排除父科目；无候选/多候选阻塞。动态填入编号和ID，不使用itemClass；缓存分析对实时科目证据复核。马李不会模糊匹配带2022钉钉后缀的旧科目。仅本公司应用此四分录规则。
- 本轮仅改代码、配置、模板并进行离线测试，未删除远端凭证、未重新上传，旧上传日志保留。用户计划自行删除旧银行凭证后再建立新批次，不可沿用旧已上传状态直接宣称重跑成功。


### 2026-09-22 银行receipt目录语义分类
- 银行生成根目录下增加manual与automatic，两者均纳入PDF verify、凭证校验、上传扫描、未上传PDF报告和上传状态重置工具。
- 新生成的有效凭证缺少PDF时放manual，有PDF时放automatic；人工补全后仍保留所在目录，不以目录名决定是否上传。旧平铺目录仍兼容；同一key在多个目录存在时阻塞生成，重复receiptId阻塞上传。
- 分析未通过的记录继续进入blocked报告，不凭空填入分录或视为可上传receipt。
- 当前company_17867515到company_23354445的2026-08银行196份已移动：manual 2份、automatic 194份；receipt.json逐个SHA256核对未变，原上传标记和幂等日志保留。未执行远端上传或删除。


### 银行manual业务分类修正
备注忽略空白后包含增值税缴税、社保缴税、公积金、个税缴税时，生成凭证必须归入manual，优先于PDF是否存在；这不是remark_exception，不排除最终上传。已有automatic凭证重新生成/复用时迁入manual，保留文件内容及附件。当前旧映射尚将原XLSX这四条列为排除记录，当前194份automatic中没有这四条；需后续重新生成银行映射与分析，不能把旧排除区产物直接视为本次有效凭证。本轮未重跑或上传。


## 2026-09-22 最新工作状态与工作区整理
- 最新银行LLM已重新分析200条、未复用旧分析；197条通过，社保缴税/个税缴税/增值税缴税3条缺有效模板，公积金已完成分析。392个原始OCR文件保留；当前manual/automatic目录为空，尚未生成新receipt，也未上传。
- 当前数据以工作区generated/ocr/bank/template_analysis.json及新bank_map.json为准，复核报告在outputs/bank-llm-rerun-20260922/result.report.json。
- 历次测试、旧银行分析/receipt备份、临时修复脚本、财务安装测试副本共71项约581.44MiB已可恢复地移动至.cleanup-archive/20260922，restore-map.json记录原位置。
- 自动审批拒绝批量永久删除，因此仅归档，未释放磁盘空间。归档已加入.gitignore。保留源码、配置、原始输入、OCR、正式dist、其他业务产物及988份历史上传记录；不清除防重记录。

- 随后用户明确确认永久删除上述71项归档，已执行，释放581.46 MiB。删除前仅解除归档内node_modules目录联接，未删除共享运行时。原输入、OCR和上传记录共2299个文件SHA256复核未变；清理清单继续保留。


## 银行流程模块拆分
- bank_pipeline缩减为阶段编排与提交入口，拆出bank_preprocessing、bank_analysis、bank_preparation。llm不再调用裁剪/OCR/匹配；prepare不再调用OCR/匹配/LLM；新增bank stage=match，原ocr仍含匹配以兼容命令。缺少前置产物报错，不隐式重跑。
- receipt分类、兼容路径、目录枚举集中到bank_receipt_layout；保持manual/automatic都上传和现有receiptId/上传幂等记录。阶段文档docs/BANK_WORKFLOW.md。
- 本轮仅代码重构与离线测试，未改月度业务配置，未重跑真实LLM或上传，最新200条分析保留。


## 2026-09-22 远端删除后按新规则重跑
用户确认远端旧银行凭证已删除并授权重新完整执行OCR之后流程。仅对应company_17867515→company_23354445/2026-08银行196条上传幂等记录已移出活动目录留档，其他业务防重记录保留。
正式运行match→llm→prepare：200条重新分析，157次API成功响应，197条可复核且最终规则复核通过；生成197张（automatic194/manual3）。392个OCR文件哈希保持不变。prepare内PDF verify返回4，状态waiting_for_pdf_binding，未执行send、未上传。
缺PDF：上海银行手续费7.20元（V026080301217636）和13.50元（V026080301232417）。未生成的3条：社保缴税18903.64、个税缴税1883.57、增值税缴税71049.47，缺有效模板。公积金已生成在manual。
最新运行与待处理清单：outputs/bank-full-rerun-20260922/result.report.json及action-required.report.json；仅人工补全PDF和未就绪分录后再继续验证、上传，不需要重跑OCR或全部LLM。


## 最新覆盖规则：四类业务按本月project排除
用户明确要求将增值税缴税、社保缴税、公积金、个税缴税加入本月sources.bank.remark_exception，后续单独处理，不再作为本批manual业务。已写入company_17867515/2026-08/project.json，保留“跳过”，同步当前运行配置并执行match。
当前201条流水：196条匹配、4条备注排除、1条内部转账跳过。公积金receipt由匹配阶段移入remark_exception_receipts留存，当前automatic194/manual2；196张既有凭证复用无覆盖，分析阻塞0，只有手续费7.20和13.50两张待绑定PDF。当前批次未上传，状态waiting_for_pdf_binding。此前三条模板阻塞已不属于本批，历史分析结果可以保留但不进入上传。

- 2026-09-22 17:13新批次完成：两笔手续费用户提供PDF路径，经修正JSON反斜杠转义及首笔.pdf后缀后，196张凭证全部上传微誉测试并回读确认，196个不同voucherId，记-299至记-494；两张人工凭证为记-493/494。待上传0，缺PDF0，四类税费/公积金仍排除单独处理，未上传PDF汇总14份。状态已更新succeeded，上传证明generated/maps/bank/upload_complete.report.json。
