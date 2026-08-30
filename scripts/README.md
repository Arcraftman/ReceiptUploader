# 实现脚本

- `commands/`：由 `commands/*.bat` 调用的 Python 命令实现。
- `windows/`：公司发现和纯 HTTP 登录的 PowerShell 实现。
- `maintenance/`：只在明确维护场景下手工执行的脚本。

普通用户不直接从本目录启动日常任务；稳定入口统一位于项目根的 `commands/`。

维护入口均要求显式输入或把输出写入 `workspaces/`：

- `maintenance/scan_xlsx_pdf_map.py`：读取标准月份的 `input/`，映射写入该月份工作区。
- `maintenance/analyze_accountid_auxiliary_relation.py`：只读检查指定 receipt 与辅助核算报告，默认报告写入 `workspaces/diagnostics/`。
