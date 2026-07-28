<#
.SYNOPSIS
    READ-ONLY inbound-transfer preflight for domains held at a FOREIGN registrar
    (neither eNom nor Cloudflare) that need to come under FFC management.

.DESCRIPTION
    The outbound pipeline (scripts/domain-transfer-preflight.ps1, workflows
    115/116/117) assumes the losing registrar is eNom. Domains that arrive at
    FFC still registered somewhere else -- Wix, Squarespace, GoDaddy -- fall out
    of that pipeline as readiness 'review' and stop there: no runbook is written
    and no path is defined. This script is that missing inbound layer.

    Offline analysis only: it consumes CSV already produced elsewhere (the RDAP
    registry-truth export is the preferred input), makes NO API calls, needs NO
    secrets, and never initiates a transfer.

    The decisive fact per registrar is whether it will let the registrant
    delegate nameservers away, because that -- not the registrar transfer --
    is what actually unblocks putting a site behind Cloudflare:

      * Wix does NOT permit editing NS records on a Wix-registered domain
        (its own help center: "it's not possible to change name servers (edit
        NS records) for a Wix domain"). Cloudflare's free plan requires full
        nameserver delegation, and partial/CNAME setup is Business-plan only.
        So a Wix domain cannot reach Cloudflare at all until the registration
        moves. Path: 'transfer-required'.

      * Squarespace / GoDaddy / most others DO permit NS delegation. Those can
        point at Cloudflare immediately, and the registrar transfer becomes an
        unhurried follow-up rather than a blocker. Path: 'ns-delegation'.

    For the transfer-required case the target is eNom (FFC's registrar of
    record, via WHMCS) rather than Cloudflare Registrar directly, because
    Cloudflare Registrar only accepts domains whose zone is already active on
    Cloudflare nameservers -- which the losing registrar is precisely what
    blocks. eNom breaks the deadlock; Cloudflare Registrar comes later via the
    existing 115/116/117 pipeline once the ICANN 60-day lock expires.

    Per domain it evaluates:
      * registrarFamily      - wix | squarespace | godaddy | enom | cloudflare | other
      * nsDelegationAllowed  - can NS be pointed at Cloudflare without transferring?
      * inboundPath          - transfer-required | ns-delegation | none
      * expiryOk             - not expired and >= -MinDaysToExpiry days remaining
      * postRegLockOk        - >= -PostRegLockDays since registration/last transfer
      * transferLocked       - registry clientTransferProhibited
      * readiness            - ready | blocked | review | done
      * bucket               - see below
      * reasons              - single human-readable summary string

    Buckets:
      AT_CLOUDFLARE             - registrar is Cloudflare; nothing to do here.
      AT_ENOM                   - already at eNom; use the outbound pipeline (115).
      FOREIGN_TRANSFER_REQUIRED - foreign registrar that blocks NS delegation (Wix).
      FOREIGN_NS_CAPABLE        - foreign registrar that permits NS delegation.
      BLOCKED                   - foreign, but expired / locked / inside ICANN lock.
      UNKNOWN                   - registrar could not be determined.

    Unlike the outbound preflight, a runbook is written for every actionable
    foreign domain including blocked ones (with its blockers listed at the top),
    so a domain never silently drops out of the worklist.

.PARAMETER Domain
    Evaluate a single domain. Provide its facts inline with -CurrentRegistrar /
    -ExpiryDate / -RegistrationDate / -TransferLocked.

.PARAMETER RegistryTruthCsv
    Path to docs/domain-registry-truth.csv or the artifact produced by
    scripts/domain-registry-probe.ps1 (columns: domain, registrarOfRecord,
    registryExpiry, registryCreated, transferLocked, bucket). Preferred input --
    RDAP is authoritative where WHMCS is frequently stale.

.PARAMETER InventoryCsv
    Any CSV with a 'domain' column plus optional 'registrar' / 'expirydate' /
    'regdate' / 'transferlocked' columns. Used when -RegistryTruthCsv is absent.

