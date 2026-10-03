[CmdletBinding(DefaultParameterSetName = 'Audit')]
param(
    [Parameter(Mandatory = $true, HelpMessage = 'Zone / domain name, e.g. example.org')]
    [string]$Zone,

    # Read the zone's custom-nameserver state and print it. Writes nothing.
    [Parameter(ParameterSetName = 'Audit', Mandatory = $true)]
    [switch]$Audit,

    # FFC's default for every zone it manages: delegate to the ACCOUNT custom
    # nameservers (ns1/ns2.freeforcharity.org) rather than the per-zone pair
    # Cloudflare assigns (e.g. dane/zainab.ns.cloudflare.com).
    [Parameter(ParameterSetName = 'Enable', Mandatory = $true)]
    [switch]$Enable,

    # Back to the zone's assigned Cloudflare pair.
    [Parameter(ParameterSetName = 'Disable', Mandatory = $true)]
    [switch]$Disable,

    # With -Enable / -Disable: decide and PRINT the change, write nothing.
    [Parameter(ParameterSetName = 'Enable')]
    [Parameter(ParameterSetName = 'Disable')]
    [switch]$DryRun,

    # Optional override of the env-discovered tokens (tests, local runs).
    [Parameter()]
    [array]$Tokens
)

<#
.SYNOPSIS
    Audit or set whether a zone uses the Cloudflare ACCOUNT custom nameservers.

.DESCRIPTION
    Every FFC-managed zone delegates to ns1.freeforcharity.org and
    ns2.freeforcharity.org - Cloudflare account-level custom nameservers that
    front the same anycast network as the assigned pair. The registrar side
    (102) has always handed those two names out, but the Cloudflare side is a
    PER-ZONE toggle that nothing in this repo set: a zone 110 created came up
    on its assigned pair (measured 2026-10-02, newheightseducation.org:
    dane/zainab.ns.cloudflare.com), so the names 102 and the onboarding docs
    give a charity would not have been authoritative for it.

    The toggle lives at `/zones/{id}/custom_ns`:

        GET  -> { enabled: bool, ns_set: int, ... }
        PUT  { enabled: true|false }

    The response shape is the one thing this script does NOT assume: the API
    reference was not reachable from the sandbox that wrote this, so a GET
    that carries no `enabled` property is REFUSED, naming the keys it did
    carry, rather than written to from memory - the same discipline as
    cloudflare-ai-crawlers.ps1. Every write is confirmed by a re-read, and the
    zone's resulting name servers are printed so a run log shows the pair the
    charity must set at their registrar.

.EXAMPLE
    .\scripts\cloudflare-custom-nameservers.ps1 -Zone example.org -Audit
    .\scripts\cloudflare-custom-nameservers.ps1 -Zone example.org -Enable
    .\scripts\cloudflare-custom-nameservers.ps1 -Zone example.org -Enable -DryRun

.NOTES
    Exit codes: 0 applied / already in the desired state / audit or dry-run plan printed;
                1 refused, write failed, or re-read disagrees;
                2 usage: no tokens, or the zone is not visible to any token.

    A Cloudflare error on the PUT that names the ACCOUNT (no custom nameservers
    configured there) is an account-level setup task, not something this
    script can do; the message says so.
#>

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

# ---------------------------------------------------------------------------
# The decision, pure and testable
# ---------------------------------------------------------------------------

function Resolve-CustomNsPatch {
    <#
        Given the zone's CURRENT custom_ns object and a desired state, decide
        what to do. Returns a hashtable:

            Action  'noop' | 'patch' | 'refuse'
            Body    the PUT body for 'patch'
            Reason  one line a human can act on

        Pure: no network, no env. tests/cloudflare-custom-nameservers.Tests.ps1
        pins it; the HTTP around it cannot be unit-tested without a zone.
    #>
    param(
        # AllowNull: a `result: null` must reach the refusal and be named as
        # such, not die at the binder.
        [Parameter(Mandatory = $true)][AllowNull()]$Current,
        [Parameter(Mandatory = $true)][ValidateSet('enable', 'disable')][string]$Desired
    )

    $names = @()
    if ($null -ne $Current) {
        $names = @($Current.PSObject.Properties | ForEach-Object { $_.Name })
    }

    if ($names -notcontains 'enabled') {
        $have = if ($names.Count) { $names -join ', ' } else { '(no properties)' }
        return @{
            Action = 'refuse'
            Body   = @{}
            Reason = "custom_ns response carries no 'enabled' field; it has: $have. Refusing to write to a shape this script cannot confirm - read the -Audit output and update the field map."
        }
    }

    $want = ($Desired -eq 'enable')
    $have = [bool]$Current.enabled
    if ($have -eq $want) {
        return @{ Action = 'noop'; Body = @{}; Reason = "already $Desired" + "d (enabled=$have)" }
    }
    return @{ Action = 'patch'; Body = @{ enabled = $want }; Reason = "enabled $have -> $want" }
}

# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------

function Get-CustomNsState {
    param([Parameter(Mandatory = $true)]$ZoneRef)
    $resp = Invoke-CfApi -Method GET -Path "/zones/$($ZoneRef.ZoneId)/custom_ns" -Token $ZoneRef.Token
    return $resp.result
}

function Show-ZoneNameServerSet {
    param([Parameter(Mandatory = $true)]$ZoneRef)
    $zone = (Invoke-CfApi -Method GET -Path "/zones/$($ZoneRef.ZoneId)" -Token $ZoneRef.Token).result
    $assigned = @($zone.name_servers)
    $vanity = @()
    if ($zone.PSObject.Properties.Name -contains 'vanity_name_servers') { $vanity = @($zone.vanity_name_servers) }
    Write-Output "Assigned Cloudflare name servers: $($assigned -join ', ')"
    if ($vanity.Count) {
        Write-Output "Custom (vanity) name servers:     $($vanity -join ', ')"
    }
    else {
        Write-Output 'Custom (vanity) name servers:     (none reported on the zone object)'
    }
}

try {
    $zoneRef = Resolve-CfZone -Domain $Zone -Tokens $Tokens
    if (-not $zoneRef) {
        Write-Output "::error::Zone '$Zone' is not visible to any Cloudflare token in the environment (FFC, CM). Create it first (workflow 110) or check the token scopes."
        exit 2
    }
    Write-Output "Zone $($zoneRef.ZoneName) ($($zoneRef.ZoneId)) via the $($zoneRef.Account) token"

    $current = Get-CustomNsState -ZoneRef $zoneRef
    Write-Output '--- custom_ns (as returned by Cloudflare) ---'
    Write-Output ($current | ConvertTo-Json -Depth 6)
    Show-ZoneNameServerSet -ZoneRef $zoneRef

    if ($Audit) { exit 0 }

    $desired = if ($Enable) { 'enable' } elseif ($Disable) { 'disable' } else {
        throw 'neither -Enable nor -Disable was bound; the parameter sets should make this unreachable'
    }
    $plan = Resolve-CustomNsPatch -Current $current -Desired $desired

    switch ($plan.Action) {
        'noop' {
            Write-Output "No change: $($plan.Reason)"
            exit 0
        }
        'refuse' {
            Write-Output "::error::$($plan.Reason)"
            exit 1
        }
        'patch' {
            if ($DryRun) {
                Write-Output "DRY RUN: would PUT /zones/$($zoneRef.ZoneId)/custom_ns body $($plan.Body | ConvertTo-Json -Compress) ($($plan.Reason)). Nothing written."
                exit 0
            }
            Write-Output "Setting account custom nameservers to '$desired': $($plan.Reason)"
            try {
                $null = Invoke-CfApi -Method PUT -Path "/zones/$($zoneRef.ZoneId)/custom_ns" -Token $zoneRef.Token -Body $plan.Body
            }
            catch {
                $msg = $_.Exception.Message
                if ($msg -match 'HTTP 403|code.?:\s*10000|not authorized|Authentication error') {
                    Write-Output "::error::Cloudflare refused the custom_ns write (permissions). The wr-all Cloudflare token needs 'Zone Settings: Edit' added in the Cloudflare dashboard; this script cannot grant it. Detail: $msg"
                    exit 1
                }
                if ($msg -match 'account' -and $msg -match 'custom') {
                    Write-Output "::error::Cloudflare refused the custom_ns write and named the ACCOUNT: the $($zoneRef.Account) account may have no custom nameservers configured (Account > Configurations > Custom Nameservers). That is account-level setup, not a zone write. Detail: $msg"
                    exit 1
                }
                throw
            }

            # Confirm by re-reading, never from the PUT's own body.
            $after = Get-CustomNsState -ZoneRef $zoneRef
            if ([bool]$after.enabled -ne [bool]$plan.Body.enabled) {
                Write-Output "::error::Write did not take - re-read says enabled=$([bool]$after.enabled), expected $($plan.Body.enabled)"
                exit 1
            }
            Write-Output "Confirmed by re-read: enabled=$([bool]$after.enabled)"
            Show-ZoneNameServerSet -ZoneRef $zoneRef
            exit 0
        }
        default {
            Write-Output "::error::Resolve-CustomNsPatch returned an unknown action '$($plan.Action)'"
            exit 1
        }
    }
}
catch {
    $msg = $_.Exception.Message
    Write-Output "::error::cloudflare-custom-nameservers.ps1 failed: $msg"
    if ($msg -like 'No Cloudflare tokens available*') { exit 2 }
    exit 1
}
