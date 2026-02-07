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

    ,
    [Parameter()]
    [switch]$FailOnValidationErrors

    ,
    [Parameter()]
    [switch]$WriteXlsx

    ,
    [Parameter()]
    [string]$XlsxOutputFile

    ,
    [Parameter()]
    [ValidateRange(1, 5000)]
    [int]$MaxValidationErrorsToReport = 200
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
    $ext = [System.IO.Path]::GetExtension($BaseOutputFile)
    if ([string]::IsNullOrWhiteSpace($ext)) { $ext = '.csv' }

    $pattern = "$baseName-part*${ext}"

    Get-ChildItem -Path $dir -Filter $pattern -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Export-ZeffyRowsToExcel {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Rows,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$WorksheetName = 'Zeffy'
    )

    if ($Rows.Count -eq 0) {
        throw "Refusing to write empty Excel file: $Path"
    }

    try {
        Import-Module ImportExcel -ErrorAction Stop
    }
    catch {
        throw "ImportExcel module is required to write .xlsx output. Install it with: Install-Module ImportExcel -Scope CurrentUser"
    }

    New-DirectoryForFile -Path $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }

    # Keep these columns as text so Excel does not auto-convert (e.g., leading zeros, date-like strings).
    $noConvert = @(
        'date (MM/DD/YYYY)',
        'postalCode',
        'receiptNumber'
    )

    $Rows |
        Export-Excel -Path $Path -WorksheetName $WorksheetName -FreezeTopRow -AutoSize -NoNumberConversion $noConvert |
        Out-Null
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

function Normalize-ZeffyCell {
    param([string]$Value)

    if ($null -eq $Value) { return $null }
    $v = $Value.ToString()

    # Avoid row breaks / odd parsing in downstream CSV importers.
    $v = $v -replace "\r\n|\r|\n", ' '
    $v = $v -replace "\t", ' '
    $v = $v.Trim()
    $v = $v -replace "\s{2,}", ' '

    if ([string]::IsNullOrWhiteSpace($v)) { return $null }
    return $v
}

function Test-ZeffyCompanyName {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }

    # Conservative allowlist: letters/numbers/spaces and common company punctuation.
    # Zeffy rejects double quotes in companyName, so we treat them as invalid.
    $allowedPattern = '^[\p{L}\p{N} \-\.,&''/()#:+]*$'
    return ($Value -match $allowedPattern)
}

function Sanitize-ZeffyCompanyName {
    param([string]$Value)

    $v = Normalize-ZeffyCell -Value $Value
    if ([string]::IsNullOrWhiteSpace($v)) { return $null }

    # Common replacements
    $v = $v -replace '@', 'At '
    $v = $v -replace "\u2018|\u2019", "'"
    # Remove double quotes entirely (Zeffy companyName validation rejects them).
    $v = $v -replace "\u201C|\u201D", ''
    $v = $v -replace '"', ''
    $v = $v -replace "\u2013|\u2014", '-'
    $v = $v -replace "\u00A0", ' '

    # Drop any remaining disallowed characters.
    $disallowedPattern = '[^\p{L}\p{N} \-\.,&''/()#:+]'
    $v = [regex]::Replace($v, $disallowedPattern, ' ')

    $v = Normalize-ZeffyCell -Value $v
    if ([string]::IsNullOrWhiteSpace($v)) { return $null }

    return $v
}

function Test-ZeffyPersonName {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }

    # Zeffy rejects digits in names (e.g., lastName=Post245). Keep this conservative.
    # NOTE: Sanitization normalizes curly apostrophes to ASCII ', so we only allow ASCII here.
    $allowedPattern = "^[\p{L}][\p{L} \-']*$"
    return ($Value -match $allowedPattern)
}

