@echo off
setlocal

if "%~1"=="" goto :usage
if not "%~2"=="" goto :usage

set "COMPANY_KEY=%~1"
set "COMPANY_CONFIG=%~dp0config\companies\%COMPANY_KEY%.json"
if not exist "%COMPANY_CONFIG%" (
  echo Company config not found: %COMPANY_CONFIG%
  exit /b 2
)

echo This will upload ONE receipt using company config: %COMPANY_KEY%
set /p "CONFIRM_KEY=Type the company key to continue: "
if /i not "%CONFIRM_KEY%"=="%COMPANY_KEY%" (
  echo Cancelled.
  exit /b 2
)

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%~dp0prepare_company_workspace.py" --config "%COMPANY_CONFIG%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" "%~dp0run_companies.py" --jobs-config "%COMPANY_CONFIG%" --accountbook "%COMPANY_KEY%" --mode confirm --limit 1 --allow-cross-entity-confirm
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:usage
echo Usage: confirm_one.bat COMPANY_KEY
echo Example: confirm_one.bat xinghai
exit /b 2
