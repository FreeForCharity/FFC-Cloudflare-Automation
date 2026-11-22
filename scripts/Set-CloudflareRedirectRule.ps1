<#
.SYNOPSIS
    Create or update a Cloudflare Single Redirect Rule that 301s a source domain to a target domain.

.DESCRIPTION
    Idempotently adds a rule to the source zone's http_request_dynamic_redirect phase ruleset.
    The rule matches on apex and www subdomain of the source domain and redirects to the same
    path on the target domain (preserving query string).

    Rule expression:
        (http.host eq "<source>") or (http.host eq "www.<source>")

    Target URL expression:
        concat("https://<target>", http.request.uri.path)

    Matches existing rule by description to avoid duplicates. Re-running updates in place.

.PARAMETER SourceDomain
    The host whose traffic should be redirected. Either a Cloudflare zone apex (e.g. ffcsites.org)
    or a host inside one (e.g. school.newheightseducation.org). For a host inside a zone, the zone
    is found by walking up the labels, and the rule is added to that zone's ruleset.

.PARAMETER TargetDomain
    The destination host, optionally with a path prefix (e.g. ffcadmin.org, or
    newheightseducation.org/school). The request path is appended to it. The script always uses
    https://.

.PARAMETER ProxySourceRecords
    Also set the source host's A/AAAA/CNAME records (and www.<source>'s, unless -ApexOnly) to
    proxied. A redirect rule only runs for proxied hostnames, so a DNS-only source record would
    never reach it. The rule is written first, so the records are never proxied without it.
    Fails if the source host has no such record.

.PARAMETER StatusCode
    HTTP status code for the redirect. Default 301.

.PARAMETER IncludeWww
    Also match www.<source> in addition to apex. Default true.

.PARAMETER PreserveQueryString
    Pass the query string through to the target. Default true.

.PARAMETER Description
    Override the rule description. Default: "Repoint <source> to <target>".

.PARAMETER Token
    Cloudflare API token. If omitted, falls back to CLOUDFLARE_API_TOKEN / CLOUDFLARE_API_TOKEN_FFC /
    CLOUDFLARE_API_TOKEN_CM env vars (auto-detects which can access the zone).

.PARAMETER DryRun
    Preview the planned ruleset PUT without applying.

.EXAMPLE
    .\Set-CloudflareRedirectRule.ps1 -SourceDomain ffcsites.org -TargetDomain ffcadmin.org

.EXAMPLE
    .\Set-CloudflareRedirectRule.ps1 -SourceDomain ffcsites.org -TargetDomain ffcadmin.org -DryRun

