@echo off
setlocal

if /i not "%~1"=="--name" goto :usage
if "%~2"=="" goto :usage
if not "%~3"=="" (
  echo Only --name is supported.
  exit /b 2
)

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%~dp0scripts\create_company.py" %*
exit /b %ERRORLEVEL%

:usage
echo Usage: create_company.bat --name "Exact company name"
exit /b 2
