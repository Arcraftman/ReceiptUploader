@echo off
setlocal

if "%~1"=="" goto :usage
if not "%~3"=="" goto :usage

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if "%~2"=="" (
  "%PYTHON_EXE%" "%~dp0concise_template_analysis.py" --company "%~1"
) else (
  "%PYTHON_EXE%" "%~dp0concise_template_analysis.py" --company "%~1" --source "%~2"
)
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:usage
echo Usage: generate_analysis_report.bat COMPANY_KEY [sales^|purchase^|bank^|misc]
echo Example: generate_analysis_report.bat xinghai sales
exit /b 2
