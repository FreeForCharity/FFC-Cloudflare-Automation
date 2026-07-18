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

function ConvertFrom-WhmcsXmlString {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Raw
    )

    try {
        return [xml]$Raw
    }
    catch {
        $clean = $Raw -replace '[\x00-\x08\x0B\x0C\x0E-\x1F]', ''
        try {
            return [xml]$clean
        }
        catch {
            # Last-resort: escape bare ampersands that often break XML parsing.
            $escaped = $clean -replace '&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)', '&amp;'
            try {
                return [xml]$escaped
            }
            catch {
                $snippet = if ($Raw.Length -gt 400) { $Raw.Substring(0, 400) + '...' } else { $Raw }
                throw "WHMCS API returned XML that could not be parsed (likely contains invalid control characters or malformed entities). Snippet: $snippet"
            }
        }
    }
}

function Invoke-WhmcsApi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,

        [Parameter(Mandatory = $true)]
        [hashtable]$Body
    )

    # SECURITY: the WHMCS credential (identifier/secret + APIM subscription key) is attached to
    # this request, so only allow it to be sent to known WHMCS hosts. A workflow input
    # (-ApiUrl / WHMCS_API_URL) must never redirect the credential to an arbitrary host.
    $allowedHosts = @('apim-ffc-gateway-prod.azure-api.net', 'freeforcharity.org')
    $parsedUri = $null
    if (-not [Uri]::TryCreate($ApiUrl, [UriKind]::Absolute, [ref]$parsedUri) -or $parsedUri.Scheme -ne 'https' -or $allowedHosts -notcontains $parsedUri.Host) {
        throw "Refusing to send WHMCS credentials to '$ApiUrl': host is not in the allowlist ($($allowedHosts -join ', '))."
    }

    $requestedResponseType = if ($Body.ContainsKey('responsetype') -and -not [string]::IsNullOrWhiteSpace($Body.responsetype)) { $Body.responsetype } else { 'json' }

    $headers = @{
        'Accept'     = 'application/json, application/xml;q=0.9, */*;q=0.8'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    # When WHMCS is reached via APIM (apim-ffc-gateway-prod), its 'whmcs' API requires this key.
    if (-not [string]::IsNullOrWhiteSpace($env:WHMCS_APIM_SUBSCRIPTION_KEY)) {
        $headers['Ocp-Apim-Subscription-Key'] = $env:WHMCS_APIM_SUBSCRIPTION_KEY
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($requestedResponseType -eq 'xml') {
        if ($resp -is [string]) {
            $raw = $resp
            $resp = ConvertFrom-WhmcsXmlString -Raw $raw
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
                        $xml = ConvertFrom-WhmcsXmlString -Raw $raw
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

function ConvertTo-NodeText {
    # Returns the inner text of an XML node, or the joined text of several nodes.
    # Avoids '[string]$node' yielding a type name ('System.Xml.XmlElement', or
    # 'System.Object[]' when XML property access matches multiple elements) for
    # name/value nodes that carry child markup. See issue #440.
    [OutputType([string])]
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) { return $Value }
    if ($Value -is [System.Xml.XmlNode]) { return $Value.InnerText }
    if ($Value -is [System.Collections.IEnumerable]) {
        $parts = foreach ($item in $Value) {
            if ($null -eq $item) { continue }
            elseif ($item -is [System.Xml.XmlNode]) { $item.InnerText }
            else { [string]$item }
        }
        return ($parts -join ' ')
    }
    return [string]$Value
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
        name               = ConvertTo-NodeText $Product.name
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

function Get-CustomFieldNodes {
    # Returns an array of @{ id; name; value } from a WHMCS node that may carry
    # <customfields><customfield>... entries (present on GetClientsProducts and,
    # on some installs, GetProducts). Tolerates both XML and JSON shapes.
    param($Node)

    if (-not $Node) { return @() }

    $cf = $null
    try { $cf = $Node.customfields } catch {}
    if (-not $cf) { return @() }

    $entries = @()
    try {
        if ($cf.customfield) { $entries = @($cf.customfield) }
        elseif ($cf -is [System.Array]) { $entries = @($cf) }
    }
    catch {}

    $out = @()
    foreach ($e in $entries) {
        if (-not $e) { continue }
        $id = $null; $name = $null; $value = $null
        try { $id = [string]$e.id } catch {}
        try { $name = ConvertTo-NodeText $e.name } catch {}
        if ([string]::IsNullOrWhiteSpace($name)) { try { $name = ConvertTo-NodeText $e.translated_name } catch {} }
        try { $value = ConvertTo-NodeText $e.value } catch {}
        if ([string]::IsNullOrWhiteSpace($name) -and [string]::IsNullOrWhiteSpace($id)) { continue }
        $out += [pscustomobject]@{ id = $id; name = $name; value = $value }
    }
    return $out
}

function ConvertTo-CustomFieldsString {
    # Flattens custom fields to "name[id]=value; ..." for a single CSV cell.
    param($Fields)
    if (-not $Fields -or @($Fields).Count -eq 0) { return $null }
    $parts = foreach ($f in $Fields) {
        $label = if ($f.id) { "$($f.name)[$($f.id)]" } else { $f.name }
        "$label=$($f.value)"
    }
    return ($parts -join '; ')
}

function ConvertTo-ClientProductRow {
    param(
        [Parameter(Mandatory = $true)]
        $Product
    )

    # Keep raw gateway identifiers for later Zeffy mapping.
    $customFields = Get-CustomFieldNodes -Node $Product
    return [PSCustomObject]([ordered]@{
            serviceid          = $Product.id
            clientid           = $Product.clientid
            pid                = $Product.pid
            name               = ConvertTo-NodeText $Product.name
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
            customfields       = ConvertTo-CustomFieldsString -Fields $customFields
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

    # --- Per-product custom field discovery -------------------------------------
    # Product custom field DEFINITIONS are not reliably exposed by GetProducts, but
    # they DO appear on each service returned by GetClientsProducts. Aggregate the
    # distinct custom field names (and ids) seen per pid so we can later populate
    # them correctly at order time (AddOrder customfields).
    $customFieldsByPid = @{}
    foreach ($cp in $allClientProducts) {
        $cpPid = $null
        try { $cpPid = [string]$cp.pid } catch {}
        if ([string]::IsNullOrWhiteSpace($cpPid)) { continue }
        if (-not $customFieldsByPid.ContainsKey($cpPid)) { $customFieldsByPid[$cpPid] = @{} }
        foreach ($f in (Get-CustomFieldNodes -Node $cp)) {
            if ([string]::IsNullOrWhiteSpace($f.name)) { continue }
            $key = if ($f.id) { "$($f.name) [id=$($f.id)]" } else { $f.name }
            $customFieldsByPid[$cpPid][$key] = $true
        }
    }

    # --- Readable catalog (printed to logs for enumeration) ---------------------
    Write-Host ''
    Write-Host '================ WHMCS PRODUCT CATALOG ================'
    foreach ($p in ($productsFlat | Sort-Object { [int]($_.gid) }, { [int]($_.pid) })) {
        $cycle = @()
        if ($p.has_monthly) { $cycle += 'monthly' }
        if ($p.has_annually) { $cycle += 'annually' }
        $cycleStr = if ($cycle.Count) { ($cycle -join ',') } else { 'one-time/other' }
        Write-Host ("pid={0,-4} gid={1,-3} type={2,-12} module={3,-14} [{4}] {5}" -f `
                $p.pid, $p.gid, $p.type, ($p.module ? $p.module : '-'), $cycleStr, $p.name)
        $pidKey = [string]$p.pid
        if ($customFieldsByPid.ContainsKey($pidKey) -and $customFieldsByPid[$pidKey].Keys.Count -gt 0) {
            foreach ($cf in ($customFieldsByPid[$pidKey].Keys | Sort-Object)) {
                Write-Host ("        custom-field: {0}" -f $cf)
            }
        }
    }
    Write-Host '======================================================='
    Write-Host ''

    Write-Host "Exported products: $($productsFlat.Count) -> $ProductsOutputFile"
    Write-Host "Exported client products: $($clientProductsFlat.Count) -> $ClientProductsOutputFile"
}
catch {
    Write-Error $_
    exit 1
}
