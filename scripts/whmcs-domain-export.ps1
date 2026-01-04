[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$CredentialsJson,

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
        [string]$SecretParam,
        [string]$CredentialsJsonParam
    )

    $id = if ($IdentifierParam) { $IdentifierParam } else { $env:WHMCS_API_IDENTIFIER }
    $sec = if ($SecretParam) { $SecretParam } else { $env:WHMCS_API_SECRET }

    if (-not [string]::IsNullOrWhiteSpace($id) -and -not [string]::IsNullOrWhiteSpace($sec)) {
        return @{ Identifier = $id; Secret = $sec }
    }

    $json = if ($CredentialsJsonParam) { $CredentialsJsonParam } else { $env:WHMCS_API_CREDENTIALS_JSON }
    if ([string]::IsNullOrWhiteSpace($json)) {
        throw 'Missing WHMCS credentials. Provide -Identifier/-Secret, set WHMCS_API_IDENTIFIER/WHMCS_API_SECRET, or set WHMCS_API_CREDENTIALS_JSON.'
    }

    $jsonTrim = $json.Trim()

    if ($jsonTrim.StartsWith('{')) {
        $obj = $jsonTrim | ConvertFrom-Json -ErrorAction Stop
        $jid = $obj.identifier
        $jsec = $obj.secret
        if ([string]::IsNullOrWhiteSpace($jid) -or [string]::IsNullOrWhiteSpace($jsec)) {
            throw 'WHMCS_API_CREDENTIALS_JSON must contain fields "identifier" and "secret".'
        }
        return @{ Identifier = $jid; Secret = $jsec }
    }

    if ($jsonTrim -match '^([^:]+):(.+)$') {
        return @{ Identifier = $Matches[1]; Secret = $Matches[2] }
    }

    throw 'WHMCS_API_CREDENTIALS_JSON must be JSON (identifier/secret) or in the format "identifier:secret".'
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

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if (-not $resp) {
        throw 'WHMCS API returned an empty response.'
    }

    if ($resp.result -ne 'success') {
        $msg = $null
        if ($resp.message) { $msg = $resp.message }
        elseif ($resp.errormessage) { $msg = $resp.errormessage }
        elseif ($resp.error) { $msg = $resp.error }
        if ([string]::IsNullOrWhiteSpace($msg)) { $msg = 'Unknown WHMCS API error.' }
        throw "WHMCS API error: $msg"
    }

    return $resp
}

function Get-WhmcsDomainsFromResponse {
    param(
        [Parameter(Mandatory = $true)]
        $Response
    )

    # Newer/cleaner response format (nested arrays)
    if ($Response.domains) {
        if ($Response.domains.domain) {
            return @($Response.domains.domain)
        }
        if ($Response.domains -is [System.Array]) {
            return @($Response.domains)
        }
    }

    # WHMCS API documentation shows a flat JSON response with keys like:
    #   "domains[domain][0][domainname]": "example.com"
    # Convert that into a list of objects.
    $rx = '^domains\[domain\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}

    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }

        $idx = [int]$m.Groups[1].Value
        $field = $m.Groups[2].Value

        if (-not $byIndex.ContainsKey($idx)) {
            $byIndex[$idx] = @{}
        }

        $byIndex[$idx][$field] = $prop.Value
    }

    if ($byIndex.Count -le 0) {
        return @()
    }

    $domains = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) {
        $domains += [PSCustomObject]$byIndex[$idx]
    }

    return $domains
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $all = @()
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

        if (-not [string]::IsNullOrWhiteSpace($accessKey)) {
            $body.accesskey = $accessKey
        }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body

        $total = 0
        if ($r.totalresults) {
            [void][int]::TryParse($r.totalresults.ToString(), [ref]$total)
        }

        $numReturnedApi = 0
        if ($r.numreturned) {
            [void][int]::TryParse($r.numreturned.ToString(), [ref]$numReturnedApi)
        }

        $domains = Get-WhmcsDomainsFromResponse -Response $r

        $all += $domains

        $returned = $domains.Count
        if ($returned -le 0) { break }

        if ($numReturnedApi -gt 0) {
            $start += $numReturnedApi
        }
        else {
            $start += $returned
        }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # Normalize and export ALL fields returned by WHMCS.
    # Note: Export-Csv uses the first object's properties as the schema, so we build a stable, complete schema.
    $domainsNormalized = $all | ForEach-Object {
        $obj = $_
        $domainName = if ($obj.domainname) { $obj.domainname } else { $obj.domain }

        if (-not $obj.PSObject.Properties['domain'] -and -not [string]::IsNullOrWhiteSpace($domainName)) {
            $obj | Add-Member -NotePropertyName domain -NotePropertyValue $domainName -Force
        }
        $obj
    } | Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) }

    $preferredColumns = @(
        'domain',
        'domainname',
        'id',
        'userid',
        'status',
        'regtype',
        'registrar',
        'regdate',
        'expirydate',
        'nextduedate',
        'firstpaymentamount',
        'recurringamount',
        'paymentmethod',
        'paymentmethodname',
        'regperiod'
    )

    $allColumnsSet = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($d in $domainsNormalized) {
        foreach ($p in $d.PSObject.Properties) {
            [void]$allColumnsSet.Add($p.Name)
        }
    }

    $extraColumns = @($allColumnsSet | Where-Object { $_ -notin $preferredColumns } | Sort-Object)
    $columns = @($preferredColumns | Where-Object { $allColumnsSet.Contains($_) }) + $extraColumns

    $rows = foreach ($d in $domainsNormalized) {
        $row = [ordered]@{}
        foreach ($c in $columns) {
            $prop = $d.PSObject.Properties[$c]
            $row[$c] = if ($null -ne $prop) { $prop.Value } else { $null }
        }
        [PSCustomObject]$row
    }

    $rows | Sort-Object domain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

    Write-Host ("Exported {0} domain(s) with {1} column(s) to {2}" -f ($rows | Measure-Object).Count, $columns.Count, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
