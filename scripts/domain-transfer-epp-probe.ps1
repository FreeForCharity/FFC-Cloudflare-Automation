<#
.SYNOPSIS
    Probe whether a domain's EPP/auth (transfer) code is returned in a
    copy-pasteable form by the WHMCS API, or only emailed to the registrant.

.DESCRIPTION
    Answers a single operational question for the eNOM -> Cloudflare transfer
    project (#157): when we ask for the transfer auth code, do we get it back
    inline (so an admin can paste it into the Cloudflare dashboard), or does the
    registrar only email it to the registrant?

    WHMCS 'DomainRequestEPP' returns an 'eppcode' field ONLY if the registrar
    module supports inline return; otherwise it returns success with no code and
    the registrar emails it. This script calls that endpoint for ONE domain and
    reports which behavior occurs.

    SAFETY: calling DomainRequestEPP has a side effect (it may trigger the
    registrar to email the registrant), so it does NOTHING live unless you pass
    -Execute. Without -Execute it only resolves the domain and explains what it
    would do.

    By default the actual code is NEVER printed (only whether it was returned and
    its length). Pass -ShowCode to include the literal code in the JSON output
    (avoid this in shared CI logs).

.PARAMETER Domain
    The domain to probe (must exist in WHMCS).

.PARAMETER Execute
    Actually call DomainRequestEPP. Without this, the probe is a dry run.

.PARAMETER ShowCode
    Include the literal EPP code in the output. Off by default.

.PARAMETER ApiUrl / Identifier / Secret / CredentialsJson / AccessKey
    WHMCS API connection details. Same convention as the other whmcs-*.ps1
    scripts (env: WHMCS_API_URL / WHMCS_API_IDENTIFIER / WHMCS_API_SECRET /
    WHMCS_API_CREDENTIALS_JSON / WHMCS_API_ACCESS_KEY).

.OUTPUTS
    A single JSON object on stdout.

.EXAMPLE
    # Dry run (no side effects):
    pwsh -File scripts/domain-transfer-epp-probe.ps1 -Domain example.org

.EXAMPLE
    # Actually request the code and report inline-vs-email:
    pwsh -File scripts/domain-transfer-epp-probe.ps1 -Domain example.org -Execute
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [switch]$Execute,

    [Parameter()]
    [switch]$ShowCode,

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

function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Resolve-WhmcsCredentials {
    param([string]$IdentifierParam, [string]$SecretParam, [string]$CredentialsJsonParam)

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
        if ([string]::IsNullOrWhiteSpace($obj.identifier) -or [string]::IsNullOrWhiteSpace($obj.secret)) {
            throw 'WHMCS_API_CREDENTIALS_JSON must contain fields "identifier" and "secret".'
        }
        return @{ Identifier = $obj.identifier; Secret = $obj.secret }
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
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Body
    )

    $headers = @{
        'Accept'     = 'application/json'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($resp -is [string]) {
        $raw = $resp
        try { $resp = $raw | ConvertFrom-Json -ErrorAction Stop }
        catch {
            $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
            throw "WHMCS API returned a non-JSON response: $snippet"
        }
    }
    if (-not $resp) { throw 'WHMCS API returned an empty response.' }

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

function Normalize-DomainName {
    param([Parameter(Mandatory = $true)][string]$Value)
    $d = $Value.Trim().ToLowerInvariant()
    if ($d.StartsWith('http://') -or $d.StartsWith('https://')) {
        try { $d = ([uri]$d).Host } catch {}
    }
    return $d.Trim('.')
}

function Get-WhmcsDomainsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.domains) {
        if ($Response.domains.domain) { return @($Response.domains.domain) }
        if ($Response.domains -is [System.Array]) { return @($Response.domains) }
    }
    $rx = '^domains\[domain\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}
    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }
        $idx = [int]$m.Groups[1].Value
        $field = $m.Groups[2].Value
        if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
        $byIndex[$idx][$field] = $prop.Value
    }
    $domains = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) { $domains += [PSCustomObject]$byIndex[$idx] }
    return $domains
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
        $domains = Get-WhmcsDomainsFromResponse -Response $r

        foreach ($d in $domains) {
            $name = $null
            try { $name = [string]$d.domainname } catch {}
            if ([string]::IsNullOrWhiteSpace($name)) { try { $name = [string]$d.domain } catch {} }
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            if ((Normalize-DomainName -Value $name) -eq $DomainName) { return $d }
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

    $auth = @{ identifier = $creds.Identifier; secret = $creds.Secret }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $auth.accesskey = $accessKey }

    $existing = Find-WhmcsDomain -ApiUrl $api -AuthBody $auth -DomainName $domainName -PageSize $PageSize
    if (-not $existing) { throw "Domain '$domainName' not found in WHMCS (GetClientsDomains)." }

    $domainId = $null
    try { $domainId = [string]$existing.id } catch {}
    if ([string]::IsNullOrWhiteSpace($domainId)) { try { $domainId = [string]$existing.domainid } catch {} }
    if ([string]::IsNullOrWhiteSpace($domainId)) { throw 'WHMCS domain record did not include an id/domainid field.' }

    $registrar = $null
    try { $registrar = [string]$existing.registrar } catch {}

    $result = [ordered]@{
        domain          = $domainName
        domainId        = $domainId
        registrar       = $registrar
        executed        = [bool]$Execute
        deliveredInline = $null
        eppPresent      = $null
        eppLength       = $null
        eppCode         = $null
        message         = $null
    }

    if (-not $Execute) {
        $result.message = "DRY RUN: would call WHMCS DomainRequestEPP for domainId=$domainId. This may email the registrant. Re-run with -Execute to probe inline-vs-email delivery."
        Write-Diag $result.message
        ([pscustomobject]$result) | ConvertTo-Json -Depth 6
        exit 0
    }

    $body = @{}
    foreach ($k in $auth.Keys) { $body[$k] = $auth[$k] }
    $body.action = 'DomainRequestEPP'
    $body.responsetype = 'json'
    $body.domainid = $domainId

    Write-Diag "[LIVE] Requesting EPP code for '$domainName' (domainId=$domainId)..."
    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body

    $epp = $null
    foreach ($n in @('eppcode', 'eppCode', 'epp')) {
        $p = $resp.PSObject.Properties[$n]
        if ($p -and -not [string]::IsNullOrWhiteSpace([string]$p.Value)) { $epp = [string]$p.Value; break }
    }

    $present = -not [string]::IsNullOrWhiteSpace($epp)
    $result.deliveredInline = $present
    $result.eppPresent = $present
    $result.eppLength = if ($present) { $epp.Length } else { 0 }
    if ($present -and $ShowCode) { $result.eppCode = $epp }

    $result.message = if ($present) {
        'EPP code returned INLINE in the API response (copy-pasteable).' + $(if (-not $ShowCode) { ' Value hidden; re-run with -ShowCode to reveal.' } else { '' })
    }
    else {
        'No EPP code in the API response. The registrar likely EMAILED it to the registrant (not copy-pasteable via the API).'
    }
    Write-Diag $result.message

    ([pscustomobject]$result) | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
