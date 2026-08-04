<#
.SYNOPSIS
    Cloudflare DNS Management Tool (PowerShell Edition)

.DESCRIPTION
    A comprehensive utility to manage Cloudflare DNS records via the API.
    Designed to replace update_dns.py for GitHub Actions and local Windows usage.
    Supports Create, Update (Upsert), Delete, and Get operations for A, AAAA, CNAME, MX, and TXT records.

.PARAMETER Zone
    The domain name (zone) to manage (e.g., "example.org").

.PARAMETER Name
    The DNS record name. Use "@" for the root domain.
    Example: "www" or "staging".

.PARAMETER Type
    DNS Record type. Supported: A, AAAA, CNAME, MX, TXT.

.PARAMETER Content
    The content of the record (IP address, Target domain, Text value).

.PARAMETER Priority
    Priority for MX records. Defaults to 10.

.PARAMETER Ttl
    Time to Live in seconds. Default is 1 (Auto).

.PARAMETER Proxied
    Switch to enable Cloudflare Proxy (Orange Cloud). Default is DNS Only (Grey Cloud).

.PARAMETER Token
    Cloudflare API Token.
    If omitted, the script will use one of:
    - $env:CLOUDFLARE_API_TOKEN (single-token mode)
    - $env:CLOUDFLARE_API_TOKEN_FFC / $env:CLOUDFLARE_API_TOKEN_CM (dual-token mode; auto-detects which can access the zone)

.PARAMETER DryRun
    Preview changes without applying them.

.PARAMETER Remove
    Delete the specified record(s).

.PARAMETER List
    List records for the zone/name.

.PARAMETER MailProvider
    Mail provider the zone should follow: 'Microsoft365' (default) or 'Google'.
    Used by -EnforceStandard and -Audit.

    With -EnforceStandard this is a CUTOVER, not an addition. The desired
    provider's MX/SPF (and, for M365, its service CNAMEs and Teams SRVs) are
    created first, then the OTHER provider's mail records are removed, so the
    zone never sits with two live MX sets pointing at different providers.
    SPF is rewritten in place rather than duplicated: a second v=spf1 TXT is a
    permerror, so the foreign include is swapped out of the existing record.

    DKIM is deliberately out of scope. A Google DKIM key is generated in the
    Google Admin console and cannot be derived here, so google._domainkey is
    never created and an existing one is never deleted.

.EXAMPLE
    # Cut a zone over to Google Workspace mail (preview first)
    .\Update-CloudflareDns.ps1 -Zone example.org -EnforceStandard -MailProvider Google -DryRun

.EXAMPLE
    # Create/Update an A record (Grey Cloud)
    .\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42

.EXAMPLE
    # Create/Update WWW as Orange Cloud
    .\Update-CloudflareDns.ps1 -Zone example.org -Name www -Type CNAME -Content example.org -Proxied

.EXAMPLE
    # Create MX Record
    .\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type MX -Content mail.example.org -Priority 0

.EXAMPLE
    # Setup GitHub Pages (Apex)
    .\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.108.153

.EXAMPLE
    # Delete a record
    .\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Remove
#>
[CmdletBinding(DefaultParameterSetName = 'Set')]
param(
    [Parameter(Mandatory = $true, HelpMessage = "The Zone/Domain name (e.g. example.org)")]
    [string]$Zone,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Get')]
    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)]
    [string]$Name,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Get')]
    [Parameter(ParameterSetName = 'Remove')]
    [ValidateSet('A', 'AAAA', 'CNAME', 'MX', 'TXT')]
    [string]$Type,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Remove')]
    [string]$Content,

    [Parameter(ParameterSetName = 'Set')]
    [int]$Priority = 10,

    [Parameter(ParameterSetName = 'Set')]
    [int]$Ttl = 1, # 1 = Auto

    [Parameter(ParameterSetName = 'Set')]
    [switch]$Proxied,

    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)]
    [switch]$Remove,

    [Parameter(ParameterSetName = 'Get', Mandatory = $true)]
    [switch]$List,

    [Parameter(ParameterSetName = 'Audit', Mandatory = $true)]
    [switch]$Audit,

    [Parameter(ParameterSetName = 'Enforce', Mandatory = $true)]
    [switch]$EnforceStandard,

    [Parameter(ParameterSetName = 'Enforce')]
    [switch]$GitHubPagesOnly,

    [Parameter(ParameterSetName = 'Enforce')]
    [Parameter(ParameterSetName = 'Audit')]
    [switch]$ProxyGitHubPages,

    # Which mail provider the zone's MX/SPF/service records should follow.
    # Defaults to Microsoft365 so existing fleet enforcement is unchanged.
    # On -EnforceStandard this is a CUTOVER: the other provider's mail records
    # are removed once the desired set is in place. On -Audit, when this is not
    # passed explicitly, the provider is auto-detected from the zone's own MX.
    [Parameter(ParameterSetName = 'Enforce')]
    [Parameter(ParameterSetName = 'Audit')]
    [ValidateSet('Microsoft365', 'Google')]
    [string]$MailProvider = 'Microsoft365',

    [Parameter(ParameterSetName = 'ExportAll', Mandatory = $true)]
    [switch]$ExportAll,

    [Parameter(ParameterSetName = 'ExportAll')]
    [string]$OutputFile = 'dns_records_full.csv',

    [string]$Token,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://api.cloudflare.com/client/v4'

# Shared Cloudflare helpers (#778): single source for the GitHub Pages IP sets
# and the www CNAME target. NOTE: this engine intentionally keeps its own
# richer Invoke-CfApi (defined below, which overrides the library's version);
# converging the REST plumbing is deferred to a follow-up to keep this change
# reviewable.
. (Join-Path $PSScriptRoot 'scripts/cloudflare-api-common.ps1')

# Guard the -ProxyGitHubPages footgun (evidence: first live cutover, #774).
if ($ProxyGitHubPages) {
    Write-Warning "-ProxyGitHubPages is set: proxied (orange-cloud) apex records MASK GitHub Pages behind Cloudflare edge IPs and BLOCK Let's Encrypt certificate issuance for the custom domain (observed live during the first cutover — issue #774). The FFC GitHub Pages standard is DNS-only (grey cloud); only proceed if you intend Cloudflare to front this site."
}

# --- Authenticate ---
function Get-AuthToken {
    param([AllowNull()][string]$ZoneName)

    if ($Token) { return $Token.Trim() }

    $candidates = @(
        $env:CLOUDFLARE_API_TOKEN,
        $env:CLOUDFLARE_API_TOKEN_FFC,
        $env:CLOUDFLARE_API_TOKEN_CM
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim() } | Select-Object -Unique

    if ($candidates.Count -eq 0) {
        throw 'Cloudflare API token not found. Pass -Token or set CLOUDFLARE_API_TOKEN, CLOUDFLARE_API_TOKEN_FFC, or CLOUDFLARE_API_TOKEN_CM.'
    }

    if ($candidates.Count -eq 1 -or [string]::IsNullOrWhiteSpace($ZoneName)) {
        return $candidates[0]
    }

    foreach ($cand in $candidates) {
        if (Test-ZoneAccess -ZoneName $ZoneName -CandidateToken $cand) {
            return $cand
        }
    }

    throw "Cloudflare API token not found for zone '$ZoneName'. Pass -Token or set the correct CLOUDFLARE_API_TOKEN_* environment variable for that zone."
}

function Test-ZoneAccess {
    param(
        [Parameter(Mandatory = $true)][string]$ZoneName,
        [Parameter(Mandatory = $true)][string]$CandidateToken
    )

    try {
        $headers = @{ Authorization = "Bearer $CandidateToken"; 'Content-Type' = 'application/json' }
        $encoded = [uri]::EscapeDataString($ZoneName)
        $uri = "$ApiBase/zones?name=$encoded"
        $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -ErrorAction Stop -TimeoutSec 30
        return ($resp.success -and $resp.result -and $resp.result.Count -gt 0)
    }
    catch {
        return $false
    }
}

$AuthToken = Get-AuthToken -ZoneName $Zone
if (-not $AuthToken) { Write-Error "No Cloudflare API Token provided."; exit 1 }

$Headers = @{
    'Authorization' = "Bearer $AuthToken"
    'Content-Type'  = 'application/json'
}

# --- Helper Functions ---

