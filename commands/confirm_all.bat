@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not "%~3"=="" goto :usage

set "COMPANY_CONFIG_NAME=%~1"
set "RUN_MONTH=%~2"
set "COMPANY_CONFIG=%PROJECT_ROOT%\config\companies\%COMPANY_CONFIG_NAME%.json"
if not exist "%COMPANY_CONFIG%" (
  echo Company config not found: %COMPANY_CONFIG%
  exit /b 2
)

echo WARNING: This will upload ALL valid receipts: %COMPANY_CONFIG_NAME% / %RUN_MONTH%.
set /p "CONFIRM_TEXT=Type UPLOAD ALL %COMPANY_CONFIG_NAME% %RUN_MONTH% to continue: "
if /i not "%CONFIRM_TEXT%"=="UPLOAD ALL %COMPANY_CONFIG_NAME% %RUN_MONTH%" (
  echo Cancelled.
  exit /b 2
)

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\prepare_company_workspace.py" --config "%COMPANY_CONFIG%" --month "%RUN_MONTH%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\run_companies.py" --jobs-config "%COMPANY_CONFIG%" --month "%RUN_MONTH%" --mode confirm --allow-confirm --allow-cross-entity-confirm
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:usage
echo Usage: commands\confirm_all.bat COMPANY_CONFIG_NAME YYYY-MM
echo Example: commands\confirm_all.bat company_20151038_星海公司 2026-08
exit /b 2
