<#
.SYNOPSIS
    Find every A record in every Cloudflare zone (FFC + CM accounts) whose
    content matches -OldIp, and update them to -NewIp.

.DESCRIPTION
    Use this when a host provider re-IPs a shared server and customer-pointed
    A records across many zones all need to follow. The script:

      1. Enumerates all zones reachable by $env:CLOUDFLARE_API_TOKEN_FFC and
         $env:CLOUDFLARE_API_TOKEN_CM (de-duped by zone id).
      2. For each zone, lists every A record.
      3. Filters to those whose content == $OldIp.
      4. In DryRun mode: prints a table of what would change.
         In real mode: PATCHes each record to content = $NewIp and emits a
         report at the end with successes and failures.

    Proxied flag and TTL are preserved per record.

.PARAMETER OldIp
    The IP address currently in the A record content.

.PARAMETER NewIp
    The IP address to set on every matching record.

.PARAMETER DryRun
    If set, no PATCH calls are made. Discovery + report only.

.EXAMPLE
    $env:CLOUDFLARE_API_TOKEN_FFC = '...'
    $env:CLOUDFLARE_API_TOKEN_CM  = '...'
    .\bulk-replace-a-record-ip.ps1 -OldIp 204.44.192.77 -NewIp 216.222.200.253 -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')]
    [string]$OldIp,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')]
    [string]$NewIp,

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
        [string]$Token,
        $Body
    )
    $url = "https://api.cloudflare.com/client/v4$Path"
    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    $params = @{ Method = $Method; Uri = $url; Headers = $headers }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 6 -Compress) }
    return Invoke-RestMethod @params
}

function Get-AllZones {
    param([string]$Token)
    $zones = @()
    $page = 1
    while ($true) {
        $resp = Invoke-Cf -Method GET -Token $Token -Path "/zones?per_page=50&page=$page"
        if (-not $resp.success) { throw "Failed to list zones: $($resp.errors | ConvertTo-Json -Depth 4)" }
        $zones += $resp.result
        $totalPages = $resp.result_info.total_pages
        if ($page -ge $totalPages) { break }
        $page += 1
    }
    return $zones
}

function Get-MatchingARecords {
    param(
        [Parameter(Mandatory)][string]$ZoneId,
        [Parameter(Mandatory)][string]$Token,
        [Parameter(Mandatory)][string]$Ip
    )
    # Cloudflare supports server-side filtering on type+content
    $records = @()
    $page = 1
    while ($true) {
        $path = "/zones/$ZoneId/dns_records?type=A&content=$Ip&per_page=50&page=$page"
        $resp = Invoke-Cf -Method GET -Token $Token -Path $path
        if (-not $resp.success) { throw "Failed to list records: $($resp.errors | ConvertTo-Json -Depth 4)" }
        $records += $resp.result
        $totalPages = $resp.result_info.total_pages
        if ($page -ge $totalPages) { break }
        $page += 1
    }
    return $records
}

Write-Host "=== Bulk A-record IP replacement ==="
Write-Host "Old IP : $OldIp"
Write-Host "New IP : $NewIp"
Write-Host "Mode   : $(if ($DryRun) { 'DRY-RUN (no changes)' } else { 'APPLY (will PATCH records)' })"
Write-Host "Tokens : $(($tokens | ForEach-Object { $_.name }) -join ', ')"
Write-Host ''

$plan = @()

foreach ($t in $tokens) {
    Write-Host "[Account: $($t.name)] Listing zones..."
    $zones = Get-AllZones -Token $t.token
    Write-Host "[Account: $($t.name)] Found $($zones.Count) zones"
    foreach ($z in $zones) {
        try {
            $matches = Get-MatchingARecords -ZoneId $z.id -Token $t.token -Ip $OldIp
        }
        catch {
            Write-Warning "[$($t.name) :: $($z.name)] List failed: $($_.Exception.Message)"
            continue
        }
        foreach ($r in $matches) {
            $plan += [pscustomobject]@{
                Account    = $t.name
                Token      = $t.token
                Zone       = $z.name
                ZoneId     = $z.id
                RecordId   = $r.id
                Name       = $r.name
                OldContent = $r.content
                Proxied    = [bool]$r.proxied
                Ttl        = $r.ttl
            }
        }
    }
}

Write-Host ''
Write-Host "=== Plan ($($plan.Count) records to update) ==="
if ($plan.Count -eq 0) {
    Write-Host 'No matching A records found across any zone.'
    return
}

$plan | Select-Object Account, Zone, Name, OldContent, Proxied, Ttl | Format-Table -AutoSize

# Emit GitHub Actions step summary if available
if ($env:GITHUB_STEP_SUMMARY) {
    $lines = @(
        "# Bulk A-record IP replacement",
        '',
        "**Old IP:** ``$OldIp``  ",
        "**New IP:** ``$NewIp``  ",
        "**Mode:** $(if ($DryRun) { 'DRY-RUN' } else { 'APPLY' })  ",
        "**Records found:** $($plan.Count)",
        '',
        '| Account | Zone | Name | Proxied | TTL |',
        '| --- | --- | --- | --- | --- |'
    )
    foreach ($p in $plan) {
        $lines += "| $($p.Account) | $($p.Zone) | $($p.Name) | $($p.Proxied) | $($p.Ttl) |"
    }
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($lines -join "`n")
}

if ($DryRun) {
    Write-Host ''
    Write-Host 'DRY-RUN: no PATCH calls made. Re-run with -DryRun:$false to apply.'
    return
}

Write-Host ''
Write-Host '=== Applying changes ==='
$succeeded = @()
$failed = @()

foreach ($p in $plan) {
    $body = @{
        type    = 'A'
        name    = $p.Name
        content = $NewIp
        ttl     = $p.Ttl
        proxied = $p.Proxied
    }
    try {
        $resp = Invoke-Cf -Method PATCH -Token $p.Token -Path "/zones/$($p.ZoneId)/dns_records/$($p.RecordId)" -Body $body
        if ($resp.success) {
            Write-Host "OK   [$($p.Account)] $($p.Zone) :: $($p.Name) -> $NewIp"
            $succeeded += $p
        }
        else {
            $msg = ($resp.errors | ConvertTo-Json -Depth 4 -Compress)
            Write-Warning "FAIL [$($p.Account)] $($p.Zone) :: $($p.Name) -> $msg"
            $failed += $p
        }
    }
    catch {
        Write-Warning "FAIL [$($p.Account)] $($p.Zone) :: $($p.Name) -> $($_.Exception.Message)"
        $failed += $p
    }
}

Write-Host ''
Write-Host "=== Result: $($succeeded.Count) succeeded, $($failed.Count) failed ==="

if ($env:GITHUB_STEP_SUMMARY) {
    $summary = @(
        '',
        "## Apply result",
        '',
        "- Succeeded: $($succeeded.Count)",
        "- Failed: $($failed.Count)"
    )
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($summary -join "`n")
}

if ($failed.Count -gt 0) { exit 1 }
