[CmdletBinding()]
param(
    [Parameter()]
    [string]$Url = 'https://ffcadmin.org/sites-list/',

    [Parameter()]
    [string]$OutputFile = 'ffcadmin_sites_list_domains.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The sites list is a Next.js page, but the key table data is present in the initial
# HTML payload. We parse the tables to export structured per-domain rows.

function Normalize-Domain {
    param([string]$Domain)
    if ([string]::IsNullOrWhiteSpace($Domain)) { return $null }

    $s = $Domain.Trim()
    # Some rows include paths like "bhakthinivedana.com/sandbox".
    if ($s -match '^(https?://)') {
        try {
            $u = [Uri]$s
            $s = $u.Host
        }
        catch {
        }
    }
    if ($s -match '^([^/]+)') {
        $s = $Matches[1]
    }

    return $s.ToLowerInvariant().TrimEnd('.')
}

function Parse-YesNo {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    $t = $Text.Trim().ToLowerInvariant()
    return ($t -eq 'yes' -or $t -eq 'true')
}

Write-Host "Downloading: $Url" -ForegroundColor Gray
$html = (Invoke-WebRequest -Uri $Url -UseBasicParsing -MaximumRedirection 5).Content

$tr = [regex]::Matches($html, '<tr[^>]*>(.*?)</tr>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
$rows = New-Object System.Collections.Generic.List[object]

foreach ($m in $tr) {
    $inner = $m.Groups[1].Value
    if ($inner -notmatch '<td') { continue }
    if ($inner -match '<th') { continue }

    $td = [regex]::Matches($inner, '<td[^>]*>(.*?)</td>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($td.Count -lt 4) { continue }

    $cells = @()
    foreach ($c in $td) {
        $raw = $c.Groups[1].Value
        $txt = [System.Net.WebUtility]::HtmlDecode(($raw -replace '<[^>]+>', ' '))
        $txt = ($txt -replace '\s+', ' ').Trim()
        $cells += $txt
    }

    # Expected schema (as of 2026-02):
    # Category | Domain | Health | Status | WHMCS | Cloudflare | WPMUDEV | Server | Notes
    if ($cells.Count -lt 9) { continue }

    $domainRaw = $cells[1]
    $domain = Normalize-Domain $domainRaw
    if (-not $domain) { continue }

    $rows.Add([PSCustomObject]@{
            domain          = $domain
            domain_raw      = $domainRaw
            category        = $cells[0]
            health          = $cells[2]
            status          = $cells[3]
            whmcs           = Parse-YesNo $cells[4]
            cloudflare      = Parse-YesNo $cells[5]
            wpmudev         = Parse-YesNo $cells[6]
            server          = $cells[7]
            notes           = $cells[8]
            source          = 'ffcadmin'
            source_url      = $Url
            extractedAtUtc  = (Get-Date).ToUniversalTime().ToString('o')
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
