param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\kdzwy.json'),
    [string]$AccountbooksPath = (Join-Path $PSScriptRoot 'config\accountbooks.json'),
    [string]$CompanyConfigDirectory = (Join-Path $PSScriptRoot 'config\companies')
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

function Write-JsonUtf8 {
    param($Value, [string]$Path, [int]$Depth = 12)

    $json = $Value | ConvertTo-Json -Depth $Depth
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($Path), $json + [Environment]::NewLine, $encoding)
}

function Set-ObjectProperty {
    param($Object, [string]$Name, $Value)

    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Get-SelectedIndexes {
    param([string]$Text, [int]$Maximum)

    $indexes = New-Object 'Collections.Generic.HashSet[int]'
    $answer = $Text.Trim()
    if ($answer -in @('a', 'A', 'all', 'ALL', '全部')) {
        1..$Maximum | ForEach-Object { [void]$indexes.Add($_) }
        return @($indexes | Sort-Object)
    }
    foreach ($part in ($answer -split '[,，;；\s]+')) {
        if (-not $part) { continue }
        if ($part -match '^(\d+)[-~～](\d+)$') {
            $first = [int]$Matches[1]
            $last = [int]$Matches[2]
            if ($first -gt $last) { throw "选择范围无效：$part" }
            $first..$last | ForEach-Object { [void]$indexes.Add($_) }
        }
        elseif ($part -match '^\d+$') {
            [void]$indexes.Add([int]$part)
        }
        else {
            throw "无法识别的选择：$part"
        }
    }
    foreach ($index in $indexes) {
        if ($index -lt 1 -or $index -gt $Maximum) { throw "公司编号超出范围：$index" }
    }
    return @($indexes | Sort-Object)
}

function Copy-JsonObject {
    param($Value)
    return ($Value | ConvertTo-Json -Depth 12 | ConvertFrom-Json)
}

Write-Host '正在登录并获取当前账号可访问的公司...'
$discoveryText = (& (Join-Path $PSScriptRoot 'discover_companies.ps1') -ConfigPath $ConfigPath | Out-String)
if (-not $discoveryText.Trim()) { throw '获取公司列表失败：接口没有返回内容。' }
$discovery = $discoveryText | ConvertFrom-Json
$companies = @($discovery.companies)
if ($companies.Count -eq 0) { throw '当前账号没有返回可访问的公司。' }

Write-Host ''
Write-Host "共找到 $($companies.Count) 家公司："
for ($i = 0; $i -lt $companies.Count; $i++) {
    Write-Host ('  [{0}] {1}' -f ($i + 1), $companies[$i].company_name)
}
Write-Host ''
$answer = Read-Host '请选择公司编号（例如 1,3-5；输入 A 选择全部）'
$indexes = @(Get-SelectedIndexes -Text $answer -Maximum $companies.Count)
if ($indexes.Count -eq 0) { throw '没有选择任何公司。' }
$selectedCompanies = @($indexes | ForEach-Object { $companies[$_ - 1] })

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.PSObject.Properties['company_name']) {
    $config.PSObject.Properties.Remove('company_name')
}
Set-ObjectProperty -Object $config -Name 'company_names' -Value @($selectedCompanies | ForEach-Object company_name)
Write-JsonUtf8 -Value $config -Path $ConfigPath

$accountbooksConfig = Get-Content -LiteralPath $AccountbooksPath -Raw | ConvertFrom-Json
$existingAccountbooks = @($accountbooksConfig.accountbooks)
$selectedAccountbooks = @()
$selectedKeys = New-Object 'Collections.Generic.HashSet[string]'

foreach ($company in $selectedCompanies) {
    $name = [string]$company.company_name
    $accountbook = $existingAccountbooks | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if ($null -eq $accountbook) {
        $keyPart = ([string]$company.company_id -replace '[^a-zA-Z0-9_-]', '_').ToLowerInvariant()
        if (-not $keyPart) { throw "公司缺少有效 companyId：$name" }
        $accountbook = [pscustomobject][ordered]@{
            key = 'company_' + $keyPart
            name = $name
            enabled = $true
            session_file = '../http_sessions/companies/' + $name + '.accountbook.cookies.json'
            pipeline_overrides = [pscustomobject]@{}
        }
    }
    Set-ObjectProperty -Object $accountbook -Name 'enabled' -Value $true
    [void]$selectedKeys.Add([string]$accountbook.key)
    $selectedAccountbooks += $accountbook
}
foreach ($accountbook in $existingAccountbooks) {
    if (-not $selectedKeys.Contains([string]$accountbook.key)) {
        Set-ObjectProperty -Object $accountbook -Name 'enabled' -Value $false
        $selectedAccountbooks += $accountbook
    }
}
$accountbooksConfig.accountbooks = @($selectedAccountbooks)
Write-JsonUtf8 -Value $accountbooksConfig -Path $AccountbooksPath

[IO.Directory]::CreateDirectory($CompanyConfigDirectory) | Out-Null
foreach ($accountbook in $selectedAccountbooks | Where-Object enabled) {
    $companyConfigPath = Join-Path $CompanyConfigDirectory ([string]$accountbook.key + '.json')
    if (-not (Test-Path -LiteralPath $companyConfigPath)) {
        $companyConfig = [pscustomobject][ordered]@{
            version = 1
            company_key = [string]$accountbook.key
            enabled = $false
            dataset = ''
            template_company = ''
            month = ''
            defaults = [pscustomobject][ordered]@{
                mode = 'analysis-only'
                analysis_stage = 'ocr'
                preload_items = $false
                purpose = 'production'
                allow_cross_entity = $false
            }
            sources = [pscustomobject][ordered]@{
                sales = [pscustomobject]@{ enabled = $false }
                purchase = [pscustomobject]@{ enabled = $false }
                bank = [pscustomobject]@{ enabled = $false }
                misc = [pscustomobject]@{ enabled = $false }
            }
        }
        Write-JsonUtf8 -Value $companyConfig -Path $companyConfigPath
    }
}

Write-Host ''
Write-Host '已保存选择，开始为所选公司建立 HTTP 会话...'
& (Join-Path $PSScriptRoot 'start_http_login.bat') --no-pause
if ($LASTEXITCODE -ne 0) { throw "HTTP 登录失败，退出码：$LASTEXITCODE" }

Write-Host ''
Write-Host '开始逐家公司运行已启用的公司配置...'
foreach ($accountbook in $selectedAccountbooks | Where-Object enabled) {
    $companyConfigPath = Join-Path $CompanyConfigDirectory ([string]$accountbook.key + '.json')
    $companyRunConfig = Get-Content -LiteralPath $companyConfigPath -Raw | ConvertFrom-Json
    if (-not $companyRunConfig.enabled) {
        Write-Host "跳过未配置公司：$($accountbook.name)。请先完善 $companyConfigPath"
        continue
    }
    Write-Host ''
    Write-Host ('===== {0}（{1}）=====' -f $accountbook.name, $accountbook.key)
    & (Join-Path $PSScriptRoot 'run_company.bat') ([string]$accountbook.key)
    if ($LASTEXITCODE -ne 0) {
        throw "公司运行失败：$($accountbook.name)，退出码：$LASTEXITCODE"
    }
}

Write-Host ''
Write-Host "全部完成：$($selectedCompanies.Count) 家公司。"
