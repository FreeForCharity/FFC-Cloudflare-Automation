<#
.SYNOPSIS
    For each provided domain, replace any DNS records at staging.<domain> with
    a single CNAME staging.<domain> -> freeforcharity.github.io (DNS-only).

.DESCRIPTION
    Used to wire `staging.<domain>` records for FFC-EX repos to GitHub Pages so
    each migrated site can be visually verified before swapping production.

    For each domain in -Domains:
      1. Resolve the Cloudflare zone id (probes FFC then CM tokens).
      2. List every DNS record at staging.<domain> (any type: A / AAAA / CNAME / etc.)
      3. Delete each existing record at that exact name.
      4. POST a new CNAME staging.<domain> -> freeforcharity.github.io (proxied = false).
      5. Emit a per-domain status row.

    GitHub Pages custom domains don't work well behind the Cloudflare orange-cloud
    proxy, so the new CNAME is created DNS-only (proxied = false).

    This script is the "bulk delete-and-create" workaround for the known gap in
    Update-CloudflareDns.ps1 / workflow #3 noted in the HostPapa handoff
    (CNAMEs can't coexist with A records at the same name, and the manage-record
    workflow does not support delete).

.PARAMETER Domains
    Comma-separated list of root domains (e.g.
      'aprilhansen.com,armstrongacesbaseball.org,nj4israel.org')
    The script will operate on staging.<each-domain>.

.PARAMETER Target
    The CNAME target. Defaults to 'freeforcharity.github.io'.

.PARAMETER DryRun
    Discover-only mode. Lists what records would be deleted and what CNAME
    would be created, without performing any writes.

.EXAMPLE
    $env:CLOUDFLARE_API_TOKEN_FFC = '...'
    .\bulk-staging-cname-github-pages.ps1 -Domains 'aprilhansen.com,nj4israel.org' -DryRun

.EXAMPLE
    $env:CLOUDFLARE_API_TOKEN_FFC = '...'
    .\bulk-staging-cname-github-pages.ps1 -Domains 'aprilhansen.com,nj4israel.org'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domains,

    [string]$Target = 'freeforcharity.github.io',

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$tokens = @()
if ($env:CLOUDFLARE_API_TOKEN_FFC) { $tokens += @{ name = 'FFC'; token = $env:CLOUDFLARE_API_TOKEN_FFC } }
if ($env:CLOUDFLARE_API_TOKEN_CM) { $tokens += @{ name = 'CM'; token = $env:CLOUDFLARE_API_TOKEN_CM } }
if ($tokens.Count -eq 0) {
    throw 'No Cloudflare tokens found. Set CLOUDFLARE_API_TOKEN_FFC and/or CLOUDFLARE_API_TOKEN_CM.'
}

function Invoke-Cf {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Token,
        $Body
    )
    $url = "https://api.cloudflare.com/client/v4$Path"
    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    $params = @{ Method = $Method; Uri = $url; Headers = $headers; TimeoutSec = 30 }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 6 -Compress) }
    return Invoke-RestMethod @params
}

function Resolve-Zone {
    param(
        [Parameter(Mandatory)][string]$Domain
    )
    foreach ($t in $tokens) {
        try {
            $encoded = [uri]::EscapeDataString($Domain)
            $resp = Invoke-Cf -Method GET -Token $t.token -Path "/zones?name=$encoded"
            if ($resp.success -and $resp.result -and $resp.result.Count -gt 0) {
                return [pscustomobject]@{
                    Account = $t.name
                    Token   = $t.token
                    ZoneId  = $resp.result[0].id
                    ZoneName = $resp.result[0].name
                }
            }
        }
        catch {
            # try next token
        }
    }
    return $null
}

function Get-RecordsAtName {
    param(
        [Parameter(Mandatory)][string]$ZoneId,
        [Parameter(Mandatory)][string]$Token,
        [Parameter(Mandatory)][string]$Fqdn
    )
    $records = @()
    $page = 1
    while ($true) {
        $encoded = [uri]::EscapeDataString($Fqdn)
        $resp = Invoke-Cf -Method GET -Token $Token -Path "/zones/$ZoneId/dns_records?name=$encoded&per_page=50&page=$page"
        if (-not $resp.success) {
            throw "Failed to list records for $Fqdn : $($resp.errors | ConvertTo-Json -Depth 4 -Compress)"
        }
        if ($resp.result) { $records += $resp.result }
        $totalPages = 1
        if ($resp.result_info -and $resp.result_info.total_pages) {
            $totalPages = [int]$resp.result_info.total_pages
        }
        if ($page -ge $totalPages) { break }
        $page += 1
    }
    return $records
}

Write-Host '=== Bulk staging.<domain> -> GitHub Pages CNAME wiring ==='
Write-Host "Target  : $Target (proxied=false, DNS-only)"
Write-Host "Mode    : $(if ($DryRun) { 'DRY-RUN (no changes)' } else { 'APPLY (will DELETE+CREATE records)' })"
Write-Host "Tokens  : $(($tokens | ForEach-Object { $_.name }) -join ', ')"
Write-Host ''

$domainList = ($Domains -split '[,\s]+') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim().ToLower() } | Select-Object -Unique
Write-Host "Domains ($($domainList.Count)):"
$domainList | ForEach-Object { Write-Host "  - $_" }
Write-Host ''

$results = @()

