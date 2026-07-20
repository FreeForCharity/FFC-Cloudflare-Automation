<#
.SYNOPSIS
    Shared Cloudflare API helpers (REST plumbing + zone resolution + the
    canonical GitHub Pages DNS targets).

.DESCRIPTION
    Dot-source from a script: . (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

    One source of truth for (issue #778):
      - Invoke-CfApi        : Cloudflare REST call with real error surfacing
                              (Cloudflare JSON errors end up in the thrown
                              message) and a single retry on transient
                              5xx/timeout failures.
      - Get-CfEnvTokens     : FFC + CM token discovery from the environment
                              (the same env vars the cloudflare-tokens-from-kv
                              composite action exports).
      - Resolve-CfZone /
        Resolve-CfZoneId    : multi-token zone lookup (probes FFC then CM,
                              returns $null when no token can see the zone —
                              same semantics as the private copies the bulk
                              scripts used to carry).
      - Get-CfDnsRecords    : paginated DNS record listing, filterable by
                              type/name/content.
      - Get-GhPagesIps / Get-GhPagesIpv6s / Get-GhPagesWwwTarget :
                              the canonical GitHub Pages apex IP sets and the
                              FFC org Pages host for the www CNAME.
      - Test-GhPagesIpsCurrent : network-optional drift check of the IPv4 set
                              against https://api.github.com/meta (warns,
                              never fails).

    Mirrors the conventions of scripts/whmcs-api-common.ps1.
#>

$script:CfApiBase = 'https://api.cloudflare.com/client/v4'

# ---------------------------------------------------------------------------
# Canonical GitHub Pages DNS targets (single source of truth — issue #778).
# Do NOT copy these values into other scripts; consume the Get-* functions.
# ---------------------------------------------------------------------------
$script:GhPagesIps = @(
    '185.199.108.153',
    '185.199.109.153',
    '185.199.110.153',
    '185.199.111.153'
)

$script:GhPagesIpv6s = @(
    '2606:50c0:8000::153',
    '2606:50c0:8001::153',
    '2606:50c0:8002::153',
    '2606:50c0:8003::153'
)

$script:GhPagesWwwTarget = 'freeforcharity.github.io'

function Get-GhPagesIps {
    # Canonical GitHub Pages apex IPv4 set. Collect with @(Get-GhPagesIps).
    [OutputType([string[]])]
    param()
    return $script:GhPagesIps
}

function Get-GhPagesIpv6s {
    # Canonical GitHub Pages apex IPv6 set. Collect with @(Get-GhPagesIpv6s).
    [OutputType([string[]])]
    param()
    return $script:GhPagesIpv6s
}

function Get-GhPagesWwwTarget {
    # Canonical target of the standard www CNAME (FFC org Pages host).
    [OutputType([string])]
    param()
    return $script:GhPagesWwwTarget
}

function Test-GhPagesIpsCurrent {
    <#
        Cross-checks the canonical IPv4 set against the `pages` list published
        at https://api.github.com/meta. Drift means one of OUR canonical IPs
        is no longer published by GitHub (the meta list also carries legacy
        Pages IPs such as 192.30.252.x, so extra remote entries are NOT
        drift). Warns on drift; NEVER fails (the check is network-optional so
        offline/dry-run environments are unaffected). Returns $true when the
        canonical set is still current (or the check could not run), $false
        when drift was detected.
    #>
    [OutputType([bool])]
    [CmdletBinding()]
    param(
        [int]$TimeoutSec = 10
    )

    try {
        $headers = @{
            Accept       = 'application/vnd.github+json'
            'User-Agent' = 'FFC-Cloudflare-Automation'
        }
        $meta = Invoke-RestMethod -Method Get -Uri 'https://api.github.com/meta' -Headers $headers -TimeoutSec $TimeoutSec -ErrorAction Stop

        $remote = @()
        if ($meta -and ($meta.PSObject.Properties.Name -contains 'pages') -and $meta.pages) {
            # Entries may be bare IPs or CIDR (e.g. 185.199.108.153/32); keep IPv4 only.
            $remote = @(
                $meta.pages |
                    ForEach-Object { ([string]$_ -split '/')[0].Trim() } |
                    Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } |
                    Sort-Object -Unique
            )
        }

        if ($remote.Count -eq 0) {
            Write-Warning "GitHub meta endpoint returned no IPv4 'pages' entries — skipping Pages IP drift check."
            return $true
        }

        $local = @($script:GhPagesIps | Sort-Object -Unique)
        $missing = @($local | Where-Object { $remote -notcontains $_ })
        if ($missing.Count -gt 0) {
            Write-Warning ("GitHub Pages IPv4 drift detected: canonical IP(s) {0} in cloudflare-api-common.ps1 no longer appear in api.github.com/meta 'pages' ({1}). Update the library (issue #778)." -f ($missing -join ', '), ($remote -join ', '))
            return $false
        }
        return $true
    }
    catch {
        Write-Warning "Could not verify GitHub Pages IPs against api.github.com/meta: $($_.Exception.Message) (network-optional check — continuing)."
        return $true
    }
}

