param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\..\config\kdzwy.json'),
    [string]$AccountKey = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

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
            return [Convert]::ToBase64String($encryptor.TransformFinalBlock($plainBytes, 0, $plainBytes.Length))
        }
        finally {
            $encryptor.Dispose()
        }
    }
    finally {
        $des.Dispose()
    }
}

function Find-CompanyRecords {
    param($Value)

    if ($null -eq $Value) { return }
    if ($Value -is [Management.Automation.PSCustomObject] -or $Value -is [Collections.IDictionary]) {
        $props = $Value.PSObject.Properties
        $companyId = $props | Where-Object Name -eq 'companyId' | Select-Object -First 1 -ExpandProperty Value
        $companyName = $props | Where-Object { $_.Name -in @('customerName', 'companyName', 'name') } | Select-Object -First 1 -ExpandProperty Value
        if ($companyId -and $companyName) {
            [pscustomobject]@{ company_id = [string]$companyId; company_name = [string]$companyName }
        }
        foreach ($prop in $props) {
            Find-CompanyRecords -Value $prop.Value
        }
        return
    }
    if ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        foreach ($item in $Value) {
            Find-CompanyRecords -Value $item
        }
    }
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

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "找不到配置文件：$ConfigPath"
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$accounts = @(Get-LoginAccounts -Config $config)
if ($AccountKey) {
    $account = $accounts | Where-Object { [string]$_.key -eq $AccountKey } | Select-Object -First 1
    if ($null -eq $account) { throw "找不到启用的登录账号：$AccountKey" }
}
elseif ($accounts.Count -eq 1) {
    $account = $accounts[0]
}
else {
    throw '配置中有多个账号，调用公司发现时必须指定 AccountKey。'
}

$encryptedPassword = Protect-PasswordWithDes -Username ([string]$account.username) -Password ([string]$account.password)
$redirectUri = 'https://vip1-gj.kdzwy.com/acct-web/guanjia/'
$loginQuery = [Web.HttpUtility]::ParseQueryString('')
$loginQuery['username'] = [string]$account.username
$loginQuery['password'] = $encryptedPassword
$loginQuery['captcha'] = ''
$loginQuery['encode'] = '1'
$loginQuery['checkCaptcha'] = 'false'
$loginQuery['loginType'] = '0'
$loginQuery['redirectUri'] = $redirectUri
$loginUri = 'https://www.kdzwy.com/bs/guanjia/login?' + $loginQuery.ToString()

$cookies = [System.Net.CookieContainer]::new()
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.CookieContainer = $cookies
$handler.UseCookies = $true
$handler.AllowAutoRedirect = $true
$handler.AutomaticDecompression = [Net.DecompressionMethods]::GZip -bor [Net.DecompressionMethods]::Deflate
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(45)
$client.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36')
$client.DefaultRequestHeaders.Accept.ParseAdd('application/json, text/plain, */*')

try {
    $emptyBody = [System.Net.Http.ByteArrayContent]::new([byte[]]::new(0))
    $loginResponse = $client.PostAsync($loginUri, $emptyBody).GetAwaiter().GetResult()
    if (-not $loginResponse.IsSuccessStatusCode) {
        throw "登录请求失败：HTTP $([int]$loginResponse.StatusCode)"
    }
    [void]$loginResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()

    $listUri = 'https://vip1-gj.kdzwy.com/guanjia/customer/search?customerName='
    $listResponse = $client.GetAsync($listUri).GetAwaiter().GetResult()
    if (-not $listResponse.IsSuccessStatusCode) {
        throw "公司列表请求失败：HTTP $([int]$listResponse.StatusCode)"
    }
    $listJson = $listResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    $companies = @(Find-CompanyRecords -Value $listJson | Sort-Object company_id -Unique)

    [pscustomobject]@{
        login = 'ok'
        login_account = [string]$account.key
        account_name = if ($account.name) { [string]$account.name } else { [string]$account.key }
        interface = $listUri
        company_count = $companies.Count
        companies = $companies
    } | ConvertTo-Json -Depth 6
}
finally {
    $client.Dispose()
    $handler.Dispose()
}
