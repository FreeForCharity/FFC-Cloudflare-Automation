<#
.SYNOPSIS
    Shared Candid API helpers (key resolution + request execution).

.DESCRIPTION
    Dot-source from a script: . (Join-Path $PSScriptRoot 'candid-api-common.ps1')

    Candid (candid.org, formerly GuideStar + Foundation Center) exposes read-only REST APIs at
    https://api.candid.org authenticated with a per-API-product subscription key sent in the
    'Subscription-Key' header (see https://developer.candid.org). Mirrors the conventions of
    whmcs-api-common.ps1:
      - keys from an explicit parameter or the env vars exported by the
        candid-keys-from-kv composite action (CANDID_ESSENTIALS_KEY / CANDID_CHARITY_CHECK_KEY)
      - host allowlist so a key can never be redirected to an arbitrary host
      - retry with exponential backoff on transient conditions (429/5xx/timeouts)
#>

function Resolve-CandidApiKey {
    param(
        [string]$KeyParam,

        # Which API product's env var to fall back to: 'essentials' or 'charity-check'.
        [Parameter(Mandatory = $true)]
        [ValidateSet('essentials', 'charity-check')]
        [string]$Api
    )

    if (-not [string]::IsNullOrWhiteSpace($KeyParam)) { return $KeyParam }

    $envName = if ($Api -eq 'essentials') { 'CANDID_ESSENTIALS_KEY' } else { 'CANDID_CHARITY_CHECK_KEY' }
    $key = [Environment]::GetEnvironmentVariable($envName)
    if (-not [string]::IsNullOrWhiteSpace($key)) { return $key }

    throw "Missing Candid API key for '$Api'. Provide -ApiKey or set $envName (in Actions, use the candid-keys-from-kv composite action)."
}

function Invoke-CandidApi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$ApiKey,

        [ValidateSet('Get', 'Post')]
        [string]$Method = 'Get',

        # Hashtable serialized to JSON for POST requests.
        [hashtable]$Body
    )

    # SECURITY: the Candid subscription key is attached to this request, so only allow it to be
    # sent to the official Candid API host. A script/workflow input must never redirect the key
    # to an arbitrary host.
    $allowedHosts = @('api.candid.org')
    $parsedUri = $null
    if (-not [Uri]::TryCreate($Uri, [UriKind]::Absolute, [ref]$parsedUri) -or $parsedUri.Scheme -ne 'https' -or $allowedHosts -notcontains $parsedUri.Host) {
        throw "Refusing to send the Candid API key to '$Uri': host is not in the allowlist ($($allowedHosts -join ', '))."
    }

    $headers = @{
        'Accept'           = 'application/json'
        'Subscription-Key' = $ApiKey
    }

    # Retry transient conditions (rate limiting, gateway errors, timeouts) with exponential
    # backoff; genuine auth/permission errors (401/403) and not-found (404) fail fast.
    $transientRe = 'too many requests|temporarily unavailable|timed out|The operation has timed out|\b(429|502|503|504)\b'
    $maxAttempts = 5
    $parsedMax = 0
    if ([int]::TryParse($env:CANDID_API_MAX_ATTEMPTS, [ref]$parsedMax) -and $parsedMax -ge 1) {
        $maxAttempts = $parsedMax
    }
    $attempt = 0
    while ($true) {
        $attempt++
        try {
            if ($Method -eq 'Post') {
                $json = $Body | ConvertTo-Json -Depth 10
                return Invoke-RestMethod -Method Post -Uri $Uri -Headers $headers -Body $json -ContentType 'application/json' -ErrorAction Stop
            }
            return Invoke-RestMethod -Method Get -Uri $Uri -Headers $headers -ErrorAction Stop
        }
        catch {
            $msg = $_.Exception.Message
            $status = $null
            if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) {
                try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
            }
            $isTransient = ($status -in @(429, 502, 503, 504)) -or ($msg -match $transientRe)
            if (-not $isTransient -or $attempt -ge $maxAttempts) {
                throw
            }
            $delay = [math]::Min(60, [math]::Pow(2, $attempt))
            Write-Warning "Candid API transient failure (attempt $attempt/$maxAttempts, status: $status): $msg. Retrying in ${delay}s..."
            Start-Sleep -Seconds $delay
        }
    }
}

function Format-CandidEin {
    <#
    .SYNOPSIS
        Normalizes an EIN to the NN-NNNNNNN form the Candid APIs expect; throws on invalid input.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Ein
    )

    $digits = ($Ein -replace '[^0-9]', '')
    if ($digits.Length -ne 9) {
        throw "Invalid EIN '$Ein': expected 9 digits (e.g. 46-2471893)."
    }
    return '{0}-{1}' -f $digits.Substring(0, 2), $digits.Substring(2)
}