foreach ($domain in $domainList) {
    $fqdn = "staging.$domain"
    Write-Host "--- [$domain] ---"

    $zone = Resolve-Zone -Domain $domain
    if (-not $zone) {
        Write-Warning "[$domain] Zone not found via any available token. Skipping."
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'SKIP'
            Detail = 'Zone not found'
            Deleted = 0
            Created = $false
        }
        continue
    }
    Write-Host "[$domain] Zone resolved via $($zone.Account) token (zone id $($zone.ZoneId))"

    try {
        $existing = @(Get-RecordsAtName -ZoneId $zone.ZoneId -Token $zone.Token -Fqdn $fqdn)
    }
    catch {
        Write-Warning "[$domain] List failed: $($_.Exception.Message)"
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'FAIL'
            Detail = "List error: $($_.Exception.Message)"
            Deleted = 0
            Created = $false
        }
        continue
    }

    if ($existing.Count -eq 0) {
        Write-Host "[$domain] No existing records at $fqdn"
    }
    else {
        Write-Host "[$domain] Found $($existing.Count) existing record(s) at $fqdn :"
        foreach ($r in $existing) {
            Write-Host ("  - {0} {1} -> {2} (proxied={3}, ttl={4}, id={5})" -f $r.type, $r.name, $r.content, $r.proxied, $r.ttl, $r.id)
        }
    }

    # Check if we already have the desired record exactly
    $alreadyCorrect = $existing | Where-Object { $_.type -eq 'CNAME' -and $_.content -eq $Target -and [bool]$_.proxied -eq $false }
    $needsWork = $true
    if ($alreadyCorrect -and $existing.Count -eq 1) {
        Write-Host "[$domain] Already correct: CNAME -> $Target (proxied=false). No action."
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'OK'
            Detail = 'Already correct'
            Deleted = 0
            Created = $false
        }
        $needsWork = $false
    }

    if (-not $needsWork) { continue }

    $deletedCount = 0
    $deleteErrors = @()
    foreach ($r in $existing) {
        if ($DryRun) {
            Write-Host "  [DRY-RUN] Would DELETE $($r.type) $($r.name) -> $($r.content) (id=$($r.id))"
            continue
        }
        try {
            $resp = Invoke-Cf -Method DELETE -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records/$($r.id)"
            if ($resp.success) {
                Write-Host "  Deleted $($r.type) $($r.name) -> $($r.content)"
                $deletedCount += 1
            }
            else {
                $msg = ($resp.errors | ConvertTo-Json -Depth 4 -Compress)
                Write-Warning "  DELETE failed for id $($r.id): $msg"
                $deleteErrors += $msg
            }
        }
        catch {
            Write-Warning "  DELETE error for id $($r.id): $($_.Exception.Message)"
            $deleteErrors += $_.Exception.Message
        }
    }

    if ($deleteErrors.Count -gt 0) {
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'FAIL'
            Detail = "Delete errors: $($deleteErrors -join ' | ')"
            Deleted = $deletedCount
            Created = $false
        }
        continue
    }

    $body = @{
        type    = 'CNAME'
        name    = $fqdn
        content = $Target
        ttl     = 1
        proxied = $false
    }
    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would CREATE CNAME $fqdn -> $Target (proxied=false)"
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'DRY-RUN'
            Detail = "Would delete $($existing.Count), create CNAME"
            Deleted = 0
            Created = $false
        }
        continue
    }

    try {
        $resp = Invoke-Cf -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records" -Body $body
        if ($resp.success) {
            Write-Host "  Created CNAME $fqdn -> $Target (proxied=false)"
            $results += [pscustomobject]@{
                Domain = $domain
                Fqdn   = $fqdn
                Status = 'OK'
                Detail = 'CNAME created'
                Deleted = $deletedCount
                Created = $true
            }
        }
        else {
            $msg = ($resp.errors | ConvertTo-Json -Depth 4 -Compress)
            Write-Warning "  CREATE failed: $msg"
            $results += [pscustomobject]@{
                Domain = $domain
                Fqdn   = $fqdn
                Status = 'FAIL'
                Detail = "Create error: $msg"
                Deleted = $deletedCount
                Created = $false
            }
        }
    }
    catch {
        Write-Warning "  CREATE error: $($_.Exception.Message)"
        $results += [pscustomobject]@{
            Domain = $domain
            Fqdn   = $fqdn
            Status = 'FAIL'
            Detail = "Create exception: $($_.Exception.Message)"
            Deleted = $deletedCount
            Created = $false
        }
    }
}

Write-Host ''
Write-Host '=== Per-domain results ==='
$results | Format-Table Domain, Status, Deleted, Created, Detail -AutoSize -Wrap

$ok = @($results | Where-Object { $_.Status -eq 'OK' }).Count
$dry = @($results | Where-Object { $_.Status -eq 'DRY-RUN' }).Count
$fail = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
$skip = @($results | Where-Object { $_.Status -eq 'SKIP' }).Count

Write-Host ''
Write-Host "Summary: OK=$ok DRY-RUN=$dry FAIL=$fail SKIP=$skip (total=$($results.Count))"

if ($env:GITHUB_STEP_SUMMARY) {
    $lines = @(
        "# Bulk staging.<domain> -> GitHub Pages CNAME wiring",
        '',
        "**Target:** ``$Target`` (proxied = false, DNS-only)  ",
        "**Mode:** $(if ($DryRun) { 'DRY-RUN' } else { 'APPLY' })  ",
        "**Domains:** $($domainList.Count)  ",
        "**Result:** OK=$ok DRY-RUN=$dry FAIL=$fail SKIP=$skip",
        '',
        '| Domain | Status | Deleted | Created | Detail |',
        '| --- | --- | --- | --- | --- |'
    )
    foreach ($r in $results) {
        $detail = ($r.Detail -replace '\|', '\\|')
        $lines += "| $($r.Domain) | $($r.Status) | $($r.Deleted) | $($r.Created) | $detail |"
    }
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($lines -join "`n")
}

if ($fail -gt 0 -or $skip -gt 0) { exit 1 }
