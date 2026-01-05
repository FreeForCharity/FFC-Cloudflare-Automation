[CmdletBinding()]
param(
    [Parameter()]
    [string]$ClientsCsv = 'artifacts/whmcs/whmcs_clients.csv',

    [Parameter()]
    [string]$TransactionsCsv = 'artifacts/whmcs/whmcs_transactions.csv',

    [Parameter()]
    [string]$OutputFile = 'artifacts/zeffy/zeffy_payments_import_draft.csv',

    [Parameter()]
    [string]$TemplatePath,

    [Parameter()]
    [ValidateSet('canonical', 'template')]
    [string]$Mode = 'canonical'

    ,
    [Parameter()]
    [ValidateSet('donation', 'ticket')]
    [string]$DefaultType = 'donation'

    ,
    [Parameter()]
    [ValidateSet('EN', 'FR')]
    [string]$DefaultLanguage = 'EN'

    ,
    [Parameter()]
    [string]$DefaultCountry = 'US'

    ,
    [Parameter()]
    [ValidateRange(1, 1000000)]
    [int]$MaxRowsPerFile = 10000

    ,
    [Parameter()]
    [string]$InvoicesCsv

    ,
    [Parameter()]
    [switch]$IncludeZeroInvoices

    ,
    [Parameter()]
    [switch]$ExcludeUserIdZero = $true
)

$ErrorActionPreference = 'Stop'

function New-DirectoryForFile {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Get-OutputPartPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseOutputFile,
        [Parameter(Mandatory = $true)]
        [int]$PartNumber
    )

    $dir = Split-Path -Parent $BaseOutputFile
    if ([string]::IsNullOrWhiteSpace($dir)) { $dir = '.' }

    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($BaseOutputFile)
    $ext = [System.IO.Path]::GetExtension($BaseOutputFile)
    if ([string]::IsNullOrWhiteSpace($ext)) { $ext = '.csv' }

    $partName = '{0}-part{1:000}{2}' -f $baseName, $PartNumber, $ext
    return (Join-Path -Path $dir -ChildPath $partName)
}

function Remove-StalePartFiles {
    param([string]$BaseOutputFile)

    $dir = Split-Path -Parent $BaseOutputFile
    if ([string]::IsNullOrWhiteSpace($dir)) { $dir = '.' }

    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($BaseOutputFile)
    $pattern = "$baseName-part*.csv"

    Get-ChildItem -Path $dir -Filter $pattern -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-ZeffyPaymentMethodSuggestion {
    param([string]$WhmcsValue)

    if ([string]::IsNullOrWhiteSpace($WhmcsValue)) { return 'unknown' }
    $v = $WhmcsValue.ToLowerInvariant()

    if ($v -match 'apple|google') { return 'applePayOrGooglePay' }
    if ($v -match 'echeck|authorizeecheck') { return 'ach' }
    if ($v -match '\bach\b') { return 'ach' }
    if ($v -match '\bpad\b') { return 'pad' }
    if ($v -match 'bank|wire|transfer') { return 'transfer' }
    if ($v -match 'mailin') { return 'manual' }
    if ($v -match 'cheque|check') { return 'cheque' }
    if ($v -match 'cash') { return 'cash' }

    if ($v -match 'stripe|authorize|authorizenet|cc|credit') { return 'card' }
    if ($v -match 'paypal') { return 'card' }

    return 'unknown'
}

function Get-TemplateHeaders {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'TemplatePath is required in template mode.' }
    if (-not (Test-Path $Path)) { throw "Template file not found: $Path" }

    $first = Get-Content -LiteralPath $Path -TotalCount 1
    if ([string]::IsNullOrWhiteSpace($first)) { throw "Template appears empty: $Path" }

    return ($first -split ',') | ForEach-Object { $_.Trim() }
}

function Get-CanonicalHeaders {
    return @(
        # Matches Zeffy Payments Import Template (per https://support.zeffy.com/importing-payments)
        'firstName',
        'lastName',
        'amount',
        'address',
        'city',
        'postalCode',
        'country',
        'type',
        'formTitle',
        'rateTitle',
        'email',
        'language',
        'date (MM/DD/YYYY)',
        'state/province',
        'paymentMethod',
        'receiptUrl',
        'ticketUrl',
        'receiptNumber',
        'companyName',
        'note',
        'annotation'
    )
}

