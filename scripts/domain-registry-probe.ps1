<#
.SYNOPSIS
    READ-ONLY registry-truth probe (RDAP) for the eNOM -> Cloudflare Registrar
    transfer project (#157).

.DESCRIPTION
    For each domain it queries the authoritative registry over RDAP and reports
    the ground truth that WHMCS cannot be trusted for:

      * registrarOfRecord / ianaId - who ACTUALLY holds the domain. FFC only owns
                                      domains held at Cloudflare or eNom; anything
                                      else is not ours (someone re-registered it,
                                      or it is hosted in our DNS for a third party).
      * registryExpiry              - the real expiry at the registry (WHMCS expiry
                                      is frequently stale and wrongly marks live
                                      domains "Expired").
      * transferLocked              - clientTransferProhibited is set, so a transfer
                                      will bounce until the lock is removed.
      * bucket                      - actionable classification (see below).

    It makes only outbound RDAP GETs (no secrets, no writes, no money) and is safe
    to run anywhere with internet access.

    Buckets:
      AT_CLOUDFLARE - registrar is Cloudflare; migration already complete.
      ENOM_READY    - registrar is eNom, not expired, not transfer-locked -> ready.
      ENOM_LOCKED   - registrar is eNom but clientTransferProhibited -> unlock first.
      ENOM_EXPIRED  - registrar is eNom but registry expiry is in the past -> renew.
      OTHER         - registrar is neither Cloudflare nor eNom -> NOT FFC's; review.
      UNREGISTERED  - registry returns no record (404) -> dropped / available.
      UNKNOWN       - no RDAP server for the TLD, or the lookup errored -> review.

.PARAMETER Domain
    One or more domains to probe directly.

.PARAMETER DomainsCsv
    Path to a CSV with a 'zone' or 'domain' column (e.g. the cf_zones.csv the
    transfer preflight derives from the Sites Master List).

.PARAMETER OutputFile
    CSV path for results. Default 'domain_registry_truth.csv'.

.PARAMETER ThrottleMs
    Milliseconds to pause between RDAP calls (politeness). Default 250.

.OUTPUTS
    Writes -OutputFile and prints a JSON summary (bucket counts) on stdout.

.EXAMPLE
    pwsh -File scripts/domain-registry-probe.ps1 -DomainsCsv artifacts/transfer/cf_zones.csv -OutputFile artifacts/transfer/registry_truth.csv

.EXAMPLE
    pwsh -File scripts/domain-registry-probe.ps1 -Domain technologymonastery.us -Domain freeforcharity.org
#>
[CmdletBinding(DefaultParameterSetName = 'Csv')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Direct')]
    [string[]]$Domain,

    [Parameter(Mandatory = $true, ParameterSetName = 'Csv')]
    [string]$DomainsCsv,

    [Parameter()]
    [string]$OutputFile = 'domain_registry_truth.csv',

    [Parameter()]
    [int]$ThrottleMs = 250
)

$ErrorActionPreference = 'Stop'

function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

# TLDs whose RDAP server is not in the IANA gTLD bootstrap (ccTLDs etc.).
$SpecialRdap = @{
    'us' = 'https://rdap.nic.us'
}

# Build a TLD -> RDAP base URL map from the IANA bootstrap (cached per run).
function Get-RdapBootstrap {
    try {
        $boot = Invoke-RestMethod -Uri 'https://data.iana.org/rdap/dns.json' -TimeoutSec 30
    }
    catch {
        Write-Diag "WARN: could not fetch RDAP bootstrap: $($_.Exception.Message)"
        return @{}
    }
    $map = @{}
    foreach ($svc in $boot.services) {
        $tlds = $svc[0]; $urls = $svc[1]
        if (-not $urls -or $urls.Count -eq 0) { continue }
        $base = ($urls[0]).TrimEnd('/')
        foreach ($t in $tlds) { $map[$t.ToLowerInvariant()] = $base }
    }
    return $map
}

# RDAP eventDate is ISO 8601; Invoke-RestMethod may hand it back as a [datetime]
# (locale-formatted on [string]) or as a raw string. Normalise to yyyy-MM-dd.
function Format-RdapDate {
    param($Value)
    if ($null -eq $Value) { return '' }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime().ToString('yyyy-MM-dd') }
    [datetime]$d = [datetime]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    if ([datetime]::TryParse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$d)) {
        return $d.ToString('yyyy-MM-dd')
    }
    return [string]$Value
}

function Resolve-RdapBase {
    param([string]$DomainName, [hashtable]$Map)
    $tld = ($DomainName -split '\.')[-1].ToLowerInvariant()
    if ($SpecialRdap.ContainsKey($tld)) { return $SpecialRdap[$tld] }
    if ($Map.ContainsKey($tld)) { return $Map[$tld] }
    return $null
}

