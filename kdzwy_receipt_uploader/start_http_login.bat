@echo off
setlocal

cd /d "%~dp0"

if not exist "..\config\kdzwy.json" (
  echo Config file not found: ..\config\kdzwy.json
  echo Please create it and fill in username/password.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Net.Http; & '%~dp0login_http.ps1' -ConfigPath '%~dp0..\config\kdzwy.json'"
if errorlevel 1 (
  echo HTTP login failed.
) else (
  echo HTTP login succeeded.
)

if /i not "%~1"=="--no-pause" pause
exit /b %ERRORLEVEL%
