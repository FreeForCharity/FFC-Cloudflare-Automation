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
    [string]$ProductsOutputFile = 'whmcs_products.csv',

    [Parameter()]
    [string]$ClientProductsOutputFile = 'whmcs_client_products.csv',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
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

function Get-WhmcsProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.products -and $Response.products.product) {
        return @($Response.products.product)
    }
    if ($Response.products -is [System.Array]) {
        return @($Response.products)
    }
    return @()
}

function ConvertTo-ProductPricingRow {
    param(
        [Parameter(Mandatory = $true)]
        $Product
    )

    $row = [ordered]@{
        pid                = $Product.pid
        gid                = $Product.gid
        type               = $Product.type
        name               = $Product.name
        slug               = $Product.slug
        module             = $Product.module
        paytype            = $Product.paytype
        allowqty           = $Product.allowqty
        quantity_available = $Product.quantity_available
    }

    # Flatten the first currency block we find (we can extend later if needed).
    $currencyCode = $null
    $pricingObj = $null
    if ($Product.pricing) {
        $currencyProps = @($Product.pricing.PSObject.Properties)
        if ($currencyProps.Count -gt 0) {
            $currencyCode = $currencyProps[0].Name
            $pricingObj = $currencyProps[0].Value
        }
    }

    $row.currency = $currencyCode

    if ($pricingObj) {
        foreach ($k in @('prefix', 'suffix', 'msetupfee', 'qsetupfee', 'ssetupfee', 'asetupfee', 'bsetupfee', 'tsetupfee',
                'monthly', 'quarterly', 'semiannually', 'annually', 'biennially', 'triennially')) {
            if ($pricingObj.PSObject.Properties.Name -contains $k) {
                $row["pricing_$k"] = $pricingObj.$k
            }
            else {
                $row["pricing_$k"] = $null
            }
        }

        # Convenience booleans for “monthly/yearly products” research.
        $row.has_monthly = ($null -ne $row.pricing_monthly -and $row.pricing_monthly.ToString() -ne '-1.00')
        $row.has_annually = ($null -ne $row.pricing_annually -and $row.pricing_annually.ToString() -ne '-1.00')
    }
    else {
        foreach ($k in @('prefix', 'suffix', 'msetupfee', 'qsetupfee', 'ssetupfee', 'asetupfee', 'bsetupfee', 'tsetupfee',
                'monthly', 'quarterly', 'semiannually', 'annually', 'biennially', 'triennially')) {
            $row["pricing_$k"] = $null
        }
        $row.has_monthly = $false
        $row.has_annually = $false
    }

    return [PSCustomObject]$row
}

function Get-WhmcsClientProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.products -and $Response.products.product) {
        return @($Response.products.product)
    }
    if ($Response.products -is [System.Array]) {
        return @($Response.products)
    }
    return @()
}

function ConvertTo-ClientProductRow {
    param(
        [Parameter(Mandatory = $true)]
        $Product
    )

    # Keep raw gateway identifiers for later Zeffy mapping.
    return [PSCustomObject]([ordered]@{
            serviceid          = $Product.id
            clientid           = $Product.clientid
            pid                = $Product.pid
            name               = $Product.name
            groupname          = $Product.groupname
            domain             = $Product.domain
            regdate            = $Product.regdate
            firstpaymentamount = $Product.firstpaymentamount
            recurringamount    = $Product.recurringamount
            billingcycle       = $Product.billingcycle
            nextduedate        = $Product.nextduedate
            status             = $Product.status
            paymentmethod      = $Product.paymentmethod
            paymentmethodname  = $Product.paymentmethodname
            subscriptionid     = $Product.subscriptionid
        })
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $ProductsOutputFile
    New-DirectoryForFile -Path $ClientProductsOutputFile

    # --- Product catalog ---
    $bodyProducts = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'GetProducts'
        responsetype = 'xml'
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) {
        $bodyProducts.accesskey = $accessKey
    }

    $rProducts = Invoke-WhmcsApi -ApiUrl $api -Body $bodyProducts
    $products = Get-WhmcsProductsFromResponse -Response $rProducts
    $productsFlat = $products | ForEach-Object { ConvertTo-ProductPricingRow -Product $_ }
    $productsFlat | Export-Csv -Path $ProductsOutputFile -NoTypeInformation -Encoding UTF8

    # --- Client products/services (used to see who is on monthly/yearly cycles, next due, etc.) ---
    $allClientProducts = @()
    $start = 0

    while ($true) {
        $bodyClientProducts = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetClientsProducts'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) {
            $bodyClientProducts.accesskey = $accessKey
        }

        $rClientProducts = Invoke-WhmcsApi -ApiUrl $api -Body $bodyClientProducts
        $page = Get-WhmcsClientProductsFromResponse -Response $rClientProducts
        if ($page.Count -le 0) { break }

        $allClientProducts += $page

        $numReturnedApi = 0
        if ($rClientProducts.numreturned) {
            [void][int]::TryParse($rClientProducts.numreturned.ToString(), [ref]$numReturnedApi)
        }

        if ($numReturnedApi -gt 0) {
            $start += $numReturnedApi
        }
        else {
            $start += $page.Count
        }

        $total = 0
        if ($rClientProducts.totalresults) {
            [void][int]::TryParse($rClientProducts.totalresults.ToString(), [ref]$total)
        }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $clientProductsFlat = $allClientProducts | ForEach-Object { ConvertTo-ClientProductRow -Product $_ }
    $clientProductsFlat | Export-Csv -Path $ClientProductsOutputFile -NoTypeInformation -Encoding UTF8

    Write-Host "Exported products: $($productsFlat.Count) -> $ProductsOutputFile"
    Write-Host "Exported client products: $($clientProductsFlat.Count) -> $ClientProductsOutputFile"
}
catch {
    Write-Error $_
    exit 1
}
