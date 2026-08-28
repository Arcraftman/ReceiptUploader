# 项目业务决策记忆

更新日期：2026-08-27

## 多账号与工作区隔离

- `config/kdzwy.json` 支持多个启用账号；每个账号必须有唯一 `key`、`username` 和 `password`。
- `start_discover_companies.bat` 不再要求用户逐家公司选择；它会遍历全部启用账号，并自动导入、启用每个账号返回的所有公司。
- 自动启用指 `accountbooks.json` 中的账套登录和会话启用；新生成的公司业务配置仍默认 `enabled=false`，必须填写 dataset、模板、月份和来源后才能运行。
- `start_discover_companies.bat` 在公司发现和 HTTP 会话建立完成后立即结束，禁止自动调用任何 `run_company.bat`，避免发现公司时误触发 analysis、prepare 或 confirm。
- `accountbooks.json` 的每个账套必须记录 `login_account`，会话固定存放在 `http_sessions/accounts/<login_account>/companies/`。
- 原始资料和月份 `.conf` 保持现有位置：`data/inbox/<dataset>/<month>/`；运行过程只读取 input，不移动用户资料。
- 每个任务的生成物固定写入 `workspaces/<login_account>/<company_key>/<dataset>/<month>/generated/`。
- OCR、DeepSeek、maps、receipts、银行裁剪、日志、失败归档、上传审计和任务状态均按工作区隔离。
- 不兼容旧 `data/inbox/<dataset>/<month>/generated`；程序不读取、不复制、不回退到旧生成目录。
- 新工作区没有分析结果时必须从 OCR 阶段重新生成，`existing` 只读取当前账号账套的工作区。
- 同一账号/账套/dataset/month/source 继续使用操作系统文件锁，禁止重复运行；不同工作区可以并行。
- 公司发现和 HTTP 登录分别使用全局进程锁；每个入口只能同时运行一个实例。

## 银行回单裁剪规则

- 每个 dataset/month 在 `input/bank/bank_split.json` 独立配置银行回单版式。
- 配置是直接 key/value 对象：key 为英文小写银行名，value 为每一页包含的回单数量，例如 `shanghaiyinhang: 2`、`shanghainongshangyinhang: 3`。
- 原始合并 PDF 必须命名为 `<bank_key>.pdf` 并放在同一 `input/bank` 目录。
- bank 任务先执行确定性等高裁剪，不再根据 PDF 文本猜测二等分或三等分。
- 裁剪后先使用银行专用快速识别：优先读取 PDF 文本，缺失时使用 RapidOCR 150 DPI。
- 快速识别支持发票号、回单号、流水号、凭证号、业务编号、参考号等明确标签；20位发票号码可作为无标签兜底。
- 裁剪结果固定写入 `generated/bank_receipts/<bank_key>/`，每份均为单页 PDF，并以唯一识别出的号码命名为 `<号码>.pdf`。
- 无法识别号码或号码重复的片段进入 `<bank_key>/unrecognized/`，并阻断后续银行流程，禁止继续使用页码临时名称。
- 裁剪清单保存原 PDF 大小、修改时间和每页份数；输入及配置不变时复用已有结果。
- 原裁剪器中的固定年份、金额猜测、日期重命名和关键词决定分割数量不属于当前集成规则。
- 当前只完成银行 PDF 预处理；银行 OCR 身份、流水匹配、模板分类、receipt 生成和正式上传仍需逐步接入并验证。

## 当前任务关系

- 数据集：`weiyu`，资料主体为上海微誉信息技术有限公司。
- 模板公司：`weiyu`。
- 目标账套：`xinghai`，显示名称为星海公司。
- 当前属于明确允许的跨主体测试：使用 weiyu 的资料和模板写入 xinghai 账套。
- 测试期间允许资料期间与账套当前期间不同，但必须保留提示。

## 已确认的销售规则

- sales 中资料主体固定为 seller，buyer 是客户。
- 销售商品收入模板为三条分录：借记应收账款，贷记主营业务收入和销项税额。
- 只有应收账款行使用客户辅助核算，真实字段为 `customerId`。
- 每张销售 voucher 的所有分录使用同一个 explanation：`销售商品收入 {发票号}`。
- explanation 必须由模板字段生成，不能使用 DeepSeek 为不同分录生成的 explanation。
- 保存载荷必须模拟网站当前前端：amount、amountFor、debitTotal、creditTotal 使用字符串。
- 保存后必须回读凭证；附件上传、绑定后必须再次回读。
- 两张凭证之间间隔 1 秒；仅对附件 Tunnel 502 进行 2、5、10 秒退避重试。
- 任意一张失败即停止后续任务。

## 运行与恢复约定

- 每个 accountbook/dataset/month/source 使用独立 `state.json` 和 `events.jsonl`；状态原子更新并保留尝试历史。
- 同一任务使用操作系统级锁，禁止两个进程并发处理；进程崩溃后锁会自动释放。
- 状态文件只用于观察和故障定位，不得据此自动跳过真实上传。
- `confirm` 每次处理全部输入 receipt，不使用本地历史成功日志自动跳过。
- 用户可能中途叫停并清空线上全部凭证；清空后可直接重新完整运行。
- 如果线上没有清空，禁止直接重跑完整 confirm，否则会重复入账。
- 附件失败发生在凭证保存之后时，不得重试保存；应根据日志中的 voucherId/voucherNo 单独恢复附件。
- `confirm` 不使用审核员参数；跨主体真实写入仍要求 `--allow-cross-entity-confirm`，预审警告必须为 0。

## Purchase 下一步

- purchase 尚未获准真实上传。
- 先确认 purchase 的特殊用途匹配、买卖方角色、供应商辅助核算、动态科目、模板 explanation 和分录规则。
- purchase 的 OCR、DeepSeek 分析结果、记忆和报告必须与 sales 按 source 隔离。
- 隔离完成后先运行 purchase analysis-only，人工核对简表，再运行 dry-run。
- dry-run 达到 0 无效 receipt、0 预审警告后，只进行一张真实上传验证；验证通过后再讨论批量。
