<#
.SYNOPSIS
    Atomically cut each FFC-EX charity site over from staging.<domain> (GH Pages)
    to the apex <domain> (GH Pages via Cloudflare DNS flip).

.DESCRIPTION
    For each domain in -Domains this script performs two coordinated steps:

      STEP 1 — CNAME flip in the FFC-EX GitHub repo
        Reads public/CNAME from FreeForCharity/FFC-EX-<domain>, verifies it
        currently contains staging.<domain>, then PUTs <domain> (apex) to
        commit the change directly to main. GH Pages picks up the new
        custom_domain within ~60-90 s.

      STEP 2 — DNS flip in Cloudflare  (skipped if -SkipDns)
        a. Resolves the Cloudflare zone via FFC then CM tokens.
        b. Finds every apex A record whose content is the dead HostPapa IP
           216.222.200.253.
        c. Deletes them all.
        d. Creates 4 new apex A records pointing to the GH Pages anycast IPs
           (185.199.108-111.153) with proxied=false and TTL=1 (auto).

      IDEMPOTENCY — each step is skipped if already in the desired state:
        - CNAME already apex? Skip CNAME write.
        - Apex A records already all four GH Pages IPs and no HostPapa IP? Skip DNS.

      ZONES IN OTHER CF ACCOUNTS — two zones are not accessible via FFC/CM tokens:
        - nj4israel.org   (Njsi2013@gmail.com CF account)
        - americanlegionpost64.org (American Legion Post 64 CF account)
      Both are reported as SKIP-MANUAL with instructions for the operator.

.PARAMETER Domains
    Comma-separated list of root domains (e.g.
      'aprilhansen.com,armstrongacesbaseball.org')
    Do not include 'staging.' prefix.

.PARAMETER DryRun
    Discover-only mode — prints what would happen without making any changes.

.PARAMETER SkipDns
    Skip the Cloudflare DNS flip entirely (useful when only testing the CNAME step).

.PARAMETER GhPagesIps
    Override the 4 GH Pages anycast IPs. Defaults are the current canonical set:
    185.199.108.153, 185.199.109.153, 185.199.110.153, 185.199.111.153

.PARAMETER HostPapaIp
    The legacy HostPapa IP to delete from Cloudflare apex A records.
    Default: 216.222.200.253

.EXAMPLE
    # Dry-run for a single domain
    $env:CLOUDFLARE_API_TOKEN_FFC = '...'
    $env:GH_TOKEN = '...'
    .\bulk-cutover-to-github-pages.ps1 -Domains 'aprilhansen.com' -DryRun

.EXAMPLE
    # Apply to all 13 in-scope domains
    .\bulk-cutover-to-github-pages.ps1 -Domains 'aprilhansen.com,...'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domains,

    [switch]$DryRun,

    [switch]$SkipDns,

    [switch]$SkipCname,

    [string[]]$GhPagesIps = @(
        '185.199.108.153',
        '185.199.109.153',
        '185.199.110.153',
        '185.199.111.153'
    ),

    [string]$HostPapaIp = '216.222.200.253'
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Zones that are NOT accessible via FFC/CM Cloudflare tokens.
# The operator will handle these manually in the respective CF dashboards.
# ---------------------------------------------------------------------------
$ManualCfZones = @{
    'nj4israel.org'            = 'Njsi2013@gmail.com Cloudflare account'
    'americanlegionpost64.org' = 'American Legion Post 64 Cloudflare account'
}

# ---------------------------------------------------------------------------
# Token setup
# ---------------------------------------------------------------------------
$cfTokens = @()
if ($env:CLOUDFLARE_API_TOKEN_FFC) { $cfTokens += @{ name = 'FFC'; token = $env:CLOUDFLARE_API_TOKEN_FFC } }
if ($env:CLOUDFLARE_API_TOKEN_CM) { $cfTokens += @{ name = 'CM'; token = $env:CLOUDFLARE_API_TOKEN_CM } }

