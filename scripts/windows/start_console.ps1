param([switch]$ValidateOnly)

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Set-Location -LiteralPath $ProjectRoot

$CommandsRoot = Join-Path $ProjectRoot 'commands'
$AccountbooksPath = Join-Path $ProjectRoot 'runtime\registry\accountbooks.json'
$CompanyConfigDirectory = Join-Path $ProjectRoot 'config\companies'
$TemplateRegistryPath = Join-Path $ProjectRoot 'config\template_companies.json'
$script:SessionsReady = $false

function Invoke-BatchCommand {
    param([string]$Path, [string[]]$Arguments = @())

    & $Path @Arguments | Out-Host
    return [int]$LASTEXITCODE
}

function Get-PythonExecutable {
    $localPython = Join-Path $ProjectRoot '.auto\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { return [IO.Path]::GetFullPath($localPython) }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $pythonCommand) { throw '找不到 Python；请先安装项目依赖。' }
    return [string]$pythonCommand.Source
}

function Read-JsonObject {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { throw "找不到 JSON：$Path" }
    $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($null -eq $value -or $value -is [Collections.IEnumerable] -and $value -isnot [Management.Automation.PSCustomObject]) {
        throw "JSON 顶层必须是对象：$Path"
    }
    return $value
}

function Get-DefaultBaseTemplate {
    $registry = Read-JsonObject -Path $TemplateRegistryPath
    $defaultKey = ([string]$registry.default_base_template).Trim().ToLowerInvariant()
    if (-not $defaultKey) { throw '模板注册表缺少 default_base_template。' }
    $matches = @($registry.template_companies | Where-Object {
        ([string]$_.key).Trim().ToLowerInvariant() -eq $defaultKey -and $_.enabled -ne $false
    })
    if ($matches.Count -ne 1) { throw "默认基础模板不存在或未启用：$defaultKey" }
    return $defaultKey
}

function Get-CompanyChoices {
    if (-not (Test-Path -LiteralPath $AccountbooksPath)) { return @() }
    $accountbooks = @(Read-JsonObject -Path $AccountbooksPath | Select-Object -ExpandProperty accountbooks)
    $configsByKey = @{}
    if (Test-Path -LiteralPath $CompanyConfigDirectory) {
        foreach ($path in Get-ChildItem -LiteralPath $CompanyConfigDirectory -File -Filter '*.json') {
            $config = Read-JsonObject -Path $path.FullName
            $key = ([string]$config.company_key).Trim()
            if (-not $key) { throw "公司配置缺少 company_key：$($path.FullName)" }
            if ($configsByKey.ContainsKey($key)) { throw "同一 company_key 存在多个配置：$key" }
            $configsByKey[$key] = [pscustomobject]@{ Path = $path; Payload = $config }
        }
    }

    $choices = @()
    foreach ($accountbook in $accountbooks | Where-Object { $_.enabled -ne $false } | Sort-Object company_id, name) {
        $key = ([string]$accountbook.key).Trim()
        $configRecord = $configsByKey[$key]
        $config = if ($null -ne $configRecord) { $configRecord.Payload } else { $null }
        $choices += [pscustomobject]@{
            CompanyKey = $key
            CompanyId = [string]$accountbook.company_id
            CompanyName = [string]$accountbook.name
            LoginAccount = [string]$accountbook.login_account
            ConfigName = if ($null -ne $configRecord) { $configRecord.Path.Name } else { '' }
            TemplateCompany = if ($null -ne $config) { [string]$config.template_company } else { '' }
        }
    }
    return @($choices)
}

function Show-Companies {
    $choices = @(Get-CompanyChoices)
    Write-Host ''
    Write-Host "可访问公司：$($choices.Count) 家；会话：$(if ($script:SessionsReady) { '已刷新' } else { '未确认' })"
    for ($i = 0; $i -lt $choices.Count; $i++) {
        $company = $choices[$i]
        Write-Host ('[{0}] {1} | {2}' -f ($i + 1), $company.CompanyId, $company.CompanyName)
    }
    if ($choices.Count -eq 0) { Write-Host '    没有可用公司。' -ForegroundColor Yellow }
    Write-Host '本列表可同时用于选择资料公司和目标账套公司；本列表不判断或复用历史月份状态。' -ForegroundColor DarkGray
}