.PARAMETER MinDaysToExpiry
    Minimum days to expiry to consider a domain transfer-ready. Default 15.

.PARAMETER PostRegLockDays
    Days since registration/last transfer before a transfer is allowed (ICANN).
    Default 60.

.PARAMETER OutputFile
    CSV path for batch results. Default 'domain_inbound_transfer_preflight.csv'.

.PARAMETER RunbookDir
    Optional directory to write a per-domain runbook (runbook-inbound-<domain>.md)
    for every actionable foreign domain.

.OUTPUTS
    For -Domain: a single JSON object on stdout.
    For CSV inputs: writes -OutputFile and prints a JSON summary on stdout.

.EXAMPLE
    pwsh -File scripts/domain-inbound-transfer-preflight.ps1 -RegistryTruthCsv docs/domain-registry-truth.csv -RunbookDir _run_artifacts/runbooks

.EXAMPLE
    pwsh -File scripts/domain-inbound-transfer-preflight.ps1 -Domain cactuswrenpreschool.com -CurrentRegistrar 'Wix.com Ltd.' -RegistrationDate 2015-03-01 -ExpiryDate 2027-03-01
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

    [Parameter(ParameterSetName = 'Single')]
    [string]$TransferLocked,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$RegistryTruthCsv,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$InventoryCsv,

    [Parameter()]
    [int]$MinDaysToExpiry = 15,

    [Parameter()]
    [int]$PostRegLockDays = 60,

    [Parameter(ParameterSetName = 'Csv')]
    [string]$OutputFile = 'domain_inbound_transfer_preflight.csv',

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

function Test-IsTruthy {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return ($Value.Trim() -match '(?i)^(true|yes|y|1)$')
}

<#
    Classify the registrar into a family and record whether it permits the
    registrant to delegate nameservers elsewhere.

    nsDelegationAllowed = $false is the expensive case: it means Cloudflare is
    unreachable until the registration itself moves. Only Wix is known to
    enforce this among registrars FFC has encountered; anything unrecognized is
    assumed capable but flagged for confirmation rather than silently trusted.
#>
function Get-RegistrarProfile {
    param([string]$Registrar)

    $r = ("$Registrar").ToLowerInvariant()

    if ([string]::IsNullOrWhiteSpace($r)) {
        return [ordered]@{ family = 'unknown'; nsDelegationAllowed = $null; isForeign = $true }
    }
    if ($r -match 'cloudflare') {
        return [ordered]@{ family = 'cloudflare'; nsDelegationAllowed = $true; isForeign = $false }
    }
    if ($r -match 'enom') {
        return [ordered]@{ family = 'enom'; nsDelegationAllowed = $true; isForeign = $false }
    }
    if ($r -match 'wix') {
        return [ordered]@{ family = 'wix'; nsDelegationAllowed = $false; isForeign = $true }
    }
    if ($r -match 'squarespace') {
        return [ordered]@{ family = 'squarespace'; nsDelegationAllowed = $true; isForeign = $true }
    }
    if ($r -match 'godaddy') {
        return [ordered]@{ family = 'godaddy'; nsDelegationAllowed = $true; isForeign = $true }
    }
    return [ordered]@{ family = 'other'; nsDelegationAllowed = $true; isForeign = $true }
}

