# 用户命令

日常操作只运行本目录中的 BAT；完整流程见 [当前操作手册](../docs/USAGE.md)。

| 命令 | 用途 |
|---|---|
| `start.bat` | 推荐入口：发现公司、刷新会话并进入安全菜单 |
| `discover_companies.bat` | 重新发现全部可访问公司；可同时初始化指定月份 |
| `login_companies.bat` | 刷新运行期账套注册表中的 HTTP 会话 |
| `create_company_template.bat` | 为新资料公司创建跨月份共享模板和 v3 公司配置 |
| `initialize_month.bat` | 创建该公司、该月份的 `project.json` v5 与四类资料目录 |
| `run_company.bat` | 执行明确的资料公司和月份 |
| `analysis_report.bat` | 生成指定月份、指定业务的人工复核简表 |
| `status.bat` | 查看隔离任务状态 |
| `confirm_one.bat` | 二次确认后真实上传一张 |
| `confirm_all.bat` | 二次确认后真实上传全部有效凭证 |
| `reset_upload_state.bat` | 清除指定公司、指定月份的本地上传断点 |

统一菜单的月份命令：

```text
month 资料公司ID YYYY-MM [目标公司ID]
```

所有月份敏感命令都必须显式传入 `YYYY-MM`：

```bat
commands\run_company.bat COMPANY_CONFIG_NAME YYYY-MM
commands\analysis_report.bat COMPANY_CONFIG_NAME YYYY-MM [sales|purchase|bank|misc]
commands\confirm_one.bat COMPANY_CONFIG_NAME YYYY-MM
commands\confirm_all.bat COMPANY_CONFIG_NAME YYYY-MM
commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM
```

`COMPANY_CONFIG_NAME` 是资料公司配置文件名，不含 `.json`。目标账套只读取该月 `project.json.target`。
