<#
.SYNOPSIS
    Shared WHMCS API helpers (credential resolution + request execution).

.DESCRIPTION
    Dot-source from a script: . (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

    Mirrors the conventions already used by the self-contained export scripts in
    this repo so behaviour is identical:
      - credentials from -Identifier/-Secret, env vars, or WHMCS_API_CREDENTIALS_JSON
      - API URL from -ApiUrl, WHMCS_API_URL, or the FFC default endpoint
      - optional access key
      - JSON responsetype with throw-on-failure error handling
#>

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

function ConvertTo-WhmcsCustomFields {
    # Builds base64(serialize(array(id => value))) as WHMCS expects for the
    # `customfields` parameter on AddClient / AddOrder. -Json must be a JSON
    # object whose keys are numeric custom-field ids, e.g. {"1":"value"}.
    param([Parameter(Mandatory = $true)][string]$Json)

    $obj = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($obj -is [System.Array]) {
        throw 'Custom fields JSON must be a JSON object mapping numeric field ids to values (e.g. {"1":"value"}), not an array.'
    }
    $pairs = @($obj.PSObject.Properties)
    foreach ($p in $pairs) {
        $idCheck = 0
        if (-not [int]::TryParse([string]$p.Name, [ref]$idCheck)) {
            throw "Custom fields JSON keys must be numeric WHMCS custom-field ids; got '$($p.Name)'."
        }
    }

    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append("a:$($pairs.Count):{")
    foreach ($p in $pairs) {
        $key = [int]$p.Name
        $val = [string]$p.Value
        $valBytes = [System.Text.Encoding]::UTF8.GetByteCount($val)
        [void]$sb.Append("i:$key;s:$valBytes`:`"$val`";")
    }
    [void]$sb.Append('}')
    $serialized = $sb.ToString()
    return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($serialized))
}

function New-WhmcsAuthBody {
    # Base request body with auth fields (for read lookups).
    param(
        [Parameter(Mandatory = $true)][hashtable]$Creds,
        [string]$AccessKey
    )
    $b = @{
        identifier   = $Creds.Identifier
        secret       = $Creds.Secret
        responsetype = 'json'
    }
    if (-not [string]::IsNullOrWhiteSpace($AccessKey)) { $b.accesskey = $AccessKey }
    return $b
}

function Find-WhmcsClientIdByEmail {
    # Returns the clientid of an existing client whose email matches exactly
    # (case-insensitive), or $null. Used for onboarding idempotency.
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][string]$Email
    )
    $target = $Email.Trim().ToLowerInvariant()
    $body = $Auth.Clone()
    $body.action = 'GetClients'
    $body.search = $Email
    $body.limitnum = 250
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    $clients = @()
    if ($resp.clients -and $resp.clients.client) { $clients = @($resp.clients.client) }
    elseif ($resp.clients -is [System.Array]) { $clients = @($resp.clients) }

    foreach ($c in $clients) {
        $e = $null
        try { $e = [string]$c.email } catch {}
        if ($e -and $e.Trim().ToLowerInvariant() -eq $target) {
            return [string]$c.id
        }
    }
    return $null
}

function Test-WhmcsClientHasProduct {
    # True if the client already has a non-terminated service for the product id.
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][int]$ClientId,
        [Parameter(Mandatory = $true)][int]$ProductId
    )
    $body = $Auth.Clone()
    $body.action = 'GetClientsProducts'
    $body.clientid = $ClientId
    $body.pid = $ProductId
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    $products = @()
    if ($resp.products -and $resp.products.product) { $products = @($resp.products.product) }
    elseif ($resp.products -is [System.Array]) { $products = @($resp.products) }

    $dead = @('Cancelled', 'Terminated', 'Fraud')
    foreach ($p in $products) {
        $pid = $null; $status = $null
        try { $pid = [string]$p.pid } catch {}
        try { $status = [string]$p.status } catch {}
        if ($pid -eq [string]$ProductId -and ($dead -notcontains $status)) {
            return $true
        }
    }
    return $false
}

function Find-WhmcsContactIdByEmail {
    # Returns the contactid of an existing contact (under the client) whose email
    # matches exactly (case-insensitive), or $null.
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][int]$ClientId,
        [Parameter(Mandatory = $true)][string]$Email
    )
    $target = $Email.Trim().ToLowerInvariant()
    $body = $Auth.Clone()
    $body.action = 'GetContacts'
    $body.userid = $ClientId
    $body.email = $Email
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    $contacts = @()
    if ($resp.contacts -and $resp.contacts.contact) { $contacts = @($resp.contacts.contact) }
    elseif ($resp.contacts -is [System.Array]) { $contacts = @($resp.contacts) }

    foreach ($c in $contacts) {
        $e = $null
        try { $e = [string]$c.email } catch {}
        if ($e -and $e.Trim().ToLowerInvariant() -eq $target) {
            return [string]$c.id
        }
    }
    return $null
}
