<#
.SYNOPSIS
    FraudLabs Pro screening for the charity-lane verdicts (READ-ONLY): one
    stored-verdict lookup per pending lane order, exactly as 228 does it.

.DESCRIPTION
    The `fraud_screen` job of workflow 232 (epic #1618, item G1.3b). It runs on
    `fraudlabspro-prod-read`, the environment 228 reads FraudLabs Pro from, and
    has no WHMCS credential. Input is the lookup-key file the verdicts job wrote
    ([{order_id, lookup_id}], a 1-day transport artifact). For each order it
    calls Invoke-FraudLabsProApi from scripts/fraudlabspro-api-common.ps1, the
    same function, host allowlist, retry policy and key source
    (FRAUDLABSPRO_API_KEY from fraudlabspro-keys-from-kv) 228 uses, and records
    the stored verdict.

    It decides nothing. The finalize job applies the results
    (Get-LaneFraudOutcome / Merge-LaneFraudOutcome in
    scripts/whmcs-charity-lane-verdicts.ps1), where anything but a clean
    APPROVE is ESCALATE.

    Output (-OutputFile) is PII-free: per order only order_id, lookup_ok,
    fraudlabs_status, fraudlabs_score and a short error code. The FraudLabs
    response carries far more (IP, email domain, geolocation); none of it is
    read. The lookup id (a WHMCS order number) is not written to the output,
    and failures are logged by order id and error code, never by the request
    URL or the raw message.

    A failed lookup never aborts the run: it is recorded as lookup_ok = false
    and becomes FRAUD_CHECK_FAILED downstream. A missing API key does abort it
    (exit 1); the finalize job then marks every row FRAUD_CHECK_FAILED.

.PARAMETER LookupFile
    JSON array of {order_id, lookup_id}.

.PARAMETER OutputFile
    Where to write the results JSON array.

.PARAMETER ApiKey
    FraudLabs Pro API key. Falls back to FRAUDLABSPRO_API_KEY.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$LookupFile = 'artifacts/whmcs/charity-lane-fraud-lookups.json',

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/charity-lane-fraud-results.json',

    [Parameter()]
    [string]$ApiKey
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'fraudlabspro-api-common.ps1')

function ConvertTo-LaneFraudLookupList {
    <#
    .SYNOPSIS
        Validate the lookup entries. Pure.
    .DESCRIPTION
        Keeps entries whose order_id is all digits, one per order id. A
        lookup_id that is not 1-64 characters of [A-Za-z0-9_-] is blanked, so
        that order is recorded as a failed lookup rather than sent. An entry
        without a usable order_id is dropped: the merge marks any row with no
        result FRAUD_CHECK_FAILED, so dropping fails closed.
    #>
    [OutputType([pscustomobject[]])]
    param([Parameter()]$Entries)
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    $out = foreach ($e in @($Entries | Where-Object { $_ })) {
        $orderId = ([string]$e.order_id).Trim()
        if ($orderId -notmatch '^\d{1,20}$') { continue }
        if (-not $seen.Add($orderId)) { continue }
        $lookupId = ([string]$e.lookup_id).Trim()
        if ($lookupId -notmatch '^[A-Za-z0-9_-]{1,64}$') { $lookupId = '' }
        [pscustomobject]@{ order_id = $orderId; lookup_id = $lookupId }
    }
    return @($out)
}

function Get-LaneFraudScreenResult {
    <#
    .SYNOPSIS
        Look ONE order up in FraudLabs Pro and return its PII-free result row.
    .DESCRIPTION
        Returns [ordered]@{ order_id; lookup_ok; fraudlabs_status;
        fraudlabs_score; error }. error is '' on success, else one of
        NO_LOOKUP_ID, REQUEST_FAILED, REQUEST_FAILED_HTTP_<n>, FLP_ERROR_<code>.
        Status is kept only if it is 1-16 letters and score only if it is a
        plain number, so nothing free-form from the response reaches the file.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$OrderId,
        [Parameter()][string]$LookupId,
        [Parameter()][string]$Key
    )
    $row = [ordered]@{ order_id = $OrderId; lookup_ok = $false; fraudlabs_status = ''; fraudlabs_score = ''; error = '' }
    if ([string]::IsNullOrWhiteSpace($LookupId)) {
        $row.error = 'NO_LOOKUP_ID'
        return [pscustomobject]$row
    }
    try {
        $resp = Invoke-FraudLabsProApi -OrderId $LookupId -ApiKey $Key
    }
    catch {
        $row.error = 'REQUEST_FAILED'
        if ($_.Exception.Message -match '\(status: (\d{3})\)') { $row.error = "REQUEST_FAILED_HTTP_$($Matches[1])" }
        return [pscustomobject]$row
    }
    $read = {
        param($Name)
        if ($null -ne $resp -and $resp.PSObject.Properties[$Name] -and $null -ne $resp.$Name) { return ([string]$resp.$Name).Trim() }
        return ''
    }
    $errCode = & $read 'fraudlabspro_error_code'
    if (-not [string]::IsNullOrWhiteSpace($errCode)) {
        $safe = ($errCode -replace '[^A-Za-z0-9]', '')
        if ($safe.Length -gt 16) { $safe = $safe.Substring(0, 16) }
        $row.error = "FLP_ERROR_$safe"
        return [pscustomobject]$row
    }
    $status = (& $read 'fraudlabspro_status').ToUpperInvariant()
    $score = & $read 'fraudlabspro_score'
    $row.fraudlabs_status = if ($status -match '^[A-Z]{1,16}$') { $status } else { '' }
    $row.fraudlabs_score = if ($score -match '^\d{1,3}(\.\d{1,4})?$') { $score } else { '' }
    $row.lookup_ok = $true
    return [pscustomobject]$row
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

try {
    if (-not (Test-Path -LiteralPath $LookupFile)) { throw "Lookup file not found: $LookupFile" }
    $raw = Get-Content -LiteralPath $LookupFile -Raw
    $entries = @()
    if (-not [string]::IsNullOrWhiteSpace($raw)) { $entries = @($raw | ConvertFrom-Json) }
    $lookups = ConvertTo-LaneFraudLookupList -Entries $entries
    Write-Host "FraudLabs lookups to run: $($lookups.Count)"

    $results = [System.Collections.Generic.List[object]]::new()
    if ($lookups.Count -gt 0) {
        # Resolved only when there is something to look up, as 228 does.
        $key = Resolve-FraudLabsProApiKey -KeyParam $ApiKey
        foreach ($l in $lookups) {
            $r = Get-LaneFraudScreenResult -OrderId $l.order_id -LookupId $l.lookup_id -Key $key
            if ($r.lookup_ok) { Write-Host "order $($r.order_id): $($r.fraudlabs_status) (score $($r.fraudlabs_score))" }
            else { Write-Output "::warning::FraudLabs lookup failed for order $($r.order_id) ($($r.error)); it will be ESCALATE FRAUD_CHECK_FAILED." }
            $results.Add($r)
        }
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # -InputObject keeps an array an array (even empty or of one); -AsArray here
    # would wrap it a second time.
    ConvertTo-Json -InputObject @($results) -Depth 3 | Out-File -FilePath $OutputFile -Encoding utf8
    $failed = @($results | Where-Object { -not $_.lookup_ok }).Count
    Write-Host "Wrote $OutputFile ($($results.Count) result(s), $failed failed lookup(s))."
    exit 0
}
catch {
    Write-Output "::error::Charity-lane FraudLabs screening failed: $($_.Exception.Message)"
    exit 1
}
