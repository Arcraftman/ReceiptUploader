Option Explicit
Private Busy As Boolean

Public Sub RefreshFinance()
    Dim http As Object, stream As Object, re As Object
    Dim source As Workbook, home As Worksheet, ws As Worksheet
    Dim names As Variant, oldValues() As Variant, oldAddresses() As String
    Dim incoming() As Variant, i As Long, r As Long, c As Long
    Dim company As String, period As String, token As String, tokenPath As String, temp As String
    Dim oldCalc As XlCalculation, oldEvents As Boolean, oldScreen As Boolean, written As Boolean
    Dim errText As String, fileNo As Integer, values As Variant
    Dim report As Worksheet, reportBackup As Variant, reportWritten As Boolean
    If Busy Then Exit Sub
    Busy = True
    Set home = ThisWorkbook.Worksheets("控制台")
    oldCalc = Application.Calculation
    oldEvents = Application.EnableEvents
    oldScreen = Application.ScreenUpdating
    On Error GoTo Failed
    company = Trim(CStr(home.Range("B4").Value2))
    period = Trim(CStr(home.Range("B5").Value2))
    Set re = CreateObject("VBScript.RegExp")
    re.Pattern = "^company_[0-9]+$"
    If Not re.Test(company) Then Err.Raise 513, , "请选择公司编号"
    re.Pattern = "^[0-9]{4}-(0[1-9]|1[0-2])$"
    If Not re.Test(period) Then Err.Raise 513, , "月份格式必须为 YYYY-MM"
    Set report = ThisWorkbook.Worksheets(Left$(period, 4) & "年利润和负债")
    If CInt(Right$(period, 2)) > 9 Then Err.Raise 513, , "当前报表模板只包含1月至9月"
    tokenPath = Environ$("APPDATA") & "\KdzwyFinance\access.token"
    fileNo = FreeFile
    Open tokenPath For Input As #fileNo
    Line Input #fileNo, token
    Close #fileNo
    token = Trim(token)
    If Len(token) < 32 Then Err.Raise 513, , "本地访问令牌无效"
    home.Range("B7").Value2 = "正在读取；请等待完成"
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 10000, 10000, 30000, 300000
    http.Open "GET", "http://127.0.0.1:18765/snapshot?company=" & company & "&month=" & period, False
    http.SetRequestHeader "Authorization", "Bearer " & token
    http.Send
    If http.Status <> 200 Then Err.Raise 513, , "刷新失败 HTTP " & http.Status & ": " & Left(http.ResponseText, 500)
    If InStr(1, http.GetResponseHeader("Content-Type"), "application/xml") = 0 Then Err.Raise 513, , "返回内容不是财务数据"
    temp = Environ$("TEMP") & "\kdzwy_finance_" & Format(Now, "yyyymmdd_hhnnss") & "_" & CStr(Int(Timer * 100)) & ".xml"
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 1
    stream.Open
    stream.Write http.ResponseBody
    stream.SaveToFile temp, 2
    stream.Close
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Set source = Workbooks.Open(Filename:=temp, UpdateLinks:=0, ReadOnly:=True)
    With source.Worksheets("刷新信息")
        If CStr(.Range("B5").Value2) <> "1" Then Err.Raise 513, , "数据版本不符"
        If CStr(.Range("B6").Value2) <> company Or CStr(.Range("B8").Value2) <> period Then Err.Raise 513, , "公司或期间不符"
    End With
    names = Array("刷新信息", "公司列表", "利润表", "资产负债表", "现金流量表", "科目余额", "凭证明细", "出纳账", "往来余额", "月度趋势")
    If source.Worksheets.Count <> UBound(names) + 1 Then Err.Raise 513, , "数据表数量不符"
    ReDim oldValues(UBound(names))
    ReDim oldAddresses(UBound(names))
    ReDim incoming(UBound(names))
    ' Validate all sheets and stage all data before touching the current snapshot.
    For i = 0 To UBound(names)
        Set ws = ThisWorkbook.Worksheets(names(i))
        oldAddresses(i) = ws.UsedRange.Address
        oldValues(i) = ws.UsedRange.Value2
        values = source.Worksheets(names(i)).UsedRange.Value2
        If UBound(values, 1) > 6000 Then Err.Raise 513, , "数据超过模板容量 6000 行"
        For r = 1 To UBound(values, 1)
            For c = 1 To UBound(values, 2)
                If VarType(values(r, c)) = vbString Then values(r, c) = "'" & values(r, c)
            Next c
        Next r
        incoming(i) = values
    Next i
    source.Close SaveChanges:=False
    Set source = Nothing
    Application.Calculation = xlCalculationManual
    written = True
    For i = 0 To UBound(names)
        Set ws = ThisWorkbook.Worksheets(names(i))
        ws.UsedRange.ClearContents
        values = incoming(i)
        ws.Range("A1").Resize(UBound(values, 1), UBound(values, 2)).Value2 = values
        If names(i) = "凭证明细" Or names(i) = "出纳账" Then ws.Range("C5:C6000").NumberFormat = "yyyy-mm-dd"
    Next i
    reportBackup = report.Range("C5:AC48").Formula
    reportWritten = True
    UpdateProfitAndLiabilityReport report, company, period
    Application.CalculateFull
    home.Range("B7").Value2 = "刷新完成"
    home.Range("B8").NumberFormat = "@"
    home.Range("B8").Value2 = "'" & ThisWorkbook.Worksheets("刷新信息").Range("B9").Value2
    GoTo Cleanup