function Sanitize-ZeffyPersonName {
    param([string]$Value)

    $v = Normalize-ZeffyCell -Value $Value
    if ([string]::IsNullOrWhiteSpace($v)) { return $null }

    $v = $v -replace "\u2018|\u2019", "'"
    $v = $v -replace "\u2013|\u2014", '-'
    $v = $v -replace "\u00A0", ' '

    # Drop digits and any other unsupported characters.
    $disallowedPattern = "[^\p{L} \-']"
    $v = [regex]::Replace($v, $disallowedPattern, ' ')
    $v = Normalize-ZeffyCell -Value $v
    if ([string]::IsNullOrWhiteSpace($v)) { return $null }
    return $v
}

function Get-InvalidCharacters {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [scriptblock]$IsAllowed
    )

    $bad = @{}
    foreach ($ch in $Value.ToCharArray()) {
        if (-not (& $IsAllowed $ch)) {
            $bad[$ch] = $true
        }
    }
    return @($bad.Keys | Sort-Object)
}

function Write-ValidationReport {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Errors,
        [Parameter(Mandatory = $true)]
        [int]$MaxRows,
        [string]$ReportBasePath = 'artifacts/zeffy/zeffy_payments_import_draft.validation_errors'
    )

    if (-not $Errors -or $Errors.Count -eq 0) { return }

    $reportCsv = "$ReportBasePath.csv"
    $reportMd = "$ReportBasePath.md"

    New-DirectoryForFile -Path $reportCsv
    New-DirectoryForFile -Path $reportMd

    $Errors | Export-Csv -NoTypeInformation -Encoding utf8 -Path $reportCsv

    $top = @($Errors | Select-Object -First $MaxRows)
    $md = @()
    $md += '## Zeffy import validation errors'
    $md += ''
    $md += "- Total invalid rows: $($Errors.Count)"
    $md += "- Showing first: $($top.Count)"
    $md += ''
    $md += '| csvRow | field | reason | value | firstName | lastName | email | whmcs_userid | whmcs_transactionid | whmcs_invoiceid |'
    $md += '|---:|---|---|---|---|---|---|---|---|---|'
    foreach ($e in $top) {
        $v = if ($e.value) { $e.value.ToString() } else { '' }
        $v = $v -replace '\|', '\\|'
        $md += "| $($e.csvRow) | $($e.field) | $($e.reason) | $v | $($e.firstName) | $($e.lastName) | $($e.email) | $($e.whmcs_userid) | $($e.whmcs_transactionid) | $($e.whmcs_invoiceid) |"
    }
    $md -join "`n" | Out-File -FilePath $reportMd -Encoding utf8

    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $md -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        '' | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        "- Validation report files: $reportCsv ; $reportMd" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    else {
        Write-Host ($md -join "`n")
        Write-Host "Validation report files: $reportCsv ; $reportMd"
    }
}

function Format-ZeffyDate {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }

    $v = $Value.Trim()
    $culture = [System.Globalization.CultureInfo]::InvariantCulture

    # 1) General parsing (handles many ISO and locale-neutral forms).
    $dto = [datetimeoffset]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AllowWhiteSpaces
    if ([datetimeoffset]::TryParse($v, $culture, $styles, [ref]$dto)) {
        return $dto.Date.ToString('MM/dd/yyyy', $culture)
    }

    # 2) Unix epoch (seconds or milliseconds).
    if ($v -match '^\d{10,13}$') {
        try {
            $n = [int64]$v
            $epoch = if ($v.Length -ge 13) { [datetimeoffset]::FromUnixTimeMilliseconds($n) } else { [datetimeoffset]::FromUnixTimeSeconds($n) }
            return $epoch.Date.ToString('MM/dd/yyyy', $culture)
        }
        catch {
            return $null
        }
    }

    return $null
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

$resolvedXlsxOutputFile = $null
if ($WriteXlsx) {
    if ([string]::IsNullOrWhiteSpace($XlsxOutputFile)) {
        $resolvedXlsxOutputFile = [System.IO.Path]::ChangeExtension($OutputFile, '.xlsx')
    }
    else {
        $resolvedXlsxOutputFile = $XlsxOutputFile
    }
    New-DirectoryForFile -Path $resolvedXlsxOutputFile
}

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

