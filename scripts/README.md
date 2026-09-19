# Scripts

- `start.py`: source-checkout bootstrap for the shared Python CLI.
- `linux/start.sh`: Linux launcher.
- `win/start.bat`, `win/start.ps1`: Windows launchers.
- `finance/`: Windows Excel installer only. Template authoring is a packaged Python finance command.
- `maintenance/`: explicit maintenance and quality-check tools.

Runtime command implementations live in `src/kdzwy_receipt_uploader/commands/`.
The finance bridge and snapshot export live in the package's `finance/` module.
See [command reference](../docs/COMMANDS.md).