.EXAMPLE
    .\Set-CloudflareRedirectRule.ps1 -SourceDomain school.newheightseducation.org `
        -TargetDomain newheightseducation.org/school -ProxySourceRecords -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceDomain,
    [Parameter(Mandatory = $true)][string]$TargetDomain,
    [ValidateSet(301, 302, 307, 308)][int]$StatusCode = 301,
    [switch]$ApexOnly,
    [switch]$NoPreserveQueryString,
    [string]$Description,
    [string]$Token,
    [switch]$ProxySourceRecords,
    [switch]$DryRun
)

$IncludeWww = -not $ApexOnly.IsPresent
$PreserveQueryString = -not $NoPreserveQueryString.IsPresent

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://api.cloudflare.com/client/v4'

# --- Validate inputs (they are interpolated into a Cloudflare rule expression) ---
$SourceDomain = $SourceDomain.Trim().ToLowerInvariant().TrimEnd('.')
$TargetDomain = $TargetDomain.Trim().TrimEnd('/')
if ($SourceDomain -notmatch '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$') {
    throw "SourceDomain '$SourceDomain' is not a valid host name."
}
if ($TargetDomain -notmatch '^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?:/[A-Za-z0-9._~/-]*)?$') {
    throw "TargetDomain '$TargetDomain' must be a host with an optional path prefix (e.g. example.org or example.org/section)."
}

if ([string]::IsNullOrWhiteSpace($Description)) {
    $Description = "Repoint $SourceDomain to $TargetDomain"
}

# --- Token resolution (mirrors Update-CloudflareDns.ps1) ---
function Test-ZoneAccess {
    param([string]$ZoneName, [string]$CandidateToken)
    try {
        $h = @{ Authorization = "Bearer $CandidateToken"; 'Content-Type' = 'application/json' }
        $encoded = [uri]::EscapeDataString($ZoneName)
        $resp = Invoke-RestMethod -Method Get -Uri "$ApiBase/zones?name=$encoded" -Headers $h -TimeoutSec 30
        return ($resp.success -and $resp.result -and $resp.result.Count -gt 0)
    }
    catch { return $false }
}

function Get-AuthToken {
    param([string]$ZoneName)
    if ($Token) { return $Token.Trim() }

    $candidates = @(
        $env:CLOUDFLARE_API_TOKEN,
        $env:CLOUDFLARE_API_TOKEN_FFC,
        $env:CLOUDFLARE_API_TOKEN_CM
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_.Trim() } | Select-Object -Unique

    if ($candidates.Count -eq 0) {
        throw 'Cloudflare API token not found. Pass -Token or set CLOUDFLARE_API_TOKEN, CLOUDFLARE_API_TOKEN_FFC, or CLOUDFLARE_API_TOKEN_CM.'
    }
    if ($candidates.Count -eq 1) { return $candidates[0] }

    foreach ($cand in $candidates) {
        if (Test-ZoneAccess -ZoneName $ZoneName -CandidateToken $cand) { return $cand }
    }
    throw "No Cloudflare API token has access to zone '$ZoneName'."
}

# Candidate zones for a host, most specific first: school.example.org -> school.example.org,
# example.org. Stops at two labels; a public suffix is never a Cloudflare zone.
function Get-ZoneCandidates {
    param([string]$HostName)
    $labels = $HostName.Split('.')
    for ($i = 0; $i -le $labels.Count - 2; $i++) {
        ($labels[$i..($labels.Count - 1)] -join '.')
    }
}

# --- Resolve the zone that holds the source host ---
# Fail on a missing token here, not inside the loop below, which skips candidates
# whose token lookup fails and would otherwise report "no zone found" instead.
if (-not $Token -and -not ($env:CLOUDFLARE_API_TOKEN -or $env:CLOUDFLARE_API_TOKEN_FFC -or $env:CLOUDFLARE_API_TOKEN_CM)) {
    throw 'Cloudflare API token not found. Pass -Token or set CLOUDFLARE_API_TOKEN, CLOUDFLARE_API_TOKEN_FFC, or CLOUDFLARE_API_TOKEN_CM.'
}
Write-Host "Resolving zone for $SourceDomain..."
$zoneName = $null
$zoneId = $null
$AuthToken = $null
foreach ($candidate in (Get-ZoneCandidates -HostName $SourceDomain)) {
    try { $candidateToken = Get-AuthToken -ZoneName $candidate } catch { continue }
    $h = @{ Authorization = "Bearer $candidateToken"; 'Content-Type' = 'application/json' }
    $encoded = [uri]::EscapeDataString($candidate)
    $zoneResp = Invoke-RestMethod -Method Get -Uri "$ApiBase/zones?name=$encoded" -Headers $h -TimeoutSec 30
    if ($zoneResp.success -and $zoneResp.result -and $zoneResp.result.Count -gt 0) {
        $zoneName = $candidate
        $zoneId = $zoneResp.result[0].id
        $AuthToken = $candidateToken
        break
    }
}
if (-not $zoneId) {
    throw "No Cloudflare zone found for '$SourceDomain' (tried: $((Get-ZoneCandidates -HostName $SourceDomain) -join ', ')), or no token has access."
}
$Headers = @{ Authorization = "Bearer $AuthToken"; 'Content-Type' = 'application/json' }
Write-Host "  zone:    $zoneName"
Write-Host "  zone_id: $zoneId"

# --- Source host DNS records that -ProxySourceRecords would proxy ---
$recordsToProxy = @()
if ($ProxySourceRecords) {
    $hosts = @($SourceDomain)
    if ($IncludeWww) { $hosts += "www.$SourceDomain" }
    foreach ($hostName in $hosts) {
        $encodedHost = [uri]::EscapeDataString($hostName)
        $recResp = Invoke-RestMethod -Method Get -Uri "$ApiBase/zones/$zoneId/dns_records?name=$encodedHost&per_page=100" -Headers $Headers -TimeoutSec 30
        $recs = @($recResp.result | Where-Object { $_.type -in @('A', 'AAAA', 'CNAME') })
        if ($recs.Count -eq 0) {
            if ($hostName -eq $SourceDomain) {
                throw "-ProxySourceRecords: no A/AAAA/CNAME record exists for '$hostName', so the redirect rule could never run. Create the record first."
            }
            Write-Host "  no A/AAAA/CNAME record for $hostName (skipping)."
            continue
        }
        foreach ($rec in $recs) {
            if ($rec.proxied) {
                Write-Host ("  {0} {1} -> {2} already proxied." -f $rec.type, $rec.name, $rec.content)
            }
            else {
                $recordsToProxy += $rec
            }
        }
    }
}

# --- Build the rule we want to ensure exists ---
$hostExpression = '(http.host eq "{0}")' -f $SourceDomain
if ($IncludeWww) {
    $hostExpression = '(http.host eq "{0}") or (http.host eq "www.{0}")' -f $SourceDomain
}

$targetExpression = 'concat("https://{0}", http.request.uri.path)' -f $TargetDomain

$desiredRule = [ordered]@{
    action            = 'redirect'
    action_parameters = [ordered]@{
        from_value = [ordered]@{
            target_url            = [ordered]@{ expression = $targetExpression }
            status_code           = $StatusCode
            preserve_query_string = $PreserveQueryString
        }
    }
    expression        = $hostExpression
    description       = $Description
    enabled           = $true
}

# --- Fetch existing http_request_dynamic_redirect phase entrypoint ---
$phaseUri = "$ApiBase/zones/$zoneId/rulesets/phases/http_request_dynamic_redirect/entrypoint"
Write-Host "Fetching existing redirect ruleset..."
$existingRuleset = $null
try {
    $existingResp = Invoke-RestMethod -Method Get -Uri $phaseUri -Headers $Headers -TimeoutSec 30
    if ($existingResp.success) { $existingRuleset = $existingResp.result }
}
catch {
    # 404 is expected when no entrypoint ruleset exists yet for this phase
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
    Write-Host "  no existing ruleset (will create new entrypoint)."
}

# --- Compose the new rules array ---
$existingRules = @()
if ($existingRuleset -and $existingRuleset.rules) {
    $existingRules = @($existingRuleset.rules)
}

# Idempotency: replace any rule whose description matches ours
$filteredRules = @($existingRules | Where-Object { $_.description -ne $Description })
$replacedCount = $existingRules.Count - $filteredRules.Count
if ($replacedCount -gt 0) {
    Write-Host "  found $replacedCount existing rule(s) with description '$Description' — will replace."
}

$newRules = @($filteredRules + $desiredRule)

$payload = [ordered]@{
    rules = $newRules
}

Write-Host ""
Write-Host "Planned rule:" -ForegroundColor Cyan
Write-Host "  match:  $hostExpression"
Write-Host "  target: $targetExpression"
Write-Host "  status: $StatusCode"
Write-Host "  preserve_query_string: $PreserveQueryString"
Write-Host "  description: $Description"
Write-Host ""
Write-Host "Ruleset will have $($newRules.Count) total rule(s) after apply." -ForegroundColor Cyan
if ($ProxySourceRecords) {
    Write-Host ""
    Write-Host "Records to switch to proxied (after the rule is written): $($recordsToProxy.Count)" -ForegroundColor Cyan
    foreach ($rec in $recordsToProxy) {
        Write-Host ("  {0} {1} -> {2} (proxied=false -> true)" -f $rec.type, $rec.name, $rec.content)
    }
}

if ($DryRun) {
    if ($existingRuleset) {
        Write-Host ""
        Write-Host "DRY RUN: would PUT to $phaseUri" -ForegroundColor Yellow
    }
    else {
        Write-Host ""
        Write-Host "DRY RUN: would POST to $ApiBase/zones/$zoneId/rulesets (no existing entrypoint)" -ForegroundColor Yellow
    }
    $payload | ConvertTo-Json -Depth 10
    return
}

# --- Apply: PUT if the phase entrypoint already exists, POST otherwise ---
Write-Host ""
Write-Host "Applying ruleset..." -ForegroundColor Green

if ($existingRuleset) {
    # Update existing entrypoint with the new rules list
    $body = $payload | ConvertTo-Json -Depth 10 -Compress
    $applyResp = Invoke-RestMethod -Method Put -Uri $phaseUri -Headers $Headers -Body $body -TimeoutSec 30
}
else {
    # Create a new entrypoint ruleset for this phase. CF requires kind+phase+name on POST.
    $createPayload = [ordered]@{
        name        = "default"
        description = "Entrypoint ruleset for http_request_dynamic_redirect phase"
        kind        = "zone"
        phase       = "http_request_dynamic_redirect"
        rules       = $newRules
    }
    $createUri = "$ApiBase/zones/$zoneId/rulesets"
    $body = $createPayload | ConvertTo-Json -Depth 10 -Compress
    $applyResp = Invoke-RestMethod -Method Post -Uri $createUri -Headers $Headers -Body $body -TimeoutSec 30
}

if (-not $applyResp.success) {
    Write-Error ("Cloudflare API returned failure: " + ($applyResp.errors | ConvertTo-Json -Depth 5))
    exit 1
}

Write-Host "SUCCESS: redirect rule for $SourceDomain → https://$TargetDomain is live." -ForegroundColor Green
Write-Host "Ruleset ID: $($applyResp.result.id)"

# --- Proxy the source records, only now that the rule exists ---
foreach ($rec in $recordsToProxy) {
    $patchBody = @{ proxied = $true } | ConvertTo-Json -Compress
    $patchResp = Invoke-RestMethod -Method Patch -Uri "$ApiBase/zones/$zoneId/dns_records/$($rec.id)" -Headers $Headers -Body $patchBody -TimeoutSec 30
    if (-not $patchResp.success) {
        Write-Error ("Failed to proxy {0} {1}: {2}" -f $rec.type, $rec.name, ($patchResp.errors | ConvertTo-Json -Depth 5))
        exit 1
    }
    Write-Host ("  proxied {0} {1} -> {2}" -f $rec.type, $rec.name, $rec.content) -ForegroundColor Green
}
Write-Host "Verify with: curl -sI https://$SourceDomain  # expect HTTP $StatusCode → https://$TargetDomain"
