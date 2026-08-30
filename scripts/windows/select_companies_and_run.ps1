param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\..\config\kdzwy.json'),
    [string]$AccountbooksPath = (Join-Path $PSScriptRoot '..\..\runtime\registry\accountbooks.json'),
    [string]$CompanyConfigDirectory = (Join-Path $PSScriptRoot '..\..\config\companies'),
    [string]$CompanySelector = '',
    [string]$Month = '',
    [string]$TargetSelector = '',
    [switch]$Quiet
)

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
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

function Get-CompanyConfigFileName {
    param([string]$CompanyId, [string]$CompanyName)

    $normalizedId = $CompanyId.Trim()
    if ($normalizedId -notmatch '^[A-Za-z0-9_-]+$') {
        throw "company_id 不能生成有效文件名：$CompanyId"
    }
    $safeName = ($CompanyName -replace '[<>:"/\\|?*]+', '_').Trim(' ', '.')
    if (-not $safeName) { throw "真实公司名不能生成有效文件名：$CompanyName" }
    return 'company_' + $normalizedId + '_' + $safeName + '.json'
}

function Get-LoginAccounts {
    param($Config)

    if (-not $Config.accounts) { throw '配置缺少 accounts。' }
    $accounts = @($Config.accounts | Where-Object { $_.enabled -ne $false })
    foreach ($account in $accounts) {
        if (-not $account.key -or -not $account.username -or -not $account.password) {
            throw '每个启用账号都必须配置 key、username 和 password。'
        }
    }
    return @($accounts)
}

function Get-PythonExecutable {
    $localPython = Join-Path $ProjectRoot '.auto\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { return [IO.Path]::GetFullPath($localPython) }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $pythonCommand) { throw '找不到 Python；请先安装依赖环境。' }
    return [string]$pythonCommand.Source
}

function Resolve-MonthInitializationChoice {
    param([object[]]$Choices, [string]$Selector)

    $normalized = $Selector.Trim()
    $configName = if ($normalized.EndsWith('.json', [StringComparison]::OrdinalIgnoreCase)) {
        $normalized
    }
    else {
        $normalized + '.json'
    }
    $matches = @($Choices | Where-Object {
        $_.CompanyId -eq $normalized -or
        $_.CompanyKey -eq $normalized -or
        $_.CompanyName -eq $normalized -or
        $_.ConfigName -eq $configName
    })
    if ($matches.Count -eq 1) { return $matches[0] }

    $available = ($Choices | ForEach-Object { "$($_.CompanyKey)=$($_.CompanyName)" }) -join '；'
    throw "无法唯一匹配 --dataset/--target：$Selector，matches=$($matches.Count)。可用公司：$available"
}

function Invoke-SpecifiedMonthInitialization {
    param([object[]]$Choices)

    $selector = $CompanySelector.Trim()
    $requestedMonth = $Month.Trim()
    $requestedTarget = $TargetSelector.Trim()
    if (-not $selector -and -not $requestedMonth -and -not $requestedTarget) {
        if (-not $Quiet) { Write-Host '未指定 --dataset、--month 和 --target，仅完成公司发现、登记与登录。' }
        return
    }
    if (-not $selector -or -not $requestedMonth -or -not $requestedTarget) {
        throw '--dataset、--month 和 --target 必须同时提供。'
    }
    if ($requestedMonth -notmatch '^\d{4}-(0[1-9]|1[0-2])$') {
        throw "--month 必须严格使用 YYYY-MM：$requestedMonth"
    }

    $choice = Resolve-MonthInitializationChoice -Choices $Choices -Selector $selector
    $targetChoice = Resolve-MonthInitializationChoice -Choices $Choices -Selector $requestedTarget
    $python = Get-PythonExecutable
    $initializer = Join-Path $ProjectRoot 'scripts\commands\initialize_company_month.py'
    $companyConfigPath = Join-Path $CompanyConfigDirectory $choice.ConfigName
    if (-not (Test-Path -LiteralPath $companyConfigPath)) {
        $templateRegistry = Get-Content -LiteralPath (Join-Path $ProjectRoot 'config\template_companies.json') -Raw | ConvertFrom-Json
        $baseTemplate = ([string]$templateRegistry.default_base_template).Trim().ToLowerInvariant()
        if (-not $baseTemplate) { throw '模板注册表缺少 default_base_template。' }
        & $python (Join-Path $ProjectRoot 'scripts\commands\create_company.py') --name $choice.CompanyName --base-template $baseTemplate
        if ($LASTEXITCODE -ne 0) { throw "公司配置初始化失败：$($choice.CompanyName)，退出码 $LASTEXITCODE" }
    }

    Write-Host ''
    Write-Host "正在创建指定月份目录：资料=$($choice.CompanyName) / $requestedMonth / 目标账套=$($targetChoice.CompanyName)"
    $arguments = @(
        $initializer,
        $choice.ConfigName,
        $requestedMonth,
        $targetChoice.CompanyKey
    )
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "月份目录初始化失败：$($choice.CompanyName) / $requestedMonth，退出码 $LASTEXITCODE"
    }
    Write-Host "月份目录初始化完成：资料=$($choice.CompanyName) / $requestedMonth / 目标账套=$($targetChoice.CompanyName)" -ForegroundColor Green
}

$loginConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$loginAccounts = @(Get-LoginAccounts -Config $loginConfig)
$selectedCompanies = @()

foreach ($loginAccount in $loginAccounts) {
    $accountLabel = if ($loginAccount.name) { [string]$loginAccount.name } else { [string]$loginAccount.key }
    if (-not $Quiet) { Write-Host "正在登录账号 $accountLabel 并获取可访问的公司..." }
    $discoveryText = (& (Join-Path $PSScriptRoot 'discover_companies.ps1') -ConfigPath $ConfigPath -AccountKey ([string]$loginAccount.key) | Out-String)
    if (-not $discoveryText.Trim()) { throw "账号 $accountLabel 获取公司列表失败：接口没有返回内容。" }
    $discovery = $discoveryText | ConvertFrom-Json
    $companies = @($discovery.companies)
    if ($companies.Count -eq 0) {
        Write-Host "账号 $accountLabel 没有返回可访问的公司，已跳过。"
        continue
    }

    if (-not $Quiet) {
        Write-Host ''
        Write-Host "账号 $accountLabel 共找到 $($companies.Count) 家公司，全部自动登记："
        for ($i = 0; $i -lt $companies.Count; $i++) {
            Write-Host ('  [{0}] {1}' -f ($i + 1), $companies[$i].company_name)
        }
    }
    foreach ($discoveredCompany in $companies) {
        $company = Copy-JsonObject $discoveredCompany
        Set-ObjectProperty -Object $company -Name 'login_account' -Value ([string]$loginAccount.key)
        $selectedCompanies += $company
    }
    if (-not $Quiet) { Write-Host '' }
}
if ($selectedCompanies.Count -eq 0) { throw '所有启用账号均未选择公司。' }

$selectedAccountbooks = @()
$selectedKeys = New-Object 'Collections.Generic.HashSet[string]'

foreach ($company in $selectedCompanies) {
    $name = [string]$company.company_name
    $loginAccountKey = [string]$company.login_account
    $keyPart = ([string]$company.company_id -replace '[^a-zA-Z0-9_-]', '_').ToLowerInvariant()
    if (-not $keyPart) { throw "公司缺少有效 companyId：$name" }
    $newKey = 'company_' + $keyPart
    if (-not $selectedKeys.Add($newKey)) { throw "发现重复 company_id：$($company.company_id)" }
    $accountbook = [pscustomobject][ordered]@{
        key = $newKey
        name = $name
        company_id = [string]$company.company_id
        login_account = $loginAccountKey
        enabled = $true
        session_file = 'http_sessions/accounts/' + $loginAccountKey + '/companies/' + $name + '.accountbook.cookies.json'
    }
    $selectedAccountbooks += $accountbook
}
$accountbooksConfig = [pscustomobject][ordered]@{
    version = 2
    accountbooks = @($selectedAccountbooks)
}
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($AccountbooksPath))) | Out-Null
Write-JsonUtf8 -Value $accountbooksConfig -Path $AccountbooksPath

$monthInitializationChoices = @(
    $selectedAccountbooks |
        Where-Object { $_.enabled -eq $true -and $_.company_id -and $_.name } |
        ForEach-Object {
            [pscustomobject]@{
                CompanyName = [string]$_.name
                CompanyId = [string]$_.company_id
                CompanyKey = [string]$_.key
                LoginAccount = [string]$_.login_account
                ConfigName = Get-CompanyConfigFileName -CompanyId ([string]$_.company_id) -CompanyName ([string]$_.name)
            }
        } |
        Sort-Object ConfigName -Unique
)

if (-not $Quiet) {
    Write-Host ''
    Write-Host '已保存全部账号和公司，开始为所有公司建立 HTTP 会话...'
    & (Join-Path $ProjectRoot 'commands\login_companies.bat') --no-pause
}
else {
    & (Join-Path $ProjectRoot 'commands\login_companies.bat') --no-pause | Out-Null
}
$loginExitCode = $LASTEXITCODE
if ($loginExitCode -ne 0) { throw "HTTP 登录失败，退出码：$loginExitCode" }

if (-not $Quiet) {
    Write-Host ''
    Write-Host "公司发现和会话建立完成：$($loginAccounts.Count) 个启用账号，自动登记 $($selectedCompanies.Count) 家公司。"
}
Invoke-SpecifiedMonthInitialization -Choices $monthInitializationChoices
if (-not $Quiet) {
    Write-Host '本入口不会运行任何公司任务；请配置完成后执行 commands\run_company.bat COMPANY_CONFIG_NAME YYYY-MM。'
}