$ghToken = $env:GH_TOKEN
if (-not $SkipCname -and [string]::IsNullOrWhiteSpace($ghToken)) {
    throw 'GH_TOKEN environment variable is not set. Required for GitHub API calls (CNAME flip). Or run with -SkipCname.'
}
if (-not $SkipDns -and $cfTokens.Count -eq 0) {
    throw 'No Cloudflare tokens found. Set CLOUDFLARE_API_TOKEN_FFC and/or CLOUDFLARE_API_TOKEN_CM, or run with -SkipDns.'
}
if ($SkipCname -and $SkipDns) {
    throw 'Both -SkipCname and -SkipDns are set — nothing to do.'
}

# ---------------------------------------------------------------------------
# Helpers — Cloudflare REST
# ---------------------------------------------------------------------------
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

function Resolve-CfZone {
    param([Parameter(Mandatory)][string]$Domain)
    foreach ($t in $cfTokens) {
        try {
            $encoded = [uri]::EscapeDataString($Domain)
            $resp = Invoke-Cf -Method GET -Token $t.token -Path "/zones?name=$encoded"
            if ($resp.success -and $resp.result -and $resp.result.Count -gt 0) {
                return [pscustomobject]@{
                    Account  = $t.name
                    Token    = $t.token
                    ZoneId   = $resp.result[0].id
                    ZoneName = $resp.result[0].name
                }
            }
        }
        catch { <# try next token #> }
    }
    return $null
}

function Get-ApexARecords {
    param(
        [Parameter(Mandatory)][string]$ZoneId,
        [Parameter(Mandatory)][string]$Token,
        [Parameter(Mandatory)][string]$Domain
    )
    $records = @()
    $page = 1
    while ($true) {
        $encoded = [uri]::EscapeDataString($Domain)
        $resp = Invoke-Cf -Method GET -Token $Token -Path "/zones/$ZoneId/dns_records?type=A&name=$encoded&per_page=50&page=$page"
        if (-not $resp.success) {
            throw "Failed to list apex A records for $Domain : $($resp.errors | ConvertTo-Json -Depth 4 -Compress)"
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

# ---------------------------------------------------------------------------
# Helpers — GitHub REST
# ---------------------------------------------------------------------------
function Invoke-Gh {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        $Body
    )
    $url = "https://api.github.com$Path"
    $headers = @{
        Authorization          = "Bearer $ghToken"
        Accept                 = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    $params = @{ Method = $Method; Uri = $url; Headers = $headers; TimeoutSec = 30 }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 8 -Compress) }
    return Invoke-RestMethod @params
}

function Get-GhFileInfo {
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$FilePath
    )
    # Returns @{ Content = <decoded string>; Sha = <sha> } or $null if 404
    try {
        $resp = Invoke-Gh -Method GET -Path "/repos/$Repo/contents/$FilePath"
        $decoded = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($resp.content))
        return @{ Content = $decoded.Trim(); Sha = $resp.sha }
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode -eq 404) {
            return $null
        }
        throw
    }
}

function Set-GhFileContent {
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$NewContent,
        [Parameter(Mandatory)][string]$Sha,
        [Parameter(Mandatory)][string]$CommitMessage
    )
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($NewContent))
    $body = @{
        message = $CommitMessage
        content = $encoded
        sha     = $Sha
    }
    return Invoke-Gh -Method PUT -Path "/repos/$Repo/contents/$FilePath" -Body $body
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host '=== Bulk cutover: staging.<domain> -> apex <domain> (GH Pages + Cloudflare) ==='
Write-Host "Mode        : $(if ($DryRun) { 'DRY-RUN (no changes)' } else { 'APPLY (will commit + update DNS)' })"
Write-Host "Skip DNS    : $($SkipDns.IsPresent)"
Write-Host "HostPapa IP : $HostPapaIp  (will be deleted)"
Write-Host "GH Pages IPs: $($GhPagesIps -join ', ')"
if ($cfTokens.Count -gt 0) {
    Write-Host "CF Tokens   : $(($cfTokens | ForEach-Object { $_.name }) -join ', ')"
}
Write-Host ''

$domainList = ($Domains -split '[,\s]+') |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    ForEach-Object { $_.Trim().ToLower() } |
    Select-Object -Unique

Write-Host "Domains ($($domainList.Count)):"
$domainList | ForEach-Object { Write-Host "  - $_" }
Write-Host ''

# ---------------------------------------------------------------------------
# Per-domain processing
# ---------------------------------------------------------------------------
$results = @()

