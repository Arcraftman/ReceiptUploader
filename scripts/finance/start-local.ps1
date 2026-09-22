# ASCII-only helper for Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$PythonExe = $env:PYTHON_EXE
if (-not $PythonExe) {
    $PythonExe = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe)) { $PythonExe = Join-Path $ProjectRoot '.auto/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $PythonExe)) { $PythonExe = 'python' }
}
$StartScript = Join-Path $ProjectRoot 'scripts/start.py'

function Test-LocalFinancePort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connection = $client.BeginConnect('127.0.0.1', 18765, $null, $null)
        if (-not $connection.AsyncWaitHandle.WaitOne(500, $false)) { return $false }
        $client.EndConnect($connection)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-LocalFinanceHealth([string]$TokenPath) {
    if (-not (Test-Path -LiteralPath $TokenPath)) { return $false }
    try {
        $Token = (Get-Content -LiteralPath $TokenPath -Raw).Trim()
        $Response = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/health' -Headers @{ Authorization = 'Bearer ' + $Token } -TimeoutSec 2
        return [string]$Response.schema -eq '2' -and $Response.readOnly -eq $true
    } catch {
        return $false
    }
}

function Stop-OwnedFinanceService {
    $PidFile = Join-Path $ProjectRoot 'runtime/finance/service.pid'
    if (-not (Test-Path -LiteralPath $PidFile)) {
        throw 'Port 18765 is occupied by an older or unrelated process. Close that finance service and click Login again.'
    }
    $ServicePid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidFile -Raw).Trim(), [ref]$ServicePid)) {
        throw 'The finance service PID file is invalid.'
    }
    $ProcessInfo = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $ServicePid) -ErrorAction SilentlyContinue
    if ($null -eq $ProcessInfo -or $ProcessInfo.CommandLine -notmatch 'finance\s+serve') {
        throw 'The recorded process is not the project finance service.'
    }
    Stop-Process -Id $ServicePid -Force
    for ($attempt = 0; $attempt -lt 20 -and (Test-LocalFinancePort); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
}

try {
    Write-Host 'Opening the authorized Kdzwy login flow...'
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'runtime/registry/accountbooks.json')) {
        & $PythonExe $StartScript login --headed
    } else {
        & $PythonExe $StartScript discover --headed
    }
    if ($LASTEXITCODE -ne 0) { throw "Login command failed with exit code $LASTEXITCODE." }

    $ProjectToken = Join-Path $ProjectRoot 'runtime/finance/access.token'
    if ((Test-LocalFinancePort) -and -not (Test-LocalFinanceHealth $ProjectToken)) {
        Stop-OwnedFinanceService
    }
    if (-not (Test-LocalFinanceHealth $ProjectToken)) {
        $arguments = '"' + $StartScript + '" finance serve'
        Start-Process -FilePath $PythonExe -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden
        for ($attempt = 0; $attempt -lt 40 -and -not (Test-LocalFinanceHealth $ProjectToken); $attempt++) {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not (Test-LocalFinanceHealth $ProjectToken)) { throw 'The compatible local finance service did not start on port 18765.' }

    for ($attempt = 0; $attempt -lt 20 -and -not (Test-Path -LiteralPath $ProjectToken); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $ProjectToken)) { throw 'The local finance access token was not created.' }
    $UserTokenDirectory = Join-Path $env:APPDATA 'KdzwyFinance'
    New-Item -ItemType Directory -Force -Path $UserTokenDirectory | Out-Null
    Copy-Item -LiteralPath $ProjectToken -Destination (Join-Path $UserTokenDirectory 'access.token') -Force
    Write-Host 'Login complete. The local read-only finance service is ready. Return to Excel and click Refresh Finance Data.'
} catch {
    Write-Host ('Local finance startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host 'Press Enter to close this window'
    exit 1
}