# Recursively find the registrar entity (role 'registrar') in an RDAP response.
function Find-Registrar {
    param([object[]]$Entities)
    foreach ($e in $Entities) {
        $roles = @($e.roles)
        if ($roles -contains 'registrar') {
            $name = $null; $iana = $null
            if ($e.vcardArray -and $e.vcardArray.Count -ge 2) {
                foreach ($v in $e.vcardArray[1]) {
                    if ($v[0] -eq 'fn') { $name = [string]$v[3] }
                }
            }
            foreach ($pubId in @($e.publicIds)) {
                if ("$($pubId.type)" -match 'IANA') { $iana = [string]$pubId.identifier }
            }
            return [pscustomobject]@{ name = $name; iana = $iana }
        }
        if ($e.entities) {
            $nested = Find-Registrar -Entities @($e.entities)
            if ($nested) { return $nested }
        }
    }
    return $null
}

function Get-RegistryTruth {
    param([string]$DomainName, [hashtable]$Map)

    $row = [ordered]@{
        domain            = $DomainName
        registrarOfRecord = ''
        ianaId            = ''
        registryExpiry    = ''
        registryCreated   = ''
        registryStatus    = ''
        transferLocked    = ''
        registryExpired   = ''
        bucket            = ''
    }

    $base = Resolve-RdapBase -DomainName $DomainName -Map $Map
    if (-not $base) { $row.bucket = 'UNKNOWN'; return [pscustomobject]$row }

    $uri = "$base/domain/$DomainName"
    try {
        $resp = Invoke-RestMethod -Uri $uri -Headers @{ Accept = 'application/rdap+json' } -TimeoutSec 30
    }
    catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -eq 404) { $row.bucket = 'UNREGISTERED'; return [pscustomobject]$row }
        Write-Diag "WARN: RDAP lookup failed for $DomainName ($code): $($_.Exception.Message)"
        $row.bucket = 'UNKNOWN'; return [pscustomobject]$row
    }

    foreach ($ev in @($resp.events)) {
        switch ($ev.eventAction) {
            'expiration'   { $row.registryExpiry = Format-RdapDate $ev.eventDate }
            'registration' { $row.registryCreated = Format-RdapDate $ev.eventDate }
        }
    }
    $status = @($resp.status)
    $row.registryStatus = ($status -join ',')
    $row.transferLocked = [bool]($status -contains 'client transfer prohibited' -or $status -contains 'server transfer prohibited')

    $reg = Find-Registrar -Entities @($resp.entities)
    if ($reg) { $row.registrarOfRecord = $reg.name; $row.ianaId = $reg.iana }

    # Real expiry vs now.
    $expired = $false
    if ($row.registryExpiry) {
        [datetime]$d = [datetime]::MinValue
        $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
        if ([datetime]::TryParse($row.registryExpiry, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$d)) {
            $expired = ($d -lt [datetime]::UtcNow)
        }
    }
    $row.registryExpired = $expired

    # Classify by registrar of record (only Cloudflare / eNom are FFC's).
    $rl = ("$($row.registrarOfRecord)").ToLowerInvariant()
    $isCf = ($rl -match 'cloudflare') -or ($row.ianaId -eq '1910')
    $isEnom = ($rl -match 'enom') -or ($row.ianaId -in @('48', '1316'))
    if ($isCf) { $row.bucket = 'AT_CLOUDFLARE' }
    elseif ($isEnom) {
        if ($expired) { $row.bucket = 'ENOM_EXPIRED' }
        elseif ($row.transferLocked) { $row.bucket = 'ENOM_LOCKED' }
        else { $row.bucket = 'ENOM_READY' }
    }
    elseif ($row.registrarOfRecord) { $row.bucket = 'OTHER' }
    else { $row.bucket = 'UNKNOWN' }

    return [pscustomobject]$row
}

# Resolve input list.
$domains = @()
if ($PSCmdlet.ParameterSetName -eq 'Direct') {
    $domains = $Domain
}
else {
    if (-not (Test-Path $DomainsCsv)) { throw "DomainsCsv not found: $DomainsCsv" }
    foreach ($r in (Import-Csv $DomainsCsv)) {
        $d = $r.zone; if (-not $d) { $d = $r.domain }
        if ($d) { $domains += ([string]$d).Trim().ToLowerInvariant() }
    }
}
$domains = $domains | Where-Object { $_ } | Sort-Object -Unique

$map = Get-RdapBootstrap
$results = New-Object System.Collections.Generic.List[object]
foreach ($d in $domains) {
    $results.Add((Get-RegistryTruth -DomainName $d -Map $map))
    if ($ThrottleMs -gt 0) { Start-Sleep -Milliseconds $ThrottleMs }
}

$results | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

$summary = [ordered]@{
    source       = if ($DomainsCsv) { $DomainsCsv } else { 'direct' }
    outputFile   = $OutputFile
    total        = $results.Count
    atCloudflare = @($results | Where-Object { $_.bucket -eq 'AT_CLOUDFLARE' }).Count
    enomReady    = @($results | Where-Object { $_.bucket -eq 'ENOM_READY' }).Count
    enomLocked   = @($results | Where-Object { $_.bucket -eq 'ENOM_LOCKED' }).Count
    enomExpired  = @($results | Where-Object { $_.bucket -eq 'ENOM_EXPIRED' }).Count
    other        = @($results | Where-Object { $_.bucket -eq 'OTHER' }).Count
    unregistered = @($results | Where-Object { $_.bucket -eq 'UNREGISTERED' }).Count
    unknown      = @($results | Where-Object { $_.bucket -eq 'UNKNOWN' }).Count
}
$summary | ConvertTo-Json -Depth 3
