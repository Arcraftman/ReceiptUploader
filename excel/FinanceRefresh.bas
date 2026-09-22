Option Explicit
Private Busy As Boolean

Public Sub LoginFinance()
    Dim rootPath As String, scriptPath As String, command As String
    On Error GoTo Failed
    rootPath = WorkbookProjectRoot()
    If Len(Dir$(rootPath & "\app\KdzwyFinance.exe")) > 0 Then
        CreateObject("WScript.Shell").Run QuoteArgument(rootPath & "\app\KdzwyFinance.exe") & " login", 1, False
        Exit Sub
    End If
    scriptPath = rootPath & "\scripts\finance\start-local.ps1"
    If Len(Dir$(scriptPath)) = 0 Then Err.Raise 513, , "本地登录脚本不存在：" & scriptPath
    command = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File " & QuoteArgument(scriptPath)
    CreateObject("WScript.Shell").Run command, 1, False
    ThisWorkbook.Worksheets("控制台").Range("B7").Value2 = "登录窗口已打开；完成后再点击刷新财务数据"
    Exit Sub
Failed:
    MsgBox Err.Description, vbExclamation, "账无忧登录"
End Sub

Public Sub RefreshFinance()
    Dim http As Object, stream As Object, re As Object
    Dim source As Workbook, home As Worksheet, ws As Worksheet
    Dim names As Variant, oldValues() As Variant, oldAddresses() As String
    Dim incoming() As Variant, i As Long, r As Long, c As Long
    Dim company As String, period As String, token As String, tokenPath As String, temp As String
    Dim oldCalc As XlCalculation, oldEvents As Boolean, oldScreen As Boolean, written As Boolean
    Dim errText As String, fileNo As Integer, values As Variant
    Dim report As Worksheet, reportBackup As Variant, reportIdentityBackup As Variant, reportWritten As Boolean
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
    tokenPath = ResolveTokenPath()
    If Len(tokenPath) = 0 Then Err.Raise 513, , "尚未登录或本地财务服务未启动，请先点击“登录账无忧”"
    fileNo = FreeFile
    Open tokenPath For Input As #fileNo
    Line Input #fileNo, token
    Close #fileNo
    token = Trim(token)
    If Len(token) < 32 Then Err.Raise 513, , "本地访问令牌无效"
    home.Range("B7").Value2 = "正在读取；请等待完成"
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 10000, 10000, 30000, 300000
    Dim servicePort As String
    servicePort = "18765"
    If Len(Dir$(WorkbookProjectRoot() & "\app\KdzwyFinance.exe")) > 0 Then servicePort = "18767"
    http.Open "GET", "http://127.0.0.1:" & servicePort & "/snapshot?company=" & company & "&month=" & period, False
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
        If CStr(.Range("B5").Value2) <> "2" Then Err.Raise 513, , "数据版本不符"
        If CStr(.Range("B6").Value2) <> company Or CStr(.Range("B8").Value2) <> period Then Err.Raise 513, , "公司或期间不符"
    End With
    names = Array("刷新信息", "公司列表", "利润表", "资产负债表", "现金流量表", "科目余额", "凭证明细", "出纳账", "往来余额", "月度趋势", "年度分析数据")
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
        If names(i) = "年度分析数据" Then
            If UBound(values, 1) > 20000 Then Err.Raise 513, , "年度数据超过模板容量 20000 行"
        Else
            If UBound(values, 1) > 6000 Then Err.Raise 513, , "数据超过模板容量 6000 行"
        End If
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
    reportBackup = report.Range("C5:AK48").Formula
    reportIdentityBackup = report.Range("AK1:AK3").Value2
    reportWritten = True
    UpdateProfitAndLiabilityReport report, company, period
    Application.CalculateFull
    home.Range("B7").Value2 = "刷新完成"
    home.Range("B8").NumberFormat = "@"
    home.Range("B8").Value2 = "'" & ThisWorkbook.Worksheets("刷新信息").Range("B9").Value2
    RefreshDashboardInterpretations
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
    If reportWritten Then
        report.Range("C5:AK48").Formula = reportBackup
        report.Range("AK1:AK3").Value2 = reportIdentityBackup
    End If
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