$validationErrors = New-Object System.Collections.Generic.List[object]
$companyNameTransforms = New-Object System.Collections.Generic.List[object]
$personNameTransforms = New-Object System.Collections.Generic.List[object]
$rowsList = New-Object System.Collections.Generic.List[object]

foreach ($t in $transactions) {
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

    $addr = Normalize-ZeffyCell -Value $addr
    $city = Normalize-ZeffyCell -Value $city
    $postal = Normalize-ZeffyCell -Value $postal
    $country = Normalize-ZeffyCell -Value $country
    $stateProv = Normalize-ZeffyCell -Value $stateProv

    if ([string]::IsNullOrWhiteSpace($addr)) { $addr = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($city)) { $city = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($postal)) { $postal = 'unknown' }
    if ([string]::IsNullOrWhiteSpace($country)) { $country = $DefaultCountry }

    $rawDate = Normalize-ZeffyCell -Value $t.date
    $date = Format-ZeffyDate -Value $rawDate
    $dateValid = -not [string]::IsNullOrWhiteSpace($date)
    if ($dateValid -and $date -match '^\d{2}/\d{2}/\d{4}$') {
        $dt = [datetime]::MinValue
        if (-not [datetime]::TryParseExact($date, 'MM/dd/yyyy', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$dt)) {
            $dateValid = $false
        }
    }
    else {
        $dateValid = $false
    }

    if (-not $dateValid) {
        if ($FailOnValidationErrors) {
            $date = $null
        }
        else {
            $date = $importDate
        }
    }

    $canonical = @{
        firstName           = Sanitize-ZeffyPersonName -Value $client.firstname
        lastName            = Sanitize-ZeffyPersonName -Value $client.lastname
        amount              = $amountFormatted
        address             = $addr
        city                = $city
        postalCode          = $postal
        country             = $country
        type                = $DefaultType
        formTitle           = Normalize-ZeffyCell -Value $defaultFormTitle
        rateTitle           = Normalize-ZeffyCell -Value $defaultFormTitle
        email               = Normalize-ZeffyCell -Value $client.email
        language            = $DefaultLanguage
        'date (MM/DD/YYYY)' = $date
        'state/province'    = $stateProv
        paymentMethod       = $suggested
        receiptUrl          = $null
        ticketUrl           = $null
        receiptNumber       = $null
        companyName         = Normalize-ZeffyCell -Value $client.companyname
        note                = Normalize-ZeffyCell -Value 'Imported from WHMCS'
        annotation          = (@(
                if ($t.transactionid) { "whmcs_transactionid=$($t.transactionid)" }
                if ($t.invoiceid) { "whmcs_invoiceid=$($t.invoiceid)" }
                if ($t.transid) { "gateway_transid=$($t.transid)" }
                if ($rawGateway) { "gateway=$rawGateway" }
                if ($t.description) { "desc=$($t.description)" }
            ) | Where-Object { $_ }) -join '; '
    }

    $canonical.annotation = Normalize-ZeffyCell -Value $canonical.annotation

    # Build output row now so we can report accurate CSV row numbers.
    $csvRowNumber = $rowsList.Count + 2

    # Track name transforms (helps debug Zeffy validation like lastName containing digits).
    $originalFirstName = Normalize-ZeffyCell -Value $client.firstname
    $originalLastName = Normalize-ZeffyCell -Value $client.lastname
    if ($originalFirstName -ne $canonical.firstName) {
        $personNameTransforms.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'firstName'
                original            = $originalFirstName
                sanitized           = $canonical.firstName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }
    if ($originalLastName -ne $canonical.lastName) {
        $personNameTransforms.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'lastName'
                original            = $originalLastName
                sanitized           = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }

    $originalCompanyName = $canonical.companyName
    $sanitizedCompanyName = Sanitize-ZeffyCompanyName -Value $originalCompanyName
    if ($originalCompanyName -ne $sanitizedCompanyName) {
        $companyNameTransforms.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'companyName'
                original            = $originalCompanyName
                sanitized           = $sanitizedCompanyName
                firstName           = $canonical.firstName
                lastName            = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }
    $canonical.companyName = $sanitizedCompanyName

    if (-not $dateValid) {
        $validationErrors.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'date (MM/DD/YYYY)'
                reason              = "unparseable or invalid date (raw='$rawDate')"
                value               = $t.date
                firstName           = $canonical.firstName
                lastName            = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }

    $cn = $canonical.companyName
    if (-not (Test-ZeffyCompanyName -Value $cn)) {
        $invalidChars = Get-InvalidCharacters -Value $cn -IsAllowed {
            param($ch)
            # Same allowlist as Test-ZeffyCompanyName
            return ($ch -match '[\p{L}\p{N} \-\.,&''/()#:+]')
        }

        $validationErrors.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'companyName'
                reason              = "contains disallowed character(s): $($invalidChars -join '')"
                value               = $cn
                firstName           = $canonical.firstName
                lastName            = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }

    if ([string]::IsNullOrWhiteSpace($canonical.firstName) -or [string]::IsNullOrWhiteSpace($canonical.lastName)) {
        continue
    }

    if (-not (Test-ZeffyPersonName -Value $canonical.firstName)) {
        $validationErrors.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'firstName'
                reason              = 'invalid format after sanitization'
                value               = $canonical.firstName
                firstName           = $canonical.firstName
                lastName            = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }
    if (-not (Test-ZeffyPersonName -Value $canonical.lastName)) {
        $validationErrors.Add([pscustomobject]@{
                csvRow              = $csvRowNumber
                field               = 'lastName'
                reason              = 'invalid format after sanitization'
                value               = $canonical.lastName
                firstName           = $canonical.firstName
                lastName            = $canonical.lastName
                email               = $canonical.email
                whmcs_userid        = $t.userid
                whmcs_transactionid = $t.transactionid
                whmcs_invoiceid     = $t.invoiceid
            })
    }

    $rowObj = Convert-RowToHeaders -Canonical $canonical -Headers $headers
    if ($null -ne $rowObj) {
        $rowsList.Add($rowObj) | Out-Null
    }
}

