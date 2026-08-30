@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if /i not "%~1"=="--name" goto :usage
if "%~2"=="" goto :usage
if not "%~3"=="" (
  if /i not "%~3"=="--base-template" goto :usage
  if "%~4"=="" goto :usage
  if not "%~5"=="" goto :usage
)

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\create_company.py" %*
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\create_company_template.bat --name "Exact company name" [--base-template KEY]
exit /b 2