# Core evaluation for one domain's facts. Returns an ordered result object.
function Get-InboundReadiness {
    param(
        [string]$DomainName,
        [string]$Registrar,
        [datetime]$Expiry,
        [bool]$HasExpiry,
        [datetime]$RegDate,
        [bool]$HasRegDate,
        [bool]$TransferLocked,
        [int]$MinDaysToExpiry,
        [int]$PostRegLockDays
    )

    # UTC, to match ConvertTo-DateOrNull's AssumeUniversal/AdjustToUniversal
    # parse. Mixing a local "today" with UTC-normalized registry dates shifts
    # daysToExpiry / daysSinceRegistration by a day either side of midnight
    # depending on the runner's timezone, which is exactly the wrong behaviour
    # at an ICANN 60-day lock boundary.
    $today = [datetime]::UtcNow.Date
    $regProfile = Get-RegistrarProfile -Registrar $Registrar

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

    $reasons = @()
    $readiness = 'ready'
    $bucket = ''
    $inboundPath = 'transfer-required'

    if ($regProfile.family -eq 'cloudflare') {
        $readiness = 'done'
        $bucket = 'AT_CLOUDFLARE'
        $inboundPath = 'none'
        $reasons += 'Already at Cloudflare Registrar; nothing to do.'
    }
    elseif ($regProfile.family -eq 'enom') {
        $readiness = 'done'
        $bucket = 'AT_ENOM'
        $inboundPath = 'none'
        $reasons += 'Already at eNom (FFC registrar); use the outbound pipeline (workflow 115) for Cloudflare Registrar.'
    }
    elseif ($regProfile.family -eq 'unknown') {
        $readiness = 'review'
        $bucket = 'UNKNOWN'
        $inboundPath = 'transfer-required'
        $reasons += 'Registrar of record unknown; run the RDAP probe (scripts/domain-registry-probe.ps1) before planning a transfer.'
    }
    else {
        # Foreign registrar. Decide the path, then apply transfer blockers.
        if ($regProfile.nsDelegationAllowed) {
            $inboundPath = 'ns-delegation'
            $bucket = 'FOREIGN_NS_CAPABLE'
            $reasons += "Registrar '$Registrar' permits nameserver delegation; point NS at Cloudflare now and transfer to eNom separately."
        }
        else {
            $inboundPath = 'transfer-required'
            $bucket = 'FOREIGN_TRANSFER_REQUIRED'
            $reasons += "Registrar '$Registrar' does not permit nameserver delegation; the registration must move to eNom before Cloudflare is reachable."
        }

        if ($regProfile.family -eq 'other') {
            $reasons += 'Registrar not in the known-behaviour list; confirm it allows NS changes and outbound transfers.'
        }

        # Blockers apply to the registrar transfer, not to an NS-only change.
        $blocked = $false
        if ($HasExpiry -and -not $expiryOk) {
            $blocked = $true
            if ($daysToExpiry -lt 0) { $reasons += "Domain expired ($([math]::Abs($daysToExpiry)) days ago); renew before transferring." }
            else { $reasons += "Only $daysToExpiry days to expiry (need >= $MinDaysToExpiry)." }
        }
        if ($HasRegDate -and -not $postRegLockOk) {
            $blocked = $true
            $reasons += "Within ICANN $PostRegLockDays-day post-registration/transfer lock ($daysSinceReg days since registration)."
        }
        if ($TransferLocked) {
            $blocked = $true
            $reasons += 'Registry reports clientTransferProhibited; unlock at the losing registrar first.'
        }
        if (-not $HasExpiry) { $reasons += 'Expiry date unknown; confirm before transferring.' }
        if (-not $HasRegDate) { $reasons += "Registration date unknown; confirm the $PostRegLockDays-day lock has passed." }

        if ($blocked) {
            $readiness = 'blocked'
            $bucket = 'BLOCKED'
            # An NS-capable registrar can still be pointed at Cloudflare today
            # even while the registrar transfer itself is blocked.
            if ($regProfile.nsDelegationAllowed) {
                $reasons += 'Transfer is blocked, but the nameserver change to Cloudflare is not — it can proceed now.'
            }
        }
    }

    return [ordered]@{
        domain                = $DomainName
        currentRegistrar      = $Registrar
        registrarFamily       = $regProfile.family
        isForeignRegistrar    = $regProfile.isForeign
        nsDelegationAllowed   = $regProfile.nsDelegationAllowed
        inboundPath           = $inboundPath
        daysToExpiry          = $daysToExpiry
        expiryOk              = $expiryOk
        daysSinceRegistration = $daysSinceReg
        postRegLockOk         = $postRegLockOk
        transferLocked        = $TransferLocked
        readiness             = $readiness
        bucket                = $bucket
        reasons               = ($reasons -join ' ')
    }
}

