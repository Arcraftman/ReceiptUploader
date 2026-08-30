@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if "%~3"=="" goto :usage
if not "%~4"=="" goto :usage

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\initialize_company_month.py" "%~1" "%~2" "%~3"
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\initialize_month.bat DATASET_COMPANY_CONFIG_NAME YYYY-MM TARGET_COMPANY_ID_OR_KEY
exit /b 2
