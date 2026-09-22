param([string]$Workbook)
$ErrorActionPreference = 'Stop'
$excel = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($Workbook), 0, $false)
    $sheet = $book.Worksheets.Item('财务分析看板')
    $homeSheet = $book.Worksheets.Item('控制台')
    $meta = $book.Worksheets.Item('刷新信息')
    $raw = $book.Worksheets.Item('月度趋势')
    $homeSheet.Range('B4').Value2 = 'company_test'
    $meta.Range('B6').Value2 = 'company_test'
    $homeSheet.Range('B5').Value2 = '2026-08'
    $meta.Range('B8').Value2 = '2026-08'
    $labels = @('一、营业收入','减：营业成本','    销售费用','    管理费用','    财务费用','三、利润总额（亏损总额以负号填列）','四、净利润','其中：研发费用')
    $keys = @('营业收入','营业成本','','','','','净利润','')
    $row = 5
    for ($month=1; $month -le 8; $month++) {
        $values = @(($month*10000),($month*6000),500,1000,-100,($month*4000-1700),($month*3000-5000),400)
        for ($index=0; $index -lt 8; $index++) {
            $raw.Cells.Item($row,1).Value2 = ('2026-{0:00}' -f $month)
            $raw.Cells.Item($row,3).Value2 = $labels[$index]
            $raw.Cells.Item($row,4).Value2 = [double]$values[$index]
            $raw.Cells.Item($row,5).Value2 = $keys[$index]
            $row++
        }
    }
    $excel.CalculateFull()
    if ($sheet.Range('A6').Value2 -ne 360000) { throw 'YTD revenue mismatch' }
    if ($sheet.Range('E6').Value2 -ne 216000) { throw 'YTD cost mismatch' }
    if ($sheet.Range('I6').Value2 -ne 11200) { throw 'Expense aggregation/double counting failure' }
    if ($sheet.Range('M6').Value2 -ne 68000) { throw 'YTD net profit mismatch' }
    if ([Math]::Abs($sheet.Range('A10').Value2-0.4) -gt 0.00001) { throw 'Weighted margin mismatch' }
    if ($sheet.Range('J83').Value2 -ne -2000) { throw 'Negative profit lost' }
    if ($sheet.Range('A91').Value2 -ne '') { throw 'Month tail is not blank' }
    foreach ($chartObject in $sheet.ChartObjects()) {
        $points = $chartObject.Chart.SeriesCollection(1).Points().Count
        if ($points -ne 8) { throw "Chart has $points points, expected 8" }
    }
    $raw.Range('D5').Value2 = [double]0
    $excel.CalculateFull()
    if ($sheet.Range('K83').Value2 -ne '') { throw 'Zero revenue margin should be blank' }
    if ($sheet.Range('P84').Value2 -ne '') { throw 'Zero denominator growth should be blank' }
    $raw.Range('D5').Value2 = [double]10000
    $raw.Range('D7').ClearContents()
    $excel.CalculateFull()
    if ($sheet.Range('I6').Value2 -ne '') { throw 'Incomplete expenses must not show partial total' }
    $raw.Range('D7').Value2 = [double]500
    $homeSheet.Range('B4').Value2 = 'company_other'
    $excel.CalculateFull()
    if ($sheet.Range('A6').Value2 -ne '' -or $sheet.Range('B83').Value2 -ne '') { throw 'Stale data shown' }
    $homeSheet.Range('B4').Value2 = 'company_test'
    foreach ($month in @(1,3,8)) {
        $period = '2026-{0:00}' -f $month
        $homeSheet.Range('B5').Value2 = $period
        $meta.Range('B8').Value2 = $period
        $excel.CalculateFull()
        if ($sheet.ChartObjects(1).Chart.SeriesCollection(1).Points().Count -ne $month) { throw 'Dynamic month count failed' }
    }
    $sheet.Activate()
    $sheet.Range('A1').Select()
    $book.Save()
    $sheet.ExportAsFixedFormat(0, [System.IO.Path]::ChangeExtension([System.IO.Path]::GetFullPath($Workbook),'.pdf'))
    Write-Output 'PASS: totals, negative profit, missing inputs, zero revenue, stale selection, 1/3/8-month native chart ranges; PDF exported.'
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
