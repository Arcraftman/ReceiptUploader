@echo off
setlocal

cd /d "%~dp0"

if not exist "..\config\kdzwy.json" (
  echo Config file not found: ..\config\kdzwy.json
  echo Please create it and fill in accounts with username/password.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d='%~dp0runtime\locks'; [void][IO.Directory]::CreateDirectory($d); try {$l=[IO.File]::Open((Join-Path $d 'company_discovery.lock'),'OpenOrCreate','ReadWrite','None')} catch {Write-Error 'Company discovery is already running.'; exit 3}; try {Add-Type -AssemblyName System.Net.Http; & '%~dp0select_companies_and_run.ps1' -ConfigPath '%~dp0..\config\kdzwy.json'; $c=$LASTEXITCODE} finally {$l.Dispose()}; exit $c"
if errorlevel 1 (
  echo Company workflow failed.
) else (
  echo Company workflow succeeded.
)

pause
