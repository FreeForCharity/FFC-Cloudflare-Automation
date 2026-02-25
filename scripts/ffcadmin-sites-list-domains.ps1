[CmdletBinding()]
param(
    [Parameter()]
    [string]$Url = 'https://ffcadmin.org/sites-list/',

    [Parameter()]
    [string]$OutputFile = 'ffcadmin_sites_list_domains.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utilsPath = Join-Path -Path $PSScriptRoot -ChildPath 'ffc-utils.psm1'
Import-Module $utilsPath -Force

# The sites list is a Next.js page, but the key table data is present in the initial
# HTML payload. We parse the tables to export structured per-domain rows.

function Parse-YesNo {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    $t = $Text.Trim().ToLowerInvariant()
    return ($t -eq 'yes' -or $t -eq 'true')
}

Write-Host "Downloading: $Url" -ForegroundColor Gray

$invokeWebRequestParams = @{
    Uri                = $Url
    MaximumRedirection = 5
}
if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey('UseBasicParsing')) {
    $invokeWebRequestParams['UseBasicParsing'] = $true
}

$html = (Invoke-WebRequest @invokeWebRequestParams).Content

$regexTimeout = [TimeSpan]::FromSeconds(2)
$trRegex = [regex]::new('<tr[^>]*>(.*?)</tr>', [System.Text.RegularExpressions.RegexOptions]::Singleline, $regexTimeout)
$tdRegex = [regex]::new('<td[^>]*>(.*?)</td>', [System.Text.RegularExpressions.RegexOptions]::Singleline, $regexTimeout)
$tr = $trRegex.Matches($html)
$rows = New-Object System.Collections.Generic.List[object]

foreach ($m in $tr) {
    $inner = $m.Groups[1].Value
    if ($inner -notmatch '<td') { continue }
    if ($inner -match '<th') { continue }

    $td = $tdRegex.Matches($inner)
    if ($td.Count -lt 4) { continue }

    $cells = @()
    foreach ($c in $td) {
        $raw = $c.Groups[1].Value
        $txt = [System.Net.WebUtility]::HtmlDecode(($raw -replace '<[^>]+>', ' '))
        $txt = ($txt -replace '\s+', ' ').Trim()
        $cells += $txt
    }

    # Expected schema (as of February 2026):
    # Category | Domain | Health | Status | WHMCS | Cloudflare | WPMUDEV | Server | Notes
    if ($cells.Count -lt 9) { continue }

    $domainRaw = $cells[1]
    $domain = Normalize-Domain -Domain $domainRaw
    if (-not $domain) { continue }

    $health = if ([string]::IsNullOrWhiteSpace($cells[2])) { '' } else { $cells[2].Trim().ToLowerInvariant() }

    $rows.Add([PSCustomObject]@{
            domain         = (Protect-CsvCell -Value $domain)
            domain_raw     = (Protect-CsvCell -Value $domainRaw)
            category       = (Protect-CsvCell -Value $cells[0])
            health         = (Protect-CsvCell -Value $health)
            status         = (Protect-CsvCell -Value $cells[3])
            whmcs          = Parse-YesNo $cells[4]
            cloudflare     = Parse-YesNo $cells[5]
            wpmudev        = Parse-YesNo $cells[6]
            server         = (Protect-CsvCell -Value $cells[7])
            notes          = (Protect-CsvCell -Value $cells[8])
            source         = 'ffcadmin'
            source_url     = $Url
            extractedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        }) | Out-Null
}

# Deduplicate by normalized domain (keep first occurrence).
$seen = @{}
$final = foreach ($r in $rows) {
    if (-not $seen.ContainsKey($r.domain)) {
        $seen[$r.domain] = $true
        $r
    }
}

$dir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$final | Sort-Object domain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Exported $($final.Count) domains to $OutputFile" -ForegroundColor Green
