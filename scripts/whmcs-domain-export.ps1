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

        $domains = @()
        if ($r.domains) {
            if ($r.domains.domain) {
                $domains = @($r.domains.domain)
            }
            elseif ($r.domains -is [System.Array]) {
                $domains = @($r.domains)
            }
        }

        $all += $domains

        $returned = $domains.Count
        if ($returned -le 0) { break }

        $start += $returned
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $rows = $all | ForEach-Object {
        [PSCustomObject]@{
            domain = $_.domain
            status = $_.status
            id     = $_.id
            userid = $_.userid
        }
    } | Where-Object { -not [string]::IsNullOrWhiteSpace($_.domain) }

    $rows | Sort-Object domain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

    Write-Host ("Exported {0} domain(s) to {1}" -f ($rows | Measure-Object).Count, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
