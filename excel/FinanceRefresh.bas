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
