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
        [string]$Token,
        [ref]$ResponseHeaders
    )

    $headersList = @(
        @{ Authorization = "Bearer $Token" },
        @{ 'X-Api-Key' = $Token },
        @{ 'X-WPMUDEV-API-Key' = $Token }
    )

    $lastError = $null
    foreach ($h in $headersList) {
        try {
            $rh = $null
            $result = Invoke-RestMethod -Method Get -Uri $Url -Headers $h -ResponseHeadersVariable rh -ErrorAction Stop
            $ResponseHeaders.Value = $rh
            return $result
        }
        catch {
            $lastError = $_
        }
    }

    if ($lastError) { throw $lastError }
    throw "Request failed: $Url"
}

$token = Resolve-WpmuDevApiToken -ApiTokenParam $ApiToken

$sites = @()
$page = 1
$totalPages = $null

do {
    $query = "?per_page=$PerPage&page=$page"
    $url = (Join-Url -Base $BaseUrl -Path 'hub/v1/sites') + $query

    $headers = $null
    $batch = Invoke-WpmuDevGet -Url $url -Token $token -ResponseHeaders ([ref]$headers)

    if ($null -eq $batch) { $batch = @() }
    elseif ($batch -isnot [System.Collections.IEnumerable] -or $batch -is [string]) { $batch = @($batch) }

    $sites += $batch

    if (-not $totalPages -and $headers) {
        foreach ($name in @('X-WP-TotalPages', 'X-WP-Totalpages', 'X-WP-Total-Pages')) {
            if ($headers.ContainsKey($name)) {
                $raw = $headers[$name]
                $parsed = 0
                if ([int]::TryParse([string]$raw, [ref]$parsed) -and $parsed -ge 1) {
                    $totalPages = $parsed
                    break
                }
            }
        }
    }

    if ($totalPages) {
        $page++
    }
    else {
        if ($batch.Count -lt $PerPage) { break }
        $page++
    }
} while (-not $totalPages -or $page -le $totalPages)

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

$rows
| Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) }
| Sort-Object -Property domain -Unique
| Export-Csv -NoTypeInformation -Encoding UTF8 -Path $OutputFile

Write-Host "WPMUDEV export complete: $OutputFile" -ForegroundColor Green
Write-Host "Sites exported: $((Import-Csv -Path $OutputFile).Count)" -ForegroundColor Cyan
