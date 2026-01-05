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
    [string]$OutputFile = 'whmcs_clients.csv',

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
    $s = [string]$Node
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return $s
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $clients = @()

    $start = 0
    $total = $null

    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetClients'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
            sorting      = 'ASC'
        }

        if ($accessKey) { $body.accesskey = $accessKey }

        $resp = Invoke-WhmcsApiXml -ApiUrl $api -Body $body

        if ($null -eq $total) {
            $totalText = Get-Text -Node $resp.totalresults
            if (-not [string]::IsNullOrWhiteSpace($totalText)) {
                $total = [int]$totalText
            }
        }

        $batch = @()
        if ($resp.clients -and $resp.clients.client) {
            $batch = @($resp.clients.client)
        }

        foreach ($c in $batch) {
            $clients += [pscustomobject]@{
                clientid    = Get-Text $c.id
                firstname   = Get-Text $c.firstname
                lastname    = Get-Text $c.lastname
                companyname = Get-Text $c.companyname
                email       = Get-Text $c.email
                address1    = Get-Text $c.address1
                address2    = Get-Text $c.address2
                city        = Get-Text $c.city
                state       = Get-Text $c.state
                postcode    = Get-Text $c.postcode
                country     = Get-Text $c.country
                phonenumber = Get-Text $c.phonenumber
                status      = Get-Text $c.status
                datecreated = Get-Text $c.datecreated
            }
        }

        if ($batch.Count -lt $PageSize) { break }
        $start += $PageSize

        if ($total -and $start -ge $total) { break }
    }

    $clients | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile

    Write-Host "Exported $($clients.Count) clients to $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
