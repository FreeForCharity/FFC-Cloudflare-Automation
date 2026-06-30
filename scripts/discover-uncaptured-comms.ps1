# Discover uncaptured charity communications from the ORG-OWNED Microsoft 365 shared mailboxes
# (automated track of Gap B / #492). Classifies charity-vs-noise, reconciles candidate sender
# domains against the sites-list, and writes a PII-masked pipeline CSV plus aggregate counts.
#
# SCAFFOLD: untested pending a Graph app registration with Mail.Read scoped to the target mailboxes
# via an Exchange Online application access policy. The personal Google Voice source is deliberately
# NOT handled here — it is human-in-the-loop only (see docs/runbooks/google-voice-metrics.md).
#
# PII: the CSV columns are receivedDate, mailbox, a first-initial-masked sender name, candidateDomain
# (a NON-personal-provider org domain only), and status — none are personal data. The raw sender
# email is never written, and personal providers like gmail.com are excluded from candidateDomain.
# A personal custom domain is rare in this org-mailbox context and the artifact is retention-capped
# (7 days) — operators still treat any personal domain per the PII policy. No raw bodies are persisted.

[CmdletBinding()]
param(
    [Parameter()]
    [string]$AccessToken,

    [Parameter()]
    [string]$Mailboxes = 'contact@freeforcharity.org,support@freeforcharity.org,info@freeforcharity.org',

    [Parameter()]
    [int]$SinceDays = 365,

    [Parameter()]
    [string]$SitesListPath = 'sites-list/sites_list.json',

    [Parameter()]
    [string]$OutputFile = 'artifacts/discovery/pipeline.csv'
)

$ErrorActionPreference = 'Stop'

# Defense-in-depth: only these org-owned shared mailboxes may ever be queried, regardless of the
# -Mailboxes input. This bounds the blast radius until the Exchange Online application access policy
# is confirmed/enforced (a broader/accidental Graph grant still can't be pointed elsewhere).
$approvedMailboxes = @(
    'contact@freeforcharity.org', 'support@freeforcharity.org', 'info@freeforcharity.org'
)

# Personal email providers are never an org "candidate domain" for reconciliation.
$personalProviders = @(
    'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com',
    'yahoo.com', 'icloud.com', 'me.com', 'aol.com', 'proton.me', 'protonmail.com'
)

# Heuristic signal. Charity wins over noise when both match.
$charityKeywords = @(
    'nonprofit', 'non-profit', 'non profit', 'charity', 'charities', '501c3', '501(c)(3)',
    'website', 'web site', 'domain', 'hosting', 'donate', 'donation', 'volunteer',
    'candid', 'guidestar', 'free for charity', 'onboard', 'wordpress', 'github pages'
)
$noiseKeywords = @(
    'verification code', 'your code is', 'one-time', 'parking', 'delivery', 'tracking number',
    'unsubscribe', 'receipt', 'shipment', 'appointment reminder'
)

function Get-LowerText {
    # Null-safe lowercase (avoids the PS7-only `??` operator so the script also parses on PS 5.1).
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    return $Text.ToLowerInvariant()
}

function Get-MaskedName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $first = $Name.Trim().Substring(0, 1).ToUpper()
    return "$first***"
}

function Test-IsCharity {
    param([string]$Text)
    $t = Get-LowerText $Text
    foreach ($k in $charityKeywords) { if ($t.Contains($k)) { return $true } }
    return $false
}

function Test-IsNoise {
    param([string]$Text)
    $t = Get-LowerText $Text
    foreach ($k in $noiseKeywords) { if ($t.Contains($k)) { return $true } }
    return $false
}

function Invoke-GraphGet {
    # GET with retry/backoff on throttling (429) and transient 5xx, honouring Retry-After.
    param([string]$Uri, [string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    $maxAttempts = 5
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            return Invoke-RestMethod -Method Get -Uri $Uri -Headers $headers -ErrorAction Stop
        }
        catch {
            $code = $null
            $resp = $_.Exception.Response
            if ($resp) { try { $code = [int]$resp.StatusCode } catch { $code = $null } }
            $retryable = ($code -eq 429) -or ($null -ne $code -and $code -ge 500 -and $code -le 599)
            if (-not $retryable -or $attempt -eq $maxAttempts) { throw }

            $delay = [math]::Min(60, [math]::Pow(2, $attempt))
            if ($resp) {
                try {
                    $ra = $resp.Headers['Retry-After']
                    $raSec = 0
                    if ($ra -and [int]::TryParse([string]$ra, [ref]$raSec)) {
                        $delay = [math]::Max($delay, $raSec)
                    }
                }
                catch { }
            }
            Write-Warning "Graph $code on attempt $attempt; retrying in $delay s."
            Start-Sleep -Seconds $delay
        }
    }
}

