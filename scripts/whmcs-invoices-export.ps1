param(
    [Parameter(Mandatory)]
    [string]$WhmcsApiUrl,

    [Parameter(Mandatory)]
    [string]$WhmcsIdentifier,

    [Parameter(Mandatory)]
    [string]$WhmcsSecret,

    [Parameter(Mandatory)]
    [string]$OutCsv,

    [Parameter()]
    [string]$StartDate,

    [Parameter()]
    [string]$EndDate,

    [Parameter()]
    [ValidateRange(1, 1000000)]
    [int]$Limit = 250,

    [Parameter()]
    [ValidateRange(0, 100000000)]
    [int]$MaxRows = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-WhmcsApi {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$Identifier,
        [Parameter(Mandatory)][string]$Secret,
        [Parameter(Mandatory)][hashtable]$Body
    )

    $request = @{
        identifier = $Identifier
        secret     = $Secret
        responsetype = 'json'
    } + $Body

    Invoke-RestMethod -Method Post -Uri $Url -Body $request -ContentType 'application/x-www-form-urlencoded'
}

function ConvertTo-WhmcsDate {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }

    # Accept yyyy-MM-dd; passthrough otherwise
    return $Value
}

$start = ConvertTo-WhmcsDate -Value $StartDate
$end = ConvertTo-WhmcsDate -Value $EndDate

$all = New-Object System.Collections.Generic.List[object]

$offset = 0
$rows = 0

while ($true) {
    $body = @{
        action = 'GetInvoices'
        limitstart = $offset
        limitnum   = $Limit
    }

    if ($start) { $body['datecreated'] = $start }
    if ($end)   { $body['datecreatedend'] = $end }

    $resp = Invoke-WhmcsApi -Url $WhmcsApiUrl -Identifier $WhmcsIdentifier -Secret $WhmcsSecret -Body $body

    if (-not $resp) { break }

    $result = $resp.result
    if ($result -and $result -ne 'success') {
        $msg = if ($resp.message) { $resp.message } else { 'Unknown WHMCS error' }
        throw "WHMCS API error: $msg"
    }

    $totalResults = 0
    try { $totalResults = [int]$resp.totalresults } catch { $totalResults = 0 }

    $items = @()
    if ($resp.invoices -and $resp.invoices.invoice) {
        $items = @($resp.invoices.invoice)
    }

    foreach ($inv in $items) {
        $all.Add([pscustomobject]@{
            invoiceid      = $inv.id
            userid         = $inv.userid
            status         = $inv.status
            date           = $inv.date
            duedate        = $inv.duedate
            total          = $inv.total
            paymentmethod  = $inv.paymentmethod
        })
        $rows++
        if ($MaxRows -gt 0 -and $rows -ge $MaxRows) { break }
    }

    if ($MaxRows -gt 0 -and $rows -ge $MaxRows) { break }

    $offset += $Limit
    if ($offset -ge $totalResults) { break }

    Write-Host "Fetched invoices: $rows / $totalResults"
}

$dir = Split-Path -Parent $OutCsv
if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir | Out-Null
}

$all | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8

Write-Host "Wrote invoices CSV: $OutCsv ($($all.Count) rows)"
