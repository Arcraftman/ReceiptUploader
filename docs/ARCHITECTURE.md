# 项目架构与开发约定

本次整理以保持现有业务行为为前提：命令、项目配置、模板、产物格式和上传阶段不变。Excel 功能优化暂时搁置。

## 模块职责

| 层次 / 位置 | 职责 | 依赖方向 |
| --- | --- | --- |
| `scripts/linux/`、`scripts/win/` | ASCII 平台启动器：解释器选择、参数透传 | 调用 `scripts/start.py` |
| `command_dispatch.py`、包内 `commands/` | 共用子命令分派、英文菜单及参数适配；保留旧 receipt CLI | 调用 Python 业务包 |
| `pipeline_runner.py` | 解析参数、加载配置、解析项目路径、分派来源 | 调用 `application/` |
| `application/context.py` | 明确传递项目根目录、账套、路径、配置、状态回调 | 不调用业务流程 |
| `application/bank_pipeline.py` | 银行拆分、排除、OCR、匹配、分析、生成和提交编排 | 调用银行组件、OCR、API |
| `application/invoice_pipeline.py` | 销售、进项和杂项的映射、分析、生成与提交编排 | 调用来源组件、OCR、API |
| `ocr/models.py` | OCR 产物类型和异常 | 标准库 |
| `ocr/fields.py` | 票据字段提取、买卖方规则 | 来源定义与标准库 |
| `ocr/engine.py` | PDF 识别、识别引擎缓存、OCR 文件读写 | 字段提取、产物类型、PDF / OCR 依赖 |
| `ocr/selector.py` | OpenAI 兼容模型请求与响应解析 | 产物异常、HTTP |
| `ocr/rules.py` | 模板候选、币种、交易对方规则 | 产物类型、银行规则 |
| `ocr/rendering.py` | 按模板生成和校验分录、摘要、金额快照 | 模板引擎、规则 |
| `ocr/memory.py` | 分析记忆并发合并、原子保存与重试 | 标准库 |
| `ocr/service.py` | 组合 OCR 产物、模型选择、规则校验和分析保存 | 上述 OCR 组件 |
| `bank_rules.py` | 姓名识别与员工付款分类 | 标准库；不加载 Excel、OCR 或 HTTP |
| `integrations/read_client.py` | 只读白名单、账套身份校验、HTTP 传输、查询参数 | 共用 API 会话加载与配置 |
| `read_api_checks.py` | 接口契约测试、诊断报告 | 只读客户端；生产功能不反向依赖它 |
| `finance/snapshot.py` | 财务报表数据整理 | 只读客户端 |
| `api.py`、`workflow.py` 等现有组件 | 金蝶操作、凭证处理及其他已有领域服务 | 保留现有职责，按实际需求继续整理 |

业务规则不能反向导入命令入口；OCR 组件不能依赖流程编排；生产代码不能依赖接口测试模块。`tests/test_architecture.py` 检查这些边界，以及旧 API 类型兼容、业务规则无 IO 依赖、各来源命令分派及路径和参数传递。

## 兼容约定

- `receipts_ocr.py` 保留为兼容导出层，旧脚本和调用方的导入不需要调整；包内代码使用具体的新模块。
- `read_api_checks.py` 继续导出旧客户端和异常名称，客户端实现只有一份。
- 银行匹配模块继续导出 `is_person_name`、`employee_payment_kind`，其他业务模块直接使用 `bank_rules.py`。
- 项目根目录仍由原入口确定，通过 `PipelineContext.root` 传入新流程。移动文件不会改变配置、模板和脚本的路径解析。
- 包内入口不再修改 `sys.path`；从源码直接启动的脚本保留既有引导方式。
- 配置字典和命令参数仍沿用原有对象，不通过隐式全局变量或动态 `locals()` 注入流程。上下文冻结的是字段绑定，并非深度冻结配置字典。
- 银行科目、工资 / 报销路由、金额校验、模型提示词、缓存版本、重试、失败退出码和阶段顺序沿用原实现。

## 开发安装与检查

当前统一安装与检查流程见 [质量检查与故障恢复](QUALITY_AND_RECOVERY.md)。在仓库根目录执行：

```bash
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python scripts/maintenance/check_project.py
```

`pyproject.toml` 声明直接依赖，`uv.lock` 锁定版本和哈希；requirements 文件由锁文件导出，兼容旧 pip 部署入口。完整 OCR 使用 `--extra ocr`。模板、配置和运行数据仍属于项目工作目录，不打入 Python wheel。

静态检查拦截语法和未定义名称等明确错误；类型检查逐步覆盖核心契约。CI 执行 Linux/Windows × Python3.10/3.13 的离线测试、关键路径覆盖率门槛与构建，另行运行完整 OCR 安装和推理测试。真实接口默认不执行；CI 配置存在不代表远端已实际运行。

## 验证与后续边界

最初架构拆分时在 Linux / Python 3.13 验证：208 项测试通过、3697 个历史分录子测试通过、34 项真实接口测试按默认规则跳过；静态检查、wheel 构建、离开源码目录后从 wheel 导入和 CLI 帮助均通过。

重构前后还比较了 OCR 的 31 个函数 / 类以及银行和发票流程的语法树，除导入位置和显式上下文提取外，业务语句保持一致。本次没有调用真实上传接口，没有验收 Windows 运行环境。

这次建立了模块边界，并不代表所有旧模块都已足够小。银行和发票编排仍分别约 760 / 1000 行，模板渲染约 610 行；公司注册与配置校验仍集中在 `company_registry.py`。后续优先在补充各阶段独立契约测试后拆分分析、生成等内部阶段，避免仅为了缩短文件引入复杂框架。无需立即加入依赖注入容器、插件系统或通用工作流引擎。


## 2026-09-19 工程保障补齐

安装、依赖升级、检查与故障恢复以 [质量检查与故障恢复](QUALITY_AND_RECOVERY.md) 为当前说明：已增加 uv.lock、带哈希的 requirements 导出、关键路径覆盖率门槛、8个模块的 strict 类型检查，以及 Linux/Windows × Python3.10/3.13 的 CI 和 OCR 冒烟任务。上文“尚无锁定流程”等状态仅描述前一轮架构拆分时点。
正常 CLI 上传增加独立的提交日志和操作系统互斥锁，保存前持久化意图，已知凭证ID只恢复回读/绑定核验，结果不明不重新保存。正常账务规则与命令保持兼容。

## 2026-09-19 Unified launchers

See [command reference](COMMANDS.md). The installed `kdzwy-receipts` command and source launchers share `command_dispatch.py`. Login uses the existing cross-platform kernel lock, released before entering the console. Business rules and configuration formats remain unchanged.

## Packaged runtime commands

`commands/` inside the Python package owns command parsing and orchestration.
`command_dispatch.py` invokes command functions directly with explicit argument
lists. `project_runtime.py` binds a workspace for one invocation and restores it
on exit, including exceptions; there is no mutable module-global workspace.
Isolated login, setup, pipeline and upload workers use `python -m` package
modules with an explicit workspace and environment. Package code neither edits
`sys.path` nor executes files from `scripts/`.

`finance/` owns the service, snapshot transformation and export. The external
`scripts/finance/` directory retains only the Windows Excel installer. Workbook authoring uses the packaged
`finance/build_template.py` and `template_layout.json` resource. Business configuration, templates and private sessions remain
workspace resources; installation does not provision or copy these resources.