function Format-ZeffyDate {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    try {
        $dt = [datetime]::Parse($Value, [System.Globalization.CultureInfo]::InvariantCulture)
        return $dt.ToString('MM/dd/yyyy', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        # If parsing fails, pass through (caller may choose to default)
        return $Value
    }
}

function Format-ZeffyAmount {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }

    try {
        $d = [decimal]::Parse($Value, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture)
        return $d.ToString('0.##', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        # Strip commas/spaces as a last resort
        return ($Value -replace '[,\s]', '')
    }
}

function Join-Address {
    param(
        [string]$Address1,
        [string]$Address2
    )

    $parts = @(
        if (-not [string]::IsNullOrWhiteSpace($Address1)) { $Address1.Trim() }
        if (-not [string]::IsNullOrWhiteSpace($Address2)) { $Address2.Trim() }
    ) | Where-Object { $_ }

    if ($parts.Count -eq 0) { return $null }
    return ($parts -join ' ')
}

function Convert-RowToHeaders {
    param(
        [hashtable]$Canonical,
        [string[]]$Headers
    )

    $row = [ordered]@{}
    foreach ($h in $Headers) {
        if ($Canonical.ContainsKey($h)) {
            $row[$h] = $Canonical[$h]
            continue
        }

        # Lightweight alias mapping for common column variants (covers Zeffy template variations)
        switch -Regex ($h) {
            '^first.?name$' { $row[$h] = $Canonical.firstName; break }
            '^last.?name$' { $row[$h] = $Canonical.lastName; break }
            '^email$' { $row[$h] = $Canonical.email; break }
            '^amount$|total' { $row[$h] = $Canonical.amount; break }
            '^address$' { $row[$h] = $Canonical.address; break }
            '^city$' { $row[$h] = $Canonical.city; break }
            '^postal\s?code$|^postalcode$' { $row[$h] = $Canonical.postalCode; break }
            '^country$' { $row[$h] = $Canonical.country; break }
            '^type$' { $row[$h] = $Canonical.type; break }
            '^formtitle$' { $row[$h] = $Canonical.formTitle; break }
            '^ratetitle$' { $row[$h] = $Canonical.rateTitle; break }
            '^language$' { $row[$h] = $Canonical.language; break }
            '^date(\s*\(mm\/dd\/yyyy\))?$' { $row[$h] = $Canonical.'date (MM/DD/YYYY)'; break }
            '^state\/?province$|^state$|^province$' { $row[$h] = $Canonical.'state/province'; break }
            '^payment.?method$|^paymentmethod$|^method$' { $row[$h] = $Canonical.paymentMethod; break }
            '^receipturl$' { $row[$h] = $Canonical.receiptUrl; break }
            '^ticketurl$' { $row[$h] = $Canonical.ticketUrl; break }
            '^receiptnumber$' { $row[$h] = $Canonical.receiptNumber; break }
            '^companyname$' { $row[$h] = $Canonical.companyName; break }
            '^note$' { $row[$h] = $Canonical.note; break }
            '^annotation$' { $row[$h] = $Canonical.annotation; break }
            default { $row[$h] = $null; break }
        }
    }

    return [pscustomobject]$row
}

if (-not (Test-Path $ClientsCsv)) { throw "Clients CSV not found: $ClientsCsv" }
if (-not (Test-Path $TransactionsCsv)) { throw "Transactions CSV not found: $TransactionsCsv" }

New-DirectoryForFile -Path $OutputFile

$clients = Import-Csv -LiteralPath $ClientsCsv
$transactions = Import-Csv -LiteralPath $TransactionsCsv

$invoices = @()
if ($IncludeZeroInvoices -and -not [string]::IsNullOrWhiteSpace($InvoicesCsv)) {
    if (-not (Test-Path -LiteralPath $InvoicesCsv)) {
        throw "Invoices CSV not found: $InvoicesCsv"
    }
    $invoices = Import-Csv -LiteralPath $InvoicesCsv
}

$invoiceIdsWithTransactions = @{}
foreach ($t in $transactions) {
    if (-not [string]::IsNullOrWhiteSpace($t.invoiceid)) {
        $invoiceIdsWithTransactions[$t.invoiceid.ToString().Trim()] = $true
    }
}

function ConvertTo-NullableDecimal {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    try {
        return [decimal]::Parse($Value, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        try {
            $clean = $Value -replace '[,\s]', ''
            return [decimal]::Parse($clean, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture)
        }
        catch {
            return $null
        }
    }
}

if ($IncludeZeroInvoices -and $invoices.Count -gt 0) {
    $added = 0
    foreach ($inv in $invoices) {
        if ([string]::IsNullOrWhiteSpace($inv.invoiceid)) { continue }

        $invoiceId = $inv.invoiceid.ToString().Trim()
        if ($invoiceIdsWithTransactions.ContainsKey($invoiceId)) { continue }

        $totalValue = ConvertTo-NullableDecimal -Value $inv.total
        if ($null -eq $totalValue) { continue }
        if ($totalValue -ne 0) { continue }

        # Append as a pseudo-transaction so we can reuse the mapping logic below.
        $transactions += [pscustomobject]@{
            transactionid = $null
            userid        = $inv.userid
            date          = $inv.date
            gateway       = if ($inv.paymentmethod) { $inv.paymentmethod } else { 'free' }
            amountin      = $inv.total
            fees          = $null
            amountout     = $null
            transid       = $null
            invoiceid     = $invoiceId
            description   = "Invoice (0 total) status=$($inv.status)"
            currency      = $null
        }
        $added++
    }

    Write-Host "Included zero-total invoices: $added"
}

$clientById = @{}
foreach ($c in $clients) {
    if (-not [string]::IsNullOrWhiteSpace($c.clientid)) {
        $clientById[$c.clientid] = $c
    }
}

$headers = if ($Mode -eq 'template') { Get-TemplateHeaders -Path $TemplatePath } else { Get-CanonicalHeaders }

$importDate = (Get-Date).ToString('MM/dd/yyyy', [System.Globalization.CultureInfo]::InvariantCulture)
$defaultFormTitle = "Import - $importDate"

$out = foreach ($t in $transactions) {
    if ($ExcludeUserIdZero) {
        if ([string]::IsNullOrWhiteSpace($t.userid) -or $t.userid -eq '0') {
            continue
        }
    }

    $client = $null
    if ($t.userid -and $clientById.ContainsKey($t.userid)) {
        $client = $clientById[$t.userid]
    }

    if (-not $client) {
        continue
    }

    $amount = $t.amountin
    if ([string]::IsNullOrWhiteSpace($amount) -and $t.amountout) {
        $amount = $t.amountout
    }

    $amountFormatted = Format-ZeffyAmount -Value $amount
    if ([string]::IsNullOrWhiteSpace($amountFormatted)) {
        continue
    }
    $amountValue = $null
    if (-not [string]::IsNullOrWhiteSpace($amountFormatted)) {
        try { $amountValue = [decimal]::Parse($amountFormatted, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture) } catch { $amountValue = $null }
    }

    $rawGateway = $t.gateway
    $suggested = Get-ZeffyPaymentMethodSuggestion -WhmcsValue $rawGateway
    if ($null -ne $amountValue -and $amountValue -eq 0) {
        $suggested = 'free'
    }

    $addr = if ($client) { Join-Address -Address1 $client.address1 -Address2 $client.address2 } else { $null }
    $city = if ($client) { $client.city } else { $null }
    $postal = if ($client) { $client.postcode } else { $null }
    $country = if ($client) { $client.country } else { $null }
    $stateProv = if ($client) { $client.state } else { $null }

    if ([string]::IsNullOrWhiteSpace($addr)) { $addr = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($city)) { $city = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($postal)) { $postal = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($country)) { $country = $DefaultCountry }

    $date = Format-ZeffyDate -Value $t.date

    $canonical = @{
        firstName           = $client.firstname
        lastName            = $client.lastname
        amount              = $amountFormatted
        address             = $addr
        city                = $city
        postalCode          = $postal
        country             = $country
        type                = $DefaultType
        formTitle           = $defaultFormTitle
        rateTitle           = $defaultFormTitle
        email               = $client.email
        language            = $DefaultLanguage
        'date (MM/DD/YYYY)' = $date
        'state/province'    = $stateProv
        paymentMethod       = $suggested
        receiptUrl          = $null
        ticketUrl           = $null
        receiptNumber       = $null
        companyName         = $client.companyname
        note                = 'Imported from WHMCS'
        annotation          = (@(
                if ($t.transactionid) { "whmcs_transactionid=$($t.transactionid)" }
                if ($t.invoiceid) { "whmcs_invoiceid=$($t.invoiceid)" }
                if ($t.transid) { "gateway_transid=$($t.transid)" }
                if ($rawGateway) { "gateway=$rawGateway" }
                if ($t.description) { "desc=$($t.description)" }
            ) | Where-Object { $_ }) -join '; '
    }

    if ([string]::IsNullOrWhiteSpace($canonical.firstName) -or [string]::IsNullOrWhiteSpace($canonical.lastName)) {
        continue
    }

    Convert-RowToHeaders -Canonical $canonical -Headers $headers
}

$rows = @($out)

Remove-StalePartFiles -BaseOutputFile $OutputFile

if ($rows.Count -le $MaxRowsPerFile) {
    $rows | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile
    Write-Host "Generated Zeffy import draft: $OutputFile"
    Write-Host "Mode=$Mode; Rows=$($rows.Count)"
}
else {
    $part = 1
    for ($i = 0; $i -lt $rows.Count; $i += $MaxRowsPerFile) {
        $endExclusive = [Math]::Min($i + $MaxRowsPerFile, $rows.Count)
        $chunk = $rows[$i..($endExclusive - 1)]

        $partPath = Get-OutputPartPath -BaseOutputFile $OutputFile -PartNumber $part
        $chunk | Export-Csv -NoTypeInformation -Encoding utf8 -Path $partPath
        Write-Host "Generated Zeffy import draft part: $partPath (Rows=$($chunk.Count))"
        $part++
    }

    $partsWritten = $part - 1
    Write-Host "Generated Zeffy import draft parts: $partsWritten"
    Write-Host "Mode=$Mode; TotalRows=$($rows.Count); MaxRowsPerFile=$MaxRowsPerFile"
}
