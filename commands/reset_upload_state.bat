@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not "%~3"=="" goto :usage

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\reset_upload_state.py" "%~1" "%~2"
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\reset_upload_state.bat COMPANY_CONFIG_NAME YYYY-MM
echo Example: commands\reset_upload_state.bat company_20151038_星海公司 2026-08
exit /b 2