<#
    Render the per-domain inbound runbook.

    The transfer-required (Wix) variant is the four-stage path; the
    ns-delegation variant skips straight to the Cloudflare zone work because
    nothing is actually blocking it.
#>
function Write-InboundRunbook {
    param([object]$R, [string]$Dir)
    if ([string]::IsNullOrWhiteSpace($Dir)) { return }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $path = Join-Path $Dir ("runbook-inbound-{0}.md" -f ($R.domain -replace '[^a-z0-9.-]', '_'))

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# Inbound transfer runbook — $($R.domain)")
    $lines.Add('')
    $lines.Add("Registrar: $($R.currentRegistrar) ($($R.registrarFamily))  |  Path: $($R.inboundPath)  |  Readiness: $($R.readiness)")
    $lines.Add('')

    if ($R.readiness -eq 'blocked') {
        $lines.Add('> **Blocked for registrar transfer.** Resolve these before starting stage 2:')
        $lines.Add('>')
        $lines.Add("> $($R.reasons)")
        $lines.Add('')
    }

    $lines.Add('## 0. Before touching anything: record current DNS')
    $lines.Add('')
    $lines.Add('Losing the existing mail routing is the main way this migration hurts a charity.')
    $lines.Add('Capture the full zone first — it is the rollback.')
    $lines.Add('')
    $lines.Add('```bash')
    $lines.Add("for t in NS A AAAA CNAME MX TXT CAA; do dig +short `$t $($R.domain); done")
    $lines.Add("dig +short CNAME www.$($R.domain)")
    $lines.Add("dig +short TXT _dmarc.$($R.domain)")
    $lines.Add('```')
    $lines.Add('')
    $lines.Add('- [ ] `MX` records saved (Google Workspace / M365 mail breaks instantly if these are dropped).')
    $lines.Add('- [ ] `TXT` SPF, DKIM selectors, and `_dmarc` saved.')
    $lines.Add('- [ ] Any verification `TXT` records saved (Google/Microsoft/Facebook site ownership).')
    $lines.Add('')

    if ($R.inboundPath -eq 'transfer-required') {
        $lines.Add('## 1. At the losing registrar')
        $lines.Add('')
        $lines.Add("This registrar (**$($R.registrarFamily)**) will not let you delegate nameservers, so the")
        $lines.Add('registration itself has to move before Cloudflare is reachable.')
        $lines.Add('')
        $lines.Add('- [ ] Confirm the domain is more than 60 days old at this registrar (ICANN bars transfer before that).')
        $lines.Add('- [ ] Disable DNSSEC — it will otherwise break resolution mid-transfer.')
        $lines.Add('- [ ] Unlock the domain (disable registrar/transfer lock).')
        $lines.Add('- [ ] Disable WHOIS privacy if it blocks the transfer.')
        $lines.Add('- [ ] Confirm the admin contact email is reachable — the EPP code and approval both go there.')
        $lines.Add('- [ ] Request the EPP / authorization code.')
        $lines.Add('')
        if ($R.registrarFamily -eq 'wix') {
            $lines.Add('Wix specifics: the transfer controls live under **Domains → select the domain →')
            $lines.Add('Advanced**. Wix will not change nameservers on request — its help center states')
            $lines.Add('plainly that editing NS records for a Wix domain is not possible — so do not spend')
            $lines.Add('time on a support ticket asking for it.')
            $lines.Add('')
        }
        $lines.Add('## 2. Transfer in to eNom (WHMCS)')
        $lines.Add('')
        $lines.Add('eNom, not Cloudflare Registrar, is the target here: Cloudflare Registrar only accepts')
        $lines.Add('domains whose zone is already live on Cloudflare nameservers, which is exactly what the')
        $lines.Add('losing registrar is blocking. eNom breaks that deadlock.')
        $lines.Add('')
        $lines.Add('- [ ] WHMCS → Orders → Add New Order → **Transfer** a domain, registrar module eNom.')
        $lines.Add("- [ ] Enter **$($R.domain)** and the EPP code from stage 1.")
        $lines.Add('- [ ] Approve the transfer-confirmation email when it arrives (5–7 days if unapproved).')
        $lines.Add('- [ ] Confirm the domain appears in WHMCS with registrar `enom`.')
        $lines.Add('')
        $lines.Add('## 3. Point nameservers at Cloudflare (the step that actually delivers)')
    }
    else {
        $lines.Add('## 1. Point nameservers at Cloudflare (do this first — nothing blocks it)')
    }

    $lines.Add('')
    $lines.Add('No waiting period applies to a nameserver change. Once this lands the site is behind')
    $lines.Add('Cloudflare, regardless of who holds the registration.')
    $lines.Add('')
    $lines.Add('- [ ] Run workflow `110. Cloudflare - Zone Create` for the domain.')
    $lines.Add('- [ ] Recreate every record captured in stage 0 in the Cloudflare zone **before** switching NS.')
    $lines.Add('- [ ] Verify MX/SPF/DKIM/DMARC match the stage-0 capture exactly.')
    $lines.Add('- [ ] Set the nameservers to the Cloudflare pair at the registrar.')
    $lines.Add('- [ ] Run workflow `102. Domain - Add to FFC Cloudflare and WHMCS`.')
    $lines.Add('- [ ] Run workflow `103. Domain - Enforce Domain Standard` once the zone is active.')
    $lines.Add('')

    if ($R.inboundPath -eq 'transfer-required') {
        $lines.Add('## 4. Later: eNom → Cloudflare Registrar')
        $lines.Add('')
        $lines.Add('The stage-2 transfer starts a fresh ICANN 60-day lock, so this cannot run immediately.')
        $lines.Add('This stage is cost optimization only — the site is already fully on Cloudflare after')
        $lines.Add('stage 3, so there is no urgency.')
        $lines.Add('')
        $lines.Add('- [ ] Wait 60 days from the stage-2 transfer completion date.')
        $lines.Add('- [ ] The domain now classifies as `ENOM_READY`; run workflow `115. Domain - Transfer Readiness Preflight`.')
        $lines.Add('- [ ] Continue with workflows `116` (EPP probe) and `117` (post-transfer verification).')
        $lines.Add('')
    }

    $lines.Add('## Rollback')
    $lines.Add('')
    $lines.Add('Before the NS switch: no action needed, nothing has changed.')
    $lines.Add('After the NS switch: restore the stage-0 nameservers at the registrar. DNS-only change,')
    $lines.Add('no registrar involvement, propagates in minutes to hours.')
    $lines.Add('')

    $lines | Out-File -FilePath $path -Encoding utf8
    Write-Diag "Wrote runbook: $path"
}

