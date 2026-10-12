<#
.SYNOPSIS
    Emit ONE charity's public website fields (the allowlist in
    config/charity-lane-site-fields.json) for the charity-lane site builder.
    Read-only.

.DESCRIPTION
    Step G3.3c of the charity-lane pipeline (docs/charity-lane-pipeline.md,
    epic #1618), run by workflow 234. The Cloud Worker that builds a new
    charity's site (src/lib/site.config.ts, src/data/*) needs the fields the
    application collects FOR PUBLICATION: the footer email / phone / city &
    state, mission, tagline, descriptions, programs, brand color, logo, social,
    donation and volunteer URLs, founding year, alternate names, EIN, Candid
    URL and template choice. 219 masks contact data, so nothing else gives an
    agent those values.

    Sources, per allowlisted field, first valid value wins:
      1. the client's Custom Client Field (GetClientsDetails), matched by NAME.
         The live install has been seen to return client custom fields without
         names (whmcs/hooks/ffc_promote_charity_record.php); an unnamed client
         field can never be matched, so it is never emitted;
      2. the product custom-field answer on the client's pid 16 / 33 / 40
         services (GetClientsProducts), matched by machine-name, then by the
         legacy label patterns. Active services first, then newest first;
         Cancelled / Terminated / Fraud services are not read.

    Every value is cleaned (HTML and control characters stripped, length
    capped) and validated by type. A value that fails validation is never
    printed, not even in the rejection list, which carries a reason code only:
    a URL field holding a personal email is exactly the case it must not echo.

    What it never emits: the mailing address, the client record's own name /
    email / phone, board / officer / team-member / contact fields, or any
    field not on the allowlist. The allowlist loader refuses an entry that
    names one of those (`deniedNamePatterns`).

    The only WHMCS actions are GetClientsDetails and GetClientsProducts.

.PARAMETER ClientId
    WHMCS client id (digits).

.PARAMETER ConfigPath
    Allowlist file. Default: config/charity-lane-site-fields.json.

.PARAMETER OutputFile
    JSON artifact path (charity-lane-site-fields.json).

.PARAMETER MarkdownFile
    Markdown rendering for the job summary / issue comment.

.PARAMETER RunUrl
    Optional link to the workflow run, printed in the markdown.
#>
[CmdletBinding()]
param(
    [Parameter()][string]$ClientId,
    [Parameter()][string]$ConfigPath = 'config/charity-lane-site-fields.json',
    [Parameter()][string]$ApiUrl,
    [Parameter()][string]$OutputFile = 'artifacts/whmcs/charity-lane-site-fields.json',
    [Parameter()][string]$MarkdownFile = 'artifacts/whmcs/charity-lane-site-fields.md',
    [Parameter()][string]$RunUrl
)

$ErrorActionPreference = 'Stop'

$script:SiteFieldsMarker = '<!-- charity-lane:site-fields -->'
$script:SiteFieldTypes = @('email', 'phone', 'location', 'text', 'hexColor', 'url', 'year', 'nameList', 'ein', 'choice')

# --- pure, testable helpers (dot-source stops below) -----------------------

function Get-SiteFieldNormNames {
    # "machine|Label" -> raw, before-pipe and after-pipe, trimmed + casefolded
    # (the same normalisation as 229's Get-NormNames).
    param([AllowEmptyString()][AllowNull()][string]$Name)
    $out = [System.Collections.Generic.List[string]]::new()
    $raw = if ($null -eq $Name) { '' } else { $Name }
    foreach ($cand in @($raw, ($raw -split '\|', 2)[0], ($raw -split '\|', 2)[-1])) {
        $n = $cand.Trim().ToLowerInvariant()
        if ($n -and -not $out.Contains($n)) { [void]$out.Add($n) }
    }
    return , $out
}

function Test-SiteFieldDeniedName {
    # True when a field NAME carries one of the denied (personal / board /
    # address) fragments.
    param([AllowEmptyString()][string]$Name, [string[]]$DeniedPatterns)
    $lower = ([string]$Name).ToLowerInvariant()
    foreach ($d in $DeniedPatterns) {
        if ($d -and $lower.Contains($d.ToLowerInvariant())) { return $true }
    }
    return $false
}

function Read-SiteFieldsConfig {
    # Load + validate the allowlist. Throws on any entry that could widen what
    # is emitted beyond publication-intended fields.
    param([Parameter(Mandatory = $true)]$ConfigObject)

    $denied = @($ConfigObject.deniedNamePatterns | Where-Object { $_ })
    if ($denied.Count -eq 0) { throw 'Site-fields config has no deniedNamePatterns; refusing to run without the personal-field guard.' }
    $pids = @($ConfigObject.productIds | ForEach-Object { [int]$_ })
    if ($pids.Count -eq 0) { throw 'Site-fields config lists no productIds.' }

    $keys = [System.Collections.Generic.HashSet[string]]::new()
    $fields = [System.Collections.Generic.List[object]]::new()
    foreach ($f in @($ConfigObject.fields)) {
        $key = [string]$f.key
        if ($key -notmatch '^[a-z][A-Za-z0-9]{1,40}$') { throw "Site-fields config: invalid key '$key'." }
        if (-not $keys.Add($key)) { throw "Site-fields config: duplicate key '$key'." }
        $type = [string]$f.type
        if ($script:SiteFieldTypes -notcontains $type) { throw "Site-fields config: field '$key' has unknown type '$type'." }
        $client = [string]$f.clientField
        $products = @($f.productFields | Where-Object { $_ } | ForEach-Object { [string]$_ })
        foreach ($n in @($client) + $products) {
            if (Test-SiteFieldDeniedName -Name $n -DeniedPatterns $denied) {
                throw "Site-fields config: field '$key' names '$n', which matches a denied personal/board/address pattern."
            }
        }
        $patterns = @($f.productLabelPatterns | Where-Object { $_ } | ForEach-Object { [string]$_ })
        foreach ($p in $patterns) { $null = [regex]::new($p) }
        $fields.Add([pscustomobject]@{
                key                  = $key
                type                 = $type
                clientField          = $client
                productFields        = $products
                productLabelPatterns = $patterns
                maxLength            = if ($f.maxLength) { [int]$f.maxLength } else { 0 }
                maxItems             = if ($f.maxItems) { [int]$f.maxItems } else { 0 }
                multiline            = [bool]$f.multiline
                hosts                = @($f.hosts | Where-Object { $_ } | ForEach-Object { ([string]$_).ToLowerInvariant() })
                rejectPathPatterns   = @($f.rejectPathPatterns | Where-Object { $_ } | ForEach-Object { [string]$_ })
            })
    }
    if ($fields.Count -eq 0) { throw 'Site-fields config lists no fields.' }

    return [pscustomobject]@{
        productIds              = $pids
        excludedServiceStatuses = @($ConfigObject.excludedServiceStatuses | ForEach-Object { [string]$_ })
        deniedNamePatterns      = $denied
        fields                  = $fields.ToArray()
    }
}

function ConvertTo-SiteFieldCleanText {
    # Strip HTML (script/style blocks with their content, then tags, then
    # entities, then any tag the decoding produced), control and bidi /
    # zero-width characters; normalise whitespace. -Multiline keeps single
    # paragraph breaks.
    param([AllowEmptyString()][AllowNull()][string]$Value, [switch]$Multiline)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $t = [regex]::Replace($Value, '(?is)<(script|style)\b[^>]*>.*?</\1\s*>', ' ')
    $t = [regex]::Replace($t, '(?i)<br\s*/?>|</p\s*>', "`n")
    $t = [regex]::Replace($t, '<[^>]*>', '')
    $t = [System.Net.WebUtility]::HtmlDecode($t)
    $t = [regex]::Replace($t, '<[^>]*>', '')
    $t = $t -replace '[\u200B-\u200F\u202A-\u202E\u2060-\u2069\uFEFF]', ''
    $t = $t -replace "`r`n?", "`n"
    $t = $t -replace "`t", ' '
    $t = $t -replace '[\x00-\x08\x0B-\x1F\x7F]', ''
    if ($Multiline) {
        $t = [regex]::Replace($t, '[ \u00A0]+', ' ')
        $t = [regex]::Replace($t, ' *\n *', "`n")
        $t = [regex]::Replace($t, '\n{3,}', "`n`n")
    }
    else {
        $t = [regex]::Replace($t, '\s+', ' ')
    }
    return $t.Trim()
}

function Limit-SiteFieldText {
    # Cap at MaxLength on a word boundary where one is near.
    param([string]$Text, [int]$MaxLength)
    if ($MaxLength -le 0 -or $Text.Length -le $MaxLength) { return $Text }
    $cut = $Text.Substring(0, $MaxLength)
    $sp = $cut.LastIndexOfAny([char[]]@(' ', "`n"))
    if ($sp -ge [int]($MaxLength * 0.6)) { $cut = $cut.Substring(0, $sp) }
    return $cut.TrimEnd(' ', ',', ';', ':', '-', "`n")
}

function Remove-SiteFieldEmbeddedContact {
    # Free text is checked for contact details typed into it: an email address
    # or a 10-15 digit phone number inside a mission is personal regardless of
    # the field it sits in. Returns @{ text; redacted }.
    param([string]$Text)
    $out = [regex]::Replace($Text, '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', '[removed]')
    $out = [regex]::Replace($out, '(?<![\w-])\+?\(?\d[\d\s().\-]{8,}\d(?![\w-])', {
            param($m)
            $digits = ($m.Value -replace '\D', '').Length
            if ($digits -ge 10 -and $digits -le 15) { '[removed]' } else { $m.Value }
        })
    return @{ text = $out; redacted = ($out -ne $Text) }
}

function Test-SiteFieldUrl {
    # https only, no credentials in the authority, no secret-looking query
    # parameter, optional host allowlist and rejected path patterns.
    param([string]$Text, $Field)
    if ($Text -match '\s') { return @{ ok = $false; reason = 'url_has_whitespace' } }
    $u = $null
    if (-not [Uri]::TryCreate($Text, [UriKind]::Absolute, [ref]$u)) { return @{ ok = $false; reason = 'url_unparseable' } }
    if ($u.Scheme -ne 'https') { return @{ ok = $false; reason = 'url_not_https' } }
    if (-not [string]::IsNullOrEmpty($u.UserInfo)) { return @{ ok = $false; reason = 'url_has_credentials' } }
    if ($u.Host -notmatch '\.') { return @{ ok = $false; reason = 'url_bad_host' } }
    $hostName = $u.Host.ToLowerInvariant()
    if ($Field.hosts.Count -gt 0) {
        $hostOk = $false
        foreach ($h in $Field.hosts) { if ($hostName -eq $h -or $hostName.EndsWith(".$h")) { $hostOk = $true; break } }
        if (-not $hostOk) { return @{ ok = $false; reason = 'url_host_not_allowed' } }
    }
    foreach ($p in $Field.rejectPathPatterns) {
        if ($u.AbsolutePath -match $p) { return @{ ok = $false; reason = 'url_path_not_allowed' } }
    }
    if ($u.Query -match '(?i)[?&][^=&]*(token|secret|password|passwd|pwd|key|sig|signature|auth|session|code|credential)[^=&]*=') {
        return @{ ok = $false; reason = 'url_query_secret' }
    }
    return @{ ok = $true; value = $u.AbsoluteUri }
}

function Test-SiteFieldValue {
    # Clean + validate one raw value for one allowlisted field. Returns
    # @{ ok; value; reason; redacted; truncated }. `value` is set only when ok.
    param([AllowEmptyString()][AllowNull()][string]$Value, [Parameter(Mandatory = $true)]$Field)

    $clean = ConvertTo-SiteFieldCleanText -Value $Value -Multiline:($Field.multiline)
    if ([string]::IsNullOrWhiteSpace($clean)) { return @{ ok = $false; reason = 'empty' } }

    switch ($Field.type) {
        'email' {
            if ($Field.maxLength -gt 0 -and $clean.Length -gt $Field.maxLength) { return @{ ok = $false; reason = 'too_long' } }
            if ($clean -notmatch '^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$') { return @{ ok = $false; reason = 'not_an_email' } }
            return @{ ok = $true; value = $clean.ToLowerInvariant() }
        }
        'phone' {
            if ($Field.maxLength -gt 0 -and $clean.Length -gt $Field.maxLength) { return @{ ok = $false; reason = 'too_long' } }
            if ($clean -notmatch '^\+?[0-9 ().\-]+$') { return @{ ok = $false; reason = 'not_a_phone' } }
            $digits = ($clean -replace '\D', '').Length
            if ($digits -lt 7 -or $digits -gt 20) { return @{ ok = $false; reason = 'phone_digit_count' } }
            return @{ ok = $true; value = $clean }
        }
        'location' {
            if ($Field.maxLength -gt 0 -and $clean.Length -gt $Field.maxLength) { return @{ ok = $false; reason = 'too_long' } }
            if ($clean -match '@|https?://') { return @{ ok = $false; reason = 'not_a_location' } }
            # City & state only: a street number, a PO box or a unit means a
            # street address was typed in, and the full address is never emitted.
            if ($clean -match '(?i)(^|[\s,])\d{1,6}\s+[A-Za-z]|\bp\.?\s?o\.?\s+box\b|\b(suite|ste|apt|unit)\b') {
                return @{ ok = $false; reason = 'looks_like_street_address' }
            }
            return @{ ok = $true; value = $clean }
        }
        'text' {
            $r = Remove-SiteFieldEmbeddedContact -Text $clean
            $limited = Limit-SiteFieldText -Text $r.text -MaxLength $Field.maxLength
            return @{ ok = $true; value = $limited; redacted = $r.redacted; truncated = ($limited.Length -lt $r.text.Length) }
        }
        'choice' {
            if ($Field.maxLength -gt 0 -and $clean.Length -gt $Field.maxLength) { return @{ ok = $false; reason = 'too_long' } }
            if ($clean -notmatch "^[A-Za-z0-9 ._()/&'+\-]+$") { return @{ ok = $false; reason = 'not_a_choice' } }
            return @{ ok = $true; value = $clean }
        }
        'hexColor' {
            if ($clean -notmatch '^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$') { return @{ ok = $false; reason = 'not_a_hex_color' } }
            return @{ ok = $true; value = ('#' + $Matches[1].ToUpperInvariant()) }
        }
        'year' {
            if ($clean -notmatch '^\d{4}$') { return @{ ok = $false; reason = 'not_a_year' } }
            $y = [int]$clean
            if ($y -lt 1700 -or $y -gt ((Get-Date).ToUniversalTime().Year)) { return @{ ok = $false; reason = 'year_out_of_range' } }
            return @{ ok = $true; value = $clean }
        }
        'ein' {
            if ($clean -notmatch '^(\d{2})-?(\d{7})$') { return @{ ok = $false; reason = 'not_an_ein' } }
            return @{ ok = $true; value = "$($Matches[1])-$($Matches[2])" }
        }
        'url' {
            if ($Field.maxLength -gt 0 -and $clean.Length -gt $Field.maxLength) { return @{ ok = $false; reason = 'too_long' } }
            $r = Test-SiteFieldUrl -Text $clean -Field $Field
            if (-not $r.ok) { return @{ ok = $false; reason = $r.reason } }
            return @{ ok = $true; value = $r.value }
        }
        'nameList' {
            $items = [System.Collections.Generic.List[string]]::new()
            $redacted = $false
            foreach ($part in ($clean -split '[,;\n]')) {
                $p = $part.Trim()
                if (-not $p) { continue }
                $r = Remove-SiteFieldEmbeddedContact -Text $p
                if ($r.redacted -or $p -match '@|https?://') { $redacted = $true; continue }
                $p = Limit-SiteFieldText -Text $p -MaxLength $Field.maxLength
                if (-not $items.Contains($p)) { $items.Add($p) }
                if ($Field.maxItems -gt 0 -and $items.Count -ge $Field.maxItems) { break }
            }
            if ($items.Count -eq 0) { return @{ ok = $false; reason = 'no_names' } }
            return @{ ok = $true; value = $items.ToArray(); redacted = $redacted }
        }
    }
    return @{ ok = $false; reason = 'unknown_type' }
}

function Get-SiteFieldClientValues {
    # GetClientsDetails response -> @{ named = bool; values = @{ normName -> value } }.
    # Only fields that come back WITH a name are usable; an unnamed field is
    # dropped here, so it can never reach the output.
    param($Detail)
    $values = @{}
    $list = @()
    if ($Detail) {
        $node = $Detail.customfields
        if ($null -eq $node -and $Detail.client) { $node = $Detail.client.customfields }
        if ($node -is [System.Array]) { $list = @($node) }
        elseif ($node -and $node.PSObject.Properties['customfield']) { $list = @($node.customfield) }
    }
    $named = $false
    foreach ($f in ($list | Where-Object { $null -ne $_ -and $_ -isnot [string] })) {
        $name = ''
        foreach ($prop in @('name', 'fieldname', 'label')) {
            if ($f.PSObject.Properties[$prop] -and -not [string]::IsNullOrWhiteSpace([string]$f.$prop)) { $name = [string]$f.$prop; break }
        }
        if (-not $name) { continue }
        $named = $true
        foreach ($nn in (Get-SiteFieldNormNames $name)) {
            if (-not $values.ContainsKey($nn)) { $values[$nn] = [string]$f.value }
        }
    }
    return @{ named = $named; values = $values }
}

function Get-SiteFieldProductSets {
    # GetClientsProducts response -> the client's lane services, ordered:
    # Active first, then newest (highest service id) first. Each set carries
    # its answers as an ordered list of @{ name; value } (field names kept so
    # the legacy label patterns can run).
    param($ProductsResponse, [Parameter(Mandatory = $true)]$Config)
    $products = @()
    if ($ProductsResponse) {
        $node = $ProductsResponse.products
        if ($node -is [System.Array]) { $products = @($node) }
        elseif ($node -and $node -isnot [string] -and $node.PSObject.Properties['product']) { $products = @($node.product) }
    }
    $sets = [System.Collections.Generic.List[object]]::new()
    foreach ($p in ($products | Where-Object { $null -ne $_ -and $_ -isnot [string] })) {
        $productPid = 0
        [void][int]::TryParse([string]$p.pid, [ref]$productPid)
        if ($Config.productIds -notcontains $productPid) { continue }
        $status = [string]$p.status
        if ($Config.excludedServiceStatuses -contains $status) { continue }
        $serviceId = 0
        [void][int]::TryParse([string]$p.id, [ref]$serviceId)
        $answers = [System.Collections.Generic.List[object]]::new()
        $cfNode = $p.customfields
        $cfs = @()
        if ($cfNode -is [System.Array]) { $cfs = @($cfNode) }
        elseif ($cfNode -and $cfNode -isnot [string] -and $cfNode.PSObject.Properties['customfield']) { $cfs = @($cfNode.customfield) }
        foreach ($f in ($cfs | Where-Object { $null -ne $_ -and $_ -isnot [string] })) {
            $name = if ($f.PSObject.Properties['name'] -and $f.name) { [string]$f.name } elseif ($f.PSObject.Properties['fieldname'] -and $f.fieldname) { [string]$f.fieldname } else { '' }
            if (-not $name) { continue }
            $answers.Add([pscustomobject]@{ name = $name; value = [string]$f.value })
        }
        $sets.Add([pscustomobject]@{
                serviceId = $serviceId
                pid       = $productPid
                status    = $status
                answers   = $answers.ToArray()
            })
    }
    $ordered = @($sets | Sort-Object -Property @{ Expression = { if ($_.status -eq 'Active') { 0 } else { 1 } } }, @{ Expression = { $_.serviceId }; Descending = $true })
    return , $ordered
}

function Get-SiteFieldProductCandidates {
    # Ordered candidate raw values for one field from one service: exact
    # machine-name matches first (in config order), then legacy label-pattern
    # matches. A label-pattern match on a field whose name carries a denied
    # fragment (board, contact, ...) is never a candidate.
    param($Field, $Set, [string[]]$DeniedPatterns)
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($slug in $Field.productFields) {
        $s = $slug.Trim().ToLowerInvariant()
        foreach ($a in $Set.answers) {
            if ((Get-SiteFieldNormNames $a.name).Contains($s)) { $out.Add($a.value) }
        }
    }
    foreach ($pat in $Field.productLabelPatterns) {
        foreach ($a in $Set.answers) {
            if ($a.name -notmatch "(?i)$pat") { continue }
            if (Test-SiteFieldDeniedName -Name $a.name -DeniedPatterns $DeniedPatterns) { continue }
            $out.Add($a.value)
        }
    }
    return , $out
}

function Resolve-SiteFields {
    # The whole decision: for each allowlisted field, client field first, then
    # product answers in service order; the first VALID value wins. Returns the
    # emitted document (only allowlisted keys can appear in `fields`).
    param(
        [Parameter(Mandatory = $true)]$Config,
        [Parameter(Mandatory = $true)][hashtable]$ClientValues,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$ProductSets,
        [Parameter(Mandatory = $true)][int]$ClientId,
        [bool]$ClientFieldsNamed = $true
    )
    $fields = [ordered]@{}
    $fieldSources = [ordered]@{}
    $missing = [System.Collections.Generic.List[string]]::new()
    $rejected = [System.Collections.Generic.List[object]]::new()
    $redacted = [System.Collections.Generic.List[string]]::new()
    $truncated = [System.Collections.Generic.List[string]]::new()

    foreach ($f in $Config.fields) {
        $winner = $null
        $source = $null
        $reasons = [System.Collections.Generic.List[string]]::new()

        # 1. Custom Client Field (by name).
        $raw = $null
        foreach ($nn in (Get-SiteFieldNormNames $f.clientField)) {
            if ($ClientValues.ContainsKey($nn)) { $raw = $ClientValues[$nn]; break }
        }
        if (-not [string]::IsNullOrWhiteSpace($raw)) {
            $r = Test-SiteFieldValue -Value $raw -Field $f
            if ($r.ok) { $winner = $r; $source = 'client_field' }
            else { $reasons.Add("client_field:$($r.reason)") }
        }

        # 2. Product answers, in service order.
        if (-not $winner) {
            foreach ($set in $ProductSets) {
                foreach ($cand in (Get-SiteFieldProductCandidates -Field $f -Set $set -DeniedPatterns $Config.deniedNamePatterns)) {
                    if ([string]::IsNullOrWhiteSpace($cand)) { continue }
                    $r = Test-SiteFieldValue -Value $cand -Field $f
                    if ($r.ok) { $winner = $r; $source = "product:pid$($set.pid):service$($set.serviceId)"; break }
                    $tag = "product:pid$($set.pid):$($r.reason)"
                    if (-not $reasons.Contains($tag)) { $reasons.Add($tag) }
                }
                if ($winner) { break }
            }
        }

        if ($winner) {
            $fields[$f.key] = $winner.value
            $fieldSources[$f.key] = $source
            if ($winner.redacted) { $redacted.Add($f.key) }
            if ($winner.truncated) { $truncated.Add($f.key) }
        }
        else {
            $missing.Add($f.key)
            if ($reasons.Count -gt 0) {
                $rejected.Add([ordered]@{ field = $f.key; reasons = $reasons.ToArray() })
            }
        }
    }

    return [ordered]@{
        schema       = 'charity-lane-site-fields/v1'
        clientId     = $ClientId
        generatedAt  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        sources      = [ordered]@{
            clientFieldsNamed = $ClientFieldsNamed
            services          = @($ProductSets | ForEach-Object { [ordered]@{ serviceId = $_.serviceId; pid = $_.pid; status = $_.status } })
        }
        fields       = $fields
        fieldSources = $fieldSources
        missing      = $missing.ToArray()
        rejected     = $rejected.ToArray()
        redacted     = $redacted.ToArray()
        truncated    = $truncated.ToArray()
    }
}

function Format-SiteFieldsMarkdown {
    # Job summary / issue comment. All values sit inside one fenced JSON block
    # (a fence longer than any backtick run in the payload), so no value can
    # render as markdown or @-mention anyone.
    param([Parameter(Mandatory = $true)]$Result, [string]$Json, [string]$RunUrl)
    $longest = 0
    foreach ($m in [regex]::Matches($Json, '`+')) { if ($m.Length -gt $longest) { $longest = $m.Length } }
    $fence = '`' * [math]::Max(3, $longest + 1)
    $total = $Result.fields.Count + $Result.missing.Count
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add($script:SiteFieldsMarker)
    $lines.Add("## Charity-lane site fields - client $($Result.clientId)")
    $lines.Add('')
    $lines.Add("Public website fields from the application (allowlist: ``config/charity-lane-site-fields.json``). Present: **$($Result.fields.Count) of $total**.")
    if ($RunUrl) { $lines.Add(''); $lines.Add("Run: $RunUrl") }
    if (-not $Result.sources.clientFieldsNamed) {
        $lines.Add('')
        $lines.Add('Note: WHMCS returned the client custom fields without names, so every value below comes from the pid 16/33/40 product answers.')
    }
    $lines.Add('')
    if ($Result.missing.Count -gt 0) {
        $lines.Add("**Missing (mark ``pending``):** $([string]::Join(', ', ($Result.missing | ForEach-Object { '`' + $_ + '`' })))")
    }
    else {
        $lines.Add('**Missing:** none')
    }
    if ($Result.rejected.Count -gt 0) {
        $lines.Add('')
        $lines.Add('**Rejected values (reason only, the value is never printed):**')
        foreach ($r in $Result.rejected) { $lines.Add("- ``$($r.field)``: $([string]::Join(', ', $r.reasons))") }
    }
    if ($Result.redacted.Count -gt 0) {
        $lines.Add('')
        $lines.Add("**Contact details removed from free text:** $([string]::Join(', ', $Result.redacted))")
    }
    $lines.Add('')
    $lines.Add($fence + 'json')
    $lines.Add($Json)
    $lines.Add($fence)
    return ($lines -join "`n")
}

if ($MyInvocation.InvocationName -eq '.') { return }

# --- main (read-only) --------------------------------------------------------
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

try {
    if ([string]$ClientId -notmatch '^[1-9][0-9]{0,9}$') {
        throw "client_id must be digits (got '$ClientId')."
    }
    $cid = [int64]$ClientId
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Site-fields config not found: $ConfigPath" }
    $config = Read-SiteFieldsConfig -ConfigObject (Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json)

    $creds = Resolve-WhmcsCredentials
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $accessKey = Resolve-WhmcsAccessKey

    $body = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $body.action = 'GetClientsDetails'
    $body.clientid = $cid
    $body.stats = 'false'
    $detail = Invoke-WhmcsApi -ApiUrl $api -Body $body

    $body = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $body.action = 'GetClientsProducts'
    $body.clientid = $cid
    $body.limitstart = 0
    $body.limitnum = 250
    $prodResp = Invoke-WhmcsApi -ApiUrl $api -Body $body

    $client = Get-SiteFieldClientValues -Detail $detail
    $sets = Get-SiteFieldProductSets -ProductsResponse $prodResp -Config $config
    $result = Resolve-SiteFields -Config $config -ClientValues $client.values -ProductSets $sets -ClientId $cid -ClientFieldsNamed $client.named

    $json = $result | ConvertTo-Json -Depth 8
    $md = Format-SiteFieldsMarkdown -Result $result -Json $json -RunUrl $RunUrl
    foreach ($pair in @(@($OutputFile, $json), @($MarkdownFile, $md))) {
        $dir = Split-Path -Parent $pair[0]
        if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Set-Content -LiteralPath $pair[0] -Value $pair[1] -Encoding utf8
    }
    if ($sets.Count -eq 0) {
        Write-Output "::warning::Client $cid has no pid $($config.productIds -join '/') service outside $($config.excludedServiceStatuses -join '/'); only client fields were read."
    }
    Write-Output "Site fields: $($result.fields.Count) present, $($result.missing.Count) missing."
    exit 0
}
catch {
    Write-Output "::error::$($_.Exception.Message)"
    exit 1
}
