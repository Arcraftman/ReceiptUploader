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

function Copy-JsonObject {
    param($Value)
    return ($Value | ConvertTo-Json -Depth 12 | ConvertFrom-Json)
}

function Get-LoginAccounts {
    param($Config)

    if ($Config.accounts) {
        $accounts = @($Config.accounts | Where-Object { $_.enabled -ne $false })
    }
    elseif ($Config.username -and $Config.password) {
        $accounts = @([pscustomobject][ordered]@{
            key = 'default'
            name = '默认账号'
            enabled = $true
            username = [string]$Config.username
            password = [string]$Config.password
        })
    }
    else {
        throw '配置缺少 accounts，或旧格式 username/password。'
    }
    foreach ($account in $accounts) {
        if (-not $account.key -or -not $account.username -or -not $account.password) {
            throw '每个启用账号都必须配置 key、username 和 password。'
        }
    }
    return @($accounts)
}

$loginConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$loginAccounts = @(Get-LoginAccounts -Config $loginConfig)
$selectedCompanies = @()

foreach ($loginAccount in $loginAccounts) {
    $accountLabel = if ($loginAccount.name) { [string]$loginAccount.name } else { [string]$loginAccount.key }
    Write-Host "正在登录账号 $accountLabel 并获取可访问的公司..."
    $discoveryText = (& (Join-Path $PSScriptRoot 'discover_companies.ps1') -ConfigPath $ConfigPath -AccountKey ([string]$loginAccount.key) | Out-String)
    if (-not $discoveryText.Trim()) { throw "账号 $accountLabel 获取公司列表失败：接口没有返回内容。" }
    $discovery = $discoveryText | ConvertFrom-Json
    $companies = @($discovery.companies)
    if ($companies.Count -eq 0) {
        Write-Host "账号 $accountLabel 没有返回可访问的公司，已跳过。"
        continue
    }

    Write-Host ''
    Write-Host "账号 $accountLabel 共找到 $($companies.Count) 家公司，全部自动启用："
    for ($i = 0; $i -lt $companies.Count; $i++) {
        Write-Host ('  [{0}] {1}' -f ($i + 1), $companies[$i].company_name)
    }
    foreach ($discoveredCompany in $companies) {
        $company = Copy-JsonObject $discoveredCompany
        Set-ObjectProperty -Object $company -Name 'login_account' -Value ([string]$loginAccount.key)
        $selectedCompanies += $company
    }
    Write-Host ''
}
if ($selectedCompanies.Count -eq 0) { throw '所有启用账号均未选择公司。' }

$accountbooksConfig = Get-Content -LiteralPath $AccountbooksPath -Raw | ConvertFrom-Json
$existingAccountbooks = @($accountbooksConfig.accountbooks)
$selectedAccountbooks = @()
$selectedKeys = New-Object 'Collections.Generic.HashSet[string]'

foreach ($company in $selectedCompanies) {
    $name = [string]$company.company_name
    $loginAccountKey = [string]$company.login_account
    $accountbook = $existingAccountbooks | Where-Object {
        $_.name -eq $name -and (([string]$_.login_account -eq $loginAccountKey) -or (-not $_.login_account -and $loginAccounts.Count -eq 1))
    } | Select-Object -First 1
    if ($null -eq $accountbook) {
        $keyPart = ([string]$company.company_id -replace '[^a-zA-Z0-9_-]', '_').ToLowerInvariant()
        if (-not $keyPart) { throw "公司缺少有效 companyId：$name" }
        $newKey = 'company_' + $keyPart
        if ($existingAccountbooks.key -contains $newKey -or $selectedAccountbooks.key -contains $newKey) {
            $newKey += '_' + ($loginAccountKey -replace '[^a-zA-Z0-9_-]', '_').ToLowerInvariant()
        }
        $accountbook = [pscustomobject][ordered]@{
            key = $newKey
            name = $name
            login_account = $loginAccountKey
            enabled = $true
            session_file = '../http_sessions/accounts/' + $loginAccountKey + '/companies/' + $name + '.accountbook.cookies.json'
            pipeline_overrides = [pscustomobject]@{}
        }
    }
    Set-ObjectProperty -Object $accountbook -Name 'login_account' -Value $loginAccountKey
    Set-ObjectProperty -Object $accountbook -Name 'session_file' -Value ('../http_sessions/accounts/' + $loginAccountKey + '/companies/' + $name + '.accountbook.cookies.json')
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
Write-Host '已保存全部账号和公司，开始为所有公司建立 HTTP 会话...'
& (Join-Path $PSScriptRoot 'start_http_login.bat') --no-pause
if ($LASTEXITCODE -ne 0) { throw "HTTP 登录失败，退出码：$LASTEXITCODE" }

Write-Host ''
Write-Host "公司发现和会话建立完成：$($loginAccounts.Count) 个启用账号，自动启用 $($selectedCompanies.Count) 家公司。"
Write-Host '本入口不会运行任何公司任务；请配置完成后单独执行 run_company.bat COMPANY_KEY。'
