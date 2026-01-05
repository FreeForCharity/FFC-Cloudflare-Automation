[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiToken,

    [Parameter()]
    [string]$BaseUrl = 'https://wpmudev.com/api',

    [Parameter()]
    [string]$OutputFile = 'wpmudev_domains.csv',

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$PerPage = 100
)

$ErrorActionPreference = 'Stop'

function Resolve-WpmuDevApiToken {
    param([string]$ApiTokenParam)

    if (-not [string]::IsNullOrWhiteSpace($ApiTokenParam)) {
        return $ApiTokenParam.Trim()
    }

    if (-not [string]::IsNullOrWhiteSpace($env:WPMUDEV_API_TOKEN)) {
        return $env:WPMUDEV_API_TOKEN.Trim()
    }

    throw 'Missing WPMUDEV API token. Provide -ApiToken or set WPMUDEV_API_TOKEN.'
}

function Join-Url {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Base,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $b = $Base.TrimEnd('/')
    $p = $Path.TrimStart('/')
    return "$b/$p"
}

function Invoke-WpmuDevGet {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $headersList = @(
        # The Hub API swagger defines an API key header literally named "AUTHORIZATION".
        # Some endpoints accept the raw key, not a Bearer token.
        @{ AUTHORIZATION = $Token },
        @{ AUTHORIZATION = "Bearer $Token" },
        @{ Authorization = $Token },
        @{ Authorization = "Bearer $Token" },
        @{ 'X-Api-Key' = $Token },
        @{ 'X-WPMUDEV-API-Key' = $Token }
    )

    $lastError = $null
    $lastHeaderLabel = $null
    foreach ($h in $headersList) {
        try {
            $lastHeaderLabel = ($h.Keys | Select-Object -First 1)
            $result = Invoke-RestMethod -Method Get -Uri $Url -Headers $h -ErrorAction Stop
            return $result
        }
        catch {
            $lastError = $_
        }
    }

    if ($lastError) {
        $message = $lastError.Exception.Message
        $statusCode = $null
        try {
            if ($lastError.Exception.Response -and $lastError.Exception.Response.StatusCode) {
                $statusCode = [int]$lastError.Exception.Response.StatusCode
            }
        }
        catch {
        }

        if ($statusCode) {
            throw "Request failed ($statusCode) using header '$lastHeaderLabel': $message"
        }

        throw "Request failed using header '$lastHeaderLabel': $message"
    }
    throw "Request failed: $Url"
}

$token = Resolve-WpmuDevApiToken -ApiTokenParam $ApiToken

$sites = @()
$page = 1

do {
    $query = "?per_page=$PerPage&page=$page"
    $url = (Join-Url -Base $BaseUrl -Path 'hub/v1/sites') + $query

    $batch = Invoke-WpmuDevGet -Url $url -Token $token

    if ($null -eq $batch) { $batch = @() }
    elseif ($batch -isnot [System.Collections.IEnumerable] -or $batch -is [string]) { $batch = @($batch) }

    $sites += $batch

    if ($batch.Count -lt $PerPage) { break }
    $page++
} while ($true)

$rows = foreach ($s in $sites) {
    $domain = $null
    if ($null -ne $s.domain) { $domain = [string]$s.domain }
    elseif ($null -ne $s.home_url) {
        try {
            $domain = ([Uri]$s.home_url).Host
        }
        catch {
            $domain = [string]$s.home_url
        }
    }

    $title = if ($null -ne $s.title) { [string]$s.title } else { '' }
    $homeUrl = if ($null -ne $s.home_url) { [string]$s.home_url } else { '' }

    [PSCustomObject]@{
        domain     = $domain
        siteId     = $s.id
        siteName   = $title
        homeUrl    = $homeUrl
        source     = 'wpmudev'
        fetchedUtc = (Get-Date).ToUniversalTime().ToString('s') + 'Z'
    }
}

$rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) } | Sort-Object -Property domain -Unique | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $OutputFile

Write-Host "WPMUDEV export complete: $OutputFile" -ForegroundColor Green
Write-Host "Sites exported: $((Import-Csv -Path $OutputFile).Count)" -ForegroundColor Cyan
