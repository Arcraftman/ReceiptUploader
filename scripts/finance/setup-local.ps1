param([switch]$CheckPython)
$ErrorActionPreference = 'Stop'
$FinanceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))

function Find-FinancePython {
    $Candidates = @($env:PYTHON_EXE, (Join-Path $FinanceRoot '.venv/Scripts/python.exe'))
    foreach ($Name in @('python.exe', 'python3.exe')) {
        $Candidates += @(Get-Command $Name -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    }
    # A launcher may have a stale default; validate the actual executables it lists.
    foreach ($Launcher in @(Get-Command py.exe -All -ErrorAction SilentlyContinue)) {
        try {
            $Listing = & $Launcher.Source -0p 2>$null
            foreach ($Line in $Listing) {
                if ([string]$Line -match '([A-Za-z]:\\.*\.exe)\s*$') { $Candidates += $Matches[1] }
            }
        } catch { }
    }
    foreach ($Pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-*\python.exe",
        "$env:ProgramFiles\Python*\python.exe",
        'C:\Python*\python.exe'
    )) {
        $Candidates += @(Get-Item -Path $Pattern -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    }
    foreach ($Candidate in @($Candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
        if ($Candidate -like '*\Microsoft\WindowsApps\*') { continue }
        try {
            $Probe = & $Candidate -I -c 'import sys, venv, ensurepip; print(310 if sys.version_info >= (3,10) else 0)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $Probe -contains '310') { return $Candidate }
        } catch { }
    }
    throw 'No working Python 3.10+ found. Install Python, or set PYTHON_EXE to the full path of a working python.exe, then retry.'
}

try {
    Push-Location $FinanceRoot
    $FinancePython = Find-FinancePython
    Write-Host ('Python: ' + $FinancePython)
    if ($CheckPython) { return }
    $VenvPython = Join-Path $FinanceRoot '.venv/Scripts/python.exe'
    if ($FinancePython -ne $VenvPython) {
        & $FinancePython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. Close programs using .venv and retry.' }
    }
    & ./.venv/Scripts/python.exe -m pip install '.[discovery]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & ./.venv/Scripts/python.exe -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed.' }
    if (-not (Test-Path 'config/kdzwy.json')) {
        Copy-Item -LiteralPath 'config/kdzwy.example.json' -Destination 'config/kdzwy.json'
    }
    Write-Host 'Ready. Enter your own credentials in config/kdzwy.json, then open excel/Finance.xlsm.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}
