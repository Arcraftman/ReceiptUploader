param([string]$Workbook,[string]$ReferenceData)
$ErrorActionPreference = 'Stop'
$excel = $null
$book = $null
$source = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($Workbook),0,$false)
    $source = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($ReferenceData),0,$true)
    $module = $book.VBProject.VBComponents.Item('FinanceRefresh').CodeModule
    $code = $module.Lines(1,$module.CountOfLines)
    $code = $code.Replace('"18765"','"19337"').Replace('"18767"','"19337"')
    $code = $code.Replace('Environ$("APPDATA") & "\KdzwyFinance\access.token"','WorkbookProjectRoot() & "\runtime\finance\access.token"')
    $module.DeleteLines(1,$module.CountOfLines)
    $module.AddFromString($code)
    $dashboard = $book.Worksheets.Item('财务分析看板')
    $raw = $book.Worksheets.Item('月度趋势')
    $sourceRaw = $source.Worksheets.Item('月度趋势')
    for ($r=5; $r -le 68; $r++) {
        for ($c=1; $c -le 5; $c++) {
            $value = $sourceRaw.Cells.Item($r,$c).Value2
            if ($value -is [string]) { $raw.Cells.Item($r,$c).Value2 = [string]$value }
            elseif ($null -ne $value) { $raw.Cells.Item($r,$c).Value2 = [double]$value }
        }
    }
    $homeSheet = $book.Worksheets.Item('控制台')
    $meta = $book.Worksheets.Item('刷新信息')
    $homeSheet.Range('B4').Value2 = 'company_1'
    $meta.Range('B6').Value2 = 'company_1'
    $homeSheet.Range('B5').Value2 = '2026-08'
    $meta.Range('B8').Value2 = '2026-08'
    $annual = $book.Worksheets.Item('年度分析数据')
    $annual.Range('A5').Value2 = '2026-08'
    $annual.Range('B5').Value2 = 'subject_debit_leaf'
    $annual.Range('C5').Value2 = '43010103'
    $annual.Range('D5').Value2 = '研发人员工资'
    $annual.Range('E5').Value2 = [double]15000
    $report = $book.Worksheets.Item('2026年利润和负债')
    $report.Range('U5').Value2 = [double]100000
    $report.Range('U47').Value2 = [double]72000
    $excel.CalculateFull()
    $hightech = $book.Worksheets.Item('高新企业相关指标监测')
    if ([Math]::Abs($hightech.Range('E8').Value2-0.15) -gt 0.000001) { throw 'R&D ratio mismatch' }
    if ([Math]::Abs($hightech.Range('E9').Value2-0.72) -gt 0.000001) { throw 'Income ratio mismatch' }
    if ($book.Worksheets.Item('2026研发费用').Range('B7').Value2 -ne '研发人员工资') { throw 'R&D descending table mismatch' }
    foreach ($case in @(@(50000000,0.05),@(50000001,0.04),@(200000000,0.04),@(200000001,0.03))) {
        $report.Range('U5').Value2 = [double]$case[0]
        $excel.CalculateFull()
        if ([Math]::Abs($hightech.Range('D8').Value2-$case[1]) -gt 0.000001) { throw 'Threshold boundary mismatch' }
    }
    $report.Range('U5').Value2 = [double]100000
    $excel.CalculateFull()
    $excel.Run("'"+$book.Name+"'!RefreshDashboardInterpretations")
    for ($i=1; $i -le 6; $i++) {
        $box = $dashboard.Shapes.Item('DeepSeekInterpretation'+$i)
        $text = $box.TextFrame2.TextRange.Text
        if (-not $text.Contains('DeepSeek 解读') -or $text.Contains('未生成')) { throw "AI text box $i failed: $text" }
    }
    $homeSheet.Range('B4').Value2 = 'company_2'
    $excel.CalculateFull()
    if ($dashboard.Shapes.Item('DeepSeekInterpretation1').TextFrame2.TextRange.Text.Contains('2026-08')) { throw 'Stale AI text still visible' }
    if ($hightech.Range('E8').Value2 -ne '') { throw 'Stale hightech ratio still visible' }
    $homeSheet.Range('B4').Value2 = 'company_1'
    $excel.CalculateFull()
    $root = Split-Path (Split-Path ([System.IO.Path]::GetFullPath($Workbook)))
    [System.IO.File]::WriteAllText((Join-Path $root 'fail.flag'),'test')
    $excel.Run("'"+$book.Name+"'!RefreshDashboardInterpretations")
    if (-not $dashboard.Range('AI2').Value2.Contains('未生成')) { throw 'AI failure must be visible' }
    if ($dashboard.Range('A6').Value2 -ne 360000) { throw 'AI failure altered financial data' }
    Remove-Item -LiteralPath (Join-Path $root 'fail.flag')
    $excel.Run("'"+$book.Name+"'!RefreshDashboardInterpretations")
    $book.Save()
    $folder = Split-Path $root
    $dashboard.ExportAsFixedFormat(0,(Join-Path $folder 'dashboard-ai.pdf'))
    $hightech.ExportAsFixedFormat(0,(Join-Path $folder 'hightech.pdf'))
    $book.Worksheets.Item('2026研发费用').ExportAsFixedFormat(0,(Join-Path $folder 'rd-centered.pdf'))
    Write-Output 'PASS: VBA POST bridge, six cell-linked textboxes, real API text replay, stale suppression, nonfatal AI failure, hightech ratios and threshold boundaries.'
} finally {
    if ($null -ne $source) { $source.Close($false) }
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
