@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not "%~4"=="" goto :usage

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if "%~3"=="" (
  "%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\reset_upload_state.py" "%~1" "%~2"
) else (
  "%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\reset_upload_state.py" "%~1" "%~2" --source "%~3"
)
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM [sales^|purchase^|bank^|misc^|all]
echo Example: commands\reset_upload_state.bat company_20151038_星海公司 2026-08 sales
exit /b 2
