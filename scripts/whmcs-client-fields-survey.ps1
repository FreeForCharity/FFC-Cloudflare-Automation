# Survey of WHMCS client classification fields (Phase 1 of the data-quality plan; no PII).
#
# The "Legal Organization Status" custom field is not in the bulk GetClients
# output, so this script loops every client through GetClientsDetails
# (throttled) and tallies, aggregate-only, how the three classification
# signals line up: the legal-status custom field, the native client group,
# and charity-product evidence. The output shows exactly how much
# classification backfill Phase 2 must do.
#
# PRIVACY: custom-field VALUES are only counted when they exactly match the
# known legal-status vocabulary (free-text field values are never printed);
# all output is aggregate integer counts.

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
    [string]$OutputFile = 'artifacts/whmcs/whmcs_client_fields_survey.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [ValidateRange(0, 5000)]
    [int]$ThrottleMs = 150,

    [Parameter()]
    [string]$CharityGids = '2,5,6,7,8'
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')
. (Join-Path $PSScriptRoot 'whmcs-metrics-common.ps1')

# The admin UI's "Legal Organization Status" vocabulary (exact matches only).
$legalVocab = @(
    '501c3 General Organization'
    '501c3 Shelter Water Hunger Organization'
    'For Profit Organization'
    'OI Only'
    'Pre 501c3 General Organization'
    'Pre 501c3 Shelter Water Hunger Organization'
)

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
    $charityGidSet = @($CharityGids -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

    function New-Body {
        param([string]$Action)
        $b = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = $Action
            responsetype = 'json'
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $b.accesskey = $accessKey }
        return $b
    }

    # --- 1. Clients (id, group) and charity-product evidence ------------------
    $clientGroup = @{}
    $start = 0
    while ($true) {
        $b = New-Body 'GetClients'
        $b.limitstart = $start
        $b.limitnum = $PageSize
        $r = Invoke-WhmcsGet -ApiUrl $api -Body $b
        $clients = Get-WhmcsListFromResponse -Response $r -Container 'clients' -Item 'client'
        if ($clients.Count -le 0) { break }
        foreach ($c in $clients) {
            $g = if ([string]::IsNullOrWhiteSpace([string]$c.groupid)) { '0' } else { "$($c.groupid)" }
            $clientGroup["$($c.id)"] = $g
        }
        $start += $clients.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }
    if ($clientGroup.Count -le 0) { throw 'GetClients returned no clients.' }

    $catalog = @{}
    $rp = Invoke-WhmcsGet -ApiUrl $api -Body (New-Body 'GetProducts')
    foreach ($p in (Get-WhmcsListFromResponse -Response $rp -Container 'products' -Item 'product')) {
        if ($null -ne $p.pid) { $catalog["$($p.pid)"] = "$($p.gid)" }
    }
    $hasCharity = New-Object 'System.Collections.Generic.HashSet[string]'
    $start = 0
    while ($true) {
        $b = New-Body 'GetClientsProducts'
        $b.limitstart = $start
        $b.limitnum = $PageSize
        $r = Invoke-WhmcsGet -ApiUrl $api -Body $b
        $services = Get-WhmcsListFromResponse -Response $r -Container 'products' -Item 'product'
        if ($services.Count -le 0) { break }
        foreach ($s in $services) {
            $gid = if ($catalog.ContainsKey("$($s.pid)")) { $catalog["$($s.pid)"] } else { '' }
            if ($charityGidSet -contains $gid) { [void]$hasCharity.Add("$($s.clientid)") }
        }
        $start += $services.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # --- 2. Per-client custom-field survey (throttled) ------------------------
    $legalCounts = @{}
    foreach ($v in $legalVocab) { $legalCounts[$v] = 0 }
    $legalByClient = @{}
    $noLegal = 0
    $detailErrors = 0

    foreach ($cid in $clientGroup.Keys) {
        Start-Sleep -Milliseconds $ThrottleMs
        $found = $null
        try {
            $b = New-Body 'GetClientsDetails'
            $b.clientid = $cid
            $b.stats = 'false'
            $d = Invoke-WhmcsGet -ApiUrl $api -Body $b
            $fields = @()
            if ($d.customfields) {
                if ($d.customfields.customfield) { $fields = @($d.customfields.customfield) }
                elseif ($d.customfields -is [System.Array]) { $fields = @($d.customfields) }
            }
            foreach ($f in $fields) {
                $v = [string]$f.value
                if ($legalVocab -contains $v) { $found = $v; break }
            }
        }
        catch {
            $detailErrors++
        }
        if ($null -ne $found) {
            $legalCounts[$found]++
            $legalByClient[$cid] = $found
        }
        else {
            $noLegal++
        }
    }

    # --- 3. Cross-tab: legal status x group assignment x charity evidence -----
    $crossRows = @()
    foreach ($v in ($legalVocab + '(none)')) {
        $cids = @()
        if ($v -eq '(none)') {
            $cids = @($clientGroup.Keys | Where-Object { -not $legalByClient.ContainsKey($_) })
        }
        else {
            $cids = @($legalByClient.Keys | Where-Object { $legalByClient[$_] -eq $v })
        }
        $grouped = @($cids | Where-Object { $clientGroup[$_] -ne '0' }).Count
        $charity = @($cids | Where-Object { $hasCharity.Contains($_) }).Count
        $crossRows += [ordered]@{
            legalStatus        = $v
            clients            = $cids.Count
            withClientGroup    = $grouped
            withCharityProduct = $charity
        }
    }

    $result = [ordered]@{
        generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        totals      = [ordered]@{
            clients            = $clientGroup.Count
            withLegalStatus    = $legalByClient.Count
            withoutLegalStatus = $noLegal
            detailCallErrors   = $detailErrors
        }
        legalCounts = $legalCounts
        crossTab    = $crossRows
        charityGids = $charityGidSet
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

    $lines = @(
        '## WHMCS client fields survey (aggregate, no PII)'
        ''
        "- Clients surveyed: **$($clientGroup.Count)**; with a Legal Organization Status: $($legalByClient.Count); without: $noLegal; detail-call errors: $detailErrors"
        ''
        '| Legal Organization Status | Clients | With client group | With charity product |'
        '| ------------------------- | ------- | ----------------- | -------------------- |'
    )
    foreach ($row in $crossRows) {
        $lines += "| $($row.legalStatus) | $($row.clients) | $($row.withClientGroup) | $($row.withCharityProduct) |"
    }
    $lines += ''
    $lines += '_Free-text custom-field values are never printed; only exact legal-status vocabulary matches are counted._'

    if ($env:GITHUB_STEP_SUMMARY) {
        $lines -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    $lines | ForEach-Object { Write-Host $_ }

    Write-Host ("Surveyed {0} clients -> {1}" -f $clientGroup.Count, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