try {
    if ($PSCmdlet.ParameterSetName -eq 'Single') {
        $d = Normalize-DomainName -Value $Domain
        if ([string]::IsNullOrWhiteSpace($d)) { throw 'Domain is required.' }

        $exp = ConvertTo-DateOrNull -Value $ExpiryDate
        $reg = ConvertTo-DateOrNull -Value $RegistrationDate

        $r = Get-InboundReadiness -DomainName $d -Registrar $CurrentRegistrar `
            -Expiry ($(if ($exp) { $exp } else { [datetime]::MinValue })) -HasExpiry ($null -ne $exp) `
            -RegDate ($(if ($reg) { $reg } else { [datetime]::MinValue })) -HasRegDate ($null -ne $reg) `
            -TransferLocked (Test-IsTruthy -Value $TransferLocked) `
            -MinDaysToExpiry $MinDaysToExpiry -PostRegLockDays $PostRegLockDays

        if ($r.bucket -in @('FOREIGN_TRANSFER_REQUIRED', 'FOREIGN_NS_CAPABLE', 'BLOCKED')) {
            Write-InboundRunbook -R ([pscustomobject]$r) -Dir $RunbookDir
        }
        ([pscustomobject]$r) | ConvertTo-Json -Depth 6
        exit 0
    }

    # CSV mode
    $sourcePath = if (-not [string]::IsNullOrWhiteSpace($RegistryTruthCsv)) { $RegistryTruthCsv } else { $InventoryCsv }
    if ([string]::IsNullOrWhiteSpace($sourcePath)) {
        throw 'Provide -RegistryTruthCsv or -InventoryCsv (or use -Domain for a single domain).'
    }
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Input CSV not found: $sourcePath" }

    $rows = @(Import-Csv -LiteralPath $sourcePath)
    Write-Diag ("Loaded {0} row(s) from {1}" -f $rows.Count, $sourcePath)

    $results = foreach ($row in $rows) {
        $name = Get-Field -Row $row -Names @('domain', 'domainname', 'zone', 'name')
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        $d = Normalize-DomainName -Value $name

        $registrar = Get-Field -Row $row -Names @('registrarOfRecord', 'registrar', 'current_registrar', 'whmcs_registrar')
        $expRaw = Get-Field -Row $row -Names @('registryExpiry', 'expirydate', 'expiry', 'expires', 'expiry_date')
        $regRaw = Get-Field -Row $row -Names @('registryCreated', 'regdate', 'registrationdate', 'registration_date')
        $lockRaw = Get-Field -Row $row -Names @('transferLocked', 'transferlocked', 'locked')

        $exp = ConvertTo-DateOrNull -Value $expRaw
        $reg = ConvertTo-DateOrNull -Value $regRaw

        $r = Get-InboundReadiness -DomainName $d -Registrar $registrar `
            -Expiry ($(if ($exp) { $exp } else { [datetime]::MinValue })) -HasExpiry ($null -ne $exp) `
            -RegDate ($(if ($reg) { $reg } else { [datetime]::MinValue })) -HasRegDate ($null -ne $reg) `
            -TransferLocked (Test-IsTruthy -Value $lockRaw) `
            -MinDaysToExpiry $MinDaysToExpiry -PostRegLockDays $PostRegLockDays

        # Runbooks for every actionable foreign domain, blocked ones included --
        # the outbound preflight only writes them for 'ready', which is how
        # non-eNom domains silently fell off the worklist.
        if ($r.bucket -in @('FOREIGN_TRANSFER_REQUIRED', 'FOREIGN_NS_CAPABLE', 'BLOCKED')) {
            Write-InboundRunbook -R ([pscustomobject]$r) -Dir $RunbookDir
        }
        [pscustomobject]$r
    }

    $results = @($results)
    $results | Sort-Object bucket, domain | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

    $summary = [ordered]@{
        source                  = $sourcePath
        outputFile              = $OutputFile
        total                   = $results.Count
        ready                   = @($results | Where-Object { $_.readiness -eq 'ready' }).Count
        blocked                 = @($results | Where-Object { $_.readiness -eq 'blocked' }).Count
        review                  = @($results | Where-Object { $_.readiness -eq 'review' }).Count
        done                    = @($results | Where-Object { $_.readiness -eq 'done' }).Count
        foreignTransferRequired = @($results | Where-Object { $_.bucket -eq 'FOREIGN_TRANSFER_REQUIRED' }).Count
        foreignNsCapable        = @($results | Where-Object { $_.bucket -eq 'FOREIGN_NS_CAPABLE' }).Count
        atEnom                  = @($results | Where-Object { $_.bucket -eq 'AT_ENOM' }).Count
        atCloudflare            = @($results | Where-Object { $_.bucket -eq 'AT_CLOUDFLARE' }).Count
        unknownRegistrar        = @($results | Where-Object { $_.bucket -eq 'UNKNOWN' }).Count
    }
    Write-Diag ("Inbound preflight: total={0} transferRequired={1} nsCapable={2} blocked={3}" -f `
            $summary.total, $summary.foreignTransferRequired, $summary.foreignNsCapable, $summary.blocked)
    ([pscustomobject]$summary) | ConvertTo-Json -Depth 5
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
