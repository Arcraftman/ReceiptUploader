param([string]$Workbook)
$ErrorActionPreference = 'Stop'
$excel = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($Workbook),0,$false)
    $code = $book.VBProject.VBComponents.Item('FinanceRefresh').CodeModule
    # Test-only entry point exercises the production private update routine without a network request.
    $code.AddFromString("`nPublic Sub VerifyAnnualPeriod(ByVal period As String)`nUpdateProfitAndLiabilityReport ThisWorkbook.Worksheets(`"2026年利润和负债`"), `"company_1`", period`nEnd Sub")
    $annual = $book.Worksheets.Item('年度分析数据')
    $trend = $book.Worksheets.Item('月度趋势')
    $homeSheet = $book.Worksheets.Item('控制台')
    $meta = $book.Worksheets.Item('刷新信息')
    $report = $book.Worksheets.Item('2026年利润和负债')
    $rd = $book.Worksheets.Item('2026研发费用')
    $dash = $book.Worksheets.Item('财务分析看板')
    $homeSheet.Range('B4').Value2 = 'company_1'
    $meta.Range('B6').Value2 = 'company_1'
    $r = 5
    $t = 5
    for ($month=1; $month -le 12; $month++) {
        $period = '2026-{0:00}' -f $month
        $items = @(
            @('profit','04*001','一、营业收入',($month*1000)),
            @('profit','04*002','减：营业成本',($month*600)),
            @('balance_asset','资产总计','资产总计',($month*10000)),
            @('subject_debit_leaf','43010103','研发人员工资',($month*100)),
            @('subject_debit_leaf','43010101','差旅费',10)
        )
        foreach ($item in $items) {
            $annual.Cells.Item($r,1).Value2 = $period
            for ($c=0; $c -lt 3; $c++) { $annual.Cells.Item($r,$c+2).Value2 = [string]$item[$c] }
            $annual.Cells.Item($r,5).Value2 = [double]$item[3]
            $r++
        }
        $trend.Cells.Item($t,1).Value2 = $period
        $trend.Cells.Item($t,3).Value2 = '一、营业收入'
        $trend.Cells.Item($t,4).Value2 = [double]($month*1000)
        $trend.Cells.Item($t,5).Value2 = '营业收入'
        $t++
    }
    # December first catches stale future data when subsequently selecting earlier months.
    foreach ($month in @(12,1,2,3,4,5,6,7,8,9,10,11,12,3)) {
        $period = '2026-{0:00}' -f $month
        $homeSheet.Range('B5').Value2 = $period
        $meta.Range('B8').Value2 = $period
        $excel.Run("'" + $book.Name + "'!VerifyAnnualPeriod",$period)
        $excel.CalculateFull()
        $sum = $month*($month+1)/2
        if ($report.Range('AI5').Value2 -ne $sum*1000) { throw "Annual YTD mismatch $period" }
        if ($rd.Range('O15').Value2 -ne $sum*100+$month*10) { throw "RD YTD mismatch $period" }
        if ($rd.Range('B7').Value2 -ne '研发人员工资') { throw 'RD sorting mismatch' }
        if ($dash.Range('A6').Value2 -ne $sum*1000) { throw 'Dashboard YTD mismatch' }
        if ($dash.ChartObjects(1).Chart.SeriesCollection(1).Points().Count -ne $month) { throw 'Dashboard month range mismatch' }
        $columns = @(3,5,7,11,13,15,19,21,23,27,29,31)
        for ($m=1; $m -le 12; $m++) {
            $value = $report.Cells.Item(5,$columns[$m-1]).Value2
            if ($m -le $month) {
                if ($value -ne $m*1000) { throw "Annual month $m wrong" }
                if ($rd.Cells.Item(7,$m+2).Value2 -ne $m*100) { throw "RD month $m wrong" }
            } else {
                if ($null -ne $value -and $value -ne '') { throw "Stale annual month $m" }
                if ($rd.Cells.Item(7,$m+2).Value2 -ne '') { throw "Stale RD month $m" }
            }
        }
        if ($month -eq 12) {
            if ($report.Range('AG5').Value2 -ne 33000) { throw 'Fourth quarter flow must sum Oct-Dec' }
            if ($report.Range('AG23').Value2 -ne 120000) { throw 'Fourth quarter balance must use December balance' }
        }
    }
    # Save a full-year test preview, including the injected test helper, only in outputs.
    $homeSheet.Range('B5').Value2 = '2026-12'
    $meta.Range('B8').Value2 = '2026-12'
    $excel.Run("'" + $book.Name + "'!VerifyAnnualPeriod",'2026-12')
    $excel.CalculateFull()
    $book.Save()
    $folder = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Workbook))
    $rd.ExportAsFixedFormat(0,(Join-Path $folder 'rd-twelve.pdf'))
    $report.ExportAsFixedFormat(0,(Join-Path $folder 'annual-twelve.pdf'))
    Write-Output 'PASS: all 12 selections; 12 -> 3 clears future data; monthly amounts, Q4 sum, quarter-end balance, YTD, RD sort and chart length.'
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
