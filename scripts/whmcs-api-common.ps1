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
        SECURITY (#1753). The retry loop in Invoke-WhmcsApi logs its reason with
        Write-Warning and repeats it in the terminal throw. This repository is public,
        so those Actions logs are world-readable, and WHMCS responses carry ticket
        text, donor email addresses, client names and billing figures. This function
        therefore returns a label drawn from a closed set of literals and NEVER any
        part of $Text, so no caller can echo a response body or an exception string
        into a public log by routing it through here.

        The status-code rules are deliberately shape-anchored. The original signature
        ended in \b(429|502|503|504)\b, which - applied to a serialized JSON body -
        matched any numeric field holding one of those values (a ticket id, a client
        id, an invoice total), because ':' and '}' are non-word characters and so
        satisfy \b. An ordinary permanent error was then classified transient, retried
        five times, and its body printed on every attempt.

        Returns $null when nothing matches; callers must treat that as permanent.
    #>
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    # A status code counts only in a status-line shape. These are the three forms
    # .NET and the hosts actually produce:
    #   PS 7    Response status code does not indicate success: 502 (Bad Gateway).
    #   PS 5.1  The remote server returned an error: (502) Bad Gateway.
    #   raw     HTTP/1.1 503 Service Unavailable
    # %S% is substituted with .Replace, not the -f operator: the pattern contains
    # [^0-9]{0,40}, and -f would read '{0,40}' as 'argument 0, width 40'.
    $statusShape = 'status code[^0-9]{0,40}%S%\b|returned an error:\s*\(?%S%\)?|HTTP(?:/[0-9.]+)?\s+%S%\b'

    # Ordered so the most specific signature wins; every value here is a literal
    # label, which is the whole point.
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

function Get-WhmcsResponseShape {
    <#
    .SYNOPSIS
        Describe a WHMCS response by its FIELD NAMES, as a log-safe diagnostic.

    .DESCRIPTION
        SECURITY (#1753). An error about an unrecognised response reaches a public
        Actions log, so it must not serialize the response. Field names are schema;
        field values are ticket text, donor email addresses, client names and billing
        figures. This returns the former and never the latter.
    #>
    param([AllowNull()]$Response)

    if ($null -eq $Response) { return '' }
    # Nothing nameable, and a bare string is exactly the payload we must not echo.
    if ($Response -is [string] -or $Response -is [ValueType]) { return '' }

    $names = @()
    if ($Response -is [System.Collections.IDictionary]) {
        $names = @($Response.Keys | ForEach-Object { [string]$_ })
    }
    elseif ($Response -is [System.Collections.IEnumerable]) {
        return ''
    }
    else {
        $names = @($Response.PSObject.Properties | ForEach-Object { $_.Name })
    }

    if ($names.Count -eq 0) { return '' }
    # A field name is schema, but a WHMCS custom-field name can be long and the list
    # unbounded, so cap it rather than flood the log.
    $shown = @($names | Select-Object -First 20)
    $shape = $shown -join ', '
    if ($names.Count -gt $shown.Count) { $shape += ', ...' }
    if ($shape.Length -gt 300) { $shape = $shape.Substring(0, 300) + '...' }
    return $shape
}

function Test-WhmcsReadOnlyAction {
    # True for WHMCS API actions that only read (GetOrders, GetClientsProducts, ...).
    param([AllowNull()][string]$Action)
    if ([string]::IsNullOrWhiteSpace($Action)) { return $false }
    return ($Action -cmatch '^Get[A-Z]')
}

function Test-WhmcsDroppedConnection {
    # True when a transport exception says the connection closed mid-response.
    # Walks the InnerException chain: when the drop comes before the headers,
    # PowerShell 7 raises HttpRequestException("An error occurred while sending the
    # request.") and the "response ended prematurely" text is only on the inner
    # IOException. Returns a boolean only, so no exception text can reach a public
    # log from here.
    param([AllowNull()]$ErrorOrText)
    $pattern = 'response ended prematurely|ResponseEnded|connection was closed|forcibly closed by the remote host|connection reset'
    $texts = [System.Collections.Generic.List[string]]::new()
    if ($ErrorOrText -is [System.Management.Automation.ErrorRecord]) {
        # ErrorDetails is deliberately NOT read. On an HTTP error status
        # Invoke-RestMethod puts the RESPONSE BODY there, which would let a WHMCS or
        # Imunify360 error page decide this predicate by quoting one of the phrases
        # above back at us. A body is also proof the connection did not drop, so it
        # can only ever produce a false positive. Measured: the before-headers drop
        # carries its text on the inner exception, which the chain walk below reads,
        # so nothing genuine is lost by ignoring it.
        $ErrorOrText = $ErrorOrText.Exception
    }
    if ($ErrorOrText -is [System.Exception]) {
        $e = $ErrorOrText
        $depth = 0
        while ($e -and $depth -lt 10) { $texts.Add([string]$e.Message); $e = $e.InnerException; $depth++ }
    }
    elseif ($null -ne $ErrorOrText) {
        $texts.Add([string]$ErrorOrText)
    }
    foreach ($t in $texts) {
        if (-not [string]::IsNullOrWhiteSpace($t) -and $t -match $pattern) { return $true }
    }
    return $false
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

    # The WHMCS host's Imunify360 bot-protection intermittently challenges
    # GitHub-hosted runner IPs ("Access denied by Imunify360 bot-protection").
    # All WHMCS workflows hit the same endpoint, so the block is transient and
    # IP-dependent, not workflow-specific. Retry just that signature (and a few
    # transient HTTP conditions) with exponential backoff; genuine auth/permission
    # errors do not match and still fail fast.
    #
    # SECURITY (#1753): $transientReason is written to a PUBLIC Actions log by the
    # Write-Warning below and repeated in the terminal throw, so it only ever holds a
    # fixed label from Get-WhmcsTransientLabel - never a response body, a field value
    # or an exception string.
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

                    # Classify on the error MESSAGE only, never on a serialization of
                    # the response. A body that parsed as JSON is not a bot-protection
                    # challenge - those arrive as non-JSON and are handled above - so
                    # there is nothing transient to find by serializing it, and
                    # serializing it is what leaked it (#1753).
                    $msgLabel = Get-WhmcsTransientLabel $msg
                    if ($msgLabel) {
                        $transientReason = $msgLabel
                    }
                    else {
                        if ([string]::IsNullOrWhiteSpace($msg)) {
                            # Report the response's SHAPE. Field names are schema;
                            # field values are donor and client data, and this string
                            # is logged (#1753).
                            $shape = Get-WhmcsResponseShape $resp
                            $msg = 'Unknown WHMCS API error.' + $(if ($shape) { " Response fields: $shape" } else { '' })
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
            # The label, not the exception text: a WebException message can carry a
            # response fragment, and this reason is logged (#1753).
            $emLabel = Get-WhmcsTransientLabel "$($_.Exception.Message)"
            if ($emLabel) {
                $transientReason = $emLabel
            }
            elseif ((Test-WhmcsReadOnlyAction $Body.action) -and (Test-WhmcsDroppedConnection $_)) {
                # The connection closed before the body arrived ("The response ended
                # prematurely"). Retrying is safe only for a read: a dropped write may
                # already have taken effect in WHMCS, and repeating an OpenTicket or
                # AcceptOrder could act twice. Writes keep failing fast. (Run
                # 43832248360 lost a whole 7-order accept to one dropped GetClientsProducts.)
                $transientReason = 'connection dropped (read)'
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

function Get-WhmcsNodeList {
    # WHMCS wraps list payloads as {products:{product:[...]}} but degenerates to
    # a bare array, a single object, or an empty string. Normalize all of those.
    #
    # The empty-string form appears at BOTH levels - {products:""} and
    # {products:{product:""}} - so blank entries are filtered out of the list
    # itself, not just matched on the container. Letting a "" through yields a
    # one-item list holding an empty string, which reads downstream as a real
    # record whose every property is $null: an inflated scan count and silently
    # wrong output rather than a visible error.
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node -or $Node -is [string]) { return @() }

    $items = if ($Node -is [System.Array]) { $Node }
    elseif ($Node.PSObject.Properties[$ChildName]) { $Node.$ChildName }
    else { @() }

    return @($items | Where-Object {
            $null -ne $_ -and -not ($_ -is [string] -and [string]::IsNullOrWhiteSpace($_))
        })
}

function Invoke-WhmcsPagedFetch {
    <#
    .SYNOPSIS
        Fetch one page of a WHMCS list action, surviving records WHMCS cannot
        JSON-encode.

    .DESCRIPTION
        If any single record in a page holds bytes that are not valid UTF-8 (a
        mis-encoded character pasted into a client/custom field), PHP's
        json_encode fails for the ENTIRE response and WHMCS returns
        "Error generating JSON encoded response: Malformed UTF-8 characters".
        The failure is per-response, not per-record, so one poisoned row makes
        every page containing it unreadable - and with a 250-row page that is
        250 rows lost, which reads as "no match found" rather than as an error.

        So: on that specific error, bisect the window until the offending row is
        alone, skip only that row, and keep the rest of the sweep. Every other
        API error still fails fast. Returns Items / Total / SkippedIndexes so the
        caller can report what it could not read.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        # Auth + action fields; limitstart/limitnum are set per call from a clone.
        [Parameter(Mandatory = $true)][hashtable]$Body,
        [Parameter(Mandatory = $true)][int]$Start,
        [Parameter(Mandatory = $true)][int]$Num,
        [Parameter(Mandatory = $true)][string]$ListProperty,
        [Parameter(Mandatory = $true)][string]$ItemProperty
    )

    $page = $Body.Clone()
    $page.limitstart = $Start
    $page.limitnum = $Num

    try {
        $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $page
    }
    catch {
        if ("$($_.Exception.Message)" -notmatch 'Malformed UTF-8|Error generating JSON encoded response') { throw }
        if ($Num -le 1) {
            Write-Warning "WHMCS cannot JSON-encode the record at index ${Start} (malformed UTF-8 in a stored value); skipping it."
            return [pscustomobject]@{ Items = @(); Total = 0; SkippedIndexes = @($Start) }
        }
        $half = [int][math]::Floor($Num / 2)
        $left = Invoke-WhmcsPagedFetch -ApiUrl $ApiUrl -Body $Body -Start $Start -Num $half `
            -ListProperty $ListProperty -ItemProperty $ItemProperty
        $right = Invoke-WhmcsPagedFetch -ApiUrl $ApiUrl -Body $Body -Start ($Start + $half) -Num ($Num - $half) `
            -ListProperty $ListProperty -ItemProperty $ItemProperty
        return [pscustomobject]@{
            Items          = @($left.Items) + @($right.Items)
            Total          = [math]::Max($left.Total, $right.Total)
            SkippedIndexes = @($left.SkippedIndexes) + @($right.SkippedIndexes)
        }
    }

    $total = 0
    if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
    $node = if ($resp.PSObject.Properties[$ListProperty]) { $resp.$ListProperty } else { $null }
    return [pscustomobject]@{
        Items          = @(Get-WhmcsNodeList -Node $node -ChildName $ItemProperty)
        Total          = $total
        SkippedIndexes = @()
    }
}

# --- Disclosure classification -------------------------------------------
# ONE classifier for what may be printed from a charity's WHMCS record. The
# policy and its legal basis live in docs/pii-classification.md; this is its
# implementation. Do not add a second copy - two classifiers disagreeing is
# exactly how the EIN ended up masked by 221 and printed by 219.

function Format-MaskedName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $t = $Name.Trim()
    if ($t.Length -le 1) { return '***' }
    return $t.Substring(0, 1) + '***'
}

function Format-MaskedEmail {
    param([string]$Email)
    if ([string]::IsNullOrWhiteSpace($Email)) { return '' }
    $at = $Email.IndexOf('@')
    if ($at -lt 1) { return '***' }
    return '***' + $Email.Substring($at)
}

function Remove-Html {
    <#
    .SYNOPSIS
        Strip HTML tags from a WHMCS field value and trim it. Returns '' for
        null/empty/whitespace input.

    .DESCRIPTION
        WHMCS wraps URL and email answers in <a href="...">...</a>, so the raw
        value of a custom field is markup, not text. Two call sites need the
        stripped text and they must agree:

        - Format-WhmcsFieldValue strips BEFORE classifying, because an
          <a href="mailto:...">-wrapped address does not match the email shape
          and would sail through as free text.
        - whmcs-application-search.ps1 strips at MATCH time, because a needle
          typed as a bare domain cannot be found inside the markup.

        This is deliberately a plain strip with NO masking. The match site must
        compare against the unmasked text: routing it through
        Format-WhmcsFieldValue instead would compare the needle against '***'
        for any personal-classified field, so an application whose domain sits
        in such a field becomes silently unfindable and the search reports "no
        matches" while looking like it worked (#928, #929). Masking belongs to
        the emitted rows, which is a separate code path.
    #>
    param([Parameter(Position = 0)][AllowEmptyString()][AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return ([regex]::Replace($Value, '<[^>]+>', '')).Trim()
}

function Get-WhmcsFieldClass {
    <#
    .SYNOPSIS
        Classify a WHMCS field name for disclosure. Returns 'public',
        'personal-contact', or 'person-name'.

    .DESCRIPTION
        The distinction is NOT "organizational vs. personal" - it is what the
        charity has already published or is legally required to disclose:

        - A nonprofit's EIN is public (IRS Pub 78 / BMF, and it appears in the
          charity's own Candid profile URL).
        - Officers and directors - names, titles, roles - are public via
          Form 990 Part VII, which is a public-disclosure document.
        - A LinkedIn profile is self-published by its owner.
        - Fields the charity explicitly designated for its website footer are
          public by the charity's own instruction.

        What Form 990 does NOT disclose is an officer's personal email address
        or phone number, so those stay masked even for the same person whose
        name and role are public. That boundary is the whole policy: role and
        identity are public, direct personal contact routes are not.

        Order matters. Email/phone is decided FIRST so that
        "Board President/Chair Individual Email" masks even though it names a
        public role, and the footer exception is checked inside that branch.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$FieldName)

    if ($FieldName -match '(?i)phone|email|e-mail') {
        # Explicitly designated for publication by the charity.
        if ($FieldName -match '(?i)\bpublic\b' -or $FieldName -match '(?i)footer') { return 'public' }
        return 'personal-contact'
    }
    # Public by statute or self-publication.
    if ($FieldName -match '(?i)\bein\b|tax[ _-]?id') { return 'public' }
    if ($FieldName -match '(?i)linkedin|guidestar|candid|facebook|instagram|twitter|\bx\b|youtube|tiktok') { return 'public' }
    # Form 990 Part VII: officer/director names and titles are disclosed.
    if ($FieldName -match '(?i)board|president|chair|secretary|treasurer|vice[ _-]?president|member[ _-]?at[ _-]?large|director|officer|\brole\b|\btitle\b') {
        return 'public'
    }
    # A natural person's name that is NOT an officer/org name.
    if ($FieldName -match '(?i)\b(first|last|your|contact|poc)[ _-]?name\b' -and
        $FieldName -notmatch '(?i)org|charity|company|nonprofit|foundation|business') {
        return 'person-name'
    }
    return 'public'
}

function Format-WhmcsFieldValue {
    # Apply Get-WhmcsFieldClass to a value. Free-text values are additionally
    # shape-checked, because a personal phone or email typed into a mission or
    # notes field is personal regardless of what the field is called.
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$FieldName,
        [Parameter()][AllowEmptyString()][string]$Value
    )
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    # WHMCS wraps URL answers in <a href>...</a>. Strip it BEFORE classifying:
    # an <a href="mailto:...">-wrapped address does not match the email shape,
    # so a value that should mask would sail through as free text. Shared with
    # the search's match site via Remove-Html so the two cannot drift - they
    # already did once, and the search shipped calling a function that had been
    # deleted from under it (#928).
    $t = Remove-Html $Value
    if ([string]::IsNullOrWhiteSpace($t)) { return '' }

    switch (Get-WhmcsFieldClass -FieldName $FieldName) {
        'personal-contact' {
            if ($t -match '@') { return Format-MaskedEmail $t }
            return '***'
        }
        'person-name' { return Format-MaskedName $t }
        default {
            # 'public' by field name - but check the VALUE's shape, since the
            # field name cannot vouch for free text someone pasted into it.
            if ($t -match '^[^@\s]+@[^@\s]+\.[^@\s]+$') { return Format-MaskedEmail $t }
            # EIN shape (NN-NNNNNNN) is public and must not trip the phone
            # matcher below, which it otherwise would.
            if ($t -match '^\d{2}-?\d{7}$') { return $t }
            if ($t -match '^\+?[0-9 ()\-\.]{7,}$') { return '***' }
            return $t
        }
    }
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
        $prodId = $null; $status = $null
        try { $prodId = [string]$p.pid } catch {}
        try { $status = [string]$p.status } catch {}
        if ($prodId -eq [string]$ProductId -and ($dead -notcontains $status)) {
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
