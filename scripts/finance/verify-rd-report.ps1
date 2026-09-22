param([string]$Workbook, [string]$Reference)
$ErrorActionPreference = 'Stop'
$excel = $null
$book = $null
$source = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($Workbook),0,$false)
    $source = $excel.Workbooks.Open([System.IO.Path]::GetFullPath($Reference),0,$true)
    $sheet = $book.Worksheets.Item('2026研发费用')
    $original = $source.Worksheets.Item('2026研发费用')
    $annual = $book.Worksheets.Item('年度分析数据')
    $homeSheet = $book.Worksheets.Item('控制台')
    $meta = $book.Worksheets.Item('刷新信息')
    $homeSheet.Range('B4').Value2 = 'company_21726397'
    $meta.Range('B6').Value2 = 'company_21726397'
    $homeSheet.Range('B5').Value2 = '2026-08'
    $meta.Range('B8').Value2 = '2026-08'
    $outputRow = 5
    $expected = @{}
    for ($row=2; $row -le 9; $row++) {
        $label = $original.Cells.Item($row,1).Value2
        $code = $sheet.Cells.Item($row,37).Value2
        $sum = 0.0
        for ($month=1; $month -le 8; $month++) {
            $amount = $original.Cells.Item($row,$month+1).Value2
            if ($null -eq $amount) { continue }
            $annual.Cells.Item($outputRow,1).Value2 = ('2026-{0:00}' -f $month)
            $annual.Cells.Item($outputRow,2).Value2 = 'subject_debit_leaf'
            $annual.Cells.Item($outputRow,3).Value2 = [string]$code
            $annual.Cells.Item($outputRow,4).Value2 = [string]$label
            $annual.Cells.Item($outputRow,5).Value2 = [double]$amount
            $sum += $amount
            $outputRow++
        }
        $expected[$label] = $sum
    }
    $excel.CalculateFull()
    for ($row=2; $row -le 9; $row++) {
        for ($col=2; $col -le 9; $col++) {
            $wanted = $original.Cells.Item($row,$col).Value2
            $actual = $sheet.Cells.Item($row,$col).Value2
            if ($null -eq $wanted) {
                if ($actual -ne '') { throw "Expected blank at $row,$col" }
            } elseif ([Math]::Abs($actual-$wanted) -gt 0.001) { throw "Reference mismatch at $row,$col" }
        }
    }
    $last = [double]::PositiveInfinity
    for ($row=2; $row -le 9; $row++) {
        $label = $sheet.Cells.Item($row,15).Value2
        $total = $sheet.Cells.Item($row,28).Value2
        if ([Math]::Abs($expected[$label]-$total) -gt 0.001) { throw 'Sorted row total mismatch' }
        if ($total -gt $last) { throw 'Sort is not descending' }
        $last = $total
        if ([Math]::Abs($sheet.Cells.Item($row,29).Value2 - $total/$sheet.Range('AB10').Value2) -gt 0.000001) { throw 'Share mismatch' }
        $leftRow = 0
        for ($r=2; $r -le 9; $r++) { if ($sheet.Cells.Item($r,1).Value2 -eq $label) { $leftRow = $r } }
        for ($month=1; $month -le 8; $month++) {
            if ($sheet.Cells.Item($row,15+$month).Value2 -ne $sheet.Cells.Item($leftRow,1+$month).Value2) { throw 'Sorted monthly values detached from label' }
        }
    }
    if ($sheet.Range('O2').Value2 -ne '研发人员工资') { throw 'Largest category not first' }
    if ([Math]::Abs($sheet.Range('AC10').Value2-1) -gt 0.000001) { throw 'Shares must sum to one' }
    $homeSheet.Range('B4').Value2 = 'company_other'
    $excel.CalculateFull()
    if ($sheet.Range('AB10').Value2 -ne '') { throw 'Stale R&D data visible' }
    $homeSheet.Range('B4').Value2 = 'company_21726397'
    $homeSheet.Range('B5').Value2 = '2026-03'
    $meta.Range('B8').Value2 = '2026-03'
    $excel.CalculateFull()
    if ($sheet.Range('E2').Value2 -ne '' -or $sheet.Range('S2').Value2 -ne '') { throw 'Months beyond selected period visible' }
    $homeSheet.Range('B5').Value2 = '2026-08'
    $meta.Range('B8').Value2 = '2026-08'
    $excel.CalculateFull()
    $savedAddress = $annual.UsedRange.Address()
    $savedValues = $annual.UsedRange.Value2
    $annual.Range('A5:E1000').ClearContents()
    # Equal totals, negative totals and missing categories must remain distinct.
    $caseAmounts = @(100,100,0,-5)
    for ($r=2; $r -le 5; $r++) {
        $annual.Cells.Item($r+3,1).Value2 = '2026-01'
        $annual.Cells.Item($r+3,2).Value2 = 'subject_debit_leaf'
        $annual.Cells.Item($r+3,3).Value2 = [string]$sheet.Cells.Item($r,37).Value2
        $annual.Cells.Item($r+3,4).Value2 = [string]$sheet.Cells.Item($r,1).Value2
        $annual.Cells.Item($r+3,5).Value2 = [double]$caseAmounts[$r-2]
    }
    $excel.CalculateFull()
    if ($sheet.Range('AB2').Value2 -ne 100 -or $sheet.Range('AB3').Value2 -ne 100 -or $sheet.Range('AB4').Value2 -ne 0 -or $sheet.Range('AB5').Value2 -ne -5 -or $sheet.Range('AB6').Value2 -ne '') { throw 'Tie/negative/missing sort failed' }
    if ($sheet.Range('O2').Value2 -eq $sheet.Range('O3').Value2) { throw 'Equal totals duplicated a category' }
    $annual.Range('A5:E1000').ClearContents()
    for ($r=1; $r -le $savedValues.GetLength(0); $r++) {
        for ($c=1; $c -le $savedValues.GetLength(1); $c++) {
            $value = $savedValues[$r,$c]
            if ($value -is [string]) { $annual.Cells.Item($r,$c).Value2 = [string]$value }
            elseif ($null -ne $value) { $annual.Cells.Item($r,$c).Value2 = [double]$value }
        }
    }
    $excel.CalculateFull()
    $book.Activate()
    $sheet.Activate()
    $book.Save()
    $pdf = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetFullPath($Workbook),'.pdf')
    $sheet.ExportAsFixedFormat(0,$pdf)
    $original.PageSetup.PrintArea = 'A1:AA33'
    $original.PageSetup.Orientation = 2
    $original.PageSetup.PaperSize = 8
    $original.PageSetup.Zoom = $false
    $original.PageSetup.FitToPagesWide = 1
    $original.PageSetup.FitToPagesTall = 1
    $original.ExportAsFixedFormat(0,$pdf.Replace('.pdf','-reference.pdf'))
    Write-Output ('PASS: 64 monthly cells, descending whole-row sort, sums, shares, selection gating. Total: ' + $sheet.Range('AB10').Value2)
} finally {
    if ($null -ne $source) { $source.Close($false) }
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $excel) { $excel.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
}