$rows = @($rowsList.ToArray())

if ($companyNameTransforms.Count -gt 0) {
    $base = 'artifacts/zeffy/zeffy_payments_import_draft.transforms_companyName'
    $csvPath = "$base.csv"
    $mdPath = "$base.md"
    New-DirectoryForFile -Path $csvPath
    New-DirectoryForFile -Path $mdPath
    @($companyNameTransforms.ToArray()) | Export-Csv -NoTypeInformation -Encoding utf8 -Path $csvPath

    $top = @($companyNameTransforms | Select-Object -First 50)
    $md = @()
    $md += '## Zeffy companyName transforms'
    $md += ''
    $md += "- Total transformed rows: $($companyNameTransforms.Count)"
    $md += "- Showing first: $($top.Count)"
    $md += ''
    $md += '| csvRow | original | sanitized | firstName | lastName | email | whmcs_userid | whmcs_transactionid | whmcs_invoiceid |'
    $md += '|---:|---|---|---|---|---|---|---|---|'
    foreach ($e in $top) {
        $o = if ($e.original) { $e.original.ToString() } else { '' }
        $s = if ($e.sanitized) { $e.sanitized.ToString() } else { '' }
        $o = $o -replace '\|', '\\|'
        $s = $s -replace '\|', '\\|'
        $md += "| $($e.csvRow) | $o | $s | $($e.firstName) | $($e.lastName) | $($e.email) | $($e.whmcs_userid) | $($e.whmcs_transactionid) | $($e.whmcs_invoiceid) |"
    }
    $md -join "`n" | Out-File -FilePath $mdPath -Encoding utf8

    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $md -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
}

