[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$AccessKey,

    [Parameter()]
    [string]$OutputFile = 'whmcs_payment_methods.csv',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [ValidateRange(1, 10000)]
    [int]$MaxExampleIds = 10
)

$ErrorActionPreference = 'Stop'

function Resolve-WhmcsCredentials {
    param(
        [string]$IdentifierParam,
        [string]$SecretParam
    )

    $id = if ($IdentifierParam) { $IdentifierParam } else { $env:WHMCS_API_IDENTIFIER }
    $sec = if ($SecretParam) { $SecretParam } else { $env:WHMCS_API_SECRET }

    if (-not [string]::IsNullOrWhiteSpace($id) -and -not [string]::IsNullOrWhiteSpace($sec)) {
        return @{ Identifier = $id; Secret = $sec }
    }

    throw 'Missing WHMCS credentials. Provide -Identifier/-Secret or set WHMCS_API_IDENTIFIER/WHMCS_API_SECRET.'
}

function Resolve-WhmcsApiUrl {
    param([string]$ApiUrlParam)

    if ($ApiUrlParam) { return $ApiUrlParam }
    if ($env:WHMCS_API_URL) { return $env:WHMCS_API_URL }

    return 'https://freeforcharity.org/hub/includes/api.php'
}

function Resolve-WhmcsAccessKey {
    param([string]$AccessKeyParam)

    if ($AccessKeyParam) { return $AccessKeyParam }
    if ($env:WHMCS_API_ACCESS_KEY) { return $env:WHMCS_API_ACCESS_KEY }

    return $null
}

function Invoke-WhmcsApi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,

        [Parameter(Mandatory = $true)]
        [hashtable]$Body
    )

    $requestedResponseType = if ($Body.ContainsKey('responsetype') -and -not [string]::IsNullOrWhiteSpace($Body.responsetype)) { $Body.responsetype } else { 'json' }

    $headers = @{
        'Accept'     = 'application/json, application/xml;q=0.9, */*;q=0.8'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($requestedResponseType -eq 'xml') {
        if ($resp -is [string]) {
            $raw = $resp
            try {
                $resp = [xml]$raw
            }
            catch {
                $clean = $raw -replace '[\x00-\x08\x0B\x0C\x0E-\x1F]', ''
                try {
                    $resp = [xml]$clean
                }
                catch {
                    $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
                    throw "WHMCS API returned XML that could not be parsed (likely contains invalid control characters). Snippet: $snippet"
                }
            }
        }

        if ($resp -is [xml] -and $resp.whmcsapi) {
            $resp = $resp.whmcsapi
        }
    }
    else {
        if ($resp -is [string]) {
            $raw = $resp
            try {
                $resp = $raw | ConvertFrom-Json -ErrorAction Stop
            }
            catch {
                if ($raw.TrimStart().StartsWith('<')) {
                    try {
                        try {
                            $xml = [xml]$raw
                        }
                        catch {
                            $clean = $raw -replace '[\x00-\x08\x0B\x0C\x0E-\x1F]', ''
                            $xml = [xml]$clean
                        }
                        $resp = if ($xml.whmcsapi) { $xml.whmcsapi } else { $xml }
                        $requestedResponseType = 'xml'
                    }
                    catch {
                        $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
                        throw "WHMCS API returned a non-JSON response: $snippet"
                    }
                }
                else {
                    $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
                    throw "WHMCS API returned a non-JSON response: $snippet"
                }
            }
        }
    }

    if (-not $resp) {
        throw 'WHMCS API returned an empty response.'
    }

    if ($resp.result -ne 'success') {
        $msg = $null
        if ($resp.message) { $msg = $resp.message }
        elseif ($resp.errormessage) { $msg = $resp.errormessage }
        elseif ($resp.error) { $msg = $resp.error }

        if ([string]::IsNullOrWhiteSpace($msg)) {
            $diag = $null
            try { $diag = ($resp | ConvertTo-Json -Depth 6 -Compress) } catch {}
            if (-not [string]::IsNullOrWhiteSpace($diag) -and $diag.Length -gt 800) {
                $diag = $diag.Substring(0, 800) + '...'
            }
            $msg = "Unknown WHMCS API error." + (if ($diag) { " Response: $diag" } else { '' })
        }

        if ($requestedResponseType -eq 'json' -and $msg -match 'Error generating JSON encoded response|Malformed UTF-8') {
            $fallbackBody = $Body.Clone()
            $fallbackBody.responsetype = 'xml'
            return Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $fallbackBody
        }

        throw "WHMCS API error: $msg"
    }

    return $resp
}

function New-DirectoryForFile {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Add-PaymentMethodObservation {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Map,

        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Value,

        [string]$ExampleId
    )

    $key = "$Source::$Value"

    if (-not $Map.ContainsKey($key)) {
        $Map[$key] = [ordered]@{
            source      = $Source
            value       = $Value
            count       = 0
            example_ids = New-Object System.Collections.Generic.List[string]
        }
    }

    $Map[$key].count = [int]$Map[$key].count + 1

    if (-not [string]::IsNullOrWhiteSpace($ExampleId)) {
        $list = $Map[$key].example_ids
        if ($list.Count -lt $MaxExampleIds -and -not $list.Contains($ExampleId)) {
            $list.Add($ExampleId) | Out-Null
        }
    }
}

