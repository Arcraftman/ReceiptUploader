@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not "%~4"=="" goto :usage

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if "%~3"=="" (
  "%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\concise_template_analysis.py" --company "%~1" --month "%~2"
) else (
  "%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\concise_template_analysis.py" --company "%~1" --month "%~2" --source "%~3"
)
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:usage
echo Usage: commands\analysis_report.bat COMPANY_CONFIG_NAME YYYY-MM [sales^|purchase^|bank^|misc]
echo Example: commands\analysis_report.bat company_20151038_星海公司 2026-08 sales
exit /b 2
