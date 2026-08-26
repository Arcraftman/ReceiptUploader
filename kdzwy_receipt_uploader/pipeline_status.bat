@echo off
setlocal
set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%~dp0pipeline_status.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