try {
    $token = if ($AccessToken) { $AccessToken } else { $env:GRAPH_ACCESS_TOKEN }
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw 'No Graph token: pass -AccessToken or set GRAPH_ACCESS_TOKEN (see 47-discover-uncaptured-comms.yml).'
    }

    # Validate the requested mailboxes against the allowlist up front; fail fast on anything else.
    $requested = @($Mailboxes -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($mbx in $requested) {
        if ($approvedMailboxes -notcontains $mbx.ToLowerInvariant()) {
            throw "Mailbox '$mbx' is not in the approved org shared-mailbox allowlist ($($approvedMailboxes -join ', '))."
        }
    }

    # Onboarded domains (reconciliation set) from the sites-list. Be tolerant of the exact field
    # name; collect anything that looks like a bare domain.
    $onboarded = New-Object System.Collections.Generic.HashSet[string]
    if (Test-Path $SitesListPath) {
        $sites = Get-Content -Raw -Path $SitesListPath | ConvertFrom-Json
        $rows = if ($sites.PSObject.Properties.Name -contains 'sites') { $sites.sites } else { $sites }
        foreach ($row in @($rows)) {
            foreach ($prop in 'domain', 'Domain', 'name', 'host') {
                $val = $row.$prop
                if ($val -and ($val -match '^[a-z0-9.-]+\.[a-z]{2,}$')) {
                    [void]$onboarded.Add($val.ToLowerInvariant()); break
                }
            }
        }
    }
    else {
        Write-Warning "Sites-list not found at $SitesListPath; every candidate will be flagged uncaptured."
    }

    $sinceIso = (Get-Date).ToUniversalTime().AddDays(-1 * [math]::Abs($SinceDays)).ToString('yyyy-MM-ddTHH:mm:ssZ')
    $filterEnc = [uri]::EscapeDataString("receivedDateTime ge $sinceIso")
    $rowsOut = New-Object System.Collections.Generic.List[object]
    $scanned = 0; $charity = 0; $uncaptured = 0

    foreach ($mbx in $requested) {
        # Inquiries live in the Inbox. Encode the mailbox and the $filter value so UPNs / reserved
        # characters can't break the request. Large page size to cut request count / throttling.
        $mbxEnc = [uri]::EscapeDataString($mbx)
        $uri = "https://graph.microsoft.com/v1.0/users/$mbxEnc/mailFolders/inbox/messages?`$filter=$filterEnc&`$select=from,subject,bodyPreview,receivedDateTime&`$top=999"
        while ($uri) {
            $resp = Invoke-GraphGet -Uri $uri -Token $token
            foreach ($m in @($resp.value)) {
                $scanned++
                $text = "$($m.subject) $($m.bodyPreview)"
                if ((Test-IsNoise -Text $text) -and -not (Test-IsCharity -Text $text)) { continue }
                if (-not (Test-IsCharity -Text $text)) { continue }
                $charity++

                # $m.from (and .emailAddress) can be null for system-generated messages; deref
                # defensively so one malformed message can't abort the whole run under
                # ErrorActionPreference=Stop.
                $emailObj = if ($m.from) { $m.from.emailAddress } else { $null }
                $addr = if ($emailObj) { [string]$emailObj.address } else { '' }
                $name = if ($emailObj) { [string]$emailObj.name } else { '' }
                $domain = if ($addr -match '@') { $addr.Split('@')[-1].ToLowerInvariant() } else { '' }
                $isOrgDomain = $domain -and ($personalProviders -notcontains $domain)

                $status = 'unknown'
                if ($isOrgDomain) {
                    $status = if ($onboarded.Contains($domain)) { 'existing' } else { 'uncaptured' }
                    if ($status -eq 'uncaptured') { $uncaptured++ }
                }

                # Only a non-personal-provider org domain is ever written; the raw sender email
                # (whose domain could be personal) is not.
                $rowsOut.Add([pscustomobject]@{
                        receivedDate    = ([datetime]$m.receivedDateTime).ToString('yyyy-MM-dd')
                        mailbox         = $mbx
                        maskedSender    = Get-MaskedName -Name $name
                        candidateDomain = if ($isOrgDomain) { $domain } else { '' }
                        status          = $status
                    })
            }
            $uri = $resp.'@odata.nextLink'
        }
    }

    $outDir = Split-Path -Parent $OutputFile
    if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
    $rowsOut | Sort-Object status, candidateDomain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

    if ($env:GITHUB_STEP_SUMMARY) {
        "## Uncaptured comms discovery (M365, PII masked)" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        "" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        "- Messages scanned: $scanned" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        "- Charity-related: $charity" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        "- Uncaptured leads (org domain not in sites-list): $uncaptured" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }

    Write-Host "Scanned $scanned, charity $charity, uncaptured $uncaptured -> $OutputFile"
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
