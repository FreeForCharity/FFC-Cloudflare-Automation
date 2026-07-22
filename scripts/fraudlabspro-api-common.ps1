<#
.SYNOPSIS
    Shared FraudLabs Pro helpers (key resolution + request execution + false-positive decision).

.DESCRIPTION
    Dot-source from a script: . (Join-Path $PSScriptRoot 'fraudlabspro-api-common.ps1')

    FraudLabs Pro (fraudlabspro.com) is the fraud-screening module configured in WHMCS. Its
    read-only REST API at https://api.fraudlabspro.com returns the stored screening verdict for an
    order. Workflow 228 uses it to tell a genuine fraud order from a false positive on a $0 charity
    onboarding order (see issue #813). Mirrors the conventions of candid-api-common.ps1:
      - key from an explicit parameter or the FRAUDLABSPRO_API_KEY env var (exported by the
        fraudlabspro-keys-from-kv composite action)
      - host allowlist so the key can never be redirected to an arbitrary host
      - retry with exponential backoff on transient conditions (429/5xx/timeouts)

    The decision logic (Get-FraudReviewRecommendation) is a pure function with a unit test at
    tests/workflow-logic/test_228_fraud_review.py — it never touches the network.
#>

function Resolve-FraudLabsProApiKey {
    <#
    .SYNOPSIS
        Returns the FraudLabs Pro API key from the -KeyParam or the FRAUDLABSPRO_API_KEY env var.
    #>
    param(
        [string]$KeyParam
    )

    if (-not [string]::IsNullOrWhiteSpace($KeyParam)) { return $KeyParam }

    $key = [Environment]::GetEnvironmentVariable('FRAUDLABSPRO_API_KEY')
    if (-not [string]::IsNullOrWhiteSpace($key)) { return $key }

    throw "Missing FraudLabs Pro API key. Provide -ApiKey or set FRAUDLABSPRO_API_KEY (in Actions, use the fraudlabspro-keys-from-kv composite action)."
}

function Invoke-FraudLabsProApi {
    <#
    .SYNOPSIS
        GETs the stored screening result for one order from the FraudLabs Pro API (read-only).

    .DESCRIPTION
        Calls GET https://api.fraudlabspro.com/v2/order/result?key=<key>&id=<OrderId>&format=json and
        returns the parsed JSON. The key travels in the query string (FraudLabs Pro's scheme), so it
        is attached only to the allowlisted host and never written to a log — the redacted URL is
        logged instead.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$OrderId,

        [Parameter(Mandatory = $true)]
        [string]$ApiKey,

        [string]$BaseUri = 'https://api.fraudlabspro.com/v2/order/result'
    )

    # SECURITY: the FraudLabs Pro key is attached to this request, so only allow it to be sent to
    # the official FraudLabs Pro API host. A script/workflow input must never redirect the key to an
    # arbitrary host.
    $allowedHosts = @('api.fraudlabspro.com')
    $parsedBase = $null
    if (-not [Uri]::TryCreate($BaseUri, [UriKind]::Absolute, [ref]$parsedBase) -or $parsedBase.Scheme -ne 'https' -or $allowedHosts -notcontains $parsedBase.Host) {
        throw "Refusing to send the FraudLabs Pro API key to '$BaseUri': host is not in the allowlist ($($allowedHosts -join ', '))."
    }

    $query = 'key={0}&id={1}&format=json' -f [Uri]::EscapeDataString($ApiKey), [Uri]::EscapeDataString($OrderId)
    $uri = "$BaseUri`?$query"
    $redacted = "$BaseUri`?key=***&id=$([Uri]::EscapeDataString($OrderId))&format=json"

    $headers = @{ 'Accept' = 'application/json' }

    # Retry transient conditions (rate limiting, gateway errors, timeouts) with exponential backoff;
    # genuine auth/permission errors (401/403) and not-found (404) fail fast.
    $transientRe = 'too many requests|temporarily unavailable|timed out|The operation has timed out|\b(429|502|503|504)\b'
    $maxAttempts = 5
    $parsedMax = 0
    if ([int]::TryParse($env:FRAUDLABSPRO_API_MAX_ATTEMPTS, [ref]$parsedMax) -and $parsedMax -ge 1) {
        $maxAttempts = $parsedMax
    }
    $attempt = 0
    while ($true) {
        $attempt++
        try {
            Write-Verbose "GET $redacted (attempt $attempt/$maxAttempts)"
            return Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -ErrorAction Stop
        }
        catch {
            $msg = $_.Exception.Message
            $status = $null
            if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) {
                try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
            }
            $isTransient = ($status -in @(429, 502, 503, 504)) -or ($msg -match $transientRe)
            if (-not $isTransient -or $attempt -ge $maxAttempts) {
                # Re-throw with the redacted URL so a leaked query string never reaches the log.
                throw "FraudLabs Pro request to $redacted failed (status: $status): $msg"
            }
            $delay = [math]::Min(60, [math]::Pow(2, $attempt))
            Write-Warning "FraudLabs Pro transient failure (attempt $attempt/$maxAttempts, status: $status): $msg. Retrying in ${delay}s..."
            Start-Sleep -Seconds $delay
        }
    }
}

