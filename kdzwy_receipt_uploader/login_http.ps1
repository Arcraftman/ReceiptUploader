param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\kdzwy.json'),
    [string[]]$CompanyName,
    [string]$CompanyListPath,
    [string]$SessionDirectory = (Join-Path $PSScriptRoot '..\http_sessions\companies'),
    [string]$SessionPath = (Join-Path $PSScriptRoot '..\http_sessions\kdzwy.accountbook.cookies.json'),
    [string]$ManifestPath = (Join-Path $PSScriptRoot '..\http_sessions\kdzwy.company_sessions.json')
)

Add-Type -AssemblyName System.Web
Add-Type -AssemblyName System.Net.Http
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Normalize-CompanyText {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $normalized = $Value.Normalize([Text.NormalizationForm]::FormKC).Trim()
    $normalized = [regex]::Replace($normalized, '\s+', '')
    return $normalized.ToLowerInvariant()
}

function Find-ExactCompany {
    param($Value, [string]$CompanyName)

    if ($null -eq $Value) { return $null }
    if ($Value -is [Collections.IDictionary] -or $Value -is [Management.Automation.PSCustomObject]) {
        $props = $Value.PSObject.Properties
        $nameProps = $props | Where-Object { $_.Name -in @('customerName', 'customer_name', 'companyName', 'company_name', 'name', 'company') } | ForEach-Object { $_.Value }
        $targetText = Normalize-CompanyText $CompanyName
        foreach ($nameValue in $nameProps) {
            $candidate = [string]$nameValue
            if (-not $candidate) { continue }
            $candidateText = Normalize-CompanyText $candidate
            if ($candidateText -eq $targetText) { return $Value }
        }
        foreach ($nameValue in $nameProps) {
            $candidate = [string]$nameValue
            if (-not $candidate) { continue }
            if ([string]$candidate -eq $CompanyName) { return $Value }
            if ([string]$candidate -ieq $CompanyName) { return $Value }
        }
        foreach ($prop in $props) {
            $found = Find-ExactCompany -Value $prop.Value -CompanyName $CompanyName
            if ($null -ne $found) { return $found }
        }
    }
    elseif ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        foreach ($item in $Value) {
            $found = Find-ExactCompany -Value $item -CompanyName $CompanyName
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
    $Value | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $tempPath -Encoding utf8
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "找不到配置文件：$ConfigPath"
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if (-not $config.username -or -not $config.password) {
    throw '配置缺少 username 或 password。'
}

$requestedNames = [Collections.Generic.List[string]]::new()
foreach ($item in @($CompanyName)) {
    foreach ($name in ([string]$item -split '[,，;；\r\n]+')) {
        $trimmed = $name.Trim()
        if ($trimmed) { $requestedNames.Add($trimmed) }
    }
}
if ($CompanyListPath) {
    foreach ($line in Get-Content -LiteralPath $CompanyListPath) {
        $trimmed = ([string]$line).Trim()
        if ($trimmed -and -not $trimmed.StartsWith('#')) { $requestedNames.Add($trimmed) }
    }
}
if ($requestedNames.Count -eq 0 -and $config.company_names -is [System.Array]) {
    foreach ($name in @($config.company_names)) {
        $trimmed = ([string]$name).Trim()
        if ($trimmed) { $requestedNames.Add($trimmed) }
    }
}
if ($requestedNames.Count -eq 0 -and $config.company_name) {
    $requestedNames.Add(([string]$config.company_name).Trim())
}

$seenNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$companyNames = @($requestedNames | Where-Object { $seenNames.Add($_) })

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
    $encryptedPassword = Protect-PasswordWithDes -Username ([string]$config.username) -Password ([string]$config.password)
    $redirectUri = 'https://vip1-gj.kdzwy.com/acct-web/guanjia/'
    $loginQuery = [System.Web.HttpUtility]::ParseQueryString('')
    $loginQuery['username'] = [string]$config.username
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

    if ($companyNames.Count -eq 0) {
        $companyListUri = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName='
        $companyListResponse = $client.GetAsync($companyListUri).GetAwaiter().GetResult()
        $companyListJson = (Get-ResponseText $companyListResponse) | ConvertFrom-Json
        $companyRecords = @(Get-CompanyRecords -Value $companyListJson | Sort-Object companyId -Unique)
        $companyNames = @($companyRecords | ForEach-Object companyName)
        if ($companyNames.Count -eq 0) {
            throw '登录成功，但公司列表接口没有返回任何公司。'
        }
    }

    $results = [Collections.Generic.List[object]]::new()
    $manifestEntries = [Collections.Generic.List[object]]::new()
    $successfulPayloads = [Collections.Generic.List[object]]::new()

    foreach ($targetCompany in $companyNames) {
        try {
            $searchUri = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName=' + [Uri]::EscapeDataString($targetCompany)
            $searchResponse = $client.GetAsync($searchUri).GetAwaiter().GetResult()
            $searchJson = (Get-ResponseText $searchResponse) | ConvertFrom-Json
            $company = Find-ExactCompany -Value $searchJson -CompanyName $targetCompany
            if ($null -eq $company -or -not $company.companyId) { throw '登录成功，但没有精确找到目标公司。' }

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
            $companyMatched = $true
            if (Normalize-CompanyText ([string]$liveCompany) -ne Normalize-CompanyText $targetCompany) {
                $companyMatched = $false
            }

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
                target_url = $accountUri.AbsoluteUri
                cookies = $sessionCookies
                access_token = [string]$accessToken
                refresh_token = [string]$refreshToken
                company_name = [string]$liveCompany
                verified_at = $verifiedAt
            }

            $safeName = Get-SafeCompanyFileName $targetCompany
            $companySessionPath = Join-Path $SessionDirectory ($safeName + '.accountbook.cookies.json')
            Write-JsonAtomically -Value $sessionPayload -Path $companySessionPath
            $successfulPayloads.Add($sessionPayload)
            $manifestEntries.Add([ordered]@{ company_name = [string]$liveCompany; company_name_expected = $targetCompany; company_matched = $companyMatched; session_file = [IO.Path]::GetFullPath($companySessionPath); verified_at = $verifiedAt })
            $results.Add([ordered]@{
                company_name = [string]$liveCompany
                status = if ($companyMatched) { 'ok' } else { 'ok-mismatch' }
                company_match = if ($companyMatched) { 'exact' } else { 'fuzzy' }
                target_company = $targetCompany
                accounting_host = $accountUri.Host
                accounting_path = $accountUri.AbsolutePath
                session_file = [IO.Path]::GetFullPath($companySessionPath)
            })
        }
        catch {
            $results.Add([ordered]@{ company_name = $targetCompany; status = 'failed'; error = $_.Exception.Message })
        }
    }

    if ($successfulPayloads.Count -eq 1) {
        Write-JsonAtomically -Value $successfulPayloads[0] -Path $SessionPath
    }

    Write-JsonAtomically -Value ([ordered]@{
        generated_at = [DateTimeOffset]::Now.ToString('o')
        requested_count = $companyNames.Count
        success_count = $successfulPayloads.Count
        sessions = @($manifestEntries)
    }) -Path $ManifestPath

    [pscustomobject]@{
        login = 'ok'
        company_list_interface = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName='
        requested_count = $companyNames.Count
        success_count = $successfulPayloads.Count
        failure_count = $companyNames.Count - $successfulPayloads.Count
        results = @($results)
        manifest_file = [IO.Path]::GetFullPath($ManifestPath)
    } | ConvertTo-Json -Depth 6

    if ($successfulPayloads.Count -ne $companyNames.Count) { exit 2 }
}
finally {
    $client.Dispose()
    $handler.Dispose()
}
