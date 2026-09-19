# ASCII-only launcher compatible with Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$PythonExe = $env:PYTHON_EXE
if (-not $PythonExe) {
    $PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path $PythonExe)) { $PythonExe = Join-Path $ProjectRoot '.auto/Scripts/python.exe' }
    if (-not (Test-Path $PythonExe)) { $PythonExe = 'python' }
}
$PreviousConsoleEncoding = [Console]::OutputEncoding
$PreviousOutputEncoding = $OutputEncoding
$PreviousUtf8 = $env:PYTHONUTF8
$PreviousIo = $env:PYTHONIOENCODING
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    & $PythonExe (Join-Path $ProjectRoot 'scripts/start.py') @args
    $Code = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $PreviousConsoleEncoding
    $OutputEncoding = $PreviousOutputEncoding
    $env:PYTHONUTF8 = $PreviousUtf8
    $env:PYTHONIOENCODING = $PreviousIo
}
exit $Code
