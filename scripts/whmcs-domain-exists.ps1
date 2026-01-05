[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

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
            $msg = 'Unknown WHMCS API error.' + (if ($diag) { " Response: $diag" } else { '' })
        }
        throw "WHMCS API error: $msg"
    }

    return $resp
}

function Normalize-DomainName {
    param([Parameter(Mandatory = $true)][string]$Value)

    $d = $Value.Trim().ToLowerInvariant()
    if ($d.StartsWith('http://') -or $d.StartsWith('https://')) {
        try {
            $u = [uri]$d
            $d = $u.Host
        }
        catch {
        }
    }
    return $d.Trim('.')
}

function Find-WhmcsDomain {
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$AuthBody,
        [Parameter(Mandatory = $true)][string]$DomainName,
        [Parameter(Mandatory = $true)][int]$PageSize
    )

    $start = 0
    while ($true) {
        $body = @{}
        foreach ($k in $AuthBody.Keys) { $body[$k] = $AuthBody[$k] }
        $body.action = 'GetClientsDomains'
        $body.responsetype = 'json'
        $body.limitstart = $start
        $body.limitnum = $PageSize

        $r = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

        $domains = @()
        if ($r.domains) {
            if ($r.domains.domain) { $domains = @($r.domains.domain) }
            elseif ($r.domains -is [System.Array]) { $domains = @($r.domains) }
        }
        else {
            $rx = '^domains\[domain\]\[(\d+)\]\[([^\]]+)\]$'
            $byIndex = @{}
            foreach ($prop in $r.PSObject.Properties) {
                $m = [regex]::Match($prop.Name, $rx)
                if (-not $m.Success) { continue }

                $idx = [int]$m.Groups[1].Value
                $field = $m.Groups[2].Value

                if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
                $byIndex[$idx][$field] = $prop.Value
            }
            foreach ($idx in ($byIndex.Keys | Sort-Object)) {
                $domains += [PSCustomObject]$byIndex[$idx]
            }
        }

        foreach ($d in $domains) {
            $name = $null
            try { $name = [string]$d.domainname } catch {}
            if ([string]::IsNullOrWhiteSpace($name)) {
                try { $name = [string]$d.domain } catch {}
            }
            if ([string]::IsNullOrWhiteSpace($name)) { continue }

            if ((Normalize-DomainName -Value $name) -eq $DomainName) {
                return $d
            }
        }

        $returned = $domains.Count
        if ($returned -le 0) { break }

        $start += $returned
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    return $null
}

try {
    $domainName = Normalize-DomainName -Value $Domain
    if ([string]::IsNullOrWhiteSpace($domainName)) { throw 'Domain is required.' }

    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $auth = @{
        identifier = $creds.Identifier
        secret = $creds.Secret
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) {
        $auth.accesskey = $accessKey
    }

    $existing = Find-WhmcsDomain -ApiUrl $api -AuthBody $auth -DomainName $domainName -PageSize $PageSize
    if (-not $existing) {
        [pscustomobject]@{ domain = $domainName; found = $false; domainId = $null } | ConvertTo-Json -Depth 6
        exit 0
    }

    $domainId = $null
    try { $domainId = [string]$existing.id } catch {}
    if ([string]::IsNullOrWhiteSpace($domainId)) {
        try { $domainId = [string]$existing.domainid } catch {}
    }

    [pscustomobject]@{ domain = $domainName; found = $true; domainId = $domainId } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
