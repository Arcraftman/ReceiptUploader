# 质量检查、安装与故障恢复

这份说明对应项目的四项工程补齐：恢复测试、关键路径覆盖率、可复现与跨平台安装、明确的类型契约。正常业务配置与命令保持兼容。

## 一次执行开发检查

在仓库根目录使用 uv 0.12.17，安装 Python 3.10 或 3.13：

```bash
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python scripts/maintenance/check_project.py
```

Linux、Windows 使用同一条 Python 检查命令，依次执行 Ruff、严格类型检查、离线测试、覆盖率报告与门槛检查、sdist / wheel 构建。任何一步失败即返回非零退出码。

HTML 报告在 `outputs/quality/coverage/index.html`，JSON 在 `coverage.json`，构建产物在 `outputs/quality/dist/`。真实接口测试仍需要显式设置公司和月份，默认不联网、不登录、不上传。

## 可复现安装

`pyproject.toml` 声明直接依赖，`uv.lock` 记录解析后的版本、平台条件与文件哈希，是依赖版本的唯一来源。CI 用 `--locked`，配置与锁文件不一致时直接失败。

- 开发与离线测试：`uv sync --locked --extra dev --python 3.13`。
- 完整 OCR：`uv sync --locked --extra dev --extra ocr --python 3.13`。
- 保留旧 pip 安装方式：`python -m pip install --require-hashes -r requirements.txt`，开发清单为 `requirements-dev.txt`。这两份清单由锁文件导出，不再手工维护另一组版本。
- Python 3.10 的 OCR 额外约束为 `onnxruntime<1.24`：干净安装发现较新版本缺少 cp310 wheel，锁文件现在选择可安装的版本。3.13 使用独立的适用版本。
- `discovery` 浏览器探测功能是单独的可选依赖；Playwright 浏览器安装不属于此次离线测试范围。

更新依赖时，修改 pyproject 后执行并提交三个生成文件：

```bash
uv lock
uv export --locked --extra ocr --no-dev --no-emit-project --output-file requirements.txt
uv export --locked --extra dev --extra ocr --no-dev --no-emit-project --output-file requirements-dev.txt
```

不要删除锁文件来绕过安装错误。wheel 只包含 Python 包，业务运行仍使用仓库中的配置、模板与入口；文件夹式项目部署方式不变。

## 覆盖率门槛

行覆盖与分支覆盖分别检查，规则在 `config/quality/coverage.json`。缺少模块或函数数据同样失败，不把“没有采集到”当作通过。

| 范围 | 行覆盖下限 | 分支覆盖下限 |
| --- | ---: | ---: |
| 上传与凭证校验 workflow | 90% | 85% |
| 提交恢复日志 | 90% | 85% |
| 只读接口客户端 | 95% | 90% |
| 员工付款分类规则 | 90% | 85% |
| 模板候选规则 | 85% | 80% |
| 模板渲染和校验 | 80% | 70% |
| 银行最终凭证生成 | 75% | 60% |
| 凭证保存、附件上传和绑定 API 函数 | 100% | 100% |

API 的会话加载、身份校验和 HTTP 传输另设函数级门槛。API 整个文件仍含未充分覆盖的目录查询等方法，不能把关键函数的 100% 解释成全模块 100%。历史模块先设置经验证的下限，后续新增业务分支必须补测试，逐步提高门槛。

新增故障用例覆盖保存超时、保存/绑定时进程中断、回读失败、原子替换失败、磁盘错误、提交状态回写失败、并发提交、损坏日志、凭证/附件变化、不同账套、无效金额和模板分摊错误。原历史分录回归继续执行。

## 提交日志与恢复

所有正常 CLI 提交现在使用 `<runtime-root>/processing/uploads/` 的提交日志。键由账套服务地址、company ID、DBID、receiptId 共同生成；日志也保存可读身份字段。凭证内容与 PDF 内容哈希绑定，换文件、换金额或换账套不能复用旧结果。

跨进程互斥使用操作系统文件锁，进程退出后自动释放；锁文件不能按“残留文件”清理。日志使用临时文件、fsync 和原子替换，Linux 还同步目录。

