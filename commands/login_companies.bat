@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

cd /d "%PROJECT_ROOT%"

if not exist "%PROJECT_ROOT%\config\kdzwy.json" (
  echo Config file not found: %PROJECT_ROOT%\config\kdzwy.json
  echo Please create it and fill in accounts with username/password.
  pause
  exit /b 1
)

if not exist "runtime\registry\accountbooks.json" (
  echo Accountbooks file not found: runtime\registry\accountbooks.json
  echo Please run commands\discover_companies.bat first.
  if /i not "%~1"=="--no-pause" pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d='%PROJECT_ROOT%\runtime\locks'; [void][IO.Directory]::CreateDirectory($d); try {$l=[IO.File]::Open((Join-Path $d 'http_login.lock'),'OpenOrCreate','ReadWrite','None')} catch {Write-Error 'HTTP login is already running.'; exit 3}; try {Add-Type -AssemblyName System.Net.Http; $result = & '%PROJECT_ROOT%\scripts\windows\login_http.ps1' -ConfigPath '%PROJECT_ROOT%\config\kdzwy.json' -AccountbooksPath '%PROJECT_ROOT%\runtime\registry\accountbooks.json'; $c=$LASTEXITCODE} finally {$l.Dispose()}; exit $c"
if errorlevel 1 (
  echo HTTP login failed.
) else (
  echo HTTP login succeeded.
)

if /i not "%~1"=="--no-pause" pause
exit /b %ERRORLEVEL%
