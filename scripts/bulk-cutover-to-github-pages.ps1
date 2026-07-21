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
    Override the 4 GH Pages anycast IPs. Defaults to the canonical set from
    scripts/cloudflare-api-common.ps1 (185.199.108-111.153), resolved after
    the shared lib is dot-sourced.

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
    # Comma/space-separated root domains. Optional at bind time so the script
    # can be dot-sourced by the Pester tests (see the runner guard below); the
    # main body throws if it is empty on a real run.
    [Parameter()]
    [string]$Domains,

    [switch]$DryRun,

    [switch]$SkipDns,

    [switch]$SkipCname,

    # Defaults to the canonical set from scripts/cloudflare-api-common.ps1
    # (#778) — resolved after the shared lib is dot-sourced below, because
    # param defaults are evaluated before the lib is loaded.
    [string[]]$GhPagesIps,

    [string]$HostPapaIp = '216.222.200.253',

    # Target of the standard www CNAME (FFC org Pages host). #774: the dns-flip
    # upserts www alongside the apex A records — the first live cutover shipped
    # without www because only the apex was written here. Defaults to the
    # canonical target from scripts/cloudflare-api-common.ps1 (#778).
    [string]$WwwTarget
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Pure decision: which CNAME-flip strategy applies to an FFC-EX repo (#767).
#   'switch-style'  — the repo's deploy workflow declares a `custom_domain`
#                     input, so basePath/CNAME are BUILD-derived from the domain
#                     signal. The flip sets the CUSTOM_DOMAIN repo variable and
#                     commits public/CNAME as the source of truth.
#   'legacy-commit' — no such input (or no deploy workflow at all): the
#                     committed public/CNAME file is flipped directly.
# Kept as a pure function of the deploy-workflow text so the selection is unit
# testable with no GitHub API calls.
# ---------------------------------------------------------------------------
function Get-CnameFlipStrategy {
    [OutputType([string])]
    param(
        # Raw text of the repo's .github/workflows/deploy.yml, or $null/'' when
        # the file does not exist.
        [AllowNull()]
        [string]$DeployWorkflowContent
    )
    if (-not [string]::IsNullOrWhiteSpace($DeployWorkflowContent) -and ($DeployWorkflowContent -match 'custom_domain')) {
        return 'switch-style'
    }
    return 'legacy-commit'
}

# When dot-sourced (e.g. by the Pester tests in scripts/tests), expose the pure
# helpers above and stop before running the cutover body.
if ($MyInvocation.InvocationName -eq '.') { return }

if ([string]::IsNullOrWhiteSpace($Domains)) {
    throw 'Domains parameter is required (comma/space-separated root domains).'
}

# ---------------------------------------------------------------------------
# Shared Cloudflare helpers (#778): REST plumbing (Invoke-CfApi), zone
# resolution (Resolve-CfZone), record listing (Get-CfDnsRecords), and the
# canonical GitHub Pages IP set + www target.
# ---------------------------------------------------------------------------
. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

if (-not $GhPagesIps -or $GhPagesIps.Count -eq 0) { $GhPagesIps = @(Get-GhPagesIps) }
if ([string]::IsNullOrWhiteSpace($WwwTarget)) { $WwwTarget = Get-GhPagesWwwTarget }

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
$cfTokens = @(Get-CfEnvTokens)

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

# Cloudflare REST helpers (Invoke-CfApi / Resolve-CfZone / Get-CfDnsRecords)
# come from scripts/cloudflare-api-common.ps1, dot-sourced above (#778).

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
        # Omit Sha to CREATE the file (GitHub contents API: PUT without sha).
        [string]$Sha,
        [Parameter(Mandatory)][string]$CommitMessage
    )
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($NewContent))
    $body = @{
        message = $CommitMessage
        content = $encoded
    }
    if (-not [string]::IsNullOrWhiteSpace($Sha)) { $body.sha = $Sha }
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

