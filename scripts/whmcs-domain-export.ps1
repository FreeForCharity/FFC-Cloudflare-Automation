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
    [string]$OutputFile = 'whmcs_domains.csv',

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

    $headers = @{
        'Accept'     = 'application/json'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($resp -is [string]) {
        $raw = $resp
        try {
            $resp = $raw | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
            throw "WHMCS API returned a non-JSON response: $snippet"
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

function ConvertTo-Scalar {
    param($Value)

    if ($null -eq $Value) { return $null }

    if ($Value -is [string] -or $Value -is [ValueType]) {
        return $Value
    }

    try {
        return ($Value | ConvertTo-Json -Depth 10 -Compress)
    }
    catch {
        return $Value.ToString()
    }
}

function Normalize-WhmcsFlatFieldName {
    param([string]$Field)

    if ([string]::IsNullOrWhiteSpace($Field)) { return $Field }

    $n = $Field
    $n = $n -replace '\]\[', '.'
    $n = $n -replace '\[', '.'
    $n = $n -replace '\]', ''
    $n = $n.Trim('.')

    return $n
}

function Get-WhmcsDomainsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)

    # Preferred: structured response
    if ($Response.domains -and $Response.domains.domain) {
        return @($Response.domains.domain)
    }

    if ($Response.domains -is [System.Array]) {
        return @($Response.domains)
    }

    # Fallback: WHMCS sometimes returns "flat-key" JSON like domains[domain][0][field]
    $matches = @()
    foreach ($prop in @($Response.PSObject.Properties)) {
        if ($prop.Name -match '^domains\[domain\]\[(\d+)\]\[(.+)\]$') {
            $matches += [PSCustomObject]@{ idx = [int]$Matches[1]; field = $Matches[2]; value = $prop.Value }
        }
    }

    if ($matches.Count -eq 0) { return @() }

    $byIndex = @{}
    foreach ($m in $matches) {
        $idx = $m.idx
        if (-not $byIndex.ContainsKey($idx)) {
            $byIndex[$idx] = [ordered]@{ __index = $idx }
        }

        $fieldName = Normalize-WhmcsFlatFieldName -Field $m.field
        $byIndex[$idx][$fieldName] = ConvertTo-Scalar -Value $m.value
    }

    return @($byIndex.GetEnumerator() | Sort-Object Name | ForEach-Object { [PSCustomObject]$_.Value })
}

function ConvertTo-DomainRow {
    param(
        [Parameter(Mandatory = $true)]
        $Domain
    )

    $row = [ordered]@{}

    foreach ($p in @($Domain.PSObject.Properties)) {
        if ($p.Name -eq '__index') { continue }
        $row[$p.Name] = ConvertTo-Scalar -Value $p.Value
    }

    return [PSCustomObject]$row
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $allDomains = @()

    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetClientsDomains'
            responsetype = 'json'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $domains = Get-WhmcsDomainsFromResponse -Response $r
        if ($domains.Count -le 0) { break }

        $allDomains += ($domains | ForEach-Object { ConvertTo-DomainRow -Domain $_ })

        $numReturnedApi = 0
        if ($r.numreturned) { [void][int]::TryParse($r.numreturned.ToString(), [ref]$numReturnedApi) }
        $start += (if ($numReturnedApi -gt 0) { $numReturnedApi } else { $domains.Count })

        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }

        # If WHMCS ignored limitstart/limitnum and keeps returning the same block, avoid an infinite loop.
        if ($total -eq 0 -and $start -gt 50000) { break }
    }

    $allDomains | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8

    Write-Host "Exported $($allDomains.Count) domains to $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