Failed:
    errText = Err.Description
    On Error Resume Next
    Close #fileNo
    If written Then
        For i = 0 To UBound(names)
            Set ws = ThisWorkbook.Worksheets(names(i))
            ws.UsedRange.ClearContents
            ws.Range(oldAddresses(i)).Value2 = oldValues(i)
        Next i
    End If
    If reportWritten Then report.Range("C5:AC48").Formula = reportBackup
    home.Range("B7").Value2 = "刷新失败，保留上次数据：" & errText
    MsgBox errText, vbExclamation, "账无忧财务刷新"
Cleanup:
    On Error Resume Next
    If Not source Is Nothing Then source.Close SaveChanges:=False
    If Len(temp) > 0 Then Kill temp
    Application.Calculation = oldCalc
    Application.EnableEvents = oldEvents
    Application.ScreenUpdating = oldScreen
    Busy = False
End Sub

Private Sub UpdateProfitAndLiabilityReport(ByVal report As Worksheet, ByVal company As String, ByVal period As String)
    Dim trend As Worksheet, balance As Worksheet, subjects As Worksheet
    Dim monthNumber As Long, monthIndex As Long, targetColumn As Long, itemIndex As Long
    Dim profitRows As Variant, profitCodes As Variant, revenueRows As Variant, revenueCodes As Variant
    Dim monthPeriod As String, value As Variant
    Set trend = ThisWorkbook.Worksheets("月度趋势")
    Set balance = ThisWorkbook.Worksheets("资产负债表")
    Set subjects = ThisWorkbook.Worksheets("科目余额")
    monthNumber = CInt(Right$(period, 2))

    If CleanImportedText(report.Range("AC1").Value2) <> company Or CleanImportedText(report.Range("AC2").Value2) <> Left$(period, 4) Then
        ClearProfitAndLiabilityInputs report
    End If

    profitRows = Array(5, 7, 10, 11, 12, 13, 15, 17)
    profitCodes = Array("04*001", "04*002", "04*014", "04*017", "04*018", "04*011", "04*030", "04*031")
    For monthIndex = 1 To monthNumber
        targetColumn = ReportMonthColumn(monthIndex)
        monthPeriod = Left$(period, 4) & "-" & Format$(monthIndex, "00")
        For itemIndex = LBound(profitRows) To UBound(profitRows)
            value = FindTrendValue(trend, monthPeriod, CStr(profitCodes(itemIndex)))
            If IsEmpty(value) And CLng(profitRows(itemIndex)) = 17 Then value = 0
            report.Cells(CLng(profitRows(itemIndex)), targetColumn).Value2 = value
        Next itemIndex
    Next monthIndex

    targetColumn = ReportMonthColumn(monthNumber)
    report.Cells(23, targetColumn).Value2 = FindBalanceValue(balance, "资产总计", True)
    report.Cells(25, targetColumn).Value2 = FindBalanceValue(balance, "应收账款", True)
    report.Cells(27, targetColumn).Value2 = FindBalanceValue(balance, "应付账款", False)
    report.Cells(29, targetColumn).Value2 = FindBalanceValue(balance, "负债合计", False)
    report.Cells(31, targetColumn).Value2 = FindBalanceValue(balance, "未分配利润", False)
    report.Cells(33, targetColumn).Value2 = FindBalanceValue(balance, "所有者权益（或股东权益）合计", False)

    revenueRows = Array(46, 47, 48)
    revenueCodes = Array("500101", "500102", "500103")
    For itemIndex = LBound(revenueRows) To UBound(revenueRows)
        report.Cells(CLng(revenueRows(itemIndex)), targetColumn).Value2 = FindSubjectCredit(subjects, CStr(revenueCodes(itemIndex)))
    Next itemIndex

    report.Range("AC1").Value2 = company
    report.Range("AC2").Value2 = Left$(period, 4)
    report.Range("AC3").Value2 = period
    report.Columns("AC").Hidden = True
