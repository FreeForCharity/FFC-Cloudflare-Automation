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

function Normalize-Text {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $v = $Value.Trim()
    if ($v -eq 'System.Xml.XmlElement') { return $null }
    return $v
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
    $client = $null
    if ($t.userid -and $clientById.ContainsKey($t.userid)) {
        $client = $clientById[$t.userid]
    }

    $amount = $t.amountin
    if ([string]::IsNullOrWhiteSpace($amount) -and $t.amountout) {
        $amount = $t.amountout
    }

    $amountFormatted = Format-ZeffyAmount -Value $amount
    $amountValue = $null
    if (-not [string]::IsNullOrWhiteSpace($amountFormatted)) {
        try { $amountValue = [decimal]::Parse($amountFormatted, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture) } catch { $amountValue = $null }
    }

    $rawGateway = $t.gateway
    $suggested = Get-ZeffyPaymentMethodSuggestion -WhmcsValue $rawGateway
    if ($amountValue -ne $null -and $amountValue -eq 0) {
        $suggested = 'free'
    }

    $addr = if ($client) { Join-Address -Address1 $client.address1 -Address2 $client.address2 } else { $null }
    $city = if ($client) { $client.city } else { $null }
    $postal = if ($client) { $client.postcode } else { $null }
    $country = if ($client) { $client.country } else { $null }
    $stateProv = if ($client) { $client.state } else { $null }

    $addr = Normalize-Text -Value $addr
    $city = Normalize-Text -Value $city
    $postal = Normalize-Text -Value $postal
    $country = Normalize-Text -Value $country
    $stateProv = Normalize-Text -Value $stateProv

    if ([string]::IsNullOrWhiteSpace($addr)) { $addr = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($city)) { $city = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($postal)) { $postal = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($country)) { $country = $DefaultCountry }

    $date = Format-ZeffyDate -Value $t.date

    $canonical = @{
        firstName       = if ($client) { Normalize-Text -Value $client.firstname } else { $null }
        lastName        = if ($client) { Normalize-Text -Value $client.lastname } else { $null }
        amount          = $amountFormatted
        address         = $addr
        city            = $city
        postalCode      = $postal
        country         = $country
        type            = $DefaultType
        formTitle       = $defaultFormTitle
        rateTitle       = $defaultFormTitle
        email           = if ($client) { Normalize-Text -Value $client.email } else { $null }
        language        = $DefaultLanguage
        'date (MM/DD/YYYY)' = $date
        'state/province' = $stateProv
        paymentMethod   = $suggested
        receiptUrl      = $null
        ticketUrl       = $null
        receiptNumber   = $null
        companyName     = if ($client) { Normalize-Text -Value $client.companyname } else { $null }
        note            = 'Imported from WHMCS'
        annotation      = (@(
            if ($t.transactionid) { "whmcs_transactionid=$($t.transactionid)" }
            if ($t.invoiceid) { "whmcs_invoiceid=$($t.invoiceid)" }
            if ($t.transid) { "gateway_transid=$($t.transid)" }
            if ($rawGateway) { "gateway=$rawGateway" }
            if ($t.description) { "desc=$($t.description)" }
        ) | Where-Object { $_ }) -join '; '
    }

    Convert-RowToHeaders -Canonical $canonical -Headers $headers
}

$out | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile

Write-Host "Generated Zeffy import draft: $OutputFile"
Write-Host "Mode=$Mode; Rows=$($transactions.Count)"
