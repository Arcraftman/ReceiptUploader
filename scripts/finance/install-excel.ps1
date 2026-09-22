param(
    [string]$Template = (Join-Path $PSScriptRoot '../../excel/finance-template.xlsx'),
    [string]$Output = (Join-Path $PSScriptRoot '../../excel/finance.xlsm')
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
    $module.CodeModule.AddFromString([System.IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../excel/FinanceRefresh.bas'), [System.Text.Encoding]::UTF8))
    $sheet = $book.Worksheets.Item(1)
    $loginButton = $sheet.Buttons().Add(510, 55, 140, 32)
    $loginButton.Caption = [string]([char]0x767B) + [char]0x5F55 + [char]0x8D26 + [char]0x65E0 + [char]0x5FE7
    $loginButton.OnAction = 'LoginFinance'
    $refreshButton = $sheet.Buttons().Add(660, 55, 140, 32)
    $refreshButton.Caption = [string]([char]0x5237) + [char]0x65B0 + [char]0x8D22 + [char]0x52A1 + [char]0x6570 + [char]0x636E
    $refreshButton.OnAction = 'RefreshFinance'
    $dashboard = $book.Worksheets.Item('财务分析看板')
    $areas = @('A29:G34','J29:P34','A51:G56','J51:P56','A73:G78','J73:P78')
    for ($index=0; $index -lt 6; $index++) {
        $area = $dashboard.Range($areas[$index])
        $box = $dashboard.Shapes.AddTextbox(1,$area.Left,$area.Top,$area.Width,$area.Height-4)
        $box.Name = 'DeepSeekInterpretation' + ($index+1)
        $box.DrawingObject.Formula = "='财务分析看板'!`$AJ`$" + ($index+2)
        $box.TextFrame2.WordWrap = -1
        $box.TextFrame2.AutoSize = 2
        $box.TextFrame2.TextRange.Font.Size = 11
        $box.TextFrame2.MarginLeft = 12
        $box.TextFrame2.MarginRight = 12
        $box.TextFrame2.MarginTop = 10
        $box.Fill.ForeColor.RGB = 16777215
        $box.Line.ForeColor.RGB = 15128538
    }
    $aiButton = $dashboard.Buttons().Add($dashboard.Range('A12').Left,$dashboard.Range('A12').Top,150,26)
    $aiButton.Caption = '生成图表解读'
    $aiButton.OnAction = 'RefreshDashboardInterpretations'
    $keyButton = $dashboard.Buttons().Add($dashboard.Range('D12').Left,$dashboard.Range('D12').Top,150,26)
    $keyButton.Caption = '配置 DeepSeek'
    $keyButton.OnAction = 'ConfigureDeepSeek'
    $book.SaveAs($outputPath, 52)
    Write-Host "Created $outputPath"
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
