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

function Get-WhmcsTransientRemedy {
    <#
    .SYNOPSIS
        Return the FIXED remediation sentence for a transient label, for the terminal throw.

    .DESCRIPTION
        #1774. The exhaustion throw used to assert one cause for every label - "the
        host's Imunify360 bot-protection is challenging the runner IP ... whitelist
        GitHub Actions egress" - which is right for a bot-protection challenge and
        wrong for everything else. A dropped connection, in particular, is a transport
        failure: sending the operator to the Imunify360 allowlist costs a detour before
        anyone looks at APIM, Cloudflare or the origin.

        Keyed on the label's PREFIX, because the non-JSON path appends
        " (non-JSON response)". Like Get-WhmcsTransientLabel, every return value is a
        literal and never any part of $Reason, so the #1753 log-safety property holds.
    #>
    param([AllowNull()][string]$Reason)

    if ([string]::IsNullOrWhiteSpace($Reason)) { return 'Re-run the workflow.' }
    if ($Reason.StartsWith('bot-protection challenge')) {
        return "The host's Imunify360 bot-protection is challenging the runner IP; re-run the workflow or whitelist GitHub Actions egress."
    }
    if ($Reason.StartsWith('connection dropped')) {
        return 'The connection closed before the response completed, which is a transport failure, not an IP block; re-run the workflow, and if it persists check the APIM gateway, Cloudflare and the WHMCS origin.'
    }
    if ($Reason.StartsWith('rate limited')) {
        return 'The host is rate limiting requests; wait, then re-run the workflow.'
    }
    if ($Reason.StartsWith('gateway error') -or $Reason.StartsWith('service unavailable') -or $Reason.StartsWith('request timed out')) {
        return 'The WHMCS host or the gateway in front of it is failing or slow; re-run the workflow, and if it persists check WHMCS and APIM health.'
    }
    return 'Re-run the workflow.'
}

