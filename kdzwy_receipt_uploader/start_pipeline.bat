@echo off
setlocal
set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Required Python packages are not installed.
  echo Install requirements.txt before running the pipeline.
  pause
  exit /b 2
)
"%PYTHON_EXE%" "%~dp0run_companies.py" --mode analysis-only
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Pipeline finished with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
