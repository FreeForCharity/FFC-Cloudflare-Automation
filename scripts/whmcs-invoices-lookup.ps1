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

    [Parameter(Mandatory = $true, ParameterSetName = 'Ids')]
    [string[]]$InvoiceId,

    [Parameter(Mandatory = $true, ParameterSetName = 'FromFile')]
    [string]$InvoiceIdFile,

    [Parameter()]
    [string]$OutputFile = 'whmcs_invoices.csv',

    [Parameter()]
    [ValidateRange(0, 2000)]
    [int]$SleepMs = 150
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

function New-DirectoryForFile {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function ConvertFrom-WhmcsXml {
    param([Parameter(Mandatory = $true)][string]$RawXml)

    $clean = $RawXml -replace "[\x00-\x08\x0B\x0C\x0E-\x1F]", ''
    try {
        return [xml]$clean
    }
    catch {
        $snippet = if ($clean.Length -gt 400) { $clean.Substring(0, 400) + '...' } else { $clean }
        throw "Failed to parse WHMCS XML response. Snippet: $snippet"
    }
}

function Invoke-WhmcsApiXml {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,

        [Parameter(Mandatory = $true)]
        [hashtable]$Body
    )

    $headers = @{
        'Accept'     = '*/*'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($resp -is [xml]) {
        $xml = $resp
    }
    elseif ($resp -is [string]) {
        $xml = ConvertFrom-WhmcsXml -RawXml $resp
    }
    else {
        $raw = $resp | Out-String
        $xml = ConvertFrom-WhmcsXml -RawXml $raw
    }

    $root = $xml.whmcsapi
    if (-not $root) {
        throw 'WHMCS API returned XML but did not contain <whmcsapi> root.'
    }

    if ($root.result -ne 'success') {
        $msg = $null
        if ($root.message) { $msg = [string]$root.message }
        elseif ($root.errormessage) { $msg = [string]$root.errormessage }
        if ([string]::IsNullOrWhiteSpace($msg)) { $msg = 'Unknown WHMCS API error.' }
        throw "WHMCS API error: $msg"
    }

    return $root
}

function Get-Text {
    param($Node)

    if (-not $Node) { return $null }
    if ($Node -is [System.Xml.XmlNode]) {
        $s = $Node.InnerText
    }
    else {
        $s = [string]$Node
    }
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return $s
}

function Get-Invoice {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,
        [Parameter(Mandatory = $true)]
        [hashtable]$Creds,
        [Parameter(Mandatory = $true)]
        [string]$InvoiceId,
        [string]$AccessKey
    )

    $body = @{
        identifier   = $Creds.Identifier
        secret       = $Creds.Secret
        action       = 'GetInvoice'
        responsetype = 'xml'
        invoiceid    = $InvoiceId
    }

    if ($AccessKey) { $body.accesskey = $AccessKey }
    return Invoke-WhmcsApiXml -ApiUrl $ApiUrl -Body $body
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $ids = @()
    if ($PSCmdlet.ParameterSetName -eq 'FromFile') {
        if (-not (Test-Path $InvoiceIdFile)) { throw "InvoiceIdFile not found: $InvoiceIdFile" }
        $ids = Get-Content -LiteralPath $InvoiceIdFile | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
    else {
        $ids = $InvoiceId | ForEach-Object { [string]$_ } | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }

    $ids = $ids | Sort-Object -Unique
    if ($ids.Count -eq 0) { throw 'No invoice IDs provided.' }

    New-DirectoryForFile -Path $OutputFile

    $rows = @()

    foreach ($id in $ids) {
        if ($SleepMs -gt 0) { Start-Sleep -Milliseconds $SleepMs }

        try {
            $resp = Get-Invoice -ApiUrl $api -Creds $creds -InvoiceId $id -AccessKey $accessKey

            $inv = $resp.invoice

            $rows += [pscustomobject]@{
                invoiceid     = $id
                userid        = Get-Text $inv.userid
                status        = Get-Text $inv.status
                date          = Get-Text $inv.date
                duedate       = Get-Text $inv.duedate
                total         = Get-Text $inv.total
                paymentmethod = Get-Text $inv.paymentmethod
                firstname     = Get-Text $inv.firstname
                lastname      = Get-Text $inv.lastname
                companyname   = Get-Text $inv.companyname
                email         = Get-Text $inv.email
                address1      = Get-Text $inv.address1
                address2      = Get-Text $inv.address2
                city          = Get-Text $inv.city
                state         = Get-Text $inv.state
                postcode      = Get-Text $inv.postcode
                country       = Get-Text $inv.country
            }
        }
        catch {
            $rows += [pscustomobject]@{
                invoiceid     = $id
                userid        = $null
                status        = 'ERROR'
                date          = $null
                duedate       = $null
                total         = $null
                paymentmethod = $null
                firstname     = $null
                lastname      = $null
                companyname   = $null
                email         = $null
                address1      = $null
                address2      = $null
                city          = $null
                state         = $null
                postcode      = $null
                country       = $null
            }
            Write-Warning "Failed to fetch invoice ${id}: $($_.Exception.Message)"
        }
    }

    $rows | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile
    Write-Host "Exported $($rows.Count) invoices to $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
