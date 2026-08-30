@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if not "%~1"=="" (
  echo Unknown argument: %~1
  exit /b 2
)

cd /d "%PROJECT_ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\windows\start_console.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%

:usage
echo Usage: commands\start.bat
echo.
echo Starts the safe company setup console. Real voucher upload is not available in this menu.
exit /b 0
