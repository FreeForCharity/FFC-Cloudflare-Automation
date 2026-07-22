<#
.SYNOPSIS
    Read-only FraudLabs Pro review of WHMCS Fraud-status orders (issue #813, workflow 228).

.DESCRIPTION
    Takes a CSV of WHMCS Fraud-status orders (as produced by whmcs-orders-export.ps1 -Status Fraud),
    looks up each order's stored FraudLabs Pro verdict, and emits a triage row with a RECOMMENDED
    action (clear-recommended / hold-for-human / review-manually / no-action). It performs NO writes:
    clearing an approved false positive is a separate, gated action via workflow 211.

    Order id sent to FraudLabs Pro: the WHMCS field named by -OrderIdField (default 'ordernum'),
    which is the id the WHMCS FraudLabs Pro module submits at screening time. Adjust if a live run
    shows the module keys results by a different id (validated at live-test time — see #813).

    Outputs:
      -OutputFile   CSV of the review (order, client, masked name, amount, statuses, score, rec)
      -SummaryFile  optional Markdown appended (e.g. $env:GITHUB_STEP_SUMMARY)
      exit 0        review completed (even if some per-order lookups failed — those rows say so)

.PARAMETER OrdersCsv
    Path to the Fraud-orders CSV to review.

.PARAMETER OutputFile
    Where to write the review CSV. Directory is created if needed.

.PARAMETER SummaryFile
    Optional path to append a Markdown summary table to (e.g. the Actions step summary).

.PARAMETER ApiKey
    FraudLabs Pro API key. Falls back to FRAUDLABSPRO_API_KEY (exported by fraudlabspro-keys-from-kv).

.PARAMETER OrderIdField
    Which CSV column to send to FraudLabs Pro as the order id. Default 'ordernum'.

.EXAMPLE
    ./whmcs-fraud-review.ps1 -OrdersCsv artifacts/whmcs/orders_Fraud.csv -OutputFile artifacts/whmcs/fraud_review.csv
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OrdersCsv,

    [string]$OutputFile = 'fraud_review.csv',

    [string]$SummaryFile,

    [string]$ApiKey,

    [ValidateNotNullOrEmpty()]
    [string]$OrderIdField = 'ordernum'
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'fraudlabspro-api-common.ps1')

function Format-MaskedName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $t = $Name.Trim()
    if ($t.Length -le 1) { return '***' }
    return $t.Substring(0, 1) + '***'
}

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

# Reads a property off the FraudLabs Pro response defensively (blank if the schema shifts).
function Get-FlpField {
    param($Object, [string[]]$Names)
    foreach ($n in $Names) {
        if ($null -ne $Object -and $Object.PSObject.Properties[$n] -and $null -ne $Object.$n -and "$($Object.$n)" -ne '') {
            return "$($Object.$n)"
        }
    }
    return ''
}

if (-not (Test-Path $OrdersCsv)) {
    throw "Orders CSV not found: $OrdersCsv"
}
$orders = @(Import-Csv -Path $OrdersCsv)

$key = $null
if ($orders.Count -gt 0) {
    # Only resolve the key when there is something to look up, so an empty queue never fails on a
    # missing key (the scaffold stays inert until the FraudLabs Pro key is provisioned).
    $key = Resolve-FraudLabsProApiKey -KeyParam $ApiKey
}

$reviewRows = [System.Collections.Generic.List[object]]::new()
foreach ($o in $orders) {
    $orderId = [string]$o.$OrderIdField
    # Distinguish "parsed as 0" (a real $0 onboarding order) from "missing/unparseable amount". Only
    # a KNOWN $0 may be recommended for clearing; an unknown amount stays conservative (see #813).
    $amount = [decimal]0
    $amountRaw = [string]$o.amount
    $amountKnown = (-not [string]::IsNullOrWhiteSpace($amountRaw)) -and [decimal]::TryParse($amountRaw, [ref]$amount)

    $flStatus = ''
    $flScore = ''
    $flError = ''
    if ([string]::IsNullOrWhiteSpace($orderId)) {
        $flError = "order row has no '$OrderIdField' value"
    }
    else {
        try {
            $resp = Invoke-FraudLabsProApi -OrderId $orderId -ApiKey $key
            $flStatus = Get-FlpField $resp @('fraudlabspro_status')
            $flScore = Get-FlpField $resp @('fraudlabspro_score')
            $errCode = Get-FlpField $resp @('fraudlabspro_error_code')
            $errMsg = Get-FlpField $resp @('fraudlabspro_message')
            if (-not [string]::IsNullOrWhiteSpace($errCode)) {
                $flError = "FraudLabs error $errCode $errMsg".Trim()
            }
        }
        catch {
            # A single failed lookup must not abort the whole review — record it and move on.
            $flError = $_.Exception.Message
        }
    }

    $rec = Get-FraudReviewRecommendation -WhmcsStatus ([string]$o.status) -FraudLabsStatus $flStatus -Amount $amount -AmountKnown $amountKnown
    $reason = $rec.Reason
    if (-not [string]::IsNullOrWhiteSpace($flError)) {
        $reason = "$reason (lookup note: $flError)"
    }

    $reviewRows.Add([pscustomobject]@{
            ordernum         = [string]$o.ordernum
            orderid          = [string]$o.id
            userid           = [string]$o.userid
            name             = Format-MaskedName ([string]$o.name)
            amount           = [string]$o.amount
            whmcs_status     = [string]$o.status
            fraudlabs_status = $flStatus
            fraudlabs_score  = $flScore
            recommendation   = $rec.Recommendation
            reason           = $reason
        })
}

New-DirectoryForFile -Path $OutputFile
$reviewRows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Reviewed $($reviewRows.Count) Fraud-status order(s); wrote $OutputFile"

if (-not [string]::IsNullOrWhiteSpace($SummaryFile)) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('## WHMCS Fraud Review (FraudLabs Pro)')
    $lines.Add('')
    if ($reviewRows.Count -eq 0) {
        $lines.Add('_No Fraud-status orders — queue is clear._')
    }
    else {
        $clear = @($reviewRows | Where-Object { $_.recommendation -eq 'clear-recommended' }).Count
        $lines.Add("Reviewed **$($reviewRows.Count)** Fraud-status order(s); **$clear** recommended for clearing (via workflow 211).")
        $lines.Add('')
        $lines.Add('| order | client | name | amount | WHMCS | FraudLabs | score | recommendation | reason |')
        $lines.Add('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        foreach ($r in $reviewRows) {
            $reasonCell = ([string]$r.reason) -replace '\|', '\\|'
            $lines.Add("| $($r.ordernum) (id $($r.orderid)) | $($r.userid) | $($r.name) | $($r.amount) | $($r.whmcs_status) | $($r.fraudlabs_status) | $($r.fraudlabs_score) | $($r.recommendation) | $reasonCell |")
        }
    }
    $lines.Add('')
    $lines | Out-File -FilePath $SummaryFile -Append -Encoding utf8
}
