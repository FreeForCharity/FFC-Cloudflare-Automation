<#
.SYNOPSIS
    Populate a WHMCS client's Custom Client Fields from the answers they submitted
    on their onboarding / website product orders (order -> client sync).

.DESCRIPTION
    WHMCS stores product custom-field answers on the *service*, not on the client
    record, so the org data a charity submits (mission, brand color, socials,
    contacts, site content, ...) never appears on the admin **client profile**
    until it is copied there. This script does that copy:

      1. Resolve the target client (by -ClientId, or -Email via GetClients).
      2. GetClientsProducts -> collect the product custom-field {name -> value}
         answers across the client's services (last non-empty answer wins).
      3. GetClientsDetails -> read the client's CURRENT custom fields, building
         {client-field-name -> @{ id; value }} so client-field ids are resolved at
         RUNTIME (never hard-coded -- ids differ per install and per field).
      4. Apply the reviewable name map (config/whmcs-client-field-populate-map.json,
         product-field-name -> client-field-name) to stage {client-field-id -> value}.
      5. UpdateClient with the base64 `customfields` blob (ConvertTo-WhmcsCustomFields).

    Idempotent + non-destructive: a client field that already has a value is left
    alone unless -Overwrite; a blank/absent product answer never clears a client
    field. dry_run is the DEFAULT (preview only); -Execute performs the write and
    the workflow gates that behind whmcs-prod.

    The name map keys are product field machine-names. Because WHMCS may return a
    "machine|Label" field as either side of the pipe, matching normalises both the
    product answer names and the map keys (exact, before-pipe, after-pipe, casefold)
    -- and the dry-run report lists every product field name seen but not mapped, so
    the map can be verified/extended against real output before any live run.

    Read + write both route through the APIM gateway (its IP is allow-listed at
    WHMCS); calling WHMCS directly is rejected with "Invalid IP".

.PARAMETER ClientId
    Target WHMCS client id. Either -ClientId or -Email is required.

.PARAMETER Email
    Target client email (resolved to a client id via GetClients).

.PARAMETER MapJson
    Path to the product-field -> client-field name map (JSON object).
    Default: config/whmcs-client-field-populate-map.json.

.PARAMETER Overwrite
    Overwrite client fields that already have a (different) value. Off by default
    (existing client values are preserved).

.PARAMETER Execute
    Perform the UpdateClient write. Omit for a dry-run preview (default).

.OUTPUTS
    Writes -OutputFile (JSON report) and a masked markdown summary to stdout.
