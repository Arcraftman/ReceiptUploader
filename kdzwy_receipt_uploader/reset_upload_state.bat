@echo off
setlocal

if "%~1"=="" goto :usage
if not "%~2"=="" goto :usage

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%~dp0scripts\reset_upload_state.py" "%~1"
exit /b %ERRORLEVEL%

:usage
echo Usage: reset_upload_state.bat COMPANY_KEY
echo Example: reset_upload_state.bat xinghai
exit /b 2