function Get-WhmcsTransientLabel {
    <#
    .SYNOPSIS
        Classify text as a transient WHMCS/transport failure and return a FIXED label.

    .DESCRIPTION
        SECURITY (#1753). Duplicated from scripts/whmcs-api-common.ps1, which this
        script deliberately mirrors rather than dot-sources (it is self-contained, as
        its copies of Resolve-WhmcsCredentials and friends already are). Keep the two
        in step; scripts/tests/whmcs-api-transient-retry.Tests.ps1 asserts both files
        are free of the pre-fix shapes.

        The retry loop below logs its reason with Write-Warning and repeats it in the
        terminal throw. This repository is public, so those Actions logs are
        world-readable, and WHMCS responses carry ticket text, donor email addresses,
        client names and billing figures. This function returns a label from a closed
        set of literals and NEVER any part of $Text.

        The status-code rules are shape-anchored. The original signature ended in
        \b(429|502|503|504)\b, which - applied to a serialized JSON body - matched any
        numeric field holding one of those values (a ticket id, a client id, an
        invoice total), because ':' and '}' satisfy \b.

        Returns $null when nothing matches; callers must treat that as permanent.
    #>
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    # %S% is substituted with .Replace, not -f: the pattern contains [^0-9]{0,40},
    # and -f would read '{0,40}' as 'argument 0, width 40'.
    $statusShape = 'status code[^0-9]{0,40}%S%\b|returned an error:\s*\(?%S%\)?|HTTP(?:/[0-9.]+)?\s+%S%\b'

    $rules = [ordered]@{
        'bot-protection challenge' = 'Imunify360|bot-protection'
        'rate limited'             = 'too many requests|' + $statusShape.Replace('%S%', '429')
        'gateway error'            = 'Bad Gateway|Gateway Time-?out|' + $statusShape.Replace('%S%', '(?:502|504)')
        'service unavailable'      = 'Service Unavailable|temporarily unavailable|' + $statusShape.Replace('%S%', '503')
        # Subsumes .NET's 'The operation has timed out'.
        'request timed out'        = 'timed out'
    }

    foreach ($label in $rules.Keys) {
        if ($Text -match $rules[$label]) { return $label }
    }
    return $null
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

    $headers = @{
        'Accept'     = 'application/json'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    # When WHMCS is reached via APIM (apim-ffc-gateway-prod), its 'whmcs' API requires this key.
    if (-not [string]::IsNullOrWhiteSpace($env:WHMCS_APIM_SUBSCRIPTION_KEY)) {
        $headers['Ocp-Apim-Subscription-Key'] = $env:WHMCS_APIM_SUBSCRIPTION_KEY
    }

    # The WHMCS host's Imunify360 bot-protection intermittently challenges GitHub-hosted runner
    # IPs. Retry that transient signature (and a few transient HTTP conditions) with exponential
    # backoff, matching scripts/whmcs-api-common.ps1. Genuine auth/permission errors do not match
    # and still fail fast.
    #
    # SECURITY (#1753): $transientReason reaches a PUBLIC Actions log via the Write-Warning below
    # and the terminal throw, so it only ever holds a fixed label from Get-WhmcsTransientLabel -
    # never a response body, a field value or an exception string.
    $maxAttempts = 5
    $parsedMax = 0
    if ([int]::TryParse($env:WHMCS_API_MAX_ATTEMPTS, [ref]$parsedMax) -and $parsedMax -ge 1) {
        $maxAttempts = $parsedMax
    }
    $attempt = 0
    while ($true) {
        $attempt++
        $transientReason = $null
        try {
            $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

            if ($resp -is [string]) {
                $raw = $resp
                try {
                    $resp = $raw | ConvertFrom-Json -ErrorAction Stop
                }
                catch {
                    $rawLabel = Get-WhmcsTransientLabel $raw
                    if ($rawLabel) {
                        $transientReason = "$rawLabel (non-JSON response)"
                    }
                    else {
                        $snippet = if ($raw.Length -gt 400) { $raw.Substring(0, 400) + '...' } else { $raw }
                        throw "WHMCS API returned a non-JSON response: $snippet"
                    }
                }
            }

            if (-not $transientReason) {
                if (-not $resp) {
                    throw 'WHMCS API returned an empty response.'
                }

                if ($resp.result -ne 'success') {
                    $msg = $null
                    if ($resp.message) { $msg = $resp.message }
                    elseif ($resp.errormessage) { $msg = $resp.errormessage }
                    elseif ($resp.error) { $msg = $resp.error }

                    # Classify on the error MESSAGE only, never on a serialization of the
                    # response. A body that parsed as JSON is not a bot-protection challenge -
                    # those arrive as non-JSON and are handled above - so there is nothing
                    # transient to find by serializing it, and serializing it is what leaked
                    # it (#1753).
                    $msgLabel = Get-WhmcsTransientLabel $msg
                    if ($msgLabel) {
                        $transientReason = $msgLabel
                    }
                    else {
                        if ([string]::IsNullOrWhiteSpace($msg)) {
                            # Key NAMES are schema and stay; the body does not. Field values are
                            # donor and client data, and this string is logged (#1753).
                            $keys = @()
                            try { $keys = @($resp.PSObject.Properties | Select-Object -ExpandProperty Name) } catch {}
                            $keysText = if ($keys.Count -gt 0) { ($keys | Sort-Object) -join ', ' } else { '(no properties)' }
                            $msg = "Unknown WHMCS API error. Response keys: $keysText"
                        }
                        throw "WHMCS API error: $msg"
                    }
                }
            }

            if (-not $transientReason) {
                return $resp
            }
        }
        catch {
            # The label, not the exception text: a WebException message can carry a response
            # fragment, and this reason is logged (#1753).
            $emLabel = Get-WhmcsTransientLabel "$($_.Exception.Message)"
            if ($emLabel) {
                $transientReason = $emLabel
            }
            else {
                throw
            }
        }

        if ($attempt -ge $maxAttempts) {
            throw "WHMCS API failed after $maxAttempts attempts (transient: $transientReason). $(Get-WhmcsTransientRemedy $transientReason)"
        }
        $delay = [int][math]::Min(30, [math]::Pow(2, $attempt))
        Write-Warning "WHMCS API transient failure (attempt $attempt/$maxAttempts): $transientReason. Retrying in ${delay}s..."
        Start-Sleep -Seconds $delay
    }
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