#>
[CmdletBinding()]
param(
    [Parameter()][string]$ClientId,
    [Parameter()][string]$Email,
    [Parameter()][string]$MapJson = 'config/whmcs-client-field-populate-map.json',
    [Parameter()][switch]$Overwrite,
    [Parameter()][switch]$Execute,
    [Parameter()][string]$ApiUrl,
    [Parameter()][string]$AccessKey,
    [Parameter()][string]$OutputFile = 'artifacts/whmcs/whmcs_client_field_populate.json'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/whmcs-api-common.ps1"

# --- pure, testable helpers (dot-source stops below) -----------------------
function Get-NormNames {
    # A product/client field name may be "machine|Label". Yield the raw string,
    # the part before '|', and the part after '|' -- each trimmed + casefolded --
    # so a map keyed on the machine-name still matches an answer returned as the
    # label (and vice versa).
    param([string]$Name)
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($cand in @($Name, ($Name -split '\|', 2)[0], ($Name -split '\|', 2)[-1])) {
        $n = ($cand ?? '').Trim().ToLowerInvariant()
        if ($n -and -not $out.Contains($n)) { [void]$out.Add($n) }
    }
    return $out
}

function ConvertTo-MapLookup {
    # {product-name -> client-name} JSON object -> normalised(product-name) -> client-name.
    # Skips meta keys like _comment.
    param([Parameter(Mandatory = $true)]$MapObject)
    $lookup = @{}
    foreach ($p in $MapObject.PSObject.Properties) {
        if ($p.Name.StartsWith('_')) { continue }
        foreach ($nn in (Get-NormNames $p.Name)) { $lookup[$nn] = [string]$p.Value }
    }
    return $lookup
}

function Get-PopulatePlan {
    # Pure staging: given the product answers, the client's current fields, and the
    # map lookup, decide per answer whether to write/skip/nochange. Returns
    # @{ staged = @{clientFieldId -> value}; plan = @(...); unmapped = @(...) }.
    param(
        [Parameter(Mandatory = $true)][hashtable]$Answers,       # norm product-name -> value
        [Parameter(Mandatory = $true)][hashtable]$ClientFields,  # norm client-name  -> @{id;value}
        [Parameter(Mandatory = $true)][hashtable]$MapLookup,     # norm product-name -> client-name
        [switch]$Overwrite
    )
    $staged = @{}
    $plan = [System.Collections.Generic.List[object]]::new()
    $unmapped = [System.Collections.Generic.List[string]]::new()

    foreach ($ans in ($Answers.GetEnumerator() | Sort-Object Name)) {
        $clientName = $MapLookup[$ans.Key]
        if (-not $clientName) {
            if (-not $unmapped.Contains($ans.Key)) { [void]$unmapped.Add($ans.Key) }
            continue
        }
        $target = $null
        foreach ($nn in (Get-NormNames $clientName)) { if ($ClientFields.ContainsKey($nn)) { $target = $ClientFields[$nn]; break } }
        if (-not $target) {
            [void]$plan.Add([pscustomobject]@{ product = $ans.Key; client = $clientName; action = 'skip'; reason = 'client field not found' })
            continue
        }
        $existing = [string]$target.value
        if (-not [string]::IsNullOrWhiteSpace($existing) -and -not $Overwrite) {
            $act = if ($existing -eq [string]$ans.Value) { 'nochange' } else { 'skip' }
            $reason = if ($act -eq 'skip') { 'client field already set (use -Overwrite)' } else { $null }
            [void]$plan.Add([pscustomobject]@{ product = $ans.Key; client = $clientName; clientFieldId = $target.id; action = $act; reason = $reason })
            continue
        }
        $staged[[string]$target.id] = [string]$ans.Value
        [void]$plan.Add([pscustomobject]@{ product = $ans.Key; client = $clientName; clientFieldId = $target.id; action = 'write' })
    }
    return @{ staged = $staged; plan = $plan; unmapped = $unmapped }
}

if ($MyInvocation.InvocationName -eq '.') { return }

# --- main ------------------------------------------------------------------
$creds = Resolve-WhmcsCredentials
$api = Resolve-WhmcsApiUrl -ApiUrl $ApiUrl
$accessKey = Resolve-WhmcsAccessKey -AccessKey $AccessKey

if ([string]::IsNullOrWhiteSpace($ClientId) -and [string]::IsNullOrWhiteSpace($Email)) {
    throw 'Provide -ClientId or -Email to identify the target client.'
}
$cid = $ClientId
if ([string]::IsNullOrWhiteSpace($cid)) {
    $cid = Find-WhmcsClientIdByEmail -ApiUrl $api -Creds $creds -AccessKey $accessKey -Email $Email
    if ([string]::IsNullOrWhiteSpace($cid)) { throw "No WHMCS client found for email '$Email'." }
}

if (-not (Test-Path $MapJson)) { throw "Field map not found: $MapJson" }
$mapLookup = ConvertTo-MapLookup -MapObject (Get-Content -Raw -Path $MapJson | ConvertFrom-Json)

# 1. product answers
$body = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
$body.action = 'GetClientsProducts'; $body.clientid = $cid
$prodResp = Invoke-WhmcsApi -ApiUrl $api -Body $body
$answers = @{}
$products = @()
if ($prodResp.products) {
    if ($prodResp.products.product) { $products = @($prodResp.products.product) }
    elseif ($prodResp.products -is [System.Array]) { $products = @($prodResp.products) }
}
foreach ($p in $products) {
    $cfs = @(); if ($p.customfields -and $p.customfields.customfield) { $cfs = @($p.customfields.customfield) }
    foreach ($f in $cfs) {
        $val = [string]$f.value
        if ([string]::IsNullOrWhiteSpace($val)) { continue }
        foreach ($nn in (Get-NormNames ([string]$f.name))) { $answers[$nn] = $val }
    }
}

# 2. current client custom fields (id resolution)
$body = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
$body.action = 'GetClientsDetails'; $body.clientid = $cid; $body.stats = 'false'
$detail = Invoke-WhmcsApi -ApiUrl $api -Body $body
$clientFields = @{}
$cfList = @()
if ($detail.customfields -and $detail.customfields.customfield) { $cfList = @($detail.customfields.customfield) }
elseif ($detail.customfields -is [System.Array]) { $cfList = @($detail.customfields) }
foreach ($f in $cfList) {
    $fid = [string]$f.id
    if ([string]::IsNullOrWhiteSpace($fid)) { continue }
    foreach ($nn in (Get-NormNames ([string]$f.name))) {
        if (-not $clientFields.ContainsKey($nn)) { $clientFields[$nn] = @{ id = $fid; value = [string]$f.value } }
    }
}

# 3. stage
$result = Get-PopulatePlan -Answers $answers -ClientFields $clientFields -MapLookup $mapLookup -Overwrite:$Overwrite
$staged = $result.staged; $plan = $result.plan; $unmapped = $result.unmapped

# 4. write (or preview)
$didWrite = $false
if ($staged.Count -gt 0 -and $Execute) {
    $cfBlob = ConvertTo-WhmcsCustomFields -Json ($staged | ConvertTo-Json -Compress)
    $body = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $body.action = 'UpdateClient'; $body.clientid = $cid; $body.customfields = $cfBlob
    $null = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $didWrite = $true
}

# 5. report
$report = [ordered]@{
    clientId = $cid; executed = $didWrite; overwrite = [bool]$Overwrite
    productAnswers = $answers.Count; staged = $staged.Count
    plan = $plan; unmappedProductFields = $unmapped
}
$dir = Split-Path -Parent $OutputFile
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $OutputFile -Encoding utf8

$mode = if ($didWrite) { 'LIVE (UpdateClient sent)' } elseif ($Execute) { 'LIVE (no changes staged)' } else { 'DRY-RUN (preview only)' }
"## WHMCS client-field population - client $cid"
"**Mode:** $mode  ·  product answers: $($answers.Count)  ·  staged writes: $($staged.Count)"
''
'| Product field | Client field | Action |'
'| --- | --- | --- |'
foreach ($row in $plan) {
    $r = if ($row.reason) { "$($row.action) ($($row.reason))" } else { $row.action }
    "| $($row.product) | $($row.client) | $r |"
}
if ($unmapped.Count -gt 0) {
    ''
    "**Unmapped product fields (add to the map if they belong on the client profile):** $([string]::Join(', ', $unmapped))"
}