if ($personNameTransforms.Count -gt 0) {
    $base = 'artifacts/zeffy/zeffy_payments_import_draft.transforms_personName'
    $csvPath = "$base.csv"
    $mdPath = "$base.md"
    New-DirectoryForFile -Path $csvPath
    New-DirectoryForFile -Path $mdPath
    @($personNameTransforms.ToArray()) | Export-Csv -NoTypeInformation -Encoding utf8 -Path $csvPath

    $top = @($personNameTransforms | Select-Object -First 50)
    $md = @()
    $md += '## Zeffy person name transforms'
    $md += ''
    $md += "- Total transformed rows: $($personNameTransforms.Count)"
    $md += "- Showing first: $($top.Count)"
    $md += ''
    $md += '| csvRow | field | original | sanitized | email | whmcs_userid | whmcs_transactionid | whmcs_invoiceid |'
    $md += '|---:|---|---|---|---|---|---|---|'
    foreach ($e in $top) {
        $o = if ($e.original) { $e.original.ToString() } else { '' }
        $s = if ($e.sanitized) { $e.sanitized.ToString() } else { '' }
        $o = $o -replace '\|', '\\|'
        $s = $s -replace '\|', '\\|'
        $md += "| $($e.csvRow) | $($e.field) | $o | $s | $($e.email) | $($e.whmcs_userid) | $($e.whmcs_transactionid) | $($e.whmcs_invoiceid) |"
    }
    $md -join "`n" | Out-File -FilePath $mdPath -Encoding utf8

    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $md -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
}

if ($FailOnValidationErrors -and $validationErrors.Count -gt 0) {
    Write-ValidationReport -Errors @($validationErrors.ToArray()) -MaxRows $MaxValidationErrorsToReport
    throw "Zeffy validation failed ($($validationErrors.Count) row(s)). Fix the source data or mapping, then re-run."
}

Remove-StalePartFiles -BaseOutputFile $OutputFile
if ($WriteXlsx -and -not [string]::IsNullOrWhiteSpace($resolvedXlsxOutputFile)) {
    Remove-StalePartFiles -BaseOutputFile $resolvedXlsxOutputFile
}

if ($rows.Count -le $MaxRowsPerFile) {
    $rows | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile
    Write-Host "Generated Zeffy import draft: $OutputFile"
    Write-Host "Mode=$Mode; Rows=$($rows.Count)"

    if ($WriteXlsx -and -not [string]::IsNullOrWhiteSpace($resolvedXlsxOutputFile)) {
        Export-ZeffyRowsToExcel -Rows $rows -Path $resolvedXlsxOutputFile
        Write-Host "Generated Zeffy import draft (xlsx): $resolvedXlsxOutputFile"
    }
}
else {
    $part = 1
    for ($i = 0; $i -lt $rows.Count; $i += $MaxRowsPerFile) {
        $endExclusive = [Math]::Min($i + $MaxRowsPerFile, $rows.Count)
        $chunk = $rows[$i..($endExclusive - 1)]

        $partPath = Get-OutputPartPath -BaseOutputFile $OutputFile -PartNumber $part
        $chunk | Export-Csv -NoTypeInformation -Encoding utf8 -Path $partPath
        Write-Host "Generated Zeffy import draft part: $partPath (Rows=$($chunk.Count))"

        if ($WriteXlsx -and -not [string]::IsNullOrWhiteSpace($resolvedXlsxOutputFile)) {
            $xlsxPartPath = Get-OutputPartPath -BaseOutputFile $resolvedXlsxOutputFile -PartNumber $part
            Export-ZeffyRowsToExcel -Rows $chunk -Path $xlsxPartPath
            Write-Host "Generated Zeffy import draft part (xlsx): $xlsxPartPath (Rows=$($chunk.Count))"
        }
        $part++
    }

    $partsWritten = $part - 1
    Write-Host "Generated Zeffy import draft parts: $partsWritten"
    Write-Host "Mode=$Mode; TotalRows=$($rows.Count); MaxRowsPerFile=$MaxRowsPerFile"
}


