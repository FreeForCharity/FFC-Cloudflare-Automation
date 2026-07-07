<#
.SYNOPSIS
  Provision a per-charity GA4 property (Wave 3, epic #508). Creates one property under the
  FFC Supported Charities GA account and one web data stream for the charity's domain, then
  reports the stream's measurement id (G-XXXX) for GTM seeding (workflow 503).

.DESCRIPTION
  Uses domain-wide delegation: the ffc-workspace-admin SA impersonates a Workspace admin
  (-Subject) to call the Analytics Admin API. The SA key comes from Key Vault (see
  docs/google-api.md). One property per charity - NOT stream-per-charity - per the multi-site
  analytics architecture in docs/google-api.md.

  Idempotent: if any property under the account already has a web stream whose defaultUri
  matches the domain, that property/stream is reported as `existing` and nothing is created.

  -DryRun prints the plan and makes no changes (default true in the calling workflow).

.EXAMPLE
  pwsh -File scripts/google-ga-property-provision.ps1 -KeyPath sa.json `
    -Subject clarkemoyer@freeforcharity.org -Domain newcharity.org
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$KeyPath,
    [Parameter(Mandatory)][string]$Subject,
    [Parameter(Mandatory)][string]$Domain,
    # Numeric GA account id; when empty the account is resolved by -AccountName.
    [string]$AccountId,
    [string]$AccountName = 'FFC Supported Charities',
    [string]$DisplayName,
    [string]$TimeZone = 'America/New_York',
    [string]$CurrencyCode = 'USD',
    [switch]$DryRun,
    [string]$Scope = 'https://www.googleapis.com/auth/analytics.edit'
)
$ErrorActionPreference = 'Stop'
$base = 'https://analyticsadmin.googleapis.com/v1beta'

function ConvertTo-B64Url([byte[]]$b) { [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_') }
function Get-DwdToken {
    param($Key, $Subject, $Scope)
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $h = @{ alg = 'RS256'; typ = 'JWT'; kid = $Key.private_key_id }; $c = @{ iss = $Key.client_email; sub = $Subject; scope = $Scope; aud = $Key.token_uri; iat = $now; exp = $now + 3600 }
    $si = "$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($h|ConvertTo-Json -Compress)))).$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($c|ConvertTo-Json -Compress))))"
    $rsa = [System.Security.Cryptography.RSA]::Create()
    try { $rsa.ImportFromPem($Key.private_key); $sig = $rsa.SignData([Text.Encoding]::ASCII.GetBytes($si), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1) } finally { $rsa.Dispose() }
    (Invoke-RestMethod -Method Post -Uri $Key.token_uri -ContentType 'application/x-www-form-urlencoded' -Body @{ grant_type = 'urn:ietf:params:oauth:grant-type:jwt-bearer'; assertion = "$si.$(ConvertTo-B64Url $sig)" }).access_token
}
function Invoke-GaAdmin {
    param([string]$Method = 'GET', [string]$Path, [object]$Body, [hashtable]$Auth)
    $p = @{ Method = $Method; Uri = "$base/$Path"; Headers = $Auth; ErrorAction = 'Stop' }
    if ($null -ne $Body) { $p.Body = ($Body | ConvertTo-Json -Depth 8); $p.ContentType = 'application/json' }
    Invoke-RestMethod @p
}

$domain = $Domain.Trim().ToLowerInvariant().TrimEnd('.')
if (-not $DisplayName) { $DisplayName = "$domain - GA4" }

$key = Get-Content $KeyPath -Raw | ConvertFrom-Json
$auth = @{ Authorization = "Bearer $(Get-DwdToken -Key $key -Subject $Subject -Scope $Scope)" }

# 1. Resolve the target GA account
if (-not $AccountId) {
    $accounts = @()
    $pageToken = $null
    do {
        $path = 'accounts?pageSize=200' + $(if ($pageToken) { "&pageToken=$pageToken" } else { '' })
        $resp = Invoke-GaAdmin -Path $path -Auth $auth
        if ($resp.accounts) { $accounts += @($resp.accounts) }
        $pageToken = $resp.nextPageToken
    } while ($pageToken)
    $acct = $accounts | Where-Object { $_.displayName -eq $AccountName } | Select-Object -First 1
    if (-not $acct) {
        $names = @($accounts | ForEach-Object { $_.displayName }) -join '; '
        throw "GA account '$AccountName' not found. Visible accounts: $names"
    }
    $AccountId = ($acct.name -replace '^accounts/', '')
}
Write-Host "ACCOUNT accounts/$AccountId ($AccountName)"

# 2. Idempotency: does any property under this account already have a web stream for the domain?
$properties = @()
$pageToken = $null
do {
    $path = "properties?filter=parent:accounts/$AccountId&pageSize=200" + $(if ($pageToken) { "&pageToken=$pageToken" } else { '' })
    $resp = Invoke-GaAdmin -Path $path -Auth $auth
    if ($resp.properties) { $properties += @($resp.properties) }
    $pageToken = $resp.nextPageToken
} while ($pageToken)

foreach ($prop in $properties) {
    $streams = (Invoke-GaAdmin -Path "$($prop.name)/dataStreams" -Auth $auth).dataStreams
    foreach ($s in @($streams)) {
        $uri = [string]$s.webStreamData.defaultUri
        if ($uri -and ($uri -replace '^https?://', '' -replace '/.*$', '' -replace '^www\.', '') -eq $domain) {
            $result = [ordered]@{
                status        = 'existing'
                account       = "accounts/$AccountId"
                property      = $prop.name
                propertyName  = $prop.displayName
                stream        = $s.name
                measurementId = [string]$s.webStreamData.measurementId
                defaultUri    = $uri
            }
            $result | ConvertTo-Json
            return
        }
    }
}

# 3. Create property + web stream
if ($DryRun) {
    [ordered]@{
        status      = 'dry-run'
        account     = "accounts/$AccountId"
        wouldCreate = [ordered]@{
            property = [ordered]@{ displayName = $DisplayName; timeZone = $TimeZone; currencyCode = $CurrencyCode }
            stream   = [ordered]@{ type = 'WEB_DATA_STREAM'; displayName = $domain; defaultUri = "https://$domain" }
        }
    } | ConvertTo-Json -Depth 5
    return
}

$prop = Invoke-GaAdmin -Method Post -Path 'properties' -Auth $auth -Body @{
    parent       = "accounts/$AccountId"
    displayName  = $DisplayName
    timeZone     = $TimeZone
    currencyCode = $CurrencyCode
}
Write-Host "CREATED property $($prop.name) '$DisplayName'"

$stream = Invoke-GaAdmin -Method Post -Path "$($prop.name)/dataStreams" -Auth $auth -Body @{
    type          = 'WEB_DATA_STREAM'
    displayName   = $domain
    webStreamData = @{ defaultUri = "https://$domain" }
}
Write-Host "CREATED stream $($stream.name)"

[ordered]@{
    status        = 'created'
    account       = "accounts/$AccountId"
    property      = $prop.name
    propertyName  = $DisplayName
    stream        = $stream.name
    measurementId = [string]$stream.webStreamData.measurementId
    defaultUri    = "https://$domain"
} | ConvertTo-Json
