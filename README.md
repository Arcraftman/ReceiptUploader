# 企业凭证 OCR、模板匹配与账无忧上传

跨平台启动入口见 [统一命令说明](docs/COMMANDS.md)：Linux 使用 `scripts/linux/start.sh`，Windows 使用 `scripts/win/start.bat` 或 `start.ps1`。旧 `commands/` 启动器已移除。

当前业务操作说明已精简并迁移到 [docs/USAGE.md](docs/USAGE.md)。请以该文档为准；旧的 `datasets.json`、`month.conf`、`config/accountbooks.json` 和 `project.json` v5 及更早版本均已移除且不兼容。

财务工作簿先在 `kdzwy>` 控制台输入 `finance` 生成 `excel/finance-template.xlsx`；按钮安装与当前功能边界见 [财务工作簿说明](docs/finance/README.md)。

开发与维护说明见 [项目架构与开发约定](docs/ARCHITECTURE.md) 和 [质量检查与故障恢复](docs/QUALITY_AND_RECOVERY.md)。
