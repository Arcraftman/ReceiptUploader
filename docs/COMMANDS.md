# Unified project launcher

Use one launcher with subcommands. Launchers, menus, help and command-adapter
messages use English. Business data (company names, account names, voucher text,
workbooks and domain reports) retains its original language. UTF-8 is used for
Python I/O and configuration; there are no separate GBK copies.

## Start from a checkout

Linux:

```bash
./scripts/linux/start.sh --help
./scripts/linux/start.sh
```

Windows CMD:

```bat
scripts\win\start.bat --help
scripts\win\start.bat
```

Windows PowerShell:

```powershell
.\scripts\win\start.ps1 --help
.\scripts\win\start.ps1
```

Launchers select `PYTHON_EXE`, then `.venv`, then `.auto`, then system Python.
Install project dependencies first. Login additionally requires the `discovery`
extra and Playwright Chromium. Launchers do not install dependencies or change
PowerShell security policies. If PowerShell scripts are restricted, use the BAT
launcher. No arguments starts the existing login/discovery setup console.
`--help` does not log in, read credentials or acquire the login lock.

## Commands

Replace `start` below with your platform launcher. Quote arguments containing
spaces. `COMPANY_CONFIG` is the existing filename under `config/companies`, with
or without `.json`; business filenames are not renamed.

| Command | Action |
| --- | --- |
| `start` | Log in, discover accountbooks and open the setup console |
| `start login [--headed]` | Refresh registered accountbook sessions |
| `start discover` | Discover accountbooks and refresh sessions |
| `start month COMPANY_CONFIG YYYY-MM TARGET` | Initialize the month; explicit target required |
| `start run COMPANY_CONFIG YYYY-MM` | Run the configured workflow without upload authorization |
| `start bank COMPANY_CONFIG YYYY-MM` | Run the bank workflow without upload authorization |
| `start verify COMPANY_CONFIG YYYY-MM` | Validate bank receipts |
| `start exceptions COMPANY_CONFIG YYYY-MM` | Show separated bank exceptions |
| `start unmatched COMPANY_CONFIG YYYY-MM` | Show unmatched bank transactions |
| `start report COMPANY_CONFIG YYYY-MM [SOURCE]` | Generate the accounting summary |
| `start status` | Show job states |
| `start confirm-one COMPANY_CONFIG YYYY-MM` | Confirm and upload at most one receipt |
| `start confirm-all COMPANY_CONFIG YYYY-MM` | Confirm and run the upload workflow |
| `start reset-upload-state COMPANY_CONFIG YYYY-MM [SOURCE]` | Clear local upload checkpoints |
| `start create-company --name "EXACT COMPANY NAME"` | Create company configuration and templates |
| `start test-read-apis --help` | Read-only API test options |
| `start finance serve [--port 18765]` | Start the existing loopback finance service |
| `start receipts --help` | Original low-level receipt command |

Inside the interactive setup console, `month DATASET YYYY-MM TARGET` retains its
registry selector behavior (company ID, key or exact name), and can create a
missing company configuration. The direct `month` subcommand takes an existing
company config filename, matching the old `initialize_month` scripts.

Inside that console, `finance` regenerates `excel/finance-template.xlsx` and
replaces the existing distributed template. This is the only user-facing entry
for template generation; the previous launcher subcommands were removed.

Upload confirmations remain required. No upload or bank accounting rules change.
English menus do not imply that upstream server errors or domain diagnostics
have been translated.

## Installed CLI

`pip install -e .` exposes `kdzwy-receipts` with the same subcommands. A built
wheel also contains the command implementations, login, pipeline workers and
finance service; runtime commands do not depend on the source scripts directory.

Set `KDZWY_PROJECT_ROOT` to the workspace directory containing `config/`. Without
it, the CLI searches the current directory and its parents, then the source
checkout when available. Business resources (configs, templates, Excel files and
private sessions) remain workspace files and are not embedded in the wheel.
Source launchers select their own checkout as the workspace. The live API
diagnostic command additionally uses `tests/fixtures/read_api_cases.json` from
the workspace; this diagnostic fixture is not embedded in the wheel.

Existing option-based receipt calls remain supported;
`kdzwy-receipts receipts --help` shows their options. Diagnostic export is also
available as `python -m kdzwy_receipt_uploader.finance.export_snapshot COMPANY MONTH`.

## Excel installer

```powershell
.\scripts\finance\install-excel.ps1
```

The defaults remain `excel/finance-template.xlsx` -> `excel/finance.xlsm`.
See [finance setup](finance/README.md) for the session, token and connection
requirements. `excel/Install-Finance.ps1` remains a compatibility forwarder.

## Supported entry points and testing

Only `scripts/linux/start.sh`, `scripts/win/start.bat`, `scripts/win/start.ps1`
and the installed `kdzwy-receipts` command are user-facing command launchers.
The old root `commands/` and `scripts/commands/` directories have been removed.
Internal implementations live in `kdzwy_receipt_uploader.commands`.

Tests exercise English help, argument preservation, nonzero exit propagation,
confirmation cancellation, lock release and month creation using the new
launchers. Native BAT and PowerShell tests run in Windows CI; passing Linux tests
does not constitute Windows or Excel COM acceptance.
