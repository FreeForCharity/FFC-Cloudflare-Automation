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

$sitesCount = $sites.Count

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

    if (-not [string]::IsNullOrWhiteSpace($domain)) {
        $domain = $domain.Trim().ToLowerInvariant()
    }

    $title = ''
    foreach ($prop in @('title', 'name', 'site_name', 'blogname', 'blog_name')) {
        $propInfo = $null
        try { $propInfo = $s.PSObject.Properties[$prop] } catch { }

        if ($null -ne $propInfo) {
            $value = $propInfo.Value
            if (-not [string]::IsNullOrWhiteSpace([string]$value)) {
                $title = [string]$value
                break
            }
        }
    }
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

$domainRows = $rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) } | Group-Object -Property domain | ForEach-Object {
    $group = $_.Group

    $siteIds = ($group | ForEach-Object { $_.siteId } | Where-Object { $null -ne $_ } | Sort-Object -Unique) -join ';'
    $siteNames = ($group | ForEach-Object { $_.siteName } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique) -join '; '
    $homeUrls = ($group | ForEach-Object { $_.homeUrl } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique) -join '; '

    [PSCustomObject]@{
        domain     = $_.Name
        siteIds    = $siteIds
        siteNames  = $siteNames
        homeUrls   = $homeUrls
        sitesCount = $group.Count
        source     = 'wpmudev'
        fetchedUtc = ($group | Select-Object -First 1).fetchedUtc
    }
} | Sort-Object -Property domain

$domainRows | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $OutputFile

Write-Host "WPMUDEV export complete: $OutputFile" -ForegroundColor Green
Write-Host "Sites fetched: $sitesCount" -ForegroundColor Cyan
Write-Host "Domains exported: $((Import-Csv -Path $OutputFile).Count)" -ForegroundColor Cyan
