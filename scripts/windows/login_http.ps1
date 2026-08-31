param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\..\config\kdzwy.json'),
    [string]$AccountbooksPath = (Join-Path $PSScriptRoot '..\..\runtime\registry\accountbooks.json'),
    [string]$ManifestPath = (Join-Path $PSScriptRoot '..\..\http_sessions\kdzwy.company_sessions.json'),
    [string]$AccountbookKey = '',
    [string]$ProjectConfigPath = ''
)

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Add-Type -AssemblyName System.Web
Add-Type -AssemblyName System.Net.Http
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Normalize-CompanyText {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $normalized = $Value.Normalize([Text.NormalizationForm]::FormKC).Trim()
    $normalized = [regex]::Replace($normalized, '[\s\p{Cf}]+', '')
    return $normalized.ToLowerInvariant()
}

function Find-ExactCompany {
    param($Value, [string]$CompanyId)

    if ($null -eq $Value) { return $null }
    if ($Value -is [Collections.IDictionary] -or $Value -is [Management.Automation.PSCustomObject]) {
        $props = $Value.PSObject.Properties
        $candidateId = $props | Where-Object { $_.Name -in @('companyId', 'company_id', 'customerId') } | Select-Object -First 1 -ExpandProperty Value
        if ($candidateId -and ([string]$candidateId).Trim() -eq $CompanyId.Trim()) { return $Value }
        foreach ($prop in $props) {
            $found = Find-ExactCompany -Value $prop.Value -CompanyId $CompanyId
            if ($null -ne $found) { return $found }
        }
    }
    elseif ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        foreach ($item in $Value) {
            $found = Find-ExactCompany -Value $item -CompanyId $CompanyId
            if ($null -ne $found) { return $found }
        }
    }
    return $null
}

function Get-CompanyRecords {
    param($Value)

    if ($null -eq $Value) { return }
    if ($Value -is [Collections.IDictionary] -or $Value -is [Management.Automation.PSCustomObject]) {
        $props = $Value.PSObject.Properties
        $companyId = $props | Where-Object Name -eq 'companyId' | Select-Object -First 1 -ExpandProperty Value
        $companyName = $props | Where-Object { $_.Name -in @('customerName', 'companyName', 'name') } | Select-Object -First 1 -ExpandProperty Value
        if ($companyId -and $companyName) {
            [pscustomobject]@{ companyId = [string]$companyId; companyName = [string]$companyName }
        }
        foreach ($prop in $props) {
            Get-CompanyRecords -Value $prop.Value
        }
    }
    elseif ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        foreach ($item in $Value) {
            Get-CompanyRecords -Value $item
        }
    }
}

function Protect-PasswordWithDes {
    param([string]$Username, [string]$Password)

    $keyText = if ($Username.Length -ge 8) { $Username.Substring(0, 8) } else { $Username.PadRight(8, [char]0) }
    $keyBytes = [Text.Encoding]::UTF8.GetBytes($keyText)
    if ($keyBytes.Length -ne 8) {
        throw '用户名的 UTF-8 前八字符不能直接用作 DES 密钥。'
    }

    $des = [Security.Cryptography.DES]::Create()
    try {
        $des.Mode = [Security.Cryptography.CipherMode]::CBC
        $des.Padding = [Security.Cryptography.PaddingMode]::PKCS7
        $des.Key = $keyBytes
        $des.IV = $keyBytes
        $plainBytes = [Text.Encoding]::UTF8.GetBytes($Password)
        $encryptor = $des.CreateEncryptor()
        try {
            $encrypted = $encryptor.TransformFinalBlock($plainBytes, 0, $plainBytes.Length)
            return [Convert]::ToBase64String($encrypted)
        }
        finally {
            $encryptor.Dispose()
        }
    }
    finally {
        $des.Dispose()
    }
}

function Get-ResponseText {
    param([System.Net.Http.HttpResponseMessage]$Response)
    $bytes = $Response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
    try {
        $text = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    }
    catch {
        $text = [Text.Encoding]::UTF8.GetString($bytes)
    }
    if (-not $Response.IsSuccessStatusCode) {
        throw "HTTP 请求失败：$([int]$Response.StatusCode) $($Response.ReasonPhrase)"
    }
    return $text
}