Private Function ResolveTokenPath() As String
    Dim candidate As String
    If Len(Dir$(WorkbookProjectRoot() & "\app\KdzwyFinance.exe")) > 0 Then
        candidate = WorkbookProjectRoot() & "\runtime\finance\access.token"
        If Len(Dir$(candidate)) > 0 Then ResolveTokenPath = candidate
        Exit Function
    End If
    candidate = Environ$("APPDATA") & "\KdzwyFinance\access.token"
    If Len(Dir$(candidate)) > 0 Then
        ResolveTokenPath = candidate
        Exit Function
    End If
    candidate = WorkbookProjectRoot() & "\runtime\finance\access.token"
    If Len(Dir$(candidate)) > 0 Then ResolveTokenPath = candidate
End Function

Public Sub ConfigureDeepSeek()
    Dim executable As String
    executable = WorkbookProjectRoot() & "\app\KdzwyFinance.exe"
    If Len(Dir$(executable)) = 0 Then
        MsgBox "源码模式请设置 DEEPSEEK_API_KEY 环境变量后重启本机服务。", vbInformation
        Exit Sub
    End If
    CreateObject("WScript.Shell").Run QuoteArgument(executable) & " configure-deepseek", 1, False
End Sub

Public Sub RefreshDashboardInterpretations()
    Dim dashboard As Worksheet, home As Worksheet, meta As Worksheet
    Dim company As String, period As String, tokenPath As String, token As String, port As String
    Dim body As String, r As Long, c As Long, monthCount As Long, value As Variant, n As Integer
    Dim http As Object, document As Object, nodes As Object, texts(1 To 6) As String, i As Long
    On Error GoTo FailedInterpretation
    Set dashboard = ThisWorkbook.Worksheets("财务分析看板")
    Set home = ThisWorkbook.Worksheets("控制台")
    Set meta = ThisWorkbook.Worksheets("刷新信息")
    company = CStr(home.Range("B4").Value2)
    period = CStr(home.Range("B5").Value2)
    If Len(company) = 0 Or company <> CStr(meta.Range("B6").Value2) Or period <> CStr(meta.Range("B8").Value2) Then Exit Sub
    monthCount = CInt(Right$(period, 2))
    If monthCount < 1 Or monthCount > 12 Then Exit Sub
    dashboard.Range("AJ1").Value2 = company
    dashboard.Range("AK1").Value2 = period
    For i = 2 To 7
        dashboard.Range("AI" & i).Value2 = "正在通过 DeepSeek 生成本期图表解读……"
    Next i
    dashboard.Calculate
    tokenPath = ResolveTokenPath()
    If Len(tokenPath) = 0 Then Err.Raise 513, , "本机服务未启动，请先登录"
    n = FreeFile
    Open tokenPath For Input As #n
    Line Input #n, token
    Close #n
    body = "{""company"":" & JsonText(company) & ",""month"":" & JsonText(period) & ",""rows"":["
    For r = 83 To 82 + monthCount
        If r > 83 Then body = body & ","
        body = body & "["
        For c = 1 To 16
            If c > 1 Then body = body & ","
            value = dashboard.Cells(r, c).Value2
            If c = 1 Then
                body = body & JsonText(CStr(value))
            ElseIf IsError(value) Then
                body = body & "null"
            ElseIf Len(CStr(value)) = 0 Then
                body = body & "null"
            ElseIf IsNumeric(value) Then
                body = body & Replace(CStr(value), Application.International(xlDecimalSeparator), ".")
            Else
                body = body & "null"
            End If
        Next c
        body = body & "]"
    Next r
    body = body & "]}"
    port = "18765"
    If Len(Dir$(WorkbookProjectRoot() & "\app\KdzwyFinance.exe")) > 0 Then port = "18767"
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 10000, 10000, 15000, 120000
    http.Open "POST", "http://127.0.0.1:" & port & "/interpretations", False
    http.SetRequestHeader "Authorization", "Bearer " & Trim$(token)
    http.SetRequestHeader "Content-Type", "application/json; charset=utf-8"
    http.Send body
    If http.Status <> 200 Then Err.Raise 513, , Left$(http.ResponseText, 300)
    Set document = CreateObject("MSXML2.DOMDocument.6.0")
    document.async = False
    document.resolveExternals = False
    document.setProperty "ProhibitDTD", True
    If Not document.LoadXML(http.ResponseText) Then Err.Raise 513, , "解读返回格式无效"
    If document.documentElement.getAttribute("company") <> company Or document.documentElement.getAttribute("month") <> period Then Err.Raise 513, , "解读公司或月份不符"
    Set nodes = document.SelectNodes("/interpretations/text")
    If nodes.Length <> 6 Then Err.Raise 513, , "解读数量不完整"
    For i = 1 To 6
        If nodes.Item(i - 1).getAttribute("id") <> CStr(i) Then Err.Raise 513, , "解读顺序无效"
        texts(i) = nodes.Item(i - 1).Text
        If Len(texts(i)) = 0 Then Err.Raise 513, , "解读内容为空"
    Next i
    If CStr(home.Range("B4").Value2) <> company Or CStr(home.Range("B5").Value2) <> period Then Exit Sub
    For i = 1 To 6
        dashboard.Range("AI" & i + 1).Value2 = "'DeepSeek 解读（" & period & "）" & vbLf & texts(i)
    Next i
    dashboard.Calculate
    Exit Sub