function Resolve-CompanyChoice {
    param([string]$Selector)

    $normalized = $Selector.Trim()
    if (-not $normalized) { throw '公司选择不能为空。' }
    $configName = if ($normalized.EndsWith('.json', [StringComparison]::OrdinalIgnoreCase)) {
        $normalized
    }
    else {
        $normalized + '.json'
    }
    $matches = @(Get-CompanyChoices | Where-Object {
        $_.CompanyId -eq $normalized -or
        $_.CompanyKey -eq $normalized -or
        $_.CompanyName -eq $normalized -or
        $_.ConfigName -eq $configName
    })
    if ($matches.Count -ne 1) {
        throw "无法唯一匹配公司：$Selector，matches=$($matches.Count)。建议使用列表中的 company_id。"
    }
    return $matches[0]
}

function Invoke-HttpLogin {
    if (-not (Test-Path -LiteralPath $AccountbooksPath) -or @(Get-CompanyChoices).Count -eq 0) {
        Write-Host '尚无已登记账套，请先运行 discover。' -ForegroundColor Yellow
        $script:SessionsReady = $false
        return
    }
    Write-Host ''
    Write-Host '刷新已登记公司的 HTTP 会话...'
    $exitCode = Invoke-BatchCommand -Path (Join-Path $CommandsRoot 'login_companies.bat') -Arguments @('--no-pause')
    $script:SessionsReady = $exitCode -eq 0
    if (-not $script:SessionsReady) { Write-Host "HTTP 登录失败，退出码：$exitCode" -ForegroundColor Red }
}

function Invoke-Discovery {
    Write-Host ''
    Write-Host '正在同步公司和 HTTP 会话...'
    $script:SessionsReady = $false
    $exitCode = Invoke-BatchCommand -Path (Join-Path $CommandsRoot 'discover_companies.bat') -Arguments @('--no-pause', '--quiet')
    if ($exitCode -ne 0) { throw "公司发现失败，退出码：$exitCode" }
    $script:SessionsReady = $true
    Write-Host '公司和会话同步完成。' -ForegroundColor Green
}

function Invoke-MonthSpecification {
    param([string[]]$Tokens)

    if ($Tokens.Count -notin @(3, 4)) { throw '格式：month SOURCE_COMPANY_ID YYYY-MM [TARGET_COMPANY_ID]' }
    $choice = Resolve-CompanyChoice -Selector $Tokens[1]
    $month = $Tokens[2]
    $targetChoice = if ($Tokens.Count -eq 4) {
        Resolve-CompanyChoice -Selector $Tokens[3]
    }
    else {
        $choice
    }
    if ($month -notmatch '^\d{4}-(0[1-9]|1[0-2])$') { throw "月份必须严格使用 YYYY-MM：$month" }
    if (-not $choice.ConfigName -or -not $choice.TemplateCompany) {
        $defaultTemplate = Get-DefaultBaseTemplate
        Write-Host "准备本月所需公司配置：$($choice.CompanyName) / 基础模板=$defaultTemplate"
        $templateExitCode = Invoke-BatchCommand -Path (Join-Path $CommandsRoot 'create_company_template.bat') -Arguments @(
            '--name',
            $choice.CompanyName,
            '--base-template',
            $defaultTemplate
        )
        if ($templateExitCode -ne 0) { throw "公司配置初始化失败，退出码：$templateExitCode" }
        $choice = Resolve-CompanyChoice -Selector $Tokens[1]
    }

    Write-Host "创建月份项目：资料=$($choice.CompanyName) / $month / 目标账套=$($targetChoice.CompanyName)"
    $exitCode = Invoke-BatchCommand -Path (Join-Path $CommandsRoot 'initialize_month.bat') -Arguments @(
        $choice.ConfigName,
        $month,
        $targetChoice.CompanyKey
    )
    if ($exitCode -ne 0) { throw "月份初始化失败，退出码：$exitCode" }
    Write-Host '下一步：编辑该月 data/inbox/.../project.json 中的 mode、analysis_stage 和 sources，再运行 run_company.bat 公司配置名 月份。' -ForegroundColor Green
}

