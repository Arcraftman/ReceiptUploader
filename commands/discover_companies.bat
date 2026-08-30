@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "DISCOVERY_COMPANY_SELECTOR="
set "DISCOVERY_MONTH="
set "DISCOVERY_TARGET_SELECTOR="
set "DISCOVERY_NO_PAUSE="
set "DISCOVERY_QUIET="

:parse_args
if "%~1"=="" goto args_ready
if /i "%~1"=="--dataset" goto arg_company
if /i "%~1"=="-Dataset" goto arg_company
if /i "%~1"=="--month" goto arg_month
if /i "%~1"=="-Month" goto arg_month
if /i "%~1"=="--target" goto arg_target
if /i "%~1"=="-Target" goto arg_target
if /i "%~1"=="--no-pause" goto arg_no_pause
if /i "%~1"=="--quiet" goto arg_quiet
if /i "%~1"=="--help" goto usage_ok
if /i "%~1"=="-h" goto usage_ok
echo Unknown argument: %~1
goto usage_error

:arg_company
if "%~2"=="" goto usage_error
set "DISCOVERY_COMPANY_SELECTOR=%~2"
shift
shift
goto parse_args

:arg_month
if "%~2"=="" goto usage_error
set "DISCOVERY_MONTH=%~2"
shift
shift
goto parse_args

:arg_target
if "%~2"=="" goto usage_error
set "DISCOVERY_TARGET_SELECTOR=%~2"
shift
shift
goto parse_args

:arg_no_pause
set "DISCOVERY_NO_PAUSE=1"
shift
goto parse_args

:arg_quiet
set "DISCOVERY_QUIET=1"
shift
goto parse_args

:args_ready

cd /d "%PROJECT_ROOT%"

if not exist "%PROJECT_ROOT%\config\kdzwy.json" (
  echo Config file not found: %PROJECT_ROOT%\config\kdzwy.json
  echo Please create it and fill in accounts with username/password.
  if not defined DISCOVERY_NO_PAUSE pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d='%PROJECT_ROOT%\runtime\locks'; [void][IO.Directory]::CreateDirectory($d); try {$l=[IO.File]::Open((Join-Path $d 'company_discovery.lock'),'OpenOrCreate','ReadWrite','None')} catch {Write-Error 'Company discovery is already running.'; exit 3}; try {Add-Type -AssemblyName System.Net.Http; & '%PROJECT_ROOT%\scripts\windows\select_companies_and_run.ps1' -ConfigPath '%PROJECT_ROOT%\config\kdzwy.json' -CompanySelector $env:DISCOVERY_COMPANY_SELECTOR -Month $env:DISCOVERY_MONTH -TargetSelector $env:DISCOVERY_TARGET_SELECTOR -Quiet:($env:DISCOVERY_QUIET -eq '1'); $c=$LASTEXITCODE} finally {$l.Dispose()}; exit $c"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo Company discovery or month initialization failed.
) else (
  if not defined DISCOVERY_QUIET echo Company discovery workflow succeeded.
)

if not defined DISCOVERY_NO_PAUSE pause
exit /b %EXIT_CODE%

:usage_ok
call :usage
exit /b 0

:usage_error
call :usage
exit /b 2

:usage
echo Usage:
echo   commands\discover_companies.bat
echo   commands\discover_companies.bat --dataset DATASET_SELECTOR --month YYYY-MM --target TARGET_SELECTOR
echo.
echo DATASET_SELECTOR and TARGET_SELECTOR accept company_key, company_id, exact company name, or standard company config filename.
exit /b 0