function Invoke-CfApi {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Body = $null,
        [hashtable]$Params = $null
    )

    $requestParams = @{
        Method      = $Method
        Uri         = "$ApiBase$Uri"
        Headers     = $Headers
        ContentType = 'application/json'
        TimeoutSec  = 30
    }

    if ($Body) { $requestParams['Body'] = ($Body | ConvertTo-Json -Depth 10 -Compress) }
    
    # Append Query Params
    if ($Params) {
        $queryString = ($Params.Keys | ForEach-Object { "$_=$($Params[$_])" }) -join '&'
        $requestParams['Uri'] += "?$queryString"
    }

    # In PowerShell 7+, we can use Invoke-WebRequest -SkipHttpErrorCheck to reliably capture
    # HTTP status codes and the raw JSON error body from Cloudflare. This makes diagnostics
    # (like DMARC Management enablement) much clearer.
    $canSkipHttpErrors = $false
    try {
        $canSkipHttpErrors = (Get-Command Invoke-WebRequest -ErrorAction Stop).Parameters.ContainsKey('SkipHttpErrorCheck')
    }
    catch {
        $canSkipHttpErrors = $false
    }

    if ($canSkipHttpErrors) {
        $iwrParams = @{
            Method             = $Method
            Uri                = "$ApiBase$Uri"
            Headers            = $Headers
            ContentType        = 'application/json'
            TimeoutSec         = 30
            SkipHttpErrorCheck = $true
        }
        if ($Body) { $iwrParams['Body'] = ($Body | ConvertTo-Json -Depth 10 -Compress) }

        # Append Query Params
        if ($Params) {
            $queryString = ($Params.Keys | ForEach-Object { "$_=$($Params[$_])" }) -join '&'
            $iwrParams['Uri'] += "?$queryString"
        }

        $resp = Invoke-WebRequest @iwrParams
        $statusCode = [int]$resp.StatusCode
        $raw = $resp.Content

        $parsed = $null
        try {
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
            }
        }
        catch {
            $parsed = $null
        }

        if ($statusCode -ge 400 -or ($parsed -and ($parsed.PSObject.Properties.Name -contains 'success') -and (-not $parsed.success))) {
            $ex = [System.Exception]::new("HTTP $statusCode from Cloudflare API")
            try {
                $ex.Data['CfStatusCode'] = $statusCode
                $ex.Data['CfErrorBody'] = $raw
            }
            catch {
                # best-effort
            }
            throw $ex
        }

        return $parsed
    }

    # Fallback path for environments without SkipHttpErrorCheck.
    try {
        $response = Invoke-RestMethod @requestParams
        if (-not $response.success) {
            $err = $response.errors | Select-Object -ExpandProperty message -ErrorAction SilentlyContinue
            throw "API Error: $err"
        }
        return $response
    }
    catch {
        Write-Error "Request Failed: $($_.Exception.Message)"
        if ($_.Exception.Response) {
            $body = $null

            # PowerShell 7 often surfaces a System.Net.Http.HttpResponseMessage
            # while Windows PowerShell 5.1 surfaces a WebResponse with a stream.
            if ($_.Exception.Response -is [System.Net.Http.HttpResponseMessage]) {
                try {
                    $body = $_.Exception.Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                }
                catch {
                    $body = $null
                }
            }
            else {
                try {
                    $stream = $_.Exception.Response.GetResponseStream()
                    if ($stream) {
                        $reader = [System.IO.StreamReader]::new($stream)
                        $body = $reader.ReadToEnd()
                    }
                }
                catch {
                    $body = $null
                }
            }
            try {
                # Preserve the body for downstream callers (e.g., diagnostics)
                # Note: the response stream can only be read once.
                $_.Exception.Data['CfErrorBody'] = $body
            }
            catch {
                # best-effort
            }

            if (-not [string]::IsNullOrWhiteSpace([string]$body)) {
                Write-Error "API Error Body: $body"
            }
        }
        throw
    }
}

function Get-ZoneId {
    param([string]$ZoneName)
    Write-Verbose "Looking up Zone ID for $ZoneName..."
    $resp = Invoke-CfApi -Method 'GET' -Uri '/zones' -Params @{ name = $ZoneName }
    $zones = $resp.result
    if ($zones.Count -eq 0) { throw "Zone '$ZoneName' not found." }
    return $zones[0].id
}

function Get-AllDnsRecords {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ZoneId
    )

    $page = 1
    $perPage = 100
    $records = @()

    while ($true) {
        $resp = Invoke-CfApi -Method 'GET' -Uri "/zones/$ZoneId/dns_records" -Params @{ per_page = $perPage; page = $page }
        if ($resp.result) { $records += $resp.result }

        $totalPages = $null
        if ($resp.PSObject.Properties.Name -contains 'result_info' -and $resp.result_info -and ($resp.result_info.PSObject.Properties.Name -contains 'total_pages')) {
            $totalPages = [int]$resp.result_info.total_pages
        }

        if ($totalPages -and $page -ge $totalPages) { break }
        if (-not $totalPages -and ($resp.result.Count -lt $perPage)) { break }

        $page++
    }

    return $records
}

function Normalize-TxtContent {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return '' }
    $trimmed = $Value.Trim()
    if ($trimmed.Length -ge 2 -and $trimmed.StartsWith('"') -and $trimmed.EndsWith('"')) {
        return $trimmed.Substring(1, $trimmed.Length - 2)
    }
    return $trimmed
}

function Is-TxtQuoted {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return $false }
    $trimmed = $Value.Trim()
    return ($trimmed.Length -ge 2 -and $trimmed.StartsWith('"') -and $trimmed.EndsWith('"'))
}

function Quote-TxtContent {
    param([AllowNull()][string]$Value)
    $normalized = Normalize-TxtContent -Value $Value
    return '"' + $normalized + '"'
}

