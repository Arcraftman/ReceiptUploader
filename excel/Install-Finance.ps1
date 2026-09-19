# Compatibility entry point. Defaults are resolved by the shared installer.
& (Join-Path $PSScriptRoot '../scripts/finance/install-excel.ps1') @args