function Get-ZeffyPaymentMethodSuggestion {
    param([string]$WhmcsValue)

    if ([string]::IsNullOrWhiteSpace($WhmcsValue)) { return 'unknown' }
    $v = $WhmcsValue.ToLowerInvariant()

    if ($v -match 'apple|google') { return 'applePayOrGooglePay' }
    if ($v -match '\bach\b') { return 'ach' }
    if ($v -match '\bpad\b') { return 'pad' }
    if ($v -match 'bank|wire|transfer') { return 'transfer' }
    if ($v -match 'cheque|check') { return 'cheque' }
    if ($v -match 'cash') { return 'cash' }

    # Common card gateways
    if ($v -match 'stripe|authorize|authorizenet|cc|credit') { return 'card' }
    if ($v -match 'paypal') { return 'card' }

    # We generally can't infer "manual" vs "free" from WHMCS gateway strings reliably.
    return 'unknown'
}

function Get-TransactionsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.transactions -and $Response.transactions.transaction) {
        return @($Response.transactions.transaction)
    }
    return @()
}

function Get-InvoicesFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.invoices -and $Response.invoices.invoice) {
        return @($Response.invoices.invoice)
    }
    return @()
}

function Get-ClientProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.products -and $Response.products.product) {
        return @($Response.products.product)
    }
    return @()
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $obs = @{}

    # 1) Transactions.gateway (actual payment gateway identifier)
    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetTransactions'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $tx = Get-TransactionsFromResponse -Response $r
        if ($tx.Count -le 0) { break }

        foreach ($t in $tx) {
            if (-not [string]::IsNullOrWhiteSpace($t.gateway)) {
                Add-PaymentMethodObservation -Map $obs -Source 'GetTransactions.gateway' -Value $t.gateway -ExampleId $t.id
            }
        }

        $numReturnedApi = 0
        if ($r.numreturned) { [void][int]::TryParse($r.numreturned.ToString(), [ref]$numReturnedApi) }
        $start += $(if ($numReturnedApi -gt 0) { $numReturnedApi } else { $tx.Count })

        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # 2) Invoices.paymentmethod (invoice-level payment method identifier)
    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetInvoices'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $invoices = Get-InvoicesFromResponse -Response $r
        if ($invoices.Count -le 0) { break }

        foreach ($inv in $invoices) {
            if (-not [string]::IsNullOrWhiteSpace($inv.paymentmethod)) {
                Add-PaymentMethodObservation -Map $obs -Source 'GetInvoices.paymentmethod' -Value $inv.paymentmethod -ExampleId $inv.id
            }
        }

        $numReturnedApi = 0
        if ($r.numreturned) { [void][int]::TryParse($r.numreturned.ToString(), [ref]$numReturnedApi) }
        $start += $(if ($numReturnedApi -gt 0) { $numReturnedApi } else { $invoices.Count })

        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # 3) Client products.paymentmethod (service-level payment method identifier)
    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetClientsProducts'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $svc = Get-ClientProductsFromResponse -Response $r
        if ($svc.Count -le 0) { break }

        foreach ($s in $svc) {
            if (-not [string]::IsNullOrWhiteSpace($s.paymentmethod)) {
                Add-PaymentMethodObservation -Map $obs -Source 'GetClientsProducts.paymentmethod' -Value $s.paymentmethod -ExampleId $s.id
            }
            if (-not [string]::IsNullOrWhiteSpace($s.paymentmethodname)) {
                Add-PaymentMethodObservation -Map $obs -Source 'GetClientsProducts.paymentmethodname' -Value $s.paymentmethodname -ExampleId $s.id
            }
        }

        $numReturnedApi = 0
        if ($r.numreturned) { [void][int]::TryParse($r.numreturned.ToString(), [ref]$numReturnedApi) }
        $start += $(if ($numReturnedApi -gt 0) { $numReturnedApi } else { $svc.Count })

        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $rows = foreach ($k in $obs.Keys) {
        $o = $obs[$k]
        $value = $o.value
        $suggested = Get-ZeffyPaymentMethodSuggestion -WhmcsValue $value
        [PSCustomObject]([ordered]@{
                source                    = $o.source
                value                     = $value
                count                     = $o.count
                example_ids               = ($o.example_ids -join ',')
                suggested_zeffy_paymentMethod = $suggested
                zeffy_allowed_values      = 'card,cash,cheque,transfer,unknown,free,manual,pad,ach,applePayOrGooglePay'
            })
    }

    $rows |
        Sort-Object -Property @{ Expression = 'count'; Descending = $true }, source, value |
        Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8

    Write-Host "Exported $($rows.Count) distinct payment method values -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
