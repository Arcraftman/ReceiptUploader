# 用户命令

日常操作只运行本目录中的 BAT；完整流程见 [当前操作手册](../docs/USAGE.md)。

| 命令 | 用途 |
|---|---|
| `start.bat` | 推荐入口：发现公司、刷新会话并进入安全菜单 |
| `discover_companies.bat` | 重新发现全部可访问公司；可同时初始化指定月份 |
| `login_companies.bat` | 刷新运行期账套注册表中的 HTTP 会话 |
| `create_company_template.bat` | 为新资料公司创建跨月份共享模板和 v3 公司配置 |
| `initialize_month.bat` | 创建该公司、该月份的 `project.json` v7 和四类资料目录 |
| `run_bank.bat` | 只读取当月配置，并按 bank 的 mode/stage 执行 OCR、LLM 或 prepare+existing |
| `list_bank_exceptions.bat` | 列出已从普通流程分离的特殊记录、裁剪原件和特殊副本，不执行后续业务 |
| `list_unmatched_bank.bat` | 只列出未被 exception 接管的普通未匹配记录 |
| `run_company.bat` | 执行明确的资料公司和月份 |
| `analysis_report.bat` | 生成指定月份、指定业务的人工复核简表 |
| `status.bat` | 查看隔离任务状态 |
| `confirm_one.bat` | 二次确认后真实上传一张 |
| `confirm_all.bat` | 二次确认后真实上传全部有效凭证 |
| `reset_upload_state.bat` | 清除指定公司、指定月份的本地上传断点 |

统一菜单的月份命令：

```text
month dataset公司ID YYYY-MM target公司ID
bank dataset公司ID YYYY-MM
exceptions dataset公司ID YYYY-MM
unmatched dataset公司ID YYYY-MM
verify dataset公司ID YYYY-MM
```

所有月份敏感命令都必须显式传入 `YYYY-MM`：

`bank` 按当月配置分阶段执行：`analysis-only+ocr` 只做裁剪、特殊对象分流、剩余 OCR/匹配；`analysis-only+llm` 只分析普通匹配；人工复核后 `prepare+existing` 才生成最终 receipt，并全部保持 `draft=true`。补齐后改为 false，再运行 `verify dataset公司ID YYYY-MM`。该入口不执行真实上传。

```bat
commands\run_company.bat COMPANY_CONFIG_NAME YYYY-MM
commands\run_bank.bat COMPANY_CONFIG_NAME YYYY-MM
commands\list_bank_exceptions.bat COMPANY_CONFIG_NAME YYYY-MM
commands\list_unmatched_bank.bat COMPANY_CONFIG_NAME YYYY-MM
commands\analysis_report.bat COMPANY_CONFIG_NAME YYYY-MM [sales|purchase|bank|misc]
commands\confirm_one.bat COMPANY_CONFIG_NAME YYYY-MM
commands\confirm_all.bat COMPANY_CONFIG_NAME YYYY-MM
commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM
```

`COMPANY_CONFIG_NAME` 是 dataset 公司的配置文件名，不含 `.json`。月份配置必须显式包含 `project.json.dataset` 和 `project.json.target`。
