<#
.SYNOPSIS
    READ-ONLY transfer-readiness preflight for moving domains to Cloudflare
    Registrar (eNOM -> Cloudflare; parent project #157).

.DESCRIPTION
    Offline analysis: consumes CSV inventory already produced elsewhere and
    computes, per domain, whether it is ready to start a registrar transfer to
    Cloudflare. It makes NO API calls and needs NO secrets, so it is safe to run
    anywhere.

    It is the readiness layer between the transition inventory
    (enom-cloudflare-transition-inventory.csv / whmcs_domains.csv) and the
    manual Cloudflare dashboard transfer-in. It never initiates a transfer.

    For each domain it evaluates:
      * currentRegistrar / alreadyAtCloudflare (skip if already there)
      * expiryOk        - not expired and >= -MinDaysToExpiry days remaining
      * postRegLockOk   - >= -PostRegLockDays since registration (ICANN 60-day
                          lock after registration or a prior transfer)
      * inCloudflareZone / nameserversAtCloudflare (when a zones CSV is supplied)
      * category        - Cat1/Cat2/Cat3 (read from inventory, else derived)
      * readiness       - ready | blocked | review | done, with reasons[]

    Lock status and WHOIS-privacy state are NOT in the inventory exports, so they
    are emitted as explicit "confirm in dashboard" items in the per-domain
    runbook rather than hard-gated here.

.PARAMETER Domain
    Evaluate a single domain. Provide its facts inline with -CurrentRegistrar /
    -ExpiryDate / -RegistrationDate (any omitted fact is treated as unknown).

.PARAMETER InventoryCsv
    Path to enom-cloudflare-transition-inventory.csv (preferred) or any CSV with
    a 'domain' column plus optional 'registrar'/'expirydate'/'regdate'/
    'in_cloudflare'/'category'/'http_health' columns.

.PARAMETER WhmcsDomainsCsv
    Path to whmcs_domains.csv (from scripts/whmcs-domain-export.ps1). Used when
    -InventoryCsv is not supplied.

.PARAMETER CloudflareZonesCsv
    Optional path to a CSV of Cloudflare zones (a 'zone' or 'domain' column, and
    an optional 'name_servers' column). Enables zone/NS evaluation.

.PARAMETER MinDaysToExpiry
    Minimum days to expiry to consider a domain transfer-ready. Default 15.

.PARAMETER PostRegLockDays
    Days since registration before a transfer is allowed (ICANN). Default 60.

.PARAMETER OutputFile
    CSV path for batch results. Default 'domain_transfer_preflight.csv'.

.PARAMETER RunbookDir
    Optional directory to write a per-domain dashboard runbook
    (runbook-<domain>.md) for every domain whose readiness is 'ready'.

.OUTPUTS
    For -Domain: a single JSON object on stdout.
    For CSV inputs: writes -OutputFile and prints a JSON summary on stdout.

.EXAMPLE
    pwsh -File scripts/domain-transfer-preflight.ps1 -InventoryCsv _run_artifacts/enom_cloudflare_transition_inventory.csv -RunbookDir _run_artifacts/runbooks

.EXAMPLE
    pwsh -File scripts/domain-transfer-preflight.ps1 -Domain example.org -CurrentRegistrar enom -ExpiryDate 2027-01-01 -RegistrationDate 2020-01-01
#>
[CmdletBinding(DefaultParameterSetName = 'Csv')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Single')]
    [string]$Domain,

    [Parameter(ParameterSetName = 'Single')]
    [string]$CurrentRegistrar,

    [Parameter(ParameterSetName = 'Single')]
    [string]$ExpiryDate,

    [Parameter(ParameterSetName = 'Single')]
    [string]$RegistrationDate,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$InventoryCsv,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$WhmcsDomainsCsv,

    [Parameter()]
    [string]$CloudflareZonesCsv,

    [Parameter()]
    [int]$MinDaysToExpiry = 15,

    [Parameter()]
    [int]$PostRegLockDays = 60,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$OutputFile = 'domain_transfer_preflight.csv',

    [Parameter()]
    [string]$RunbookDir
)

$ErrorActionPreference = 'Stop'

# Human-readable diagnostics go to stderr so stdout stays parseable.
function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Normalize-DomainName {
    param([Parameter(Mandatory = $true)][string]$Value)
    $d = $Value.Trim().ToLowerInvariant()
    if ($d.StartsWith('http://') -or $d.StartsWith('https://')) {
        try { $d = ([uri]$d).Host } catch {}
    }
    return $d.Trim('.')
}

# Tolerant date parse (WHMCS uses yyyy-MM-dd; treat '0000-00-00'/blank as null).
function ConvertTo-DateOrNull {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $v = $Value.Trim()
    if ($v -eq '0000-00-00' -or $v -eq '0000-00-00 00:00:00') { return $null }
    [datetime]$parsed = [datetime]::MinValue
    $invariant = [System.Globalization.CultureInfo]::InvariantCulture
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    if ([datetime]::TryParse($v, $invariant, $styles, [ref]$parsed)) { return $parsed }
    return $null
}

# First non-empty property value among a list of candidate column names.
function Get-Field {
    param([object]$Row, [string[]]$Names)
    foreach ($n in $Names) {
        $p = $Row.PSObject.Properties[$n]
        if ($p -and -not [string]::IsNullOrWhiteSpace([string]$p.Value)) { return [string]$p.Value }
    }
    return $null
}

function Test-AtCloudflare {
    param([string]$Registrar)
    if ([string]::IsNullOrWhiteSpace($Registrar)) { return $false }
    return ($Registrar -match '(?i)cloudflare')
}

function Test-IsTruthy {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return ($Value.Trim() -match '(?i)^(true|yes|y|1)$')
}

# Build the normalized set of Cloudflare zones (domain -> name_servers) if given.
function Import-ZonesIndex {
    param([string]$Path)
    $index = @{}
    if ([string]::IsNullOrWhiteSpace($Path)) { return $index }
    if (-not (Test-Path -LiteralPath $Path)) { throw "CloudflareZonesCsv not found: $Path" }
    foreach ($row in (Import-Csv -LiteralPath $Path)) {
        $z = Get-Field -Row $row -Names @('zone', 'domain', 'name')
        if ([string]::IsNullOrWhiteSpace($z)) { continue }
        $key = Normalize-DomainName -Value $z
        $ns = Get-Field -Row $row -Names @('name_servers', 'nameservers', 'ns')
        $index[$key] = $ns
    }
    return $index
}

# Core evaluation for one domain's facts. Returns an ordered result object.
function Get-Readiness {
    param(
        [string]$DomainName,
        [string]$Registrar,
        [datetime]$Expiry,
        [bool]$HasExpiry,
        [datetime]$RegDate,
        [bool]$HasRegDate,
        [string]$Category,
        [bool]$HasZoneInfo,
        [bool]$InZone,
        [string]$NameServers,
        [string]$HttpHealth
    )

    $today = (Get-Date).Date
    $atCloudflare = Test-AtCloudflare -Registrar $Registrar

    $daysToExpiry = $null
    $expiryOk = $null
    if ($HasExpiry) {
        $daysToExpiry = [int]([math]::Floor(($Expiry.Date - $today).TotalDays))
        $expiryOk = ($daysToExpiry -ge $MinDaysToExpiry)
    }

    $daysSinceReg = $null
    $postRegLockOk = $null
    if ($HasRegDate) {
        $daysSinceReg = [int]([math]::Floor(($today - $RegDate.Date).TotalDays))
        $postRegLockOk = ($daysSinceReg -ge $PostRegLockDays)
    }

    $nsAtCloudflare = $null
    if (-not [string]::IsNullOrWhiteSpace($NameServers)) {
        $nsAtCloudflare = ($NameServers -match '(?i)cloudflare|freeforcharity')
    }

    # Derive category when not supplied by the inventory.
    if ([string]::IsNullOrWhiteSpace($Category) -and $HasZoneInfo) {
        if ($InZone -and -not $atCloudflare) { $Category = 'Cat1' }
        elseif (-not $InZone) { $Category = 'Cat2' }
    }

    $reasons = @()
    $readiness = 'ready'

    if ($atCloudflare) {
        $readiness = 'done'
        $reasons += 'Already registered at Cloudflare; no transfer needed.'
    }
    else {
        if (-not [string]::IsNullOrWhiteSpace($Registrar) -and $Registrar -notmatch '(?i)enom') {
            $readiness = 'review'
            $reasons += "Registrar '$Registrar' is not eNOM; confirm it is transferable."
        }
        if ([string]::IsNullOrWhiteSpace($Registrar)) {
            $readiness = 'review'
            $reasons += 'Registrar unknown (domain may not be in WHMCS); confirm losing registrar.'
        }
        if ($HasExpiry -and -not $expiryOk) {
            $readiness = 'blocked'
            if ($daysToExpiry -lt 0) { $reasons += "Domain expired ($([math]::Abs($daysToExpiry)) days ago)." }
            else { $reasons += "Only $daysToExpiry days to expiry (need >= $MinDaysToExpiry)." }
        }
        if ($HasRegDate -and -not $postRegLockOk) {
            $readiness = 'blocked'
            $reasons += "Within ICANN $PostRegLockDays-day post-registration lock ($daysSinceReg days since registration)."
        }
        if (-not $HasExpiry) { $reasons += 'Expiry date unknown; confirm before transferring.' }
        if (-not $HasRegDate) { $reasons += "Registration date unknown; confirm the $PostRegLockDays-day lock has passed." }
        if ($readiness -eq 'ready') { $reasons += 'No blockers detected from inventory data.' }
    }

    return [ordered]@{
        domain                  = $DomainName
        currentRegistrar        = $Registrar
        alreadyAtCloudflare     = $atCloudflare
        category                = $Category
        inCloudflareZone        = $(if ($HasZoneInfo) { $InZone } else { $null })
        nameServers             = $NameServers
        nameserversAtCloudflare = $nsAtCloudflare
        httpHealth              = $HttpHealth
        daysToExpiry            = $daysToExpiry
        expiryOk                = $expiryOk
        daysSinceRegistration   = $daysSinceReg
        postRegLockOk           = $postRegLockOk
        readiness               = $readiness
        reasons                 = ($reasons -join ' ')
    }
}

# Render the per-domain dashboard runbook (the one manual step the API can't do).
function Write-Runbook {
    param([object]$R, [string]$Dir)
    if ([string]::IsNullOrWhiteSpace($Dir)) { return }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $path = Join-Path $Dir ("runbook-{0}.md" -f ($R.domain -replace '[^a-z0-9.-]', '_'))
    $lines = @(
        "# Transfer runbook — $($R.domain)",
        '',
        "Category: $($R.category)  |  Current registrar: $($R.currentRegistrar)  |  Readiness: $($R.readiness)",
        '',
        '## 1. At the losing registrar (eNOM / WHMCS)',
        '- [ ] Unlock the domain (disable registrar lock).',
        '- [ ] Disable WHOIS privacy if it blocks transfer.',
        '- [ ] Obtain the EPP/auth code (run workflow "16. Domain - Transfer EPP/Auth Code Probe").',
        '- [ ] Confirm admin contact email is reachable (approval emails go there).',
        '',
        '## 2. At Cloudflare (dashboard — the one manual step)',
        '- [ ] dash.cloudflare.com -> Domain Registration -> Transfer Domains.',
        "- [ ] Select **$($R.domain)** and enter the EPP/auth code.",
        '- [ ] Confirm contacts and complete the transfer (this charges/renews +1 year).',
        '',
        '## 3. After transfer',
        '- [ ] Approve the transfer from the losing registrar email if prompted.',
        '- [ ] Run workflow "25. Domain - Post-Transfer Verification" to confirm registrar = Cloudflare.',
        ''
    )
    $lines | Out-File -FilePath $path -Encoding utf8
    Write-Diag "Wrote runbook: $path"
}

try {
    $zones = Import-ZonesIndex -Path $CloudflareZonesCsv
    $hasZones = ($zones.Count -gt 0)

    if ($PSCmdlet.ParameterSetName -eq 'Single') {
        $d = Normalize-DomainName -Value $Domain
        if ([string]::IsNullOrWhiteSpace($d)) { throw 'Domain is required.' }

        $exp = ConvertTo-DateOrNull -Value $ExpiryDate
        $reg = ConvertTo-DateOrNull -Value $RegistrationDate
        $ns = if ($hasZones -and $zones.ContainsKey($d)) { $zones[$d] } else { $null }
        $inZone = $hasZones -and $zones.ContainsKey($d)

        $r = Get-Readiness -DomainName $d -Registrar $CurrentRegistrar `
            -Expiry ($(if ($exp) { $exp } else { [datetime]::MinValue })) -HasExpiry ($null -ne $exp) `
            -RegDate ($(if ($reg) { $reg } else { [datetime]::MinValue })) -HasRegDate ($null -ne $reg) `
            -Category $null -HasZoneInfo $hasZones -InZone $inZone -NameServers $ns -HttpHealth $null

        if ($r.readiness -eq 'ready') { Write-Runbook -R ([pscustomobject]$r) -Dir $RunbookDir }
        ([pscustomobject]$r) | ConvertTo-Json -Depth 6
        exit 0
    }

    # CSV mode
    $sourcePath = if (-not [string]::IsNullOrWhiteSpace($InventoryCsv)) { $InventoryCsv } else { $WhmcsDomainsCsv }
    if ([string]::IsNullOrWhiteSpace($sourcePath)) {
        throw 'Provide -InventoryCsv or -WhmcsDomainsCsv (or use -Domain for a single domain).'
    }
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Input CSV not found: $sourcePath" }

    $rows = @(Import-Csv -LiteralPath $sourcePath)
    Write-Diag ("Loaded {0} row(s) from {1}" -f $rows.Count, $sourcePath)

    $results = foreach ($row in $rows) {
        $name = Get-Field -Row $row -Names @('domain', 'domainname', 'zone', 'name')
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        $d = Normalize-DomainName -Value $name

        $registrar = Get-Field -Row $row -Names @('registrar', 'current_registrar', 'whmcs_registrar')
        $expRaw = Get-Field -Row $row -Names @('expirydate', 'expiry', 'expires', 'expiry_date')
        $regRaw = Get-Field -Row $row -Names @('regdate', 'registrationdate', 'registration_date')
        $category = Get-Field -Row $row -Names @('category', 'cat')
        $httpHealth = Get-Field -Row $row -Names @('http_health', 'health', 'http_status')

        $exp = ConvertTo-DateOrNull -Value $expRaw
        $reg = ConvertTo-DateOrNull -Value $regRaw

        # Zone presence: prefer an explicit inventory column, else the zones CSV.
        $inZoneCol = Get-Field -Row $row -Names @('in_cloudflare', 'incloudflare', 'in_zone')
        $hasZoneInfo = $hasZones -or (-not [string]::IsNullOrWhiteSpace($inZoneCol))
        $inZone = $false
        $ns = $null
        if (-not [string]::IsNullOrWhiteSpace($inZoneCol)) { $inZone = Test-IsTruthy -Value $inZoneCol }
        if ($hasZones -and $zones.ContainsKey($d)) { $inZone = $true; $ns = $zones[$d] }
        if ([string]::IsNullOrWhiteSpace($ns)) { $ns = Get-Field -Row $row -Names @('name_servers', 'nameservers') }

        $r = Get-Readiness -DomainName $d -Registrar $registrar `
            -Expiry ($(if ($exp) { $exp } else { [datetime]::MinValue })) -HasExpiry ($null -ne $exp) `
            -RegDate ($(if ($reg) { $reg } else { [datetime]::MinValue })) -HasRegDate ($null -ne $reg) `
            -Category $category -HasZoneInfo $hasZoneInfo -InZone $inZone -NameServers $ns -HttpHealth $httpHealth

        if ($r.readiness -eq 'ready') { Write-Runbook -R ([pscustomobject]$r) -Dir $RunbookDir }
        [pscustomobject]$r
    }

    $results = @($results)
    $results | Sort-Object readiness, domain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

    $summary = [ordered]@{
        source     = $sourcePath
        outputFile = $OutputFile
        total      = $results.Count
        ready      = @($results | Where-Object { $_.readiness -eq 'ready' }).Count
        blocked    = @($results | Where-Object { $_.readiness -eq 'blocked' }).Count
        review     = @($results | Where-Object { $_.readiness -eq 'review' }).Count
        done       = @($results | Where-Object { $_.readiness -eq 'done' }).Count
    }
    Write-Diag ("Preflight: total={0} ready={1} blocked={2} review={3} done={4}" -f `
            $summary.total, $summary.ready, $summary.blocked, $summary.review, $summary.done)
    ([pscustomobject]$summary) | ConvertTo-Json -Depth 5
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