# Network-optional drift check: warn (never fail) if the canonical Pages IPv4
# set in the shared lib no longer matches api.github.com/meta (#778).
$null = Test-GhPagesIpsCurrent

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
        Write-Warning "[$domain]   Delete : $HostPapaIp"
        Write-Warning "[$domain]   Create : $($GhPagesIps -join ' / ') (proxied=false)"
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
            # #767/#774: switch-style repos (deploy workflow declares a
            # custom_domain input; the build derives basePath from the domain
            # signal). Operator ruling 2026-07-20: the committed public/CNAME
            # file is the SOURCE OF TRUTH (GitHub Pages standard), so this path
            # commits the file (which triggers the push deploy) and sets the
            # CUSTOM_DOMAIN variable only as a fallback for file-less builds.
            # The Pages custom-domain binding is deliberately NOT set here:
            # binding before DNS is clean stalls Let's Encrypt issuance (state
            # 'none'). The 120 workflow's post-DNS job binds + cert-kicks.
            $deployWf = Get-GhFileInfo -Repo $repo -FilePath '.github/workflows/deploy.yml'
            $deployContent = if ($deployWf) { $deployWf.Content } else { $null }
            $flipStrategy = Get-CnameFlipStrategy -DeployWorkflowContent $deployContent
            $switchStyle = $flipStrategy -eq 'switch-style'
            Write-Host "[$domain]   CNAME flip strategy: $flipStrategy"
            $fileInfo = Get-GhFileInfo -Repo $repo -FilePath 'public/CNAME'

            if ($switchStyle) {
                Write-Host "[$domain]   Switch-style repo (deploy.yml declares custom_domain)."
                if ($DryRun) {
                    Write-Host "[$domain]   [DRY-RUN] Would set repo variable CUSTOM_DOMAIN=$domain and commit public/CNAME='$domain' (push triggers deploy; binding is set post-DNS by the workflow)"
                    $result.CnameStatus = 'DRY-RUN'
                    $result.CnameDetail = "Would set var CUSTOM_DOMAIN=$domain + commit public/CNAME (switch-style)"
                }
                else {
                    Write-Host "[$domain]   Setting repo variable CUSTOM_DOMAIN=$domain"
                    try {
                        $null = Invoke-Gh -Method PATCH -Path "/repos/$repo/actions/variables/CUSTOM_DOMAIN" -Body @{ name = 'CUSTOM_DOMAIN'; value = $domain }
                    }
                    catch {
                        $null = Invoke-Gh -Method POST -Path "/repos/$repo/actions/variables" -Body @{ name = 'CUSTOM_DOMAIN'; value = $domain }
                    }
                    if ($fileInfo -and $fileInfo.Content -eq $domain) {
                        Write-Host "[$domain]   public/CNAME already '$domain' — no commit needed."
                    }
                    else {
                        Write-Host "[$domain]   Committing public/CNAME='$domain' (source of truth; push triggers deploy)"
                        $commitMsg = "chore: commit CNAME $domain as custom-domain source of truth`n`n[automated cutover #774]"
                        $null = Set-GhFileContent -Repo $repo -FilePath 'public/CNAME' `
                            -NewContent "$domain`n" -Sha $(if ($fileInfo) { $fileInfo.Sha } else { '' }) -CommitMessage $commitMsg
                    }
                    $result.CnameStatus = 'OK'
                    $result.CnameDetail = "Set var CUSTOM_DOMAIN=$domain + committed public/CNAME (switch-style; binding post-DNS)"
                }
            }
            elseif ($null -eq $fileInfo) {
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
            $zone = Resolve-CfZone -Domain $domain -Tokens $cfTokens
            if (-not $zone) {
                Write-Warning "[$domain]   Zone not found via any available CF token."
                $result.DnsStatus = 'SKIP'
                $result.DnsDetail = 'CF zone not found'
            }
            else {
                Write-Host "[$domain]   Zone resolved via $($zone.Account) token (id $($zone.ZoneId))"

                $apexRecords = @(Get-CfDnsRecords -ZoneId $zone.ZoneId -Token $zone.Token -Type A -Name $domain)
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

                        # Delete HostPapa record(s). Invoke-CfApi throws on any
                        # failure (with the Cloudflare JSON errors in the
                        # message), so a single catch covers both HTTP and
                        # envelope failures.
                        foreach ($r in $toDelete) {
                            Write-Host "[$domain]   Deleting A -> $($r.content) (id=$($r.id))..."
                            try {
                                $null = Invoke-CfApi -Method DELETE -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records/$($r.id)"
                                Write-Host "[$domain]   Deleted A -> $($r.content)"
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
                                $null = Invoke-CfApi -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records" -Body $body
                                Write-Host "[$domain]   Created A -> $ip"
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

                # --- www CNAME upsert (#774) ---
                # The apex/www standard is BOTH: apex A x4 AND www CNAME -> the
                # org Pages host (dns-only, so Pages can serve the redirect and
                # issue the www SAN cert). Runs even when the apex was already
                # correct — the first live cutover shipped apex-only.
                $wwwName = "www.$domain"
                $wwwRecords = @(Get-CfDnsRecords -ZoneId $zone.ZoneId -Token $zone.Token -Name $wwwName)
                $wwwGood = @($wwwRecords | Where-Object { $_.type -eq 'CNAME' -and $_.content -eq $WwwTarget -and -not $_.proxied })

                if ($wwwGood.Count -gt 0 -and $wwwRecords.Count -eq $wwwGood.Count) {
                    Write-Host "[$domain]   www already correct: CNAME -> $WwwTarget (dns-only)."
                }
                elseif ($wwwRecords.Count -gt 0) {
                    Write-Warning "[$domain]   www has $($wwwRecords.Count) unexpected record(s) — left untouched (fix manually or via 106):"
                    foreach ($r in $wwwRecords) {
                        Write-Warning ("    - {0} {1} -> {2} (proxied={3})" -f $r.type, $r.name, $r.content, $r.proxied)
                    }
                    $result.DnsDetail = "$($result.DnsDetail); www UNEXPECTED ($($wwwRecords.Count) record(s) left untouched)".TrimStart('; ')
                }
                elseif ($DryRun) {
                    Write-Host "[$domain]   [DRY-RUN] Would CREATE CNAME $wwwName -> $WwwTarget (proxied=false)"
                    $result.DnsDetail = "$($result.DnsDetail); would create www CNAME".TrimStart('; ')
                }
                else {
                    Write-Host "[$domain]   Creating CNAME $wwwName -> $WwwTarget (proxied=false)..."
                    $wwwBody = @{
                        type    = 'CNAME'
                        name    = 'www'
                        content = $WwwTarget
                        ttl     = 1
                        proxied = $false
                    }
                    try {
                        $null = Invoke-CfApi -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/dns_records" -Body $wwwBody
                        Write-Host "[$domain]   Created www CNAME."
                        $result.DnsDetail = "$($result.DnsDetail); created www CNAME".TrimStart('; ')
                    }
                    catch {
                        Write-Warning "[$domain]   www CREATE failed: $($_.Exception.Message)"
                        $result.DnsStatus = 'FAIL'
                        $result.DnsDetail = "$($result.DnsDetail); www CREATE failed: $($_.Exception.Message)".TrimStart('; ')
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
        Write-Host "    DELETE    : A @ $HostPapaIp"
        $ipIdx = 0
        foreach ($ip in $GhPagesIps) {
            if ($ipIdx -eq 0) { Write-Host "    CREATE    : A @ $ip" }
            else { Write-Host "               A @ $ip" }
            $ipIdx++
        }
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
        $dd = ($r.DnsDetail -replace '\|', '\\|')
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
            $lines += "DELETE : A @ $HostPapaIp"
            $ipIdx = 0
            foreach ($ip in $GhPagesIps) {
                if ($ipIdx -eq 0) { $lines += "CREATE : A @ $ip  (proxied=false)" }
                else { $lines += "         A @ $ip" }
                $ipIdx++
            }
            $lines += '```'
            $lines += ''
        }
    }

    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($lines -join "`n")
}

# Exit 1 if any hard failures
$hardFail = @($results | Where-Object { $_.CnameStatus -eq 'FAIL' -or $_.DnsStatus -eq 'FAIL' }).Count
if ($hardFail -gt 0) { exit 1 }
