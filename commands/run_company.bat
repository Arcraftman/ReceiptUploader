@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not "%~3"=="" goto :usage

set "COMPANY_CONFIG_NAME=%~1"
if /i "%~x1"==".json" set "COMPANY_CONFIG_NAME=%~n1"
set "RUN_MONTH=%~2"
set "COMPANY_CONFIG=%PROJECT_ROOT%\config\companies\%COMPANY_CONFIG_NAME%.json"

if not exist "%COMPANY_CONFIG%" (
  echo Company config not found: %COMPANY_CONFIG%
  exit /b 2
)

set "PYTHON_EXE=%PROJECT_ROOT%\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\prepare_company_workspace.py" --config "%COMPANY_CONFIG%" --month "%RUN_MONTH%"
if errorlevel 1 exit /b %ERRORLEVEL%

set "PROJECT_CONFIG=%PROJECT_ROOT%\data\inbox\%COMPANY_CONFIG_NAME%\%RUN_MONTH%\project.json"
call "%PROJECT_ROOT%\commands\login_companies.bat" --project-config "%PROJECT_CONFIG%" --no-pause
if errorlevel 1 exit /b %ERRORLEVEL%

"%PYTHON_EXE%" "%PROJECT_ROOT%\scripts\commands\run_companies.py" --jobs-config "%COMPANY_CONFIG%" --month "%RUN_MONTH%"
exit /b %ERRORLEVEL%

:usage
echo Usage: commands\run_company.bat COMPANY_CONFIG_NAME YYYY-MM
echo Example: commands\run_company.bat company_17867515_上海微誉信息技术有限公司 2026-08
exit /b 2