function Enable-DmarcManagement {
    param(
        [Parameter(Mandatory = $true)][string]$ZoneId,
        [Parameter(Mandatory = $true)][string]$ZoneName
    )

    # Cloudflare DMARC Management enablement appears to be a Dashboard-only action for this account.
    # We previously attempted to discover/enable via several likely API endpoints, but the Cloudflare
    # API consistently returned "Could not route" responses (even after enabling in the UI).
    #
    # To avoid noisy logs and unnecessary API calls during standard enforcement runs, we skip all
    # DMARC Management API probing unless explicitly enabled for debugging.
    $debugDmarcMgmt = ($env:FFC_CF_DMARCMGMT_DEBUG -eq '1')
    if (-not $debugDmarcMgmt) {
        Write-Warning "[OPTIONAL] Cloudflare DMARC Management cannot be enabled via API by this automation. Enable it manually in the dashboard: Email > DMARC Management. (Set FFC_CF_DMARCMGMT_DEBUG=1 to run the old API probe/attempts.)"
        return $false
    }

    function Get-DmarcManagementStatus {
        param(
            [Parameter(Mandatory = $true)][string]$ZoneId,
            [Parameter(Mandatory = $true)][string]$ZoneName
        )

        $getCandidates = @(
            @{ Uri = "/zones/$ZoneId/dmarc_management" },
            @{ Uri = "/zones/$ZoneId/email/dmarc_management" }
        )

        foreach ($c in $getCandidates) {
            try {
                $resp = Invoke-CfApi -Method 'GET' -Uri $c.Uri

                # Try to infer an 'enabled' flag from common shapes.
                $enabled = $null
                try {
                    if ($resp -and ($resp.PSObject.Properties.Name -contains 'result') -and $resp.result) {
                        if ($resp.result.PSObject.Properties.Name -contains 'enabled') { $enabled = [bool]$resp.result.enabled }
                        elseif ($resp.result.PSObject.Properties.Name -contains 'is_enabled') { $enabled = [bool]$resp.result.is_enabled }
                        elseif ($resp.result.PSObject.Properties.Name -contains 'status') { $enabled = ($resp.result.status -match '^(?i)enabled|active$') }
                    }
                }
                catch {
                    $enabled = $null
                }

                if ($null -ne $enabled) {
                    Write-Host "[INFO] DMARC Management status via GET $($c.Uri): enabled=$enabled" -ForegroundColor Cyan
                    return [pscustomobject]@{ Uri = $c.Uri; Enabled = $enabled }
                }

                Write-Host "[INFO] DMARC Management status via GET $($c.Uri): reachable (unable to infer enabled flag)" -ForegroundColor Cyan
                return [pscustomobject]@{ Uri = $c.Uri; Enabled = $null }
            }
            catch {
                # Try next
                continue
            }
        }

        Write-Host "[INFO] DMARC Management status GET not routable (no DMARC Management API surface detected)" -ForegroundColor Yellow
        return $null
    }

    $status = Get-DmarcManagementStatus -ZoneId $ZoneId -ZoneName $ZoneName
    if ($status -and $status.Enabled -eq $true) {
        Write-Host "[OK] DMARC Management already enabled for $ZoneName (via $($status.Uri))" -ForegroundColor Green
        return $true
    }

    # Cloudflare DMARC Management enablement is primarily documented as a Dashboard action.
    # Cloudflare exposes token permissions for "Dmarc Management Edit", but the public API
    # surface for enablement is not clearly documented. We attempt a small set of likely
    # endpoints used by the Dashboard. If all fail, we warn (do not fail enforcement).
    $candidates = @(
        @{ Method = 'POST'; Uri = "/zones/$ZoneId/dmarc_management/enable"; Body = @{} },
        @{ Method = 'POST'; Uri = "/zones/$ZoneId/dmarc_management"; Body = @{ enabled = $true } },
        @{ Method = 'PUT'; Uri = "/zones/$ZoneId/dmarc_management"; Body = @{ enabled = $true } },
        @{ Method = 'PATCH'; Uri = "/zones/$ZoneId/dmarc_management"; Body = @{ enabled = $true } },
        @{ Method = 'POST'; Uri = "/zones/$ZoneId/email/dmarc_management/enable"; Body = @{} },
        @{ Method = 'POST'; Uri = "/zones/$ZoneId/email/dmarc_management"; Body = @{ enabled = $true } },
        @{ Method = 'PUT'; Uri = "/zones/$ZoneId/email/dmarc_management"; Body = @{ enabled = $true } },
        @{ Method = 'PATCH'; Uri = "/zones/$ZoneId/email/dmarc_management"; Body = @{ enabled = $true } }
    )

    $attemptSummaries = @()

    function Get-CfHttpErrorDetails {
        param([Parameter(Mandatory = $true)]$Exception)

        $statusCode = $null
        $errorBody = $null

        try {
            if ($Exception.Data) {
                try {
                    $preservedStatus = $Exception.Data['CfStatusCode']
                    if ($null -ne $preservedStatus) {
                        $statusCode = [int]$preservedStatus
                    }
                }
                catch {
                    # best-effort
                }

                $preserved = $Exception.Data['CfErrorBody']
                if (-not [string]::IsNullOrWhiteSpace([string]$preserved)) {
                    $errorBody = [string]$preserved
                }
            }
        }
        catch {
            $errorBody = $null
        }

        try {
            if ($Exception.Response) {
                try {
                    if ($Exception.Response -is [System.Net.Http.HttpResponseMessage]) {
                        $statusCode = [int]$Exception.Response.StatusCode
                    }
                    else {
                        $statusCode = [int]$Exception.Response.StatusCode
                    }
                }
                catch {
                    $statusCode = $null
                }
                if ([string]::IsNullOrWhiteSpace($errorBody)) {
                    try {
                        if ($Exception.Response -is [System.Net.Http.HttpResponseMessage]) {
                            $errorBody = $Exception.Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                        }
                        else {
                            $stream = $Exception.Response.GetResponseStream()
                            if ($stream) {
                                $reader = [System.IO.StreamReader]::new($stream)
                                $errorBody = $reader.ReadToEnd()
                            }
                        }
                    }
                    catch {
                        $errorBody = $null
                    }
                }
            }
        }
        catch {
            $statusCode = $null
            # keep any preserved $errorBody
        }

        return [pscustomobject]@{
            StatusCode = $statusCode
            ErrorBody  = $errorBody
        }
    }

    function Get-CfApiErrorMessage {
        param([AllowNull()][string]$ErrorBody)

        if ([string]::IsNullOrWhiteSpace($ErrorBody)) { return $null }

        # Cloudflare error responses are typically JSON with an `errors` array.
        # Keep output short (single line) so it is readable in Actions logs.
        try {
            $json = $ErrorBody | ConvertFrom-Json -ErrorAction Stop
            if ($json -and $json.errors -and $json.errors.Count -gt 0) {
                $msg = $json.errors[0].message
                if ($msg) { return ($msg -replace "\r|\n", ' ').Trim() }
            }
            if ($json -and $json.messages -and $json.messages.Count -gt 0) {
                $m = $json.messages[0]
                if ($m) { return (($m | Out-String) -replace "\r|\n", ' ').Trim() }
            }
        }
        catch {
            # Not JSON; fall through
        }

        # Non-JSON: return a trimmed snippet
        $snippet = ($ErrorBody -replace "\r|\n", ' ').Trim()
        if ($snippet.Length -gt 180) { return $snippet.Substring(0, 180) + 'â€¦' }
        return $snippet
    }

    foreach ($candidate in $candidates) {
        try {
            $null = Invoke-CfApi -Method $candidate.Method -Uri $candidate.Uri -Body $candidate.Body
            Write-Host "[OK] DMARC Management enabled (or already enabled) for $ZoneName" -ForegroundColor Green
            return $true
        }
        catch {
            $details = Get-CfHttpErrorDetails -Exception $_.Exception
            $msg = $_.Exception.Message
            $cfMsg = Get-CfApiErrorMessage -ErrorBody $details.ErrorBody

            if ($details.StatusCode) {
                $line = "$($candidate.Method) $($candidate.Uri) -> HTTP $($details.StatusCode): $msg"
            }
            else {
                $line = "$($candidate.Method) $($candidate.Uri) -> $msg"
            }

            if (-not [string]::IsNullOrWhiteSpace($cfMsg)) {
                $line += " | cf: $cfMsg"
            }

            $attemptSummaries += $line

            # Try next endpoint
            continue
        }
    }

    if ($attemptSummaries.Count -gt 0) {
        Write-Warning "[OPTIONAL] Could not enable Cloudflare DMARC Management for $ZoneName via API. Enable in dashboard: Email > DMARC Management. (Token must include 'Dmarc Management Edit' permission.)"
        Write-Host "DMARC Management enable attempts (first 6):" -ForegroundColor Yellow
        $attemptSummaries | Select-Object -First 6 | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
    }
    else {
        Write-Warning "[OPTIONAL] Could not enable Cloudflare DMARC Management for $ZoneName via API. Enable in dashboard: Email > DMARC Management. (Token must include 'Dmarc Management Edit' permission.)"
    }
    return $false
}

function Get-DmarcRuaMailtos {
    param([AllowNull()][string]$DmarcContent)
    $normalized = Normalize-TxtContent -Value $DmarcContent
    if (-not $normalized) { return @() }

    $tags = $normalized -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $ruaTag = $tags | Where-Object { $_ -match '^(?i)rua\s*=' } | Select-Object -First 1
    if (-not $ruaTag) { return @() }

    $ruaValue = ($ruaTag -split '=', 2)[1].Trim()
    if (-not $ruaValue) { return @() }

    return ($ruaValue -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -like 'mailto:*' })
}

function Set-DmarcRuaMailtos {
    param(
        [Parameter(Mandatory = $true)][string]$DmarcContent,
        [Parameter(Mandatory = $true)][string[]]$RuaMailtos
    )

    $normalized = Normalize-TxtContent -Value $DmarcContent
    $tags = @()
    if ($normalized) {
        $tags = $normalized -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }

    # Remove existing rua tag(s)
    $tags = $tags | Where-Object { $_ -notmatch '^(?i)rua\s*=' }

    # Ensure v and p are present and normalized
    if (-not ($tags | Where-Object { $_ -match '^(?i)v\s*=\s*DMARC1$' })) {
        # Remove any existing v tag then re-add
        $tags = $tags | Where-Object { $_ -notmatch '^(?i)v\s*=' }
        $tags = @('v=DMARC1') + $tags
    }
    if (-not ($tags | Where-Object { $_ -match '^(?i)p\s*=' })) {
        $tags += 'p=none'
    }

    $uniqueRua = @($RuaMailtos | Where-Object { $_ } | Select-Object -Unique)
    if ($uniqueRua.Count -gt 0) {
        $tags += ('rua=' + ($uniqueRua -join ','))
    }

    return ($tags -join '; ')
}

# Single source of truth for "what does this mail provider's DNS look like".
# Both -EnforceStandard and -Audit read from here so the two cannot drift:
# before this existed, the M365 record set was written out twice (once as the
# enforced standard, once as the audited expectation).
function Get-MailProviderProfile {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Microsoft365', 'Google')][string]$Provider,
        [Parameter(Mandatory = $true)][string]$ZoneName
    )

    if ($Provider -eq 'Google') {
        # Google's current documented setup for a new domain is a single MX at
        # priority 1. The legacy ASPMX.L.GOOGLE.COM + ALT1-4 set is still valid
        # at Google's end; MxMatch is deliberately broad enough to recognise it
        # so a zone already on the legacy layout is not reported as missing.
        return [pscustomobject]@{
            DisplayName = 'Google Workspace'
            MxRecords   = @(
                @{ Content = 'smtp.google.com'; Priority = 1 }
            )
            MxMatch     = '*google.com'
            SpfInclude  = 'include:_spf.google.com'
            SpfContent  = 'v=spf1 include:_spf.google.com ~all'
            Cnames      = @()
            Srvs        = @()
        }
    }

    return [pscustomobject]@{
        DisplayName = 'Microsoft 365'
        MxRecords   = @(
            @{ Content = (($ZoneName -replace '\.', '-') + '.mail.protection.outlook.com'); Priority = 0 }
        )
        MxMatch     = '*.mail.protection.outlook.com'
        SpfInclude  = 'include:spf.protection.outlook.com'
        SpfContent  = 'v=spf1 include:spf.protection.outlook.com -all'
        Cnames      = @(
            @{ Name = 'autodiscover'; Content = 'autodiscover.outlook.com' },
            @{ Name = 'enterpriseenrollment'; Content = 'enterpriseenrollment-s.manage.microsoft.com' },
            @{ Name = 'enterpriseregistration'; Content = 'enterpriseregistration.windows.net' },
            @{ Name = 'lyncdiscover'; Content = 'webdir.online.lync.com' },
            @{ Name = 'sip'; Content = 'sipdir.online.lync.com' }
        )
        Srvs        = @(
            @{ Name = '_sip._tls'; Data = @{ service = '_sip'; proto = '_tls'; name = $ZoneName; priority = 100; weight = 1; port = 443; target = 'sipdir.online.lync.com' } },
            @{ Name = '_sipfederationtls._tcp'; Data = @{ service = '_sipfederationtls'; proto = '_tcp'; name = $ZoneName; priority = 100; weight = 1; port = 5061; target = 'sipfed.online.lync.com' } }
        )
    }
}