foreach ($domain in $domainList) {
    Write-Host "--- [$domain] ---"

    $result = [pscustomobject]@{
        Domain      = $domain
        CnameStatus = 'PENDING'
        CnameDetail = ''
        DnsStatus   = 'PENDING'
        DnsDetail   = ''
    }

    # -------------------------------------------------------------------
    # Check for manual-CF-account zones (can't flip DNS programmatically)
    # -------------------------------------------------------------------
    $manualAccount = $ManualCfZones[$domain]
    if ($manualAccount) {
        Write-Warning "[$domain] Zone lives in $manualAccount — not accessible via FFC/CM tokens."
        Write-Warning "[$domain] DNS flip: flip apex A records MANUALLY in the $manualAccount CF dashboard."
        Write-Warning "[$domain]   Delete : 216.222.200.253"
        Write-Warning "[$domain]   Create : 185.199.108.153 / .109.153 / .110.153 / .111.153 (proxied=false)"
        $result.DnsStatus = 'SKIP-MANUAL'
        $result.DnsDetail = "Zone in $manualAccount — flip apex A manually in CF dashboard"
        # Still do the CNAME flip via GH API (that works regardless of CF account)
    }

    # ===================================================================
    # STEP 1: CNAME flip in FFC-EX GitHub repo
    # ===================================================================
    if ($SkipCname) {
        Write-Host "[$domain] STEP 1 — CNAME flip SKIPPED (-SkipCname)"
        $result.CnameStatus = 'SKIP'
        $result.CnameDetail = 'Skipped (-SkipCname)'
    }
    else {
    $repo = "FreeForCharity/FFC-EX-$domain"
    Write-Host "[$domain] STEP 1 — CNAME flip in $repo"

    try {
        $fileInfo = Get-GhFileInfo -Repo $repo -FilePath 'public/CNAME'

        if ($null -eq $fileInfo) {
            Write-Warning "[$domain] public/CNAME not found in $repo — skipping CNAME step."
    $result.CnameStatus = 'SKIP'
    $result.CnameDetail = 'public/CNAME not found in repo'
}
else {
    $currentContent = $fileInfo.Content
    $apexContent = $domain
    $stagingContent = "staging.$domain"

    Write-Host "[$domain]   Current CNAME content: '$currentContent'"

    if ($currentContent -eq $apexContent) {
        Write-Host "[$domain]   Already apex — no CNAME change needed."
                $result.CnameStatus = 'OK'
                $result.CnameDetail = 'Already apex'
            }
            elseif ($currentContent -ne $stagingContent) {
                Write-Warning "[$domain]   Unexpected CNAME content '$currentContent' (expected '$stagingContent' or '$apexContent'). Skipping."
                $result.CnameStatus = 'SKIP'
                $result.CnameDetail = "Unexpected content: '$currentContent'"
            }
            else {
                # Current is staging.<domain> — flip to apex
        if ($DryRun) {
            Write-Host "[$domain]   [DRY-RUN] Would commit public/CNAME: '$currentContent' -> '$apexContent'"
            $result.CnameStatus = 'DRY-RUN'
            $result.CnameDetail = "Would update: '$currentContent' -> '$apexContent'"
        }
        else {
            Write-Host "[$domain]   Committing public/CNAME: '$stagingContent' -> '$apexContent'"
            $commitMsg = "chore: flip GH Pages custom domain to apex $domain`n`n[automated cutover]"
            $null = Set-GhFileContent -Repo $repo -FilePath 'public/CNAME' `
                -NewContent $apexContent -Sha $fileInfo.Sha -CommitMessage $commitMsg
            Write-Host "[$domain]   CNAME commit OK."
            $result.CnameStatus = 'OK'
            $result.CnameDetail = "Updated: '$stagingContent' -> '$apexContent'"
        }
    }
}
}
catch {
    $errMsg = $_.Exception.Message
    # Capture HTTP response body for diagnostics (PowerShell hides it by default)
    $respBody = ''
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        $respBody = $_.ErrorDetails.Message
    }
    elseif ($_.Exception.Response) {
        try {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $respBody = $reader.ReadToEnd()
        }
        catch { }
    }
    # Token diagnostics (length only — never print value)
    $tokLen = if ($ghToken) { $ghToken.Length } else { 0 }
    $tokPrefix = if ($ghToken -and $ghToken.Length -ge 4) { $ghToken.Substring(0, 4) } else { '' }
    Write-Warning "[$domain]   CNAME step error: $errMsg"
    if ($respBody) { Write-Warning "[$domain]   Response body: $respBody" }
    Write-Warning "[$domain]   GH_TOKEN length=$tokLen prefix=$tokPrefix (prefix tells PAT type: ghp_=classic, github_pat_=fine-grained)"
    $result.CnameStatus = 'FAIL'
    $result.CnameDetail = if ($respBody) { "$errMsg | $respBody" } else { $errMsg }
}
    }  # end else (SkipCname)

# ===================================================================
# STEP 2: DNS flip in Cloudflare
# ===================================================================
if ($SkipDns) {
    Write-Host "[$domain] STEP 2 — DNS skipped (-SkipDns flag set)"
        $result.DnsStatus = 'SKIP'
        $result.DnsDetail = '-SkipDns flag'
    }
    elseif ($result.DnsStatus -eq 'SKIP-MANUAL') {
        Write-Host "[$domain] STEP 2 — DNS skipped (manual zone — see warning above)"
    }
    else {
        Write-Host "[$domain] STEP 2 — DNS flip in Cloudflare"

try {
    $zone = Resolve-CfZone -Domain $domain
    if (-not $zone) {
        Write-Warning "[$domain]   Zone not found via any available CF token."
        $result.DnsStatus = 'SKIP'
        $result.DnsDetail = 'CF zone not found'
    }
    else {
        Write-Host "[$domain]   Zone resolved via $($zone.Account) token (id $($zone.ZoneId))"

        $apexRecords = @(Get-ApexARecords -ZoneId $zone.ZoneId -Token $zone.Token -Domain $domain)
        Write-Host "[$domain]   Found $($apexRecords.Count) apex A record(s):"
        foreach ($r in $apexRecords) {
            Write-Host ("    - A {0} -> {1} (proxied={2}, ttl={3}, id={4})" -f $r.name, $r.content, $r.proxied, $r.ttl, $r.id)
        }

        # --- Idempotency check ---
        $hostPapaRecords = @($apexRecords | Where-Object { $_.content -eq $HostPapaIp })
        $ghPagesRecords = @($apexRecords | Where-Object { $GhPagesIps -contains $_.content })
        $otherRecords = @($apexRecords | Where-Object { $_.content -ne $HostPapaIp -and $GhPagesIps -notcontains $_.content })

        $allGhPagesPresent = ($GhPagesIps | Where-Object {
                $ip = $_
                $ghPagesRecords | Where-Object { $_.content -eq $ip }
            }).Count -eq $GhPagesIps.Count

        if ($hostPapaRecords.Count -eq 0 -and $allGhPagesPresent -and $otherRecords.Count -eq 0) {
            Write-Host "[$domain]   Already correct: 4 GH Pages A records, no HostPapa IP. No DNS change needed."
            $result.DnsStatus = 'OK'
            $result.DnsDetail = 'Already correct (4 GH Pages IPs, no HostPapa IP)'
        }
        else {
            # Report what we'll do
            $toDelete = @($apexRecords | Where-Object { $_.content -eq $HostPapaIp })
            $ipsAlreadyPresent = @($ghPagesRecords | ForEach-Object { $_.content })
            $toCreate = @($GhPagesIps | Where-Object { $ipsAlreadyPresent -notcontains $_ })

            if ($toDelete.Count -gt 0) {
                foreach ($r in $toDelete) {
                    Write-Host "[$domain]   Would DELETE apex A -> $($r.content) (id=$($r.id))"
                }
            }
            if ($toCreate.Count -gt 0) {
                Write-Host "[$domain]   Would CREATE apex A -> $($toCreate -join ', ') (proxied=false)"
            }
            if ($otherRecords.Count -gt 0) {
                Write-Warning "[$domain]   WARNING: $($otherRecords.Count) apex A record(s) with unexpected IPs will be left untouched:"
                foreach ($r in $otherRecords) {
                    Write-Warning "    - A -> $($r.content) (id=$($r.id))"
                }
            }

            if ($DryRun) {
                $dnsDetail = @()
                if ($toDelete.Count -gt 0) { $dnsDetail += "Would DELETE $($toDelete.Count) HostPapa A record(s)" }
                if ($toCreate.Count -gt 0) { $dnsDetail += "Would CREATE $($toCreate.Count) GH Pages A record(s) ($($toCreate -join ', '))" }
                if ($otherRecords.Count -gt 0) { $dnsDetail += "$($otherRecords.Count) unexpected A record(s) left untouched" }
                $result.DnsStatus = 'DRY-RUN'
                $result.DnsDetail = $dnsDetail -join '; '
            }
            else {
                $dnsErrors = @()

                # Delete HostPapa record(s)
                foreach ($r in $toDelete) {
                    Write-Host "[$domain]   Deleting A -> $($r.content) (id=$($r.id))..."
                    try {
                        $delResp = Invoke-Cf -Method DELETE -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records/$($r.id)"
                        if ($delResp.success) {
                            Write-Host "[$domain]   Deleted A -> $($r.content)"
                        }
                        else {
                            $msg = ($delResp.errors | ConvertTo-Json -Depth 4 -Compress)
                            Write-Warning "[$domain]   DELETE failed: $msg"
                            $dnsErrors += "DELETE $($r.content): $msg"
                        }
                    }
                    catch {
                        Write-Warning "[$domain]   DELETE error: $($_.Exception.Message)"
                        $dnsErrors += "DELETE $($r.content): $($_.Exception.Message)"
                    }
                }

                # Create missing GH Pages A records
                foreach ($ip in $toCreate) {
                    Write-Host "[$domain]   Creating A -> $ip (proxied=false)..."
                    $body = @{
                        type    = 'A'
                        name    = $domain
                        content = $ip
                        ttl     = 1
                        proxied = $false
                    }
                    try {
                        $createResp = Invoke-Cf -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records" -Body $body
                        if ($createResp.success) {
                            Write-Host "[$domain]   Created A -> $ip"
                        }
                        else {
                            $msg = ($createResp.errors | ConvertTo-Json -Depth 4 -Compress)
                            Write-Warning "[$domain]   CREATE failed for $ip : $msg"
                            $dnsErrors += "CREATE $ip : $msg"
                        }
                    }
                    catch {
                        Write-Warning "[$domain]   CREATE error for $ip : $($_.Exception.Message)"
                        $dnsErrors += "CREATE $ip : $($_.Exception.Message)"
                    }
                }

                if ($dnsErrors.Count -gt 0) {
                    $result.DnsStatus = 'FAIL'
                    $result.DnsDetail = $dnsErrors -join ' | '
                }
                else {
                    $result.DnsStatus = 'OK'
                    $deletedMsg = if ($toDelete.Count -gt 0) { "Deleted $($toDelete.Count) HostPapa record(s); " } else { '' }
                    $createdMsg = if ($toCreate.Count -gt 0) { "Created $($toCreate.Count) GH Pages record(s)" } else { '' }
                    $result.DnsDetail = "$deletedMsg$createdMsg".TrimEnd(' ;')
                }
            }
        }
    }
}
catch {
    Write-Warning "[$domain]   DNS step error: $($_.Exception.Message)"
    $result.DnsStatus = 'FAIL'
    $result.DnsDetail = $_.Exception.Message
}
}

$results += $result
Write-Host ''
}

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
Write-Host '=== Per-domain results ==='
$results | Format-Table Domain, CnameStatus, DnsStatus, CnameDetail, DnsDetail -AutoSize -Wrap

$cnameOk = @($results | Where-Object { $_.CnameStatus -eq 'OK' }).Count
$cnameDry = @($results | Where-Object { $_.CnameStatus -eq 'DRY-RUN' }).Count
$cnameFail = @($results | Where-Object { $_.CnameStatus -eq 'FAIL' }).Count
$cnameSkip = @($results | Where-Object { $_.CnameStatus -in 'SKIP', 'SKIP-MANUAL' }).Count
$dnsOk = @($results | Where-Object { $_.DnsStatus -eq 'OK' }).Count
$dnsDry = @($results | Where-Object { $_.DnsStatus -eq 'DRY-RUN' }).Count
$dnsFail = @($results | Where-Object { $_.DnsStatus -eq 'FAIL' }).Count
$dnsSkip = @($results | Where-Object { $_.DnsStatus -in 'SKIP', 'SKIP-MANUAL' }).Count
$dnsManual = @($results | Where-Object { $_.DnsStatus -eq 'SKIP-MANUAL' }).Count

Write-Host ''
Write-Host "CNAME : OK=$cnameOk DRY-RUN=$cnameDry FAIL=$cnameFail SKIP=$cnameSkip"
Write-Host "DNS   : OK=$dnsOk DRY-RUN=$dnsDry FAIL=$dnsFail SKIP=$dnsSkip (manual=$dnsManual)"

if ($dnsManual -gt 0) {
    Write-Host ''
    Write-Host '=== Manual DNS steps required ==='
    Write-Host 'The following zones are in CF accounts not accessible via FFC/CM tokens.'
    Write-Host 'After this workflow completes, flip apex A records manually in each account:'
    Write-Host ''
    foreach ($r in ($results | Where-Object { $_.DnsStatus -eq 'SKIP-MANUAL' })) {
        Write-Host "  $($r.Domain) — $($ManualCfZones[$r.Domain])"
        Write-Host "    Dashboard : https://dash.cloudflare.com"
        Write-Host "    DELETE    : A @ 216.222.200.253"
        Write-Host "    CREATE    : A @ 185.199.108.153"
        Write-Host "               A @ 185.199.109.153"
        Write-Host "               A @ 185.199.110.153"
        Write-Host "               A @ 185.199.111.153"
        Write-Host "    proxied   : false (DNS-only)"
        Write-Host ''
    }
}

# ---------------------------------------------------------------------------
# GitHub Actions step summary
# ---------------------------------------------------------------------------
if ($env:GITHUB_STEP_SUMMARY) {
    $lines = @(
        '# Bulk cutover: staging -> apex (GH Pages + Cloudflare)',
        '',
        "**Mode:** $(if ($DryRun) { 'DRY-RUN' } else { 'APPLY' })  ",
        "**Skip DNS:** $($SkipDns.IsPresent)  ",
        "**Domains:** $($domainList.Count)  ",
        "**CNAME Result:** OK=$cnameOk DRY-RUN=$cnameDry FAIL=$cnameFail SKIP=$cnameSkip  ",
        "**DNS Result:** OK=$dnsOk DRY-RUN=$dnsDry FAIL=$dnsFail SKIP=$dnsSkip (manual=$dnsManual)",
        '',
        '| Domain | CNAME Status | DNS Status | CNAME Detail | DNS Detail |',
        '| --- | --- | --- | --- | --- |'
    )
    foreach ($r in $results) {
        $cd = ($r.CnameDetail -replace '\|', '\\|')
        $dd = ($r.DnsDetail   -replace '\|', '\\|')
        $lines += "| $($r.Domain) | $($r.CnameStatus) | $($r.DnsStatus) | $cd | $dd | "
    }

    if ($dnsManual -gt 0) {
        $lines += ''
        $lines += '## Manual DNS steps required'
        $lines += ''
        $lines += 'Zones in CF accounts not accessible via FFC/CM tokens — flip apex A records manually:'
        $lines += ''
        foreach ($r in ($results | Where-Object { $_.DnsStatus -eq 'SKIP-MANUAL' })) {
            $lines += "### $($r.Domain) — $($ManualCfZones[$r.Domain])"
            $lines += '```'
        $lines += 'DELETE : A @ 216.222.200.253'
        $lines += 'CREATE : A @ 185.199.108.153  (proxied=false)'
        $lines += '         A @ 185.199.109.153'
        $lines += '         A @ 185.199.110.153'
        $lines += '         A @ 185.199.111.153'
        $lines += '```'
        $lines += ''
    }
}

Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($lines -join "`n")
}

# Exit 1 if any hard failures
$hardFail = @($results | Where-Object { $_.CnameStatus -eq 'FAIL' -or $_.DnsStatus -eq 'FAIL' }).Count
if ($hardFail -gt 0) { exit 1 }