End Sub

Private Sub ClearProfitAndLiabilityInputs(ByVal report As Worksheet)
    Dim columns As Variant, rows As Variant, columnIndex As Long, rowIndex As Long
    columns = Array(3, 5, 7, 11, 13, 15, 19, 21, 23)
    rows = Array(5, 7, 10, 11, 12, 13, 15, 17, 23, 25, 27, 29, 31, 33, 46, 47, 48)
    For columnIndex = LBound(columns) To UBound(columns)
        For rowIndex = LBound(rows) To UBound(rows)
            report.Cells(CLng(rows(rowIndex)), CLng(columns(columnIndex))).ClearContents
        Next rowIndex
    Next columnIndex
End Sub

Private Function ReportMonthColumn(ByVal monthNumber As Long) As Long
    Dim columns As Variant
    columns = Array(0, 3, 5, 7, 11, 13, 15, 19, 21, 23)
    If monthNumber < 1 Or monthNumber > 9 Then Err.Raise 513, , "当前报表模板只包含1月至9月"
    ReportMonthColumn = CLng(columns(monthNumber))
End Function

Private Function FindTrendValue(ByVal sheet As Worksheet, ByVal period As String, ByVal itemCode As String) As Variant
    Dim lastRow As Long, rowNumber As Long
    lastRow = sheet.Cells(sheet.Rows.Count, 1).End(xlUp).Row
    For rowNumber = 5 To lastRow
        If CleanImportedText(sheet.Cells(rowNumber, 1).Value2) = period And CleanImportedText(sheet.Cells(rowNumber, 2).Value2) = itemCode Then
            FindTrendValue = sheet.Cells(rowNumber, 4).Value2
            Exit Function
        End If
    Next rowNumber
    FindTrendValue = Empty
End Function

Private Function FindBalanceValue(ByVal sheet As Worksheet, ByVal itemName As String, ByVal assetSide As Boolean) As Variant
    Dim lastRow As Long, rowNumber As Long, labelColumn As Long, valueColumn As Long
    labelColumn = IIf(assetSide, 1, 4)
    valueColumn = IIf(assetSide, 2, 5)
    lastRow = sheet.Cells(sheet.Rows.Count, labelColumn).End(xlUp).Row
    For rowNumber = 5 To lastRow
        If Trim$(CleanImportedText(sheet.Cells(rowNumber, labelColumn).Value2)) = itemName Then
            FindBalanceValue = sheet.Cells(rowNumber, valueColumn).Value2
            Exit Function
        End If
    Next rowNumber
    FindBalanceValue = Empty
End Function

Private Function FindSubjectCredit(ByVal sheet As Worksheet, ByVal itemCode As String) As Variant
    Dim lastRow As Long, rowNumber As Long
    lastRow = sheet.Cells(sheet.Rows.Count, 1).End(xlUp).Row
    For rowNumber = 5 To lastRow
        If CleanImportedText(sheet.Cells(rowNumber, 1).Value2) = itemCode Then
            FindSubjectCredit = sheet.Cells(rowNumber, 6).Value2
            Exit Function
        End If
    Next rowNumber
    FindSubjectCredit = Empty
End Function

Private Function CleanImportedText(ByVal value As Variant) As String
    Dim text As String
    text = CStr(value)
    If Left$(text, 1) = "'" Then text = Mid$(text, 2)
    CleanImportedText = text
End Function
