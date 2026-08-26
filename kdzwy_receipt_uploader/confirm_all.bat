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

echo WARNING: This will upload ALL valid receipts enabled by the company config.
set /p "CONFIRM_TEXT=Type UPLOAD ALL %COMPANY_KEY% to continue: "
if /i not "%CONFIRM_TEXT%"=="UPLOAD ALL %COMPANY_KEY%" (
  echo Cancelled.
  exit /b 2
)

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%~dp0prepare_company_workspace.py" --config "%COMPANY_CONFIG%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" "%~dp0run_companies.py" --jobs-config "%COMPANY_CONFIG%" --accountbook "%COMPANY_KEY%" --mode confirm --allow-cross-entity-confirm
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:usage
echo Usage: confirm_all.bat COMPANY_KEY
echo Example: confirm_all.bat xinghai
exit /b 2
