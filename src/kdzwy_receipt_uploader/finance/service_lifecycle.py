"""Replace only verified, current-user packaged finance services."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import time

from kdzwy_receipt_uploader.upload_journal import exclusive_lock


RETIRE_SERVICES = r'''
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$profile = [Environment]::GetFolderPath('UserProfile').TrimEnd('\') + '\'
Get-CimInstance Win32_Process -Filter "Name='KdzwyFinance.exe'" | ForEach-Object {
    $item = $_
    $exe = $item.ExecutablePath
    if (-not $exe -or -not $exe.StartsWith($profile,[StringComparison]::OrdinalIgnoreCase)) { return }
    if ($item.CommandLine -notmatch ('^"?' + [regex]::Escape($exe) + '"?\s+serve\s*$')) { return }
    if ((Invoke-CimMethod -InputObject $item -MethodName GetOwnerSid).Sid -ne $sid) { return }
    $root = Split-Path (Split-Path $exe)
    if (-not (Test-Path -LiteralPath (Join-Path $root 'config/finance_read_sources.json'))) { return }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'excel/Finance.xlsm'))) { return }
    $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return }
    if ($process.Path -ne $exe -or [Math]::Abs(($process.StartTime-$item.CreationDate).TotalSeconds) -gt 1) { return }
    Stop-Process -InputObject $process -Force
    if (-not $process.WaitForExit(5000)) { throw 'Previous finance service did not exit' }
}
'''


def retire_services():
    encoded = base64.b64encode(RETIRE_SERVICES.encode('utf-16le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)


def ensure_service(root, health):
    lock = Path(os.environ['LOCALAPPDATA']) / 'KdzwyFinance/service-start.lock'
    with exclusive_lock(lock):
        if health(root):
            return
        retire_services()
        folder = root / 'runtime/finance'
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / 'service-start.log'
        with log.open('ab') as output:
            child = subprocess.Popen([sys.executable, 'serve'], cwd=root,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(120):
                if child.poll() is not None:
                    raise RuntimeError(f'财务服务已退出（代码 {child.returncode}），启动日志：{log}')
                if health(root):
                    return
                time.sleep(.25)
            raise RuntimeError(f'财务服务启动超时；请检查端口18767是否被其他程序占用。启动日志：{log}')
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            raise
