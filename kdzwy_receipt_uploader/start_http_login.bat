@echo off
setlocal

cd /d "%~dp0"

if not exist "..\config\kdzwy.json" (
  echo Config file not found: ..\config\kdzwy.json
  echo Please create it and fill in accounts with username/password.
  pause
  exit /b 1
)

if not exist "config\accountbooks.json" (
  echo Accountbooks file not found: config\accountbooks.json
  echo Please run start_discover_companies.bat first.
  if /i not "%~1"=="--no-pause" pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d='%~dp0runtime\locks'; [void][IO.Directory]::CreateDirectory($d); try {$l=[IO.File]::Open((Join-Path $d 'http_login.lock'),'OpenOrCreate','ReadWrite','None')} catch {Write-Error 'HTTP login is already running.'; exit 3}; try {Add-Type -AssemblyName System.Net.Http; $result = & '%~dp0login_http.ps1' -ConfigPath '%~dp0..\config\kdzwy.json' -AccountbooksPath '%~dp0config\accountbooks.json'; $c=$LASTEXITCODE} finally {$l.Dispose()}; exit $c"
if errorlevel 1 (
  echo HTTP login failed.
) else (
  echo HTTP login succeeded.
)

if /i not "%~1"=="--no-pause" pause
exit /b %ERRORLEVEL%
