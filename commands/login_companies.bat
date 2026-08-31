@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "NO_PAUSE=0"
set "LOGIN_ACCOUNTBOOK_KEY="
set "LOGIN_PROJECT_CONFIG="

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--no-pause" goto :set_no_pause
if /i "%~1"=="--accountbook-key" goto :set_accountbook_key
if /i "%~1"=="--project-config" goto :set_project_config
goto :usage

:set_no_pause
set "NO_PAUSE=1"
shift
goto :parse_args

:set_accountbook_key
if "%~2"=="" goto :usage
set "LOGIN_ACCOUNTBOOK_KEY=%~2"
shift
shift
goto :parse_args

:set_project_config
if "%~2"=="" goto :usage
set "LOGIN_PROJECT_CONFIG=%~2"
shift
shift
goto :parse_args

:args_done

cd /d "%PROJECT_ROOT%"

if not exist "%PROJECT_ROOT%\config\kdzwy.json" (
  echo Config file not found: %PROJECT_ROOT%\config\kdzwy.json
  echo Please create it and fill in accounts with username/password.
  if "%NO_PAUSE%"=="0" pause
  exit /b 1
)

if not exist "runtime\registry\accountbooks.json" (
  echo Accountbooks file not found: runtime\registry\accountbooks.json
  echo Please run commands\discover_companies.bat first.
  if "%NO_PAUSE%"=="0" pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d='%PROJECT_ROOT%\runtime\locks'; [void][IO.Directory]::CreateDirectory($d); try {$l=[IO.File]::Open((Join-Path $d 'http_login.lock'),'OpenOrCreate','ReadWrite','None')} catch {Write-Error 'HTTP login is already running.'; exit 3}; try {Add-Type -AssemblyName System.Net.Http; $p=@{ConfigPath='%PROJECT_ROOT%\config\kdzwy.json';AccountbooksPath='%PROJECT_ROOT%\runtime\registry\accountbooks.json';AccountbookKey='%LOGIN_ACCOUNTBOOK_KEY%';ProjectConfigPath='%LOGIN_PROJECT_CONFIG%'}; $result = & '%PROJECT_ROOT%\scripts\windows\login_http.ps1' @p; $c=$LASTEXITCODE} finally {$l.Dispose()}; exit $c"
if errorlevel 1 (
  echo HTTP login failed.
) else (
  echo HTTP login succeeded.
)

if "%NO_PAUSE%"=="0" pause
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\login_companies.bat [--accountbook-key ACCOUNTBOOK_KEY] [--project-config PROJECT_JSON] [--no-pause]
exit /b 2
