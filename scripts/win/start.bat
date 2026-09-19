@echo off
setlocal DisableDelayedExpansion
set "PROJECT_ROOT=%~dp0..\.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if defined PYTHON_EXE goto launch
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto launch
set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto launch
set "PYTHON_EXE=python"
:launch
"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\start.py" %*
exit /b %ERRORLEVEL%
