@echo off
if defined PYTHON_EXE ("%PYTHON_EXE%" "%~dp0..\scripts\finance\serve.py" %*) else (python "%~dp0..\scripts\finance\serve.py" %*)
