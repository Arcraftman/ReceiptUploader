@echo off
setlocal

if "%~1"=="" (
  echo Usage: run_company.bat ACCOUNTBOOK_KEY [MODE]
  exit /b 2
)

set "ACCOUNTBOOK_KEY=%~1"
set "RUN_MODE=%~2"
set "CONFIRM_FLAG=%~3"
set "COMPANY_CONFIG=%~dp0config\companies\%ACCOUNTBOOK_KEY%.json"

if not exist "%COMPANY_CONFIG%" (
  echo Company config not found: %COMPANY_CONFIG%
  exit /b 2
)

set "PYTHON_EXE=%~dp0..\.auto\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%~dp0prepare_company_workspace.py" --config "%COMPANY_CONFIG%"
if errorlevel 1 exit /b %ERRORLEVEL%

if "%RUN_MODE%"=="" (
  "%PYTHON_EXE%" "%~dp0run_companies.py" --jobs-config "%COMPANY_CONFIG%" --accountbook "%ACCOUNTBOOK_KEY%"
) else (
  if /i "%CONFIRM_FLAG%"=="--allow-cross-entity-confirm" (
    "%PYTHON_EXE%" "%~dp0run_companies.py" --jobs-config "%COMPANY_CONFIG%" --accountbook "%ACCOUNTBOOK_KEY%" --mode "%RUN_MODE%" --allow-cross-entity-confirm
  ) else (
    "%PYTHON_EXE%" "%~dp0run_companies.py" --jobs-config "%COMPANY_CONFIG%" --accountbook "%ACCOUNTBOOK_KEY%" --mode "%RUN_MODE%"
  )
)
exit /b %ERRORLEVEL%