function Get-SafeCompanyFileName {
    param([string]$Value)
    $invalid = [IO.Path]::GetInvalidFileNameChars()
    $safe = -join ($Value.ToCharArray() | ForEach-Object { if ($invalid -contains $_) { '_' } else { $_ } })
    $safe = $safe.Trim().TrimEnd('.')
    if (-not $safe) { throw '公司名称无法转换为有效文件名。' }
    return $safe
}

function Write-JsonAtomically {
    param($Value, [string]$Path, [int]$Depth = 8)
    $directory = Split-Path -Parent $Path
    if ($directory) { [IO.Directory]::CreateDirectory($directory) | Out-Null }
    $tempPath = $Path + '.tmp'
    $json = $Value | ConvertTo-Json -Depth $Depth
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($tempPath), $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
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

function Resolve-CompanySessionPath {
    param($Accountbook, [string]$LoginAccountKey, [string]$CompanyName)

    if ($Accountbook.session_file) {
        $configured = [string]$Accountbook.session_file
        if ([IO.Path]::IsPathRooted($configured)) { return [IO.Path]::GetFullPath($configured) }
        return [IO.Path]::GetFullPath((Join-Path $ProjectRoot $configured))
    }
    $safeName = Get-SafeCompanyFileName $CompanyName
    return Join-Path (Join-Path (Join-Path $ProjectRoot 'http_sessions\accounts') $LoginAccountKey) ('companies\' + $safeName + '.accountbook.cookies.json')
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "找不到配置文件：$ConfigPath"
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$loginAccounts = @(Get-LoginAccounts -Config $config)

if (-not (Test-Path -LiteralPath $AccountbooksPath)) {
    throw "找不到账套清单：$AccountbooksPath；请先运行 commands\discover_companies.bat。"
}
$accountbooksConfig = Get-Content -LiteralPath $AccountbooksPath -Raw | ConvertFrom-Json
$enabledAccountbooks = @($accountbooksConfig.accountbooks | Where-Object { $_.enabled -eq $true })
if ($ProjectConfigPath) {
    if (-not (Test-Path -LiteralPath $ProjectConfigPath)) { throw "找不到月份配置：$ProjectConfigPath" }
    $projectConfig = Get-Content -LiteralPath $ProjectConfigPath -Raw | ConvertFrom-Json
    $AccountbookKey = ([string]$projectConfig.target.accountbook_key).Trim()
    if (-not $AccountbookKey) { throw "月份配置缺少 target.accountbook_key：$ProjectConfigPath" }
}
if ($AccountbookKey) {
    $enabledAccountbooks = @($enabledAccountbooks | Where-Object { ([string]$_.key).Trim() -eq $AccountbookKey.Trim() })
    if ($enabledAccountbooks.Count -ne 1) { throw "无法唯一找到目标账套：$AccountbookKey" }
}
if ($enabledAccountbooks.Count -eq 0) {
    throw "账套清单没有 enabled=true 的公司：$AccountbooksPath；请先运行 commands\discover_companies.bat。"
}

$results = @()
$manifestEntries = @()
$successfulPayloads = @()
$requestedCount = 0

foreach ($loginAccount in $loginAccounts) {
    $accountKey = [string]$loginAccount.key
    $accountbooksForLogin = @($enabledAccountbooks | Where-Object {
        ([string]$_.login_account -eq $accountKey) -or (-not $_.login_account -and $loginAccounts.Count -eq 1)
    })
    if ($accountbooksForLogin.Count -eq 0) { continue }
    $requestedCount += $accountbooksForLogin.Count

    $cookies = [System.Net.CookieContainer]::new()
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.CookieContainer = $cookies
    $handler.UseCookies = $true
    $handler.AllowAutoRedirect = $true
    $handler.AutomaticDecompression = [System.Net.DecompressionMethods]::GZip -bor [System.Net.DecompressionMethods]::Deflate
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(45)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36')
    $client.DefaultRequestHeaders.Accept.ParseAdd('application/json, text/plain, */*')

    try {
    $encryptedPassword = Protect-PasswordWithDes -Username ([string]$loginAccount.username) -Password ([string]$loginAccount.password)
    $redirectUri = 'https://vip1-gj.kdzwy.com/acct-web/guanjia/'
    $loginQuery = [System.Web.HttpUtility]::ParseQueryString('')
    $loginQuery['username'] = [string]$loginAccount.username
    $loginQuery['password'] = $encryptedPassword
    $loginQuery['captcha'] = ''
    $loginQuery['encode'] = '1'
    $loginQuery['checkCaptcha'] = 'false'
    $loginQuery['loginType'] = '0'
    $loginQuery['redirectUri'] = $redirectUri
    $loginUri = 'https://www.kdzwy.com/bs/guanjia/login?' + $loginQuery.ToString()

    $emptyBody = [System.Net.Http.ByteArrayContent]::new([byte[]]::new(0))
    $loginResponse = $client.PostAsync($loginUri, $emptyBody).GetAwaiter().GetResult()
    [void](Get-ResponseText $loginResponse)

    foreach ($accountbook in $accountbooksForLogin) {
        $targetCompany = ([string]$accountbook.name).Trim()
        $targetCompanyId = ([string]$accountbook.company_id).Trim()
        if (-not $targetCompanyId) { throw "目标账套缺少 company_id：$targetCompany" }
        try {
            $searchUri = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName=' + [Uri]::EscapeDataString($targetCompany)
            $searchResponse = $client.GetAsync($searchUri).GetAwaiter().GetResult()
            $searchJson = (Get-ResponseText $searchResponse) | ConvertFrom-Json
            $company = Find-ExactCompany -Value $searchJson -CompanyId $targetCompanyId
            if ($null -eq $company -or ([string]$company.companyId).Trim() -ne $targetCompanyId) {
                throw "登录成功，但没有按 company_id 精确找到目标公司：$targetCompanyId / $targetCompany"
            }

            $accountUrlUri = 'https://vip1-gj.kdzwy.com/guanjia/customer/accounturl?companyId=' + [Uri]::EscapeDataString([string]$company.companyId)
            $accountUrlResponse = $client.GetAsync($accountUrlUri).GetAwaiter().GetResult()
            $accountUrlJson = (Get-ResponseText $accountUrlResponse) | ConvertFrom-Json
            $accountUrl = if ($accountUrlJson.data -is [string]) { $accountUrlJson.data } elseif ($accountUrlJson.url) { $accountUrlJson.url } elseif ($accountUrlJson.data.url) { $accountUrlJson.data.url } else { $null }
            if (-not $accountUrl) { throw '目标公司存在，但未取得官方账套跳转地址。' }

            $accountResponse = $client.GetAsync([string]$accountUrl).GetAwaiter().GetResult()
            [void](Get-ResponseText $accountResponse)
            $accountUri = $accountResponse.RequestMessage.RequestUri
            if ($accountUri.Host -ne 'vip4-kj.kdzwy.com' -or $accountUri.AbsolutePath -notlike '/accounting/*') {
                throw '账套单点登录没有进入预期的账务站点。'
            }

            $authCodeCookie = $cookies.GetCookies([Uri]'https://vip4-kj.kdzwy.com/')['authCode']
            if ($null -eq $authCodeCookie -or -not $authCodeCookie.Value) {
                throw '账务站点未返回 authCode，无法交换接口令牌。'
            }

            $exchangeBody = @{ authCode = $authCodeCookie.Value } | ConvertTo-Json -Compress
            $exchangeContent = [System.Net.Http.StringContent]::new($exchangeBody, [Text.Encoding]::UTF8, 'application/json')
            $exchangeRequest = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, 'https://vip4-kj.kdzwy.com/auth/exchangeToken')
            $exchangeRequest.Headers.TryAddWithoutValidation('ajax_flag', '1') | Out-Null
            $exchangeRequest.Content = $exchangeContent
            $exchangeResponse = $client.SendAsync($exchangeRequest).GetAwaiter().GetResult()
            $exchangeJson = (Get-ResponseText $exchangeResponse) | ConvertFrom-Json
            $accessToken = $exchangeJson.data.access_token
            $refreshToken = $exchangeJson.data.refresh_token
            if (-not $accessToken -and $exchangeJson.data.data) {
                $accessToken = $exchangeJson.data.data.access_token
                $refreshToken = $exchangeJson.data.data.refresh_token
            }
            if (-not $accessToken) { throw 'authCode 交换成功返回，但未找到 access_token。' }

            $initRequest = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, 'https://vip4-kj.kdzwy.com/basedata/initParams?m=getSystemParams')
            $initRequest.Headers.TryAddWithoutValidation('ajax_flag', '1') | Out-Null
            $initRequest.Headers.TryAddWithoutValidation('app-token', [string]$accessToken) | Out-Null
            $initRequest.Headers.Referrer = $accountUri
            $initResponse = $client.SendAsync($initRequest).GetAwaiter().GetResult()
            $initText = Get-ResponseText $initResponse
            if (-not $initText) { throw '账套初始化接口返回空内容。' }
            $initJson = $initText | ConvertFrom-Json
            $liveCompany = if ($initJson.data.myCompany) { $initJson.data.myCompany } elseif ($initJson.myCompany) { $initJson.myCompany } else { $null }
            $liveCompanyId = if ($initJson.data.companyId) { [string]$initJson.data.companyId } elseif ($initJson.companyId) { [string]$initJson.companyId } else { '' }
            $liveDbid = if ($initJson.data.DBID) { [string]$initJson.data.DBID } elseif ($initJson.DBID) { [string]$initJson.DBID } else { '' }
            if ($liveCompanyId -ne $targetCompanyId) { throw "账套初始化 company_id 不匹配：期望 $targetCompanyId，实际 $liveCompanyId" }
            if (-not $liveDbid) { throw '账套初始化未返回 DBID。' }

            $sessionCookies = @(
                foreach ($cookie in $cookies.GetCookies([Uri]'https://vip4-kj.kdzwy.com/')) {
                    [ordered]@{
                        name = $cookie.Name
                        value = $cookie.Value
                        domain = $cookie.Domain
                        path = $cookie.Path
                        expires = if ($cookie.Expires -eq [DateTime]::MinValue) { -1 } else { [DateTimeOffset]$cookie.Expires.ToUniversalTime() | ForEach-Object ToUnixTimeSeconds }
                        httpOnly = $cookie.HttpOnly
                        secure = $cookie.Secure
                    }
                }
            )
            if ($sessionCookies.Count -eq 0) { throw '账套验证成功，但没有可持久化的账务 Cookie。' }

            $verifiedAt = [DateTimeOffset]::Now.ToString('o')
            $sessionPayload = [ordered]@{
                login_account = $accountKey
                target_url = $accountUri.AbsoluteUri
                cookies = $sessionCookies
                access_token = [string]$accessToken
                refresh_token = [string]$refreshToken
                company_name = $targetCompany
                reported_company_name = [string]$liveCompany
                company_id = $liveCompanyId
                dbid = $liveDbid
                verified_at = $verifiedAt
            }

            $companySessionPath = Resolve-CompanySessionPath -Accountbook $accountbook -LoginAccountKey $accountKey -CompanyName $targetCompany
            Write-JsonAtomically -Value $sessionPayload -Path $companySessionPath
            $successfulPayloads += $sessionPayload
            $manifestEntries += [ordered]@{ login_account = $accountKey; company_name = $targetCompany; company_id = $liveCompanyId; dbid = $liveDbid; session_file = [IO.Path]::GetFullPath($companySessionPath); verified_at = $verifiedAt }
            $results += [ordered]@{
                login_account = $accountKey
                company_name = $targetCompany
                company_id = $liveCompanyId
                dbid = $liveDbid
                status = 'ok'
                company_match = 'company-id-exact'
                target_company = $targetCompany
                accounting_host = $accountUri.Host
                accounting_path = $accountUri.AbsolutePath
                session_file = [IO.Path]::GetFullPath($companySessionPath)
            }
        }
        catch {
            $results += [ordered]@{ login_account = $accountKey; company_name = $targetCompany; status = 'failed'; error = $_.Exception.Message }
        }
    }
    }
    catch {
        foreach ($accountbook in $accountbooksForLogin) {
            $results += [ordered]@{ login_account = $accountKey; company_name = [string]$accountbook.name; status = 'failed'; error = $_.Exception.Message }
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

if ($requestedCount -eq 0) {
    throw '启用的账套没有关联到任何启用登录账号，请重新运行 commands\discover_companies.bat。'
}

Write-JsonAtomically -Value ([ordered]@{
    generated_at = [DateTimeOffset]::Now.ToString('o')
    account_count = $loginAccounts.Count
    requested_count = $requestedCount
    success_count = $successfulPayloads.Count
    sessions = @($manifestEntries)
}) -Path $ManifestPath

[pscustomobject]@{
    login = 'ok'
    account_count = $loginAccounts.Count
    company_list_interface = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName='
    requested_count = $requestedCount
    success_count = $successfulPayloads.Count
    failure_count = $requestedCount - $successfulPayloads.Count
    results = @($results)
    manifest_file = [IO.Path]::GetFullPath($ManifestPath)
} | ConvertTo-Json -Depth 6

if ($successfulPayloads.Count -ne $requestedCount) { exit 2 }