# The provider a zone is CURRENTLY on, read from its live MX records.
# Used by -Audit so a fleet-wide compliance sweep does not report every
# Google zone as a pile of missing Microsoft records.
function Resolve-MailProvider {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Records,
        [Parameter(Mandatory = $true)][string]$ZoneName,
        [Parameter(Mandatory = $true)][string]$Fallback
    )

    $mx = @($Records | Where-Object { $_.type -eq 'MX' })
    if ($mx.Count -eq 0) { return $Fallback }

    foreach ($provider in @('Microsoft365', 'Google')) {
        $profileForProvider = Get-MailProviderProfile -Provider $provider -ZoneName $ZoneName
        if ($mx | Where-Object { $_.content -like $profileForProvider.MxMatch }) { return $provider }
    }

    return $Fallback
}

# Swap the mail-provider include in an existing SPF record.
#
# Two SPF TXT records on one name is a permerror (RFC 7208 s.4.5), so a
# provider cutover MUST rewrite the record it finds rather than add a second
# one. Only the provider includes are touched: any other mechanism the charity
# has added (a newsletter sender, a CRM) is preserved in its original order.
function Get-SpfWithInclude {
    param(
        [Parameter(Mandatory = $true)][string]$SpfContent,
        [Parameter(Mandatory = $true)][string]$DesiredInclude,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$RemoveIncludes
    )

    $normalized = Normalize-TxtContent -Value $SpfContent
    $terms = @($normalized -split '\s+' | Where-Object { $_ })

    $kept = @()
    foreach ($term in $terms) {
        if ($RemoveIncludes -contains $term) { continue }
        $kept += $term
    }

    if ($kept -notcontains $DesiredInclude) {
        # Insert before the trailing all-qualifier if there is one, so the
        # record keeps its "...include:x ~all" shape instead of ending up with
        # a mechanism after the catch-all (which would never be evaluated).
        $allIndex = -1
        for ($i = 0; $i -lt $kept.Count; $i++) {
            if ($kept[$i] -match '^[-~?+]?all$') { $allIndex = $i; break }
        }
        if ($allIndex -ge 0) {
            $head = if ($allIndex -gt 0) { $kept[0..($allIndex - 1)] } else { @() }
            $tail = $kept[$allIndex..($kept.Count - 1)]
            $kept = @($head) + @($DesiredInclude) + @($tail)
        }
        else {
            $kept += $DesiredInclude
        }
    }

    return ($kept -join ' ')
}


# --- Main Logic ---

