<#
.SYNOPSIS
    Shared Zeffy API helpers (key resolution + request execution + cursor pagination).

.DESCRIPTION
    Dot-source from a script: . (Join-Path $PSScriptRoot 'zeffy-api-common.ps1')

    The Zeffy public API (https://api.zeffy.com/api/v1) is read-only and authenticated with an
    organization API key as a Bearer token. Lists are cursor-paginated (has_more + next_cursor;
    pass next_cursor as starting_after). Rate limit is 100 req/min (HTTP 429).
#>

function Resolve-ZeffyApiKey {
    param([string]$ApiKeyParam)

    $key = if ($ApiKeyParam) { $ApiKeyParam } else { $env:ZEFFY_API_KEY }
    if (-not [string]::IsNullOrWhiteSpace($key)) { return $key }

    throw 'Missing Zeffy API key. Provide -ApiKey or set ZEFFY_API_KEY (loaded from Key Vault via the zeffy-secrets-from-kv action).'
}

function Resolve-ZeffyBaseUrl {
    param([string]$BaseUrlParam)

    if ($BaseUrlParam) { return $BaseUrlParam.TrimEnd('/') }
    if ($env:ZEFFY_API_URL) { return $env:ZEFFY_API_URL.TrimEnd('/') }
    return 'https://api.zeffy.com'
}

function ConvertFrom-UnixSeconds {
    # Unix seconds -> ISO 8601 UTC string, or $null. Zeffy timestamps are seconds.
    [OutputType([string])]
    param($Unix)

    if ($null -eq $Unix -or "$Unix" -eq '') { return $null }
    $n = 0L
    if (-not [long]::TryParse([string]$Unix, [ref]$n)) { return $null }
    return [DateTimeOffset]::FromUnixTimeSeconds($n).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ')
}

function Get-ZeffyText {
    # Zeffy returns several nominally-string fields typed as nullable objects; coerce to a string.
    [OutputType([string])]
    param($Value)

    if ($null -eq $Value) { return $null }
    $s = [string]$Value
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return $s
}

function Invoke-ZeffyApi {
    <#
      Single GET against the Zeffy API. Returns the parsed JSON object. Retries HTTP 429 (and a few
      transient 5xx/timeout conditions) with exponential backoff, honouring Retry-After when present.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$ApiKey,
        [Parameter(Mandatory = $true)][string]$Path,
        [hashtable]$Query
    )

    $uri = "$BaseUrl$Path"
    if ($Query -and $Query.Count -gt 0) {
        $pairs = foreach ($k in $Query.Keys) {
            $v = $Query[$k]
            if ($null -eq $v -or "$v" -eq '') { continue }
            "{0}={1}" -f [uri]::EscapeDataString([string]$k), [uri]::EscapeDataString([string]$v)
        }
        $qs = ($pairs | Where-Object { $_ }) -join '&'
        if ($qs) { $uri = "$uri`?$qs" }
    }

    $headers = @{
        'Authorization' = "Bearer $ApiKey"
        'Accept'        = 'application/json'
    }

    $maxAttempts = 6
    if ($env:ZEFFY_API_MAX_ATTEMPTS -and ([int]::TryParse($env:ZEFFY_API_MAX_ATTEMPTS, [ref]([int]$null)))) {
        $maxAttempts = [int]$env:ZEFFY_API_MAX_ATTEMPTS
    }

    $attempt = 0
    while ($true) {
        $attempt++
        try {
            return Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -ErrorAction Stop
        }
        catch {
            $status = $null
            try { $status = [int]$_.Exception.Response.StatusCode } catch {}
            $retryable = ($status -eq 429 -or $status -eq 502 -or $status -eq 503 -or $status -eq 504)
            if (-not $retryable -or $attempt -ge $maxAttempts) {
                if ($status -eq 401) { throw "Zeffy API returned 401 Unauthorized. The ZEFFY_API_KEY is missing, invalid, or revoked." }
                throw
            }
            $delay = [int][math]::Min(60, [math]::Pow(2, $attempt))
            $retryAfter = $null
            try { $retryAfter = [int]$_.Exception.Response.Headers['Retry-After'] } catch {}
            if ($retryAfter -and $retryAfter -gt 0) { $delay = [math]::Min(60, $retryAfter) }
            Write-Warning "Zeffy API transient HTTP $status (attempt $attempt/$maxAttempts). Retrying in ${delay}s..."
            Start-Sleep -Seconds $delay
        }
    }
}

function Get-ZeffyList {
    <#
      Pages a Zeffy list endpoint (payments/contacts/campaigns) and returns all `data` items as an
      array. Honours cursor pagination (has_more + next_cursor) and a -MaxItems safety cap.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$ApiKey,
        [Parameter(Mandatory = $true)][string]$Path,
        [hashtable]$Query,
        [int]$PageSize = 100,
        [int]$MaxItems = 100000
    )

    $all = [System.Collections.Generic.List[object]]::new()
    $cursor = $null
    while ($true) {
        $q = @{}
        if ($Query) { foreach ($k in $Query.Keys) { $q[$k] = $Query[$k] } }
        $q['limit'] = $PageSize
        if ($cursor) { $q['starting_after'] = $cursor }

        $resp = Invoke-ZeffyApi -BaseUrl $BaseUrl -ApiKey $ApiKey -Path $Path -Query $q

        $page = @()
        if ($resp -and $resp.data) { $page = @($resp.data) }
        if ($page.Count -le 0) { break }
        $all.AddRange([object[]]$page)

        if ($all.Count -ge $MaxItems) {
            Write-Warning "Reached MaxItems=$MaxItems for '$Path'; stopping pagination early."
            break
        }
        if (-not $resp.has_more) { break }

        $next = $null
        try { $next = [string]$resp.next_cursor } catch {}
        if ([string]::IsNullOrWhiteSpace($next)) { break }
        $cursor = $next
    }
    return $all.ToArray()
}
