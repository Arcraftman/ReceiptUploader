param(
    [string]$Template = (Join-Path $PSScriptRoot 'finance-template.xlsx'),
    [string]$Output = (Join-Path $PSScriptRoot 'finance.xlsm')
)
$ErrorActionPreference = 'Stop'
$inputPath = (Resolve-Path $Template).Path
$outputPath = [System.IO.Path]::GetFullPath($Output)
if ([System.IO.Path]::GetExtension($outputPath) -ne '.xlsm') { throw 'Output must end in .xlsm' }
if (Test-Path $outputPath) { throw 'Output already exists; choose a new filename.' }
$excel = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $true
    $book = $excel.Workbooks.Open($inputPath, 0, $false)
    # Requires the user-controlled Excel setting "Trust access to the VBA project object model".
    # Never change Trust Center / registry settings automatically.
    $module = $book.VBProject.VBComponents.Add(1)
    $module.Name = 'FinanceRefresh'
    $module.CodeModule.AddFromString([System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'FinanceRefresh.bas'), [System.Text.Encoding]::UTF8))
    $sheet = $book.Worksheets.Item(1)
    $button = $sheet.Buttons().Add(510, 55, 140, 32)
    $button.Caption = [string]([char]0x5237) + [char]0x65B0 + [char]0x8D22 + [char]0x52A1 + [char]0x6570 + [char]0x636E
    $button.OnAction = 'RefreshFinance'
    $book.SaveAs($outputPath, 52)
    Write-Host "Created $outputPath"
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