function Invoke-StatusView {
    $python = Get-PythonExecutable
    & $python (Join-Path $ProjectRoot 'scripts\commands\pipeline_status.py') | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "状态命令退出码：$LASTEXITCODE" -ForegroundColor Yellow }
}

function Show-Help {
    Write-Host ''
    Write-Host '命令：'
    Write-Host '  month 资料公司ID YYYY-MM [目标公司ID]  创建月份项目并显式写入目标账套'
    Write-Host '    同公司示例：month 17867515 2026-09'
    Write-Host '    指定目标：month 17867515 2026-09 20151038'
    Write-Host '  list                   查看可访问公司'
    Write-Host '  login                  刷新已有公司会话'
    Write-Host '  discover               重新发现公司并刷新会话'
    Write-Host '  status                 查看任务状态'
    Write-Host '  help / quit            帮助 / 退出'
    Write-Host ''
    Write-Host '安全边界：本菜单不提供 confirm、批量上传、上传状态重置或删除操作。' -ForegroundColor Yellow
}

function Show-QuickHelp {
    Write-Host ''
    Write-Host '下一步：month 资料公司ID YYYY-MM [目标公司ID]（省略目标时明确使用资料公司自己的账套）'
    Write-Host '其他命令：list | status | login | discover | help | quit'
}

function Split-MenuCommand {
    param([string]$Text)

    return @($Text.Trim() -split '\s+' | Where-Object { $_ })
}

if ($ValidateOnly) {
    $defaultTemplate = Get-DefaultBaseTemplate
    $validationChoices = @(Get-CompanyChoices)
    if ($validationChoices.Count -gt 0) {
        $resolvedChoice = Resolve-CompanyChoice -Selector $validationChoices[0].CompanyId
        if ($resolvedChoice.CompanyKey -ne $validationChoices[0].CompanyKey) {
            throw 'Company selector validation failed.'
        }
    }
    $sampleTokens = @(Split-MenuCommand -Text 'month 17867515 2026-09 20151038')
    if ($sampleTokens.Count -ne 4) { throw 'Menu command parser validation failed.' }
    $batchExit = Invoke-BatchCommand -Path (Join-Path $CommandsRoot 'discover_companies.bat') -Arguments @('--help')
    if ($batchExit -ne 0) { throw "Nested BAT exit-code validation failed: $batchExit" }
    Show-Companies
    Show-QuickHelp
    Write-Host "Unified console validation OK: $($validationChoices.Count) accessible companies loaded; month configuration ready."
    return
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\kdzwy.json'))) {
    throw '找不到 config/kdzwy.json；请先填写登录账号。'
}

Write-Host '=== 企业凭证项目统一启动器 ===' -ForegroundColor Cyan
Invoke-Discovery
Show-Companies
Show-QuickHelp

while ($true) {
    try {
        $tokens = @(Split-MenuCommand -Text (Read-Host 'company-console>'))
        if ($tokens.Count -eq 0) { continue }
        switch ($tokens[0].ToLowerInvariant()) {
            { $_ -in @('quit', 'exit', 'q', '0') } { return }
            'list' { Show-Companies; break }
            'month' { Invoke-MonthSpecification -Tokens $tokens; break }
            'login' { Invoke-HttpLogin; break }
            'discover' { Invoke-Discovery; Show-Companies; break }
            'status' { Invoke-StatusView; break }
            'help' { Show-Help; break }
            default { Write-Host "未知命令：$($tokens[0])；输入 help 查看说明。" -ForegroundColor Yellow }
        }
    }
    catch {
        Write-Host "操作失败：$($_.Exception.Message)" -ForegroundColor Red
    }
}