| 阶段 | 意义与下一次运行的处理 |
| --- | --- |
| `prepared` | 尚未发起凭证保存；已确认的附件 fileId 可复用 |
| `saving` | 保存意图已经落盘，但没有可靠的凭证 ID；阻止再次保存，先核对远端 |
| `saved` | 已持久化凭证 ID；只恢复回读和后续附件流程，不重新取号/保存 |
| `binding` | 绑定请求可能已发送；只回读，要求 usedAttachments 提供实际使用数量证据；仅声明 attachments 数量不够，不盲目重复绑定 |
| `verified` | 已完成核验；返回持久化结果，补做本地审计/状态回写，不重复远端提交 |

已知凭证 ID 的失败保留原 receipt，审计标记 `recovery_pending`，再次执行原命令可尝试恢复。保存结果不明时沿用异常台账隔离，停止后续凭证；核对远端时保留原日志、receipt、凭证号及审计记录。不要直接删除日志、重置 uploaded 标记或换 receiptId 重试，这会破坏防重依据。远端确切结果没有被核实前，不自动宣称可安全重提。

并发竞争失败的进程不移动另一进程正在处理的 receipt，不写入阻断台账。保存意图无法落盘时不发出保存请求；保存已成功但本地记录失败时，下次保守停在“结果不明”。

边界：这不是远端事务或服务端幂等键。附件服务在响应丢失时仍可能留下未绑定文件；本项目保证不因此自动重复保存凭证。日志缺失、跨机器各用一份工作区、人工删除/修改远端凭证，不在本地日志自动恢复的保证范围内。旧版已提交但未留下日志的记录不会凭空获得历史防重证据。直接调用底层 `process_one` 的集成方必须传入 UploadJournal；未传参数的旧兼容调用不具有跨进程恢复能力。

## 类型契约

`PipelineOptions` 为命令选项提供冻结的数据类；`StageCheckpoint` 明确阶段状态回调的参数；`UploadState` 约束阶段枚举、身份与持久化字段并执行运行时校验；`VerifiedAttachmentResult` 明确结果字段和状态值。外部 JSON 格式不变，旧配置继续读取。

Mypy strict 已覆盖 workflow、提交日志与结果契约、应用上下文与选项、应用配置、月份配置和银行纯规则共 8 个模块。其余历史模块仍允许逐步收紧，不宣称全仓库已通过严格类型检查。

## 跨平台验收

CI 矩阵为 Ubuntu / Windows × Python 3.10 / 3.13，各自安装锁定依赖并运行完整离线检查。原 Linux 月份初始化测试现在在 Windows 调用真实 `.bat`，在 Linux 调用 `.sh`，再验证 PDF 拆分、流水匹配与凭证生成；OCR/LLM 边界在该流程测试中使用替身。

另有四环境 OCR 安装/推理任务：安装完整 OCR extra，在本地合成数字图上执行真实 RapidOCR 推理，不读取业务资料。手动运行时设 `KDZWY_OCR_SMOKE=1`，执行 `python -m pytest tests/test_ocr_runtime.py -q`。

当前机器为 Linux：本地可验收两个 Python 版本与实际 OCR；Windows 的测试与 CI 已配置，但要以远端 Windows runner 的结果作为执行证据。本文不把配置存在等同于 Windows 已通过。


## 本地验收记录（2026-09-19）

Linux 的全新 Python 3.10.21 / 3.13.15 环境均通过 342 项测试和 3697 个历史分录子测试；默认跳过 34 项真实接口测试和 1 项独立 OCR 冒烟测试。两个版本的真实 OCR 冒烟分别通过，Ruff、8 模块 strict Mypy、全部覆盖率门槛、sdist / wheel 构建通过。依赖一致性检查通过。Windows 仍需等待远端 CI 实际运行。

当次行 / 分支覆盖率：workflow 91.4% / 89.8%，提交日志约 96% / 92%，只读客户端 97.5% / 91.7%，模板候选 87.5% / 81.7%，模板渲染 89.3% / 80.1%，银行最终生成 78.3% / 63.2%。精确数据以生成的 coverage.json 为准。