# ---------------------------------------------------------------------------
# Token discovery
# ---------------------------------------------------------------------------
function Get-CfEnvTokens {
    <#
        Returns an array of @{ Name = 'FFC'|'CM'; Token = '<value>' } for the
        Cloudflare tokens present in the environment (the same env vars the
        cloudflare-tokens-from-kv composite action exports). May be empty —
        callers decide whether that is fatal. Collect with @(Get-CfEnvTokens).
    #>
    [OutputType([hashtable[]])]
    param()

    $tokens = @()
    if (-not [string]::IsNullOrWhiteSpace($env:CLOUDFLARE_API_TOKEN_FFC)) {
        $tokens += , @{ Name = 'FFC'; Token = $env:CLOUDFLARE_API_TOKEN_FFC }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CLOUDFLARE_API_TOKEN_CM)) {
        $tokens += , @{ Name = 'CM'; Token = $env:CLOUDFLARE_API_TOKEN_CM }
    }
    return $tokens
}

# ---------------------------------------------------------------------------
# REST plumbing
# ---------------------------------------------------------------------------
function Invoke-CfApi {
    <#
        Cloudflare REST call. Throws on failure with the Cloudflare JSON
        errors included in the message (Invoke-RestMethod normally hides the
        response body inside ErrorDetails). Retries ONCE on transient
        failures (HTTP 5xx or timeout); envelope failures (success=false)
        and 4xx are never retried.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Token,
        $Body,
        [int]$TimeoutSec = 30
    )

    $requestParams = @{
        Method      = $Method
        Uri         = "$script:CfApiBase$Path"
        Headers     = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
        TimeoutSec  = $TimeoutSec
        ErrorAction = 'Stop'
    }
    if ($null -ne $Body) { $requestParams.Body = ($Body | ConvertTo-Json -Depth 10 -Compress) }

    $maxAttempts = 2
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            $resp = Invoke-RestMethod @requestParams
            # Cloudflare envelope: treat success=false as a failure even on HTTP 2xx.
            if ($resp -and ($resp.PSObject.Properties.Name -contains 'success') -and -not $resp.success) {
                $errJson = $null
                try { $errJson = ($resp.errors | ConvertTo-Json -Depth 6 -Compress) } catch { $errJson = [string]$resp.errors }
                throw "Cloudflare API error ($Method $Path): $errJson"
            }
            return $resp
        }
        catch {
            $exMsg = $_.Exception.Message
            if ($exMsg -like 'Cloudflare API error*') { throw }

            $statusCode = $null
            try {
                if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode }
            }
            catch { $statusCode = $null }

            # Surface the Cloudflare JSON errors from the hidden response body.
            $cfErrors = $null
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                $respBody = $_.ErrorDetails.Message
                try {
                    $parsed = $respBody | ConvertFrom-Json -ErrorAction Stop
                    if ($parsed -and ($parsed.PSObject.Properties.Name -contains 'errors') -and $parsed.errors) {
                        $cfErrors = ($parsed.errors | ConvertTo-Json -Depth 6 -Compress)
                    }
                }
                catch { $cfErrors = $null }
                if (-not $cfErrors) {
                    $cfErrors = if ($respBody.Length -gt 500) { $respBody.Substring(0, 500) + '...' } else { $respBody }
                }
            }

            $detail = "Cloudflare API request failed ($Method $Path)"
            if ($statusCode) { $detail += " HTTP $statusCode" }
            $detail += ": $exMsg"
            if ($cfErrors) { $detail += " | errors: $cfErrors" }

            $isTransient = ($statusCode -ge 500 -and $statusCode -le 599) -or ($exMsg -match '(?i)timed?\s?out|timeout')
            if ($isTransient -and $attempt -lt $maxAttempts) {
                Write-Warning "$detail — transient; retrying once in 2s..."
                Start-Sleep -Seconds 2
                continue
            }
            throw $detail
        }
    }
}