FailedInterpretation:
    Dim message As String
    message = "DeepSeek 解读未生成：" & Err.Description & vbLf & "财务数据已保留；可配置密钥后点击“生成图表解读”重试。"
    On Error Resume Next
    Close #n
    For i = 2 To 7
        dashboard.Range("AI" & i).Value2 = "'" & message
    Next i
    dashboard.Calculate
End Sub

Private Function JsonText(ByVal value As String) As String
    value = Replace(value, "\", "\\")
    value = Replace(value, Chr$(34), "\" & Chr$(34))
    value = Replace(value, vbCr, "\r")
    value = Replace(value, vbLf, "\n")
    JsonText = Chr$(34) & value & Chr$(34)
End Function

Private Function WorkbookProjectRoot() As String
    Dim fso As Object
    Set fso = CreateObject("Scripting.FileSystemObject")
    WorkbookProjectRoot = fso.GetParentFolderName(ThisWorkbook.Path)
End Function

Private Function QuoteArgument(ByVal value As String) As String
    QuoteArgument = Chr$(34) & Replace(value, Chr$(34), Chr$(34) & Chr$(34)) & Chr$(34)
End Function

Private Sub UpdateProfitAndLiabilityReport(ByVal report As Worksheet, ByVal company As String, ByVal period As String)
    Dim annual As Worksheet
    Dim monthNumber As Long, monthIndex As Long, targetColumn As Long, itemIndex As Long
    Dim profitRows As Variant, profitCodes As Variant, revenueRows As Variant, revenueCodes As Variant
    Dim balanceRows As Variant, balanceTypes As Variant, balanceCodes As Variant
    Dim monthPeriod As String, value As Variant
    Set annual = ThisWorkbook.Worksheets("年度分析数据")
    monthNumber = CInt(Right$(period, 2))

    ' Always remove future-month values when moving from December back to an earlier month.
    ClearProfitAndLiabilityInputs report

    profitRows = Array(5, 7, 10, 11, 12, 13, 15, 17)
    profitCodes = Array("04*001", "04*002", "04*014", "04*017", "04*018", "04*011", "04*030", "04*031")
    balanceRows = Array(23, 25, 27, 29, 31, 33)
    balanceTypes = Array("balance_asset", "balance_asset", "balance_liability", "balance_liability", "balance_liability", "balance_liability")
    balanceCodes = Array("资产总计", "应收账款", "应付账款", "负债合计", "未分配利润", "所有者权益（或股东权益）合计")
    revenueRows = Array(46, 47, 48)
    revenueCodes = Array("500101", "500102", "500103")
    For monthIndex = 1 To monthNumber
        targetColumn = ReportMonthColumn(monthIndex)
        monthPeriod = Left$(period, 4) & "-" & Format$(monthIndex, "00")
        For itemIndex = LBound(profitRows) To UBound(profitRows)
            value = FindAnnualValue(annual, monthPeriod, "profit", CStr(profitCodes(itemIndex)))
            If IsEmpty(value) And CLng(profitRows(itemIndex)) = 17 Then value = 0
            report.Cells(CLng(profitRows(itemIndex)), targetColumn).Value2 = value
        Next itemIndex
        For itemIndex = LBound(balanceRows) To UBound(balanceRows)
            report.Cells(CLng(balanceRows(itemIndex)), targetColumn).Value2 = FindAnnualValue( _
                annual, monthPeriod, CStr(balanceTypes(itemIndex)), CStr(balanceCodes(itemIndex)))
        Next itemIndex
        For itemIndex = LBound(revenueRows) To UBound(revenueRows)
            report.Cells(CLng(revenueRows(itemIndex)), targetColumn).Value2 = FindAnnualValue( _
                annual, monthPeriod, "subject_credit", CStr(revenueCodes(itemIndex)))
        Next itemIndex
    Next monthIndex

    report.Range("AK1").Value2 = company
    report.Range("AK2").Value2 = Left$(period, 4)
    report.Range("AK3").Value2 = period
    report.Columns("AK").Hidden = True
End Sub

Private Sub ClearProfitAndLiabilityInputs(ByVal report As Worksheet)
    Dim columns As Variant, rows As Variant, columnIndex As Long, rowIndex As Long
    columns = Array(3, 5, 7, 11, 13, 15, 19, 21, 23, 27, 29, 31)
    rows = Array(5, 7, 10, 11, 12, 13, 15, 17, 23, 25, 27, 29, 31, 33, 46, 47, 48)
    For columnIndex = LBound(columns) To UBound(columns)
        For rowIndex = LBound(rows) To UBound(rows)
            report.Cells(CLng(rows(rowIndex)), CLng(columns(columnIndex))).ClearContents
        Next rowIndex
    Next columnIndex
End Sub

Private Function ReportMonthColumn(ByVal monthNumber As Long) As Long
    Dim columns As Variant
    columns = Array(0, 3, 5, 7, 11, 13, 15, 19, 21, 23, 27, 29, 31)
    If monthNumber < 1 Or monthNumber > 12 Then Err.Raise 513, , "月份必须在1月至12月之间"
    ReportMonthColumn = CLng(columns(monthNumber))
End Function

Private Function FindAnnualValue(ByVal sheet As Worksheet, ByVal period As String, ByVal dataType As String, ByVal itemCode As String) As Variant
    Dim lastRow As Long, rowNumber As Long
    lastRow = sheet.Cells(sheet.Rows.Count, 1).End(xlUp).Row
    For rowNumber = 5 To lastRow
        If CleanImportedText(sheet.Cells(rowNumber, 1).Value2) = period _
                And CleanImportedText(sheet.Cells(rowNumber, 2).Value2) = dataType _
                And CleanImportedText(sheet.Cells(rowNumber, 3).Value2) = itemCode Then
            FindAnnualValue = sheet.Cells(rowNumber, 5).Value2
            Exit Function
        End If
    Next rowNumber
    FindAnnualValue = Empty
End Function

Private Function CleanImportedText(ByVal value As Variant) As String
    Dim text As String
    text = CStr(value)
    If Left$(text, 1) = "'" Then text = Mid$(text, 2)
    CleanImportedText = text
End Function