function Get-FraudReviewRecommendation {
    <#
    .SYNOPSIS
        Pure decision function: given a WHMCS order's status + FraudLabs Pro verdict, recommend an
        action. No network, no side effects — unit-tested in tests/workflow-logic/test_228_fraud_review.py.

    .DESCRIPTION
        FFC onboards known, vetted 501(c)(3)/pre-501(c)(3) charities, so a high FraudLabs score on a
        $0 onboarding order is almost always a false positive (free-email + residential IP + new
        domain), not real fraud. This function encodes that policy conservatively — it only ever
        RECOMMENDS; the actual clear is a separate, gated action via workflow 211.

        Returns a PSCustomObject with:
          Recommendation : one of 'clear-recommended', 'hold-for-human', 'review-manually', 'no-action'
          Reason         : a short human-readable justification

    .PARAMETER WhmcsStatus
        The WHMCS order status (e.g. 'Fraud', 'Pending', 'Active').

    .PARAMETER FraudLabsStatus
        The FraudLabs Pro verdict: 'APPROVE', 'REJECT', 'REVIEW', or '' when no verdict is stored.

    .PARAMETER Amount
        The order amount. $0 marks the free onboarding order that dominates FFC's queue.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$WhmcsStatus,

        [string]$FraudLabsStatus,

        [decimal]$Amount = 0
    )

    $whmcs = ([string]$WhmcsStatus).Trim()
    $fraudlabs = ([string]$FraudLabsStatus).Trim().ToUpperInvariant()

    # Only orders WHMCS is actually holding in Fraud status are in scope for a clear recommendation.
    if ($whmcs -ne 'Fraud') {
        return [pscustomobject]@{
            Recommendation = 'no-action'
            Reason         = "WHMCS status is '$whmcs', not 'Fraud' — nothing to clear."
        }
    }

    switch ($fraudlabs) {
        'REJECT' {
            return [pscustomobject]@{
                Recommendation = 'hold-for-human'
                Reason         = 'FraudLabs Pro verdict is REJECT — do not auto-clear; a human must review.'
            }
        }
        'APPROVE' {
            if ($Amount -eq [decimal]0) {
                return [pscustomobject]@{
                    Recommendation = 'clear-recommended'
                    Reason         = 'FraudLabs Pro APPROVE on a $0 onboarding order — likely false positive; recommend clearing via workflow 211.'
                }
            }
            return [pscustomobject]@{
                Recommendation = 'review-manually'
                Reason         = "FraudLabs Pro APPROVE but order amount is $Amount (not a $0 onboarding order) — human review before clearing."
            }
        }
        'REVIEW' {
            return [pscustomobject]@{
                Recommendation = 'review-manually'
                Reason         = 'FraudLabs Pro verdict is REVIEW — human review before clearing.'
            }
        }
        default {
            return [pscustomobject]@{
                Recommendation = 'review-manually'
                Reason         = 'No FraudLabs Pro verdict available for this order — human review before clearing.'
            }
        }
    }
}