# ---------------------------------------------------------------------------
# Zone resolution (multi-token: FFC then CM)
# ---------------------------------------------------------------------------
function Resolve-CfZone {
    <#
        Resolves a zone by probing each token in order (default: the FFC then
        CM env tokens). Returns [pscustomobject] @{ Account; Token; ZoneId;
        ZoneName } for the first token that can see the zone, or $null when
        none can (same semantics as the private copies formerly embedded in
        the bulk-*.ps1 scripts).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Domain,
        [array]$Tokens
    )

    if (-not $Tokens -or $Tokens.Count -eq 0) { $Tokens = @(Get-CfEnvTokens) }
    if (-not $Tokens -or $Tokens.Count -eq 0) {
        throw 'No Cloudflare tokens available. Set CLOUDFLARE_API_TOKEN_FFC and/or CLOUDFLARE_API_TOKEN_CM, or pass -Tokens.'
    }

    foreach ($t in $Tokens) {
        try {
            $encoded = [uri]::EscapeDataString($Domain)
            $resp = Invoke-CfApi -Method GET -Token $t.Token -Path "/zones?name=$encoded"
            if ($resp.success -and $resp.result -and $resp.result.Count -gt 0) {
                return [pscustomobject]@{
                    Account  = $t.Name
                    Token    = $t.Token
                    ZoneId   = $resp.result[0].id
                    ZoneName = $resp.result[0].name
                }
            }
        }
        catch {
            # Zone not visible to this token — try the next one.
        }
    }
    return $null
}

function Resolve-CfZoneId {
    # Zone-id-only convenience wrapper around Resolve-CfZone.
    [OutputType([string])]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Domain,
        [array]$Tokens
    )

    $zone = Resolve-CfZone -Domain $Domain -Tokens $Tokens
    if ($zone) { return $zone.ZoneId }
    return $null
}

# ---------------------------------------------------------------------------
# DNS record listing (paginated)
# ---------------------------------------------------------------------------
function Get-CfDnsRecords {
    <#
        Lists DNS records in a zone, following pagination. Optional filters
        (applied server-side by Cloudflare): -Type, -Name (FQDN), -Content.
        Returns the records; collect with @(Get-CfDnsRecords ...) — the array
        may be empty.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ZoneId,
        [Parameter(Mandatory = $true)][string]$Token,
        [string]$Type,
        [string]$Name,
        [string]$Content
    )

    $records = @()
    $page = 1
    while ($true) {
        $query = @()
        if ($Type) { $query += "type=$([uri]::EscapeDataString($Type))" }
        if ($Name) { $query += "name=$([uri]::EscapeDataString($Name))" }
        if ($Content) { $query += "content=$([uri]::EscapeDataString($Content))" }
        $query += "per_page=100"
        $query += "page=$page"

        $resp = Invoke-CfApi -Method GET -Token $Token -Path "/zones/$ZoneId/dns_records?$($query -join '&')"
        if ($resp.result) { $records += $resp.result }

        $totalPages = 1
        if (($resp.PSObject.Properties.Name -contains 'result_info') -and $resp.result_info -and
            ($resp.result_info.PSObject.Properties.Name -contains 'total_pages') -and $resp.result_info.total_pages) {
            $totalPages = [int]$resp.result_info.total_pages
        }
        if ($page -ge $totalPages) { break }
        $page++
    }
    return $records
}