try {
    # 1. Resolve Zone ID
    $ZoneId = Get-ZoneId -ZoneName $Zone
    Write-Verbose "Found Zone ID: $ZoneId"

    # --- EXPORT ALL Operation (read-only full record dump) ---
    # Surfaces the complete, paginated record set (the same Get-AllDnsRecords
    # the Audit path fetches) to stdout AND a CSV artifact. Unlike -List, this
    # is not name-filtered, so it returns every record in the zone.
    if ($ExportAll) {
        $allRecords = Get-AllDnsRecords -ZoneId $ZoneId
        $projected = $allRecords |
            Select-Object id, type, name, content, proxied, ttl, priority |
            Sort-Object type, name
        Write-Host "Full DNS record export for zone '$Zone' ($($projected.Count) records):" -ForegroundColor Cyan
        $projected | Format-Table -AutoSize
        $projected | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
        Write-Host "Exported $($projected.Count) records to $OutputFile" -ForegroundColor Green
        return
    }

    # 2. Resolve Full Record Name
    if ($Name -eq '@') {
        $RecordName = $Zone
    }
    else {
        # If user passed full fqdn (e.g. staging.example.org) use it, otherwise append zone
        if ($Name.EndsWith($Zone)) { $RecordName = $Name }
        else { $RecordName = "$Name.$Zone" }
    }

    # 3. Search for existing records
    $queryParams = @{
        name = $RecordName
    }
    if ($Type) { $queryParams['type'] = $Type }

    $existing = (Invoke-CfApi -Method 'GET' -Uri "/zones/$ZoneId/dns_records" -Params $queryParams).result

    # --- LIST Operation ---
    if ($List) {
        if ($existing.Count -eq 0) { Write-Warning "No records found for $RecordName" }
        else {
            $existing | Select-Object id, type, name, content, proxied, ttl | Format-Table -AutoSize
        }
        return
    }

    # --- AUDIT Operation ---
    if ($Audit) {
        Write-Host "Running Compliance Audit for Zone: $Zone" -ForegroundColor Cyan
        
        # Helper to find
        $allRecords = Get-AllDnsRecords -ZoneId $ZoneId

        # 0. CNAME Inventory (helps identify other required CNAMEs beyond WWW)
        $cnameRecords = $allRecords | Where-Object { $_.type -eq 'CNAME' } | Sort-Object name
        if (-not $cnameRecords -or $cnameRecords.Count -eq 0) {
            Write-Host "[INFO] No CNAME records found in zone." -ForegroundColor DarkGray
        }
        else {
            Write-Host "CNAME inventory:" -ForegroundColor DarkCyan
            foreach ($rec in $cnameRecords) {
                Write-Host (" - {0} -> {1} (proxied={2}, ttl={3})" -f $rec.name, $rec.content, $rec.proxied, $rec.ttl)
            }
        }

        # Which provider to audit against. When -MailProvider was not passed
        # explicitly, read it off the zone's own MX records: a fleet sweep
        # (107) audits many zones in one run and must not report every Google
        # zone as a pile of missing Microsoft records.
        $auditProvider = $MailProvider
        if (-not $PSBoundParameters.ContainsKey('MailProvider')) {
            $auditProvider = Resolve-MailProvider -Records $allRecords -ZoneName $Zone -Fallback $MailProvider
        }
        $mailProfile = Get-MailProviderProfile -Provider $auditProvider -ZoneName $Zone
        Write-Host "Auditing mail against provider: $($mailProfile.DisplayName)" -ForegroundColor Cyan

        # 0b. Required CNAMEs (provider service records + GitHub Pages)
        $githubPagesProxied = [bool]$ProxyGitHubPages
        $requiredCnames = @()
        foreach ($cname in $mailProfile.Cnames) {
            $requiredCnames += @{ Name = "$($cname.Name).$Zone"; Content = $cname.Content; Proxied = $false }
        }
        $requiredCnames += @{ Name = "www.$Zone"; Content = (Get-GhPagesWwwTarget); Proxied = $githubPagesProxied }

        foreach ($req in $requiredCnames) {
            $candidates = $allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq $req.Name }
            $match = $candidates | Where-Object { $_.content -eq $req.Content -and $_.proxied -eq $req.Proxied }

            if ($match) {
                Write-Host "[OK] Required CNAME present: $($req.Name)" -ForegroundColor Green
            }
            elseif ($candidates) {
                $example = $candidates | Select-Object -First 1
                Write-Warning "[DIFFERS] Required CNAME $($req.Name): found '$($example.content)' (proxied=$($example.proxied)), expected '$($req.Content)' (proxied=$($req.Proxied))"
            }
            else {
                Write-Warning "[MISSING] Required CNAME $($req.Name) -> $($req.Content) (proxied=$($req.Proxied))"
            }
        }

        # 0c. Required SRV records (provider service records; Google has none)
        $requiredSrvs = @()
        foreach ($srv in $mailProfile.Srvs) {
            $requiredSrvs += @{ Name = "$($srv.Name).$Zone"; Priority = $srv.Data.priority; Weight = $srv.Data.weight; Port = $srv.Data.port; Target = $srv.Data.target }
        }

        foreach ($req in $requiredSrvs) {
            $candidates = $allRecords | Where-Object { $_.type -eq 'SRV' -and $_.name -eq $req.Name }
            $match = $candidates | Where-Object {
                $_.data -and
                [int]$_.data.priority -eq [int]$req.Priority -and
                [int]$_.data.weight -eq [int]$req.Weight -and
                [int]$_.data.port -eq [int]$req.Port -and
                $_.data.target -eq $req.Target
            }

            if ($match) {
                Write-Host "[OK] Required SRV present: $($req.Name)" -ForegroundColor Green
            }
            elseif ($candidates) {
                $example = $candidates | Select-Object -First 1
                Write-Warning "[DIFFERS] Required SRV $($req.Name): found '$($example.data.priority) $($example.data.weight) $($example.data.port) $($example.data.target)', expected '$($req.Priority) $($req.Weight) $($req.Port) $($req.Target)'"
            }
            else {
                Write-Warning "[MISSING] Required SRV $($req.Name) -> $($req.Priority) $($req.Weight) $($req.Port) $($req.Target)"
            }
        }
        
        # 1. Mail provider MX
        $mx = $allRecords | Where-Object { $_.type -eq 'MX' -and $_.content -like $mailProfile.MxMatch }
        if ($mx) { Write-Host "[OK] $($mailProfile.DisplayName) MX Record found ($($mx.content))" -ForegroundColor Green }
        else { Write-Warning "[MISSING] $($mailProfile.DisplayName) MX Record ($($mailProfile.MxMatch))" }

        # 1b. A second provider's MX alongside the expected one splits inbound
        # delivery, so surface it as a finding rather than leaving it silent.
        $otherProvider = if ($auditProvider -eq 'Google') { 'Microsoft365' } else { 'Google' }
        $otherProfile = Get-MailProviderProfile -Provider $otherProvider -ZoneName $Zone
        $strayMx = $allRecords | Where-Object { $_.type -eq 'MX' -and $_.content -like $otherProfile.MxMatch }
        if ($strayMx) {
            Write-Warning "[CONFLICT] $($otherProfile.DisplayName) MX also present ($($strayMx.content)) - inbound mail is split between two providers"
        }

        # 2. SPF
        # Note: Cloudflare's API/UI frequently normalizes TXT quoting. Treat normalized content as authoritative
        # to avoid false diffs and unnecessary rewrites.
        $spf = $allRecords | Where-Object { $_.type -eq 'TXT' -and (Normalize-TxtContent -Value $_.content) -like "*$($mailProfile.SpfInclude)*" }
        if ($spf) {
            Write-Host "[OK] $($mailProfile.DisplayName) SPF Record found" -ForegroundColor Green
        }
        else { Write-Warning "[MISSING] $($mailProfile.DisplayName) SPF Record ($($mailProfile.SpfInclude))" }

        # 3. DMARC
        $dmarc = $allRecords | Where-Object { $_.type -eq 'TXT' -and $_.name -like "_dmarc.$Zone" }
        $dmarcValid = $dmarc | Where-Object { (Normalize-TxtContent -Value $_.content) -like 'v=DMARC1*' }
        if ($dmarcValid) {
            $normalizedDmarc = Normalize-TxtContent -Value ($dmarcValid | Select-Object -First 1).content
            $ruaMailtos = Get-DmarcRuaMailtos -DmarcContent $normalizedDmarc

            $hasInternalRua = $ruaMailtos -contains 'mailto:dmarc-rua@freeforcharity.org'
            $hasCloudflareRua = [bool]($ruaMailtos | Where-Object { $_ -like 'mailto:*@dmarc-reports.cloudflare.net' } | Select-Object -First 1)

            if ($hasInternalRua) {
                if ($hasCloudflareRua) {
                    Write-Host "[OK] DMARC Record found (internal+Cloudflare rua)" -ForegroundColor Green
                }
                else {
                    Write-Host "[OK] DMARC Record found (internal rua only)" -ForegroundColor Green
                    Write-Warning "[OPTIONAL] DMARC Record has no Cloudflare rua; enable Cloudflare DMARC Management to add it"
                }
            }
            else {
                if (-not $hasInternalRua) { Write-Warning "[DIFFERS] DMARC Record missing internal rua (mailto:dmarc-rua@freeforcharity.org)" }
                if (-not $hasCloudflareRua) { Write-Warning "[OPTIONAL] DMARC Record has no Cloudflare rua; enable Cloudflare DMARC Management to add it" }
            }
        }
        else { Write-Warning "[MISSING] DMARC Record (_dmarc.$Zone)" }

        # 4. GitHub Pages (A + AAAA Records) — canonical sets from the shared lib (#778)
        $ghV4Ips = @(Get-GhPagesIps)
        $ghV6Ips = @(Get-GhPagesIpv6s)

        $aRecords = $allRecords | Where-Object { $_.type -eq 'A' -and $_.name -eq $Zone }
        $missingV4 = $ghV4Ips | Where-Object { $_ -notin $aRecords.content }
        if ($missingV4.Count -eq 0 -and $aRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages A Records found" -ForegroundColor Green
        }
        else {
            Write-Warning "[MISSING/PARTIAL] GitHub Pages A Records. Missing: $($missingV4 -join ', ')"
        }

        $aaaaRecords = $allRecords | Where-Object { $_.type -eq 'AAAA' -and $_.name -eq $Zone }
        $missingV6 = $ghV6Ips | Where-Object { $_ -notin $aaaaRecords.content }
        if ($missingV6.Count -eq 0 -and $aaaaRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages AAAA Records found" -ForegroundColor Green
        }
        else {
            Write-Warning "[MISSING/PARTIAL] GitHub Pages AAAA Records. Missing: $($missingV6 -join ', ')"
        }

        # 5. WWW CNAME
        $www = $allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq "www.$Zone" }
        if ($www) { Write-Host "[OK] WWW CNAME found ($($www.content))" -ForegroundColor Green }
        else { Write-Warning "[MISSING] WWW CNAME record" }

        return
    }

    # --- ENFORCE STANDARD Operation ---
    if ($EnforceStandard) {
        Write-Host "Enforcing FFC Standard Configuration for Zone: $Zone" -ForegroundColor Cyan

        # IMPORTANT: Enforce needs a full record inventory. Without this, it will treat everything as missing.
        $allRecords = Get-AllDnsRecords -ZoneId $ZoneId

        if ($GitHubPagesOnly) {
            Write-Host "GitHubPagesOnly enabled: will only update apex A/AAAA + www CNAME. MX/TXT/SRV/etc will not be modified." -ForegroundColor Yellow

            # Canonical GitHub Pages targets from the shared lib (#778)
            $desiredA = @(Get-GhPagesIps)
            $desiredAAAA = @(Get-GhPagesIpv6s)
            $wwwTarget = Get-GhPagesWwwTarget
            $apexFqdn = $Zone
            $wwwFqdn = "www.$Zone"
            $pagesProxied = [bool]$ProxyGitHubPages
            $pagesTtl = 1

            function Remove-RecordById {
                param(
                    [Parameter(Mandatory = $true)][string]$RecordId,
                    [Parameter(Mandatory = $true)][string]$Type,
                    [Parameter(Mandatory = $true)][string]$Name,
                    [Parameter(Mandatory = $true)][string]$Content
                )
                if ($DryRun) {
                    Write-Host "[DRY-RUN] Would DELETE record: $Type $Name -> $Content (ID: $RecordId)" -ForegroundColor Yellow
                    return
                }
                Write-Host "Deleting record: $Type $Name -> $Content..." -NoNewline
                $null = Invoke-CfApi -Method 'DELETE' -Uri "/zones/$ZoneId/dns_records/$RecordId"
                Write-Host " DONE" -ForegroundColor Green
            }

            function Ensure-MultiValueRecordSet {
                param(
                    [Parameter(Mandatory = $true)][ValidateSet('A', 'AAAA')][string]$Type,
                    [Parameter(Mandatory = $true)][string]$Fqdn,
                    [Parameter(Mandatory = $true)][string[]]$DesiredContents,
                    [Parameter(Mandatory = $true)][bool]$DesiredProxied,
                    [Parameter(Mandatory = $true)][int]$DesiredTtl
                )

                $existingSet = @($allRecords | Where-Object { $_.type -eq $Type -and $_.name -eq $Fqdn })

                # 1) Remove any records that are not part of the desired set
                foreach ($rec in $existingSet) {
                    if ($DesiredContents -notcontains $rec.content) {
                        Remove-RecordById -RecordId $rec.id -Type $rec.type -Name $rec.name -Content $rec.content
                    }
                }

                # Refresh view if we did deletes (Cloudflare API state changed)
                if (-not $DryRun) {
                    $allRecords = Get-AllDnsRecords -ZoneId $ZoneId
                    $existingSet = @($allRecords | Where-Object { $_.type -eq $Type -and $_.name -eq $Fqdn })
                }

                # 2) Ensure each desired content exists, and matches proxied/ttl
                foreach ($content in $DesiredContents) {
                    $match = $existingSet | Where-Object { $_.content -eq $content } | Select-Object -First 1
                    if (-not $match) {
                        $payload = @{ type = $Type; name = $Fqdn; content = $content; ttl = $DesiredTtl; proxied = $DesiredProxied }
                        if ($DryRun) {
                            Write-Host "[DRY-RUN] Would CREATE new record: $Type $Fqdn -> $content (Proxied: $DesiredProxied)" -ForegroundColor Yellow
                        }
                        else {
                            Write-Host "Creating new record: $Type $Fqdn -> $content..." -NoNewline
                            $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
                            Write-Host " DONE" -ForegroundColor Green
                        }
                        continue
                    }

                    $needsUpdate = $false
                    if ([bool]$match.proxied -ne $DesiredProxied) { $needsUpdate = $true }
                    if ([int]$match.ttl -ne [int]$DesiredTtl) { $needsUpdate = $true }

                    if ($needsUpdate) {
                        $payload = @{ type = $Type; name = $Fqdn; content = $content; ttl = $DesiredTtl; proxied = $DesiredProxied }
                        if ($DryRun) {
                            Write-Host "[DRY-RUN] Would UPDATE record $($match.id): $Type $Fqdn -> $content (Proxied: $DesiredProxied)" -ForegroundColor Yellow
                        }
                        else {
                            Write-Host "Updating record $($match.id)..." -NoNewline
                            $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($match.id)" -Body $payload
                            Write-Host " DONE" -ForegroundColor Green
                        }
                    }
                    else {
                        Write-Host "  [Skip] $Type $Fqdn -> $content already matches." -ForegroundColor DarkGray
                    }
                }
            }

            function Ensure-WwwCname {
                param(
                    [Parameter(Mandatory = $true)][string]$Fqdn,
                    [Parameter(Mandatory = $true)][string]$Target,
                    [Parameter(Mandatory = $true)][bool]$DesiredProxied,
                    [Parameter(Mandatory = $true)][int]$DesiredTtl
                )

                # Remove any A/AAAA records at www (avoid conflicts)
                $wwwConflicts = @($allRecords | Where-Object { $_.name -eq $Fqdn -and $_.type -in @('A', 'AAAA') })
                foreach ($rec in $wwwConflicts) {
                    Remove-RecordById -RecordId $rec.id -Type $rec.type -Name $rec.name -Content $rec.content
                }

                if (-not $DryRun) {
                    $allRecords = Get-AllDnsRecords -ZoneId $ZoneId
                }

                $existingCnames = @($allRecords | Where-Object { $_.name -eq $Fqdn -and $_.type -eq 'CNAME' })

                if ($existingCnames.Count -eq 0) {
                    $payload = @{ type = 'CNAME'; name = $Fqdn; content = $Target; ttl = $DesiredTtl; proxied = $DesiredProxied }
                    if ($DryRun) {
                        Write-Host "[DRY-RUN] Would CREATE new record: CNAME $Fqdn -> $Target (Proxied: $DesiredProxied)" -ForegroundColor Yellow
                    }
                    else {
                        Write-Host "Creating new record: CNAME $Fqdn -> $Target..." -NoNewline
                        $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
                        Write-Host " DONE" -ForegroundColor Green
                    }
                    return
                }

                foreach ($rec in $existingCnames) {
                    $needsUpdate = $false
                    if ($rec.content -ne $Target) { $needsUpdate = $true }
                    if ([bool]$rec.proxied -ne $DesiredProxied) { $needsUpdate = $true }
                    if ([int]$rec.ttl -ne [int]$DesiredTtl) { $needsUpdate = $true }

                    if (-not $needsUpdate) {
                        Write-Host "  [Skip] CNAME $Fqdn matches desired state." -ForegroundColor DarkGray
                        continue
                    }

                    $payload = @{ type = 'CNAME'; name = $Fqdn; content = $Target; ttl = $DesiredTtl; proxied = $DesiredProxied }
                    if ($DryRun) {
                        Write-Host "[DRY-RUN] Would UPDATE record $($rec.id): CNAME $Fqdn -> $Target (Proxied: $DesiredProxied)" -ForegroundColor Yellow
                    }
                    else {
                        Write-Host "Updating record $($rec.id)..." -NoNewline
                        $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($rec.id)" -Body $payload
                        Write-Host " DONE" -ForegroundColor Green
                    }
                }
            }

            Ensure-MultiValueRecordSet -Type 'A' -Fqdn $apexFqdn -DesiredContents $desiredA -DesiredProxied $pagesProxied -DesiredTtl $pagesTtl
            Ensure-MultiValueRecordSet -Type 'AAAA' -Fqdn $apexFqdn -DesiredContents $desiredAAAA -DesiredProxied $pagesProxied -DesiredTtl $pagesTtl
            Ensure-WwwCname -Fqdn $wwwFqdn -Target $wwwTarget -DesiredProxied $pagesProxied -DesiredTtl $pagesTtl

            return
        }

        # DMARC Management API probing is intentionally disabled by default (too noisy and not routable).
        # Set FFC_CF_DMARCMGMT_DEBUG=1 to run the probe/attempts.
        if ($env:FFC_CF_DMARCMGMT_DEBUG -eq '1') {
            if ($DryRun) {
                Write-Host "[DRY-RUN] Would attempt DMARC Management API probe/enable for $Zone" -ForegroundColor Yellow
            }
            else {
                $null = Enable-DmarcManagement -ZoneId $ZoneId -ZoneName $Zone
            }
        }

        $mailProfile = Get-MailProviderProfile -Provider $MailProvider -ZoneName $Zone
        $foreignProvider = if ($MailProvider -eq 'Google') { 'Microsoft365' } else { 'Google' }
        $foreignProfile = Get-MailProviderProfile -Provider $foreignProvider -ZoneName $Zone

        # A cutover only happens when the caller NAMED a provider. Callers that
        # just want the standard enforced (101, 102, 103) pass no -MailProvider
        # and must keep their pre-existing, non-destructive behaviour: no
        # deletions, and no rewriting of an SPF record that already exists.
        $mailCutover = $PSBoundParameters.ContainsKey('MailProvider')
        Write-Host "Mail provider: $($mailProfile.DisplayName) (cutover: $mailCutover)" -ForegroundColor Cyan

        $wwwTarget = Get-GhPagesWwwTarget

        # GitHub Pages should be DNS-only by default to allow GitHub to validate and issue SSL.
        # Use -ProxyGitHubPages to preserve the ability to orange-cloud these records if needed.
        $githubPagesProxied = [bool]$ProxyGitHubPages
        
        # Define Standard Records
        #
        # The mail half comes from the provider profile; DMARC and the GitHub
        # Pages records below are provider-agnostic and apply either way.
        $standards = @(
            # SPF: rewrite the provider include in place. MatchContains finds an
            # already-correct record; SpfCutover tells the TXT branch to edit an
            # existing v=spf1 rather than add a second one (two = permerror).
            @{ Type = 'TXT'; Name = '@'; Content = $mailProfile.SpfContent; MatchContains = $mailProfile.SpfInclude; SpfCutover = $mailCutover; SpfRemoveIncludes = @($foreignProfile.SpfInclude) },
            # DMARC: preserve Cloudflare-generated rua (if present) and always include internal rua.
            @{ Type = 'TXT'; Name = '_dmarc'; Content = 'v=DMARC1; p=none'; EnsureInternalRua = $true; PreserveCloudflareRua = $true; InternalRua = 'mailto:dmarc-rua@freeforcharity.org' }
        )

        # Mail: MX first, then any provider service records (M365 has CNAMEs and
        # Teams SRVs; Google has none). Created BEFORE the foreign provider's
        # records are removed, so the zone is never left without an MX.
        foreach ($mx in $mailProfile.MxRecords) {
            $standards += @{ Type = 'MX'; Name = '@'; Content = $mx.Content; Priority = $mx.Priority; MatchContent = $mailProfile.MxMatch }
        }
        foreach ($cname in $mailProfile.Cnames) {
            $standards += @{ Type = 'CNAME'; Name = $cname.Name; Content = $cname.Content; Proxied = $false }
        }
        foreach ($srv in $mailProfile.Srvs) {
            $standards += @{ Type = 'SRV'; Name = $srv.Name; Data = $srv.Data }
        }

        # GitHub Pages apex A/AAAA + www CNAME — generated from the shared lib
        # (#778) so the Pages IP set has exactly one source. Appended in the
        # same order the literals used to be (A x4, AAAA x4, www) so the
        # enforcement output is unchanged.
        foreach ($ghIp in @(Get-GhPagesIps)) {
            $standards += @{ Type = 'A'; Name = '@'; Content = $ghIp; Proxied = $githubPagesProxied }
        }
        foreach ($ghIp6 in @(Get-GhPagesIpv6s)) {
            $standards += @{ Type = 'AAAA'; Name = '@'; Content = $ghIp6; Proxied = $githubPagesProxied }
        }
        $standards += @{ Type = 'CNAME'; Name = 'www'; Content = $wwwTarget; Proxied = $githubPagesProxied }

        foreach ($std in $standards) {
            $recName = if ($std.Name -eq '@') { $Zone } else { "$($std.Name).$Zone" }
            
            # Re-use the existing logic by calling the script recursively or refactoring.
            # For simplicity and safety within this function, we will call the logic we just verified.
            # We construct the arguments to simulate a "Single Record Ensure" call.
            
            Write-Host "Checking standard record: $($std.Type) $($std.Name)..." -NoNewline
            
            # We can't easily recurse inside the same script execution context without dot-sourcing, 
            # so we will use the logic we built for internal function usage? 
            # Actually, `Update-CloudflareDns.ps1` is a script, so we can call it.
            # But calling it 8 times might be slow on auth. 
            # BETTER: We implement the "Check & Create" logic right here, reusing the helper variables.

            # 1. Check existence (and identify update candidates)
            $stdContent = $std.Content
            $desiredData = $null
            if ($std.Type -eq 'SRV' -and $std.ContainsKey('Data')) { $desiredData = $std.Data }
            $desiredProxied = $null
            if ($std.Type -in @('A', 'AAAA', 'CNAME')) {
                $desiredProxied = $true
                if ($std.ContainsKey('Proxied')) { $desiredProxied = [bool]$std.Proxied }
            }
            $candidates = $allRecords | Where-Object { $_.type -eq $std.Type -and $_.name -eq $recName }

            $foundRecord = $null
            $updateCandidate = $null

            switch ($std.Type) {
                'MX' {
                    # Cloudflare stores MX target in `content` and priority separately.
                    # Treat as present if any apex MX points at the desired provider's
                    # mail hosts with the desired priority. MatchContent is a wildcard
                    # so a provider with several valid targets (Google's legacy ALT*
                    # set, M365's per-tenant host) still matches.
                    $mxMatch = if ($std.ContainsKey('MatchContent') -and $std.MatchContent) { $std.MatchContent } else { $stdContent }
                    $foundRecord = $candidates | Where-Object {
                        $_.content -like $mxMatch -and
                        ($null -eq $std.Priority -or $_.priority -eq $std.Priority)
                    }
                }
                'TXT' {
                    if ($std.ContainsKey('MatchContains') -and $std.MatchContains) {
                        # SPF: present if it already carries this provider's include;
                        # do not overwrite other mechanisms.
                        $foundRecord = $candidates | Where-Object { (Normalize-TxtContent -Value $_.content) -like ("*" + $std.MatchContains + "*") }
                        if ($foundRecord) {
                            $spfCandidate = $foundRecord | Select-Object -First 1
                            $stdContent = $spfCandidate.content
                        }
                        elseif ($std.ContainsKey('SpfCutover') -and $std.SpfCutover) {
                            # Provider cutover: an SPF record exists but points at the
                            # other provider. Rewrite THAT record — adding a second
                            # v=spf1 TXT is a permerror and would break all outbound
                            # mail authentication, not just the new provider's.
                            $existingSpf = $candidates | Where-Object { (Normalize-TxtContent -Value $_.content) -like 'v=spf1*' } | Select-Object -First 1
                            if ($existingSpf) {
                                $removeIncludes = @()
                                if ($std.ContainsKey('SpfRemoveIncludes') -and $std.SpfRemoveIncludes) { $removeIncludes = @($std.SpfRemoveIncludes) }
                                $updateCandidate = $existingSpf
                                $stdContent = Quote-TxtContent -Value (Get-SpfWithInclude -SpfContent $existingSpf.content -DesiredInclude $std.MatchContains -RemoveIncludes $removeIncludes)
                            }
                        }
                    }
                    elseif ($std.Name -eq '_dmarc') {
                        $foundRecord = $candidates | Where-Object { (Normalize-TxtContent -Value $_.content) -like 'v=DMARC1*' }
                        $ensureInternalRua = $false
                        $preserveCloudflareRua = $false
                        $internalRua = 'mailto:dmarc-rua@freeforcharity.org'
                        if ($std.ContainsKey('EnsureInternalRua')) { $ensureInternalRua = [bool]$std.EnsureInternalRua }
                        if ($std.ContainsKey('PreserveCloudflareRua')) { $preserveCloudflareRua = [bool]$std.PreserveCloudflareRua }
                        if ($std.ContainsKey('InternalRua') -and $std.InternalRua) { $internalRua = [string]$std.InternalRua }

                        if ($foundRecord) {
                            $dmarcCandidate = $foundRecord | Select-Object -First 1
                            $normalized = Normalize-TxtContent -Value $dmarcCandidate.content
                            $existingRua = Get-DmarcRuaMailtos -DmarcContent $normalized

                            $hasInternalRua = $existingRua -contains $internalRua
                            $hasCloudflareRua = $false
                            if ($preserveCloudflareRua) {
                                $hasCloudflareRua = [bool]($existingRua | Where-Object { $_ -like 'mailto:*@dmarc-reports.cloudflare.net' } | Select-Object -First 1)
                            }
                            # Only update when needed: missing required internal rua.
                            if ($ensureInternalRua -and -not $hasInternalRua) {
                                $updateCandidate = $dmarcCandidate
                                $desiredRua = @($existingRua)
                                $desiredRua += $internalRua
                                $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $normalized -RuaMailtos $desiredRua
                                $stdContent = $desiredNormalized
                                $foundRecord = $null
                            }
                            else {
                                # Already compliant; do not rewrite just to normalize formatting/order.
                                $stdContent = $dmarcCandidate.content
                            }
                        }
                        elseif ($candidates) {
                            # Prefer updating an existing _dmarc TXT rather than creating duplicates.
                            $updateCandidate = $candidates | Select-Object -First 1
                            $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $stdContent -RuaMailtos @($internalRua)
                            $stdContent = $desiredNormalized
                        }
                        else {
                            $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $stdContent -RuaMailtos @($internalRua)
                            $stdContent = $desiredNormalized
                        }
                    }
                    else {
                        $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                    }
                }
                'A' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    # A records are multi-value in our standard (GitHub Pages needs multiple A records at the apex).
                    # If a required value is missing, CREATE it rather than updating an arbitrary existing A record,
                    # which can "swap" values and leave one required IP missing.
                }
                'AAAA' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    # AAAA records are multi-value in our standard (GitHub Pages needs multiple AAAA records at the apex).
                    # If a required value is missing, CREATE it rather than updating an arbitrary existing AAAA record.
                }
                'CNAME' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    if (-not $foundRecord -and $candidates) {
                        # Prefer updating existing CNAME rather than creating a second one.
                        $updateCandidate = ($candidates | Where-Object { $_.content -eq $stdContent } | Select-Object -First 1)
                        if (-not $updateCandidate) { $updateCandidate = $candidates | Select-Object -First 1 }
                    }
                }
                'SRV' {
                    if (-not $desiredData) { break }
                    $foundRecord = $candidates | Where-Object {
                        $_.data -and
                        ($null -eq $_.data.service -or $_.data.service -eq $desiredData.service) -and
                        ($null -eq $_.data.proto -or $_.data.proto -eq $desiredData.proto) -and
                        ($null -eq $_.data.name -or $_.data.name -eq $desiredData.name) -and
                        [int]$_.data.priority -eq [int]$desiredData.priority -and
                        [int]$_.data.weight -eq [int]$desiredData.weight -and
                        [int]$_.data.port -eq [int]$desiredData.port -and
                        $_.data.target -eq $desiredData.target
                    }
                    if (-not $foundRecord -and $candidates) {
                        $updateCandidate = $candidates | Select-Object -First 1
                    }
                }
                default {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                }
            }

            # Cloudflare UI warns when TXT content isn't wrapped in quotes.
            # Normalize-TxtContent already strips quotes for comparisons, so we can safely update
            # existing TXT records into the quoted form without changing the normalized value.
            if ($std.Type -eq 'TXT' -and $foundRecord) {
                $existingTxt = $foundRecord | Select-Object -First 1
                if ($existingTxt -and -not (Is-TxtQuoted -Value $existingTxt.content)) {
                    $updateCandidate = $existingTxt
                    $stdContent = Quote-TxtContent -Value $existingTxt.content
                    $foundRecord = $null
                }
            }

            if ($foundRecord) {
                Write-Host " [OK]" -ForegroundColor Green
            }
            else {
                if ($updateCandidate) {
                    Write-Host " [DIFFERS] -> Updating..." -ForegroundColor Yellow

                    $updatePayload = @{
                        type = $std.Type
                        name = $recName
                        ttl  = 1
                    }
                    if ($std.Type -eq 'SRV') {
                        $updatePayload['data'] = $desiredData
                    }
                    else {
                        if ($std.Type -eq 'TXT') {
                            $updatePayload['content'] = Quote-TxtContent -Value $stdContent
                        }
                        else {
                            $updatePayload['content'] = $stdContent
                        }
                    }
                    if ($std.Type -eq 'MX') { $updatePayload['priority'] = $std.Priority }
                    if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) { $updatePayload['proxied'] = $desiredProxied }

                    if (-not $DryRun) {
                        try {
                            $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($updateCandidate.id)" -Body $updatePayload
                            Write-Host "UPDATED" -ForegroundColor Green
                        }
                        catch {
                            Write-Error "Failed to update $($std.Type) $recName (ID: $($updateCandidate.id))"
                        }
                    }
                    else {
                        if ($std.Type -eq 'SRV') {
                            $d = $desiredData
                            $details = "service=$($d.service) proto=$($d.proto) name=$($d.name) priority=$($d.priority) weight=$($d.weight) port=$($d.port) target=$($d.target)"
                            Write-Host "[DRY-RUN] Would UPDATE record $($updateCandidate.id): $($std.Type) $recName -> $details" -ForegroundColor Yellow
                        }
                        else {
                            $details = $stdContent
                            if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) {
                                $details = "$details (Proxied: $desiredProxied)"
                            }
                            Write-Host "[DRY-RUN] Would UPDATE record $($updateCandidate.id): $($std.Type) $recName -> $details" -ForegroundColor Yellow
                        }
                    }
                    continue
                }

                Write-Host " [MISSING] -> Creating..." -ForegroundColor Yellow
                
                # Payload
                $newPayload = @{
                    type = $std.Type
                    ttl  = 1 # Auto
                }
                if ($std.Type -eq 'SRV') {
                    $newPayload['data'] = $desiredData
                }
                else {
                    if ($std.Type -eq 'TXT') {
                        $newPayload['content'] = Quote-TxtContent -Value $stdContent
                    }
                    else {
                        $newPayload['content'] = $stdContent
                    }
                }
                if ($std.Type -eq 'MX') { $newPayload['priority'] = $std.Priority }
                # Proxied? Standard FFC: Pages A/CNAME = Proxied? Usually Yes for SSL. 
                # M365 = No.
                if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) { $newPayload['proxied'] = $desiredProxied }
                
                # Name must be FQDN for the API
                $newPayload['name'] = $recName

                if (-not $DryRun) {
                    try {
                        $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $newPayload
                        Write-Host "CREATED" -ForegroundColor Green
                    }
                    catch {
                        Write-Error "Failed to create $($std.Type) $recName"
                    }
                }
                else {
                    if ($std.Type -eq 'SRV') {
                        $d = $desiredData
                        $details = "service=$($d.service) proto=$($d.proto) name=$($d.name) priority=$($d.priority) weight=$($d.weight) port=$($d.port) target=$($d.target)"
                        Write-Host "[DRY-RUN] Would CREATE new record: $($std.Type) $recName -> $details" -ForegroundColor Yellow
                    }
                    else {
                        $details = $stdContent
                        if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) {
                            $details = "$details (Proxied: $desiredProxied)"
                        }
                        Write-Host "[DRY-RUN] Would CREATE new record: $($std.Type) $recName -> $details" -ForegroundColor Yellow
                    }
                }
            }
        }

        # --- Mail provider cutover: retire the OTHER provider's records ---
        #
        # OPT-IN ONLY. Deleting runs solely when the caller named a provider.
        # 101, 102 and 103 all call -EnforceStandard with no -MailProvider, and
        # 102/103 run live against the whole fleet; if this pass were
        # unconditional they would start deleting the Google MX off any zone an
        # admin had deliberately put on Google outside this tooling. Enforcing
        # "the standard" has never meant removing mail records, and it must not
        # start meaning that as a side effect of adding provider support.
        #
        # Runs last, on purpose. The desired provider's MX is already in place by
        # the time anything is deleted, so the zone never sits with no MX at all —
        # which would be worse than a brief overlap, because senders fall back to
        # the apex A record (Cloudflare proxy IPs here) and mail is lost outright.
        #
        # Only MX / service CNAMEs / SRVs are considered. SPF is NOT deleted here:
        # it is a single shared record that the standards loop above rewrites in
        # place, and DKIM (google._domainkey, selector*._domainkey) is left alone
        # because those keys are minted in the provider's admin console.
        if (-not $mailCutover) {
            Write-Host "--- Mail Cutover: skipped (no -MailProvider given; existing mail records left alone) ---" -ForegroundColor DarkGray
            return
        }

        Write-Host "--- Mail Cutover: retiring $($foreignProfile.DisplayName) records ---" -ForegroundColor Cyan

        $foreignRecords = @()
        $foreignRecords += @($allRecords | Where-Object { $_.type -eq 'MX' -and $_.content -like $foreignProfile.MxMatch })
        foreach ($cname in $foreignProfile.Cnames) {
            $cnameFqdn = "$($cname.Name).$Zone"
            $foreignRecords += @($allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq $cnameFqdn -and $_.content -like "*$($cname.Content)*" })
        }
        foreach ($srv in $foreignProfile.Srvs) {
            $srvFqdn = "$($srv.Name).$Zone"
            $foreignRecords += @($allRecords | Where-Object { $_.type -eq 'SRV' -and $_.name -eq $srvFqdn })
        }

        if ($foreignRecords.Count -eq 0) {
            Write-Host "  [OK] No $($foreignProfile.DisplayName) mail records present." -ForegroundColor Green
        }
        else {
            foreach ($rec in $foreignRecords) {
                $what = "$($rec.type) $($rec.name) -> $($rec.content)"
                if ($DryRun) {
                    Write-Host "[DRY-RUN] Would DELETE $($foreignProfile.DisplayName) record: $what (ID: $($rec.id))" -ForegroundColor Yellow
                }
                else {
                    Write-Host "Deleting $($foreignProfile.DisplayName) record: $what..." -NoNewline
                    try {
                        $null = Invoke-CfApi -Method 'DELETE' -Uri "/zones/$ZoneId/dns_records/$($rec.id)"
                        Write-Host " DELETED" -ForegroundColor Green
                    }
                    catch {
                        Write-Error "Failed to delete $what"
                    }
                }
            }
        }

        return
    }

    # --- REMOVE Operation ---
    if ($Remove) {
        if ($existing.Count -eq 0) {
            Write-Warning "No records found to delete for $RecordName ($Type)"
            return
        }
        foreach ($rec in $existing) {
            # Safety: If Content is specified, only delete matching records
            if ($Content -and $rec.content -ne $Content) {
                Write-Verbose "Skipping record $($rec.id) (Content mismatch: '$($rec.content)' != '$Content')"
                continue
            }

            if ($DryRun) {
                Write-Host "[DRY-RUN] Would DELETE record: $($rec.type) $RecordName -> $($rec.content) (ID: $($rec.id))" -ForegroundColor Yellow
            }
            else {
                Write-Host "Deleting record: $($rec.type) $RecordName -> $($rec.content)..." -NoNewline
                $null = Invoke-CfApi -Method 'DELETE' -Uri "/zones/$ZoneId/dns_records/$($rec.id)"
                Write-Host " DONE" -ForegroundColor Green
            }
        }
        return
    }

    # --- SET (Create/Update) Operation ---
    
    # Prepare payload
    $payload = @{
        type    = $Type
        name    = $RecordName
        content = $Content
        ttl     = $Ttl
    }

    # Only add proxied for record types that support it
    if ($Type -notin @('MX', 'TXT')) {
        $payload['proxied'] = $Proxied.IsPresent
    }

    # Add priority for MX records
    if ($Type -eq 'MX') {
        $payload['priority'] = $Priority
    }

    # Check for matches (Same Type)
    $existingSameType = $existing | Where-Object { $_.type -eq $Type }

    # --- LOGIC BRANCH: Multi-Value Types (MX, TXT) ---
    # These types allow multiple records with the same name.
    # Logic: Ensure ONE record exists with this content. Do not overwrite others.
    if ($Type -in @('MX', 'TXT')) {
        $exactMatch = $existingSameType | Where-Object { 
            $_.content -eq $Content -and 
            ($Type -ne 'MX' -or $_.priority -eq $Priority)
        }

        if ($exactMatch) {
            Write-Verbose "Exact match found (ID: $($exactMatch[0].id))."
            Write-Host "  [Skip] $Type $RecordName matches desired state." -ForegroundColor DarkGray
        }
        else {
            # No exact match -> CREATE (Append)
            if ($DryRun) {
                Write-Host "[DRY-RUN] Would CREATE new record: $Type $RecordName -> $Content" -ForegroundColor Yellow
            }
            else {
                Write-Host "Creating new record: $Type $RecordName -> $Content..." -NoNewline
                $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
                Write-Host " DONE" -ForegroundColor Green
            }
        }
        return
    }

    # --- LOGIC BRANCH: Single-Value / Managed Types (A, AAAA, CNAME) ---
    # These often imply a single state for a subdomain (e.g. www points to X).
    # Logic: Update ALL matching records to the new state.
    
    if ($existingSameType.Count -eq 0) {
        # CREATE
        if ($DryRun) {
            Write-Host "[DRY-RUN] Would CREATE new record: $Type $RecordName -> $Content (Proxied: $Proxied)" -ForegroundColor Yellow
        }
        else {
            Write-Host "Creating new record: $Type $RecordName -> $Content..." -NoNewline
            $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
            Write-Host " DONE" -ForegroundColor Green
        }
    }
    else {
        # UPDATE
        foreach ($rec in $existingSameType) {
            $needsUpdate = $false
            
            if ($rec.content -ne $Content) { $needsUpdate = $true }
            
            # Check proxy status
            if ([bool]$rec.proxied -ne $Proxied) { $needsUpdate = $true } 

            if (-not $needsUpdate) {
                Write-Verbose "Record $($rec.id) is up to date."
                Write-Host "  [Skip] $Type $RecordName matches desired state." -ForegroundColor DarkGray
                continue
            }

            if ($DryRun) {
                Write-Host "[DRY-RUN] Would UPDATE record $($rec.id): $Content (Proxied: $Proxied)" -ForegroundColor Yellow
            }
            else {
                Write-Host "Updating record $($rec.id)..." -NoNewline
                $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($rec.id)" -Body $payload
                Write-Host " DONE" -ForegroundColor Green
            }
        }
    }

}
catch {
    Write-Error $_
    exit 1
}

