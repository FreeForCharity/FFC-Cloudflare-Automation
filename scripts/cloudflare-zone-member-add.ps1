<#
.SYNOPSIS
    Invite an email address as a Cloudflare account member scoped to a single zone.

.DESCRIPTION
    Adds a person to the FFC Cloudflare account as a member whose access policy is
    scoped to ONE zone (domain) via IAM member policies:

      1. Resolve account id            -> GET  /accounts (single-account guard)
      2. Resolve zone id by name       -> GET  /zones?name=<domain>
      3. Resolve permission group      -> GET  /accounts/{id}/iam/permission_groups?name=...
      4. Find/create zone resource grp -> GET/POST /accounts/{id}/iam/resource_groups
      5. Check existing membership     -> GET  /accounts/{id}/members (paginated)
      6. Invite member (scoped policy) -> POST /accounts/{id}/members

    Safety model:
      - Default is DRY RUN: steps 1-5 run (read-only) and the exact POST payloads
        are printed, but nothing is created. Pass -Execute for the live invite.
      - Idempotent: if the email is already a member (any status), the script
        reports the existing member + policies and makes NO changes.
      - Read probes that fail with 401/403 are reported as 'denied' (the token
        lacks that permission) instead of crashing, so a dry run can also serve
        as a token-capability check. A live run requires all probes green.

    Token permissions required for the live run (on the account token):
      - Account Members: Edit  (invite)
      - Account Resource Groups / IAM: Edit (create the zone resource group)
    The zone/DNS-scoped tokens used by most workflows can resolve the zone but
    will show 'denied' on the IAM/member probes; that outcome means the KV token
    needs those permissions added before a live run can succeed.

.PARAMETER Domain
    Zone name the member should administer (e.g., example.org).

.PARAMETER Email
    Email address to invite as a member.

.PARAMETER PermissionGroupName
    IAM permission group to grant on the zone. Default: 'Domain Administrator'.

.PARAMETER Account
    Which token to use: 'FFC' or 'CM'. Reads env CLOUDFLARE_API_TOKEN_FFC /
    CLOUDFLARE_API_TOKEN_CM (same convention as the other cloudflare-*.ps1).

.PARAMETER Execute
    Actually create the resource group (if missing) and send the invite.
    Without this switch the script is a dry run.

.OUTPUTS
    Human-readable progress on the host stream and a single JSON verdict object
    on stdout (fields: domain, email, mode, status, accountId, zoneId,
    permissionGroupId, resourceGroupId, probes).

.EXAMPLE
    pwsh -File scripts/cloudflare-zone-member-add.ps1 -Domain example.org -Email person@example.com

.EXAMPLE
    pwsh -File scripts/cloudflare-zone-member-add.ps1 -Domain example.org -Email person@example.com -Execute
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$Email,

    [Parameter()]
    [string]$PermissionGroupName = 'Domain Administrator',

    [Parameter()]
    [ValidateSet('FFC', 'CM')]
    [string]$Account = 'FFC',

    [Parameter()]
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'

# Diagnostics go to stderr so stdout is strictly the final JSON object.
# AllowEmptyString: blank lines are used as separators in the dry-run output.
function Write-Diag {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Get-TokenForAccount {
    param(
        [Parameter(Mandatory = $true)][string]$Account
    )

    switch ($Account) {
        'FFC' {
            if (-not $env:CLOUDFLARE_API_TOKEN_FFC) { throw 'CLOUDFLARE_API_TOKEN_FFC is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_FFC
        }
        'CM' {
            if (-not $env:CLOUDFLARE_API_TOKEN_CM) { throw 'CLOUDFLARE_API_TOKEN_CM is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_CM
        }
        default {
            throw "Unsupported Account value: $Account"
        }
    }
}

# Non-throwing probe: returns status code + parsed body regardless of HTTP result.
function Invoke-CfProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][object]$Body
    )

    $params = @{
        Method             = $Method
        Uri                = "https://api.cloudflare.com/client/v4$Uri"
        Headers            = @{ Authorization = "Bearer $Token" }
        SkipHttpErrorCheck = $true
        StatusCodeVariable = 'statusCode'
        ErrorAction        = 'Stop'
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = 'application/json'
    }

    $resp = Invoke-RestMethod @params
    return [pscustomobject]@{
        status = [int]$statusCode
        body   = $resp
        ok     = ([int]$statusCode -ge 200 -and [int]$statusCode -lt 300 -and [bool]$resp.success)
    }
}

# Classify a failed probe: 'denied' for auth/permission errors, else 'error'.
function Get-ProbeState {
    param([Parameter(Mandatory = $true)][object]$Probe)

    if ($Probe.ok) { return 'granted' }
    $message = $null
    if ($Probe.body -and $Probe.body.errors) {
        $message = ($Probe.body.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
    }
    if (-not $message) { $message = "HTTP $($Probe.status)" }
    if ($Probe.status -eq 401 -or $Probe.status -eq 403 -or $message -match 'authenticat|authoriz|permission|not allowed|unauthorized') {
        return 'denied'
    }
    return 'error'
}

function Get-ProbeErrorText {
    param([Parameter(Mandatory = $true)][object]$Probe)

    $message = $null
    if ($Probe.body -and $Probe.body.errors) {
        $message = ($Probe.body.errors | ForEach-Object { "$($_.code): $($_.message)" }) -join '; '
    }
    if (-not $message) { $message = 'no error detail' }
    return "HTTP $($Probe.status) ($message)"
}

try {
    $token = Get-TokenForAccount -Account $Account
    $mode = if ($Execute) { 'execute' } else { 'dry-run' }
    Write-Diag "Mode: $mode | Domain: $Domain | Email: $Email | Permission group: $PermissionGroupName"

    $probes = [ordered]@{}

    # 1) Resolve the account id (single-account guard, matching cloudflare-registrar-access-check.ps1).
    $acctProbe = Invoke-CfProbe -Method 'GET' -Uri '/accounts' -Token $token
    $probes.accounts = Get-ProbeState -Probe $acctProbe
    if (-not $acctProbe.ok) {
        throw "Could not list accounts for token '$Account': $(Get-ProbeErrorText -Probe $acctProbe). Token may be invalid or lack Account read."
    }
    $accounts = @($acctProbe.body.result)
    if ($accounts.Count -lt 1) { throw "Token '$Account' resolved no accounts." }
    if ($accounts.Count -gt 1) {
        $names = ($accounts | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue)
        throw ("Token '{0}' can access multiple accounts; refusing to guess. Accounts: {1}" -f $Account, ($names -join ', '))
    }
    $accountId = $accounts[0].id
    Write-Diag "Account: $($accounts[0].name) ($accountId)"

    # 2) Resolve the zone id by name.
    $zoneProbe = Invoke-CfProbe -Method 'GET' -Uri "/zones?name=$([uri]::EscapeDataString($Domain))&per_page=5" -Token $token
    $probes.zones = Get-ProbeState -Probe $zoneProbe
    if (-not $zoneProbe.ok) {
        throw "Could not look up zone '$Domain': $(Get-ProbeErrorText -Probe $zoneProbe)"
    }
    $zone = @($zoneProbe.body.result) | Where-Object { $_.name -eq $Domain } | Select-Object -First 1
    if (-not $zone) { throw "Zone '$Domain' not found in account '$($accounts[0].name)'. Add the domain to FFC Cloudflare first (workflow 102/110)." }
    $zoneId = $zone.id
    Write-Diag "Zone: $Domain ($zoneId), status: $($zone.status)"

    # 3) Resolve the permission group id by exact name.
    $pgId = $null
    $pgUri = "/accounts/$accountId/iam/permission_groups?name=$([uri]::EscapeDataString($PermissionGroupName))&per_page=50"
    $pgProbe = Invoke-CfProbe -Method 'GET' -Uri $pgUri -Token $token
    $probes.permissionGroups = Get-ProbeState -Probe $pgProbe
    if ($pgProbe.ok) {
        $pg = @($pgProbe.body.result) | Where-Object { $_.name -eq $PermissionGroupName } | Select-Object -First 1
        if ($pg) {
            $pgId = $pg.id
            Write-Diag "Permission group: '$PermissionGroupName' ($pgId)"
        }
        else {
            $available = (@($pgProbe.body.result) | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue) -join ', '
            throw "Permission group '$PermissionGroupName' not found. Returned: $available"
        }
    }
    else {
        Write-Diag "WARNING: cannot read IAM permission groups: $(Get-ProbeErrorText -Probe $pgProbe)"
    }

    # 4) Find an existing resource group scoped to this zone, or plan creating one.
    $zoneScopeKey = "com.cloudflare.api.account.zone.$zoneId"
    $rgId = $null
    $rgProbe = Invoke-CfProbe -Method 'GET' -Uri "/accounts/$accountId/iam/resource_groups?per_page=100" -Token $token
    $probes.resourceGroups = Get-ProbeState -Probe $rgProbe
    if ($rgProbe.ok) {
        foreach ($rg in @($rgProbe.body.result)) {
            $scopes = @($rg.scope) + @($rg.scopes) | Where-Object { $_ }
            foreach ($scope in $scopes) {
                if (@($scope.objects | Select-Object -ExpandProperty key -ErrorAction SilentlyContinue) -contains $zoneScopeKey) {
                    $rgId = $rg.id
                    Write-Diag "Resource group for zone found: '$($rg.name)' ($rgId)"
                    break
                }
            }
            if ($rgId) { break }
        }
        if (-not $rgId) { Write-Diag "No existing resource group scoped to $Domain; one will be created." }
    }
    else {
        Write-Diag "WARNING: cannot read IAM resource groups: $(Get-ProbeErrorText -Probe $rgProbe)"
    }

    $rgCreateBody = [ordered]@{
        name  = "zone:$Domain"
        scope = [ordered]@{
            key     = "com.cloudflare.api.account.$accountId"
            objects = @([ordered]@{ key = $zoneScopeKey })
        }
    }

    # 5) Check whether the email is already a member (paginate).
    $existingMember = $null
    $membersListed = $true
    $page = 1
    do {
        $memProbe = Invoke-CfProbe -Method 'GET' -Uri "/accounts/$accountId/members?per_page=50&page=$page" -Token $token
        if ($page -eq 1) { $probes.members = Get-ProbeState -Probe $memProbe }
        if (-not $memProbe.ok) {
            Write-Diag "WARNING: cannot list account members: $(Get-ProbeErrorText -Probe $memProbe)"
            $membersListed = $false
            break
        }
        $members = @($memProbe.body.result)
        $existingMember = $members | Where-Object {
            ($_.user -and $_.user.email -eq $Email) -or ($_.email -eq $Email)
        } | Select-Object -First 1
        $totalPages = 1
        if ($memProbe.body.result_info -and $memProbe.body.result_info.total_pages) {
            $totalPages = [int]$memProbe.body.result_info.total_pages
        }
        $page++
    } until ($existingMember -or $page -gt $totalPages)

    $inviteBody = [ordered]@{
        email    = $Email
        status   = 'pending'
        policies = @(
            [ordered]@{
                access            = 'allow'
                permission_groups = @([ordered]@{ id = $pgId })
                resource_groups   = @([ordered]@{ id = $rgId })
            }
        )
    }

    $status = $null

    if ($existingMember) {
        # Idempotency guard: never modify an existing member's access here.
        $status = 'already-member'
        Write-Diag "Member already exists: $Email (id: $($existingMember.id), status: $($existingMember.status)). No changes made."
        Write-Diag 'Review or adjust their access in the Cloudflare dashboard (Manage Account > Members).'
    }
    elseif (-not $Execute) {
        $status = 'dry-run'
        Write-Diag ''
        Write-Diag '=== DRY RUN - no changes made. Planned operations: ==='
        if (-not $rgId) {
            Write-Diag "1) POST /accounts/$accountId/iam/resource_groups"
            Write-Diag (($rgCreateBody | ConvertTo-Json -Depth 10) -replace '(?m)^', '   ')
        }
        Write-Diag "2) POST /accounts/$accountId/members"
        Write-Diag (($inviteBody | ConvertTo-Json -Depth 10) -replace '(?m)^', '   ')
        $deniedProbes = @($probes.GetEnumerator() | Where-Object { $_.Value -eq 'denied' })
        $errorProbes = @($probes.GetEnumerator() | Where-Object { $_.Value -eq 'error' })
        if ($deniedProbes.Count -gt 0) {
            Write-Diag ''
            Write-Diag ("BLOCKER for live run - permission denied on: " + (($deniedProbes | ForEach-Object { $_.Key }) -join ', '))
            Write-Diag "The '$Account' token needs 'Account Members: Edit' (and IAM read) before -Execute can succeed."
        }
        if ($errorProbes.Count -gt 0) {
            Write-Diag ''
            Write-Diag ("WARNING: these probes failed for a non-permission reason (transient API error?): " + (($errorProbes | ForEach-Object { $_.Key }) -join ', '))
            Write-Diag 'Re-run the dry run; if it persists, inspect the probe error text above before touching token permissions.'
        }
    }
    else {
        # Live run: every prerequisite must have resolved.
        if (-not $membersListed) { throw "Cannot execute: could not list account members (probe: $($probes.members)), so the idempotency check is impossible. Refusing to risk a duplicate invite." }
        if (-not $pgId) { throw "Cannot execute: permission group id unresolved (probe: $($probes.permissionGroups))." }
        if (-not $rgId -and $probes.resourceGroups -ne 'granted') { throw "Cannot execute: could not list IAM resource groups (probe: $($probes.resourceGroups)), so an existing zone resource group cannot be ruled out. Refusing to risk creating a duplicate." }
        if (-not $rgId) {
            $rgCreate = Invoke-CfProbe -Method 'POST' -Uri "/accounts/$accountId/iam/resource_groups" -Token $token -Body $rgCreateBody
            if (-not $rgCreate.ok) {
                throw "Failed to create resource group for zone '$Domain': $(Get-ProbeErrorText -Probe $rgCreate)"
            }
            $rgId = $rgCreate.body.result.id
            Write-Diag "Created resource group 'zone:$Domain' ($rgId)"
            $inviteBody.policies[0].resource_groups = @([ordered]@{ id = $rgId })
        }
        $invite = Invoke-CfProbe -Method 'POST' -Uri "/accounts/$accountId/members" -Token $token -Body $inviteBody
        if (-not $invite.ok) {
            throw "Failed to invite '$Email': $(Get-ProbeErrorText -Probe $invite)"
        }
        $status = 'invited'
        Write-Diag "SUCCESS: invited $Email (member id: $($invite.body.result.id), status: $($invite.body.result.status))."
        Write-Diag 'They must accept the emailed invitation before the access is active.'
    }

    $verdict = [ordered]@{
        domain            = $Domain
        email             = $Email
        mode              = $mode
        status            = $status
        accountId         = $accountId
        zoneId            = $zoneId
        permissionGroup   = $PermissionGroupName
        permissionGroupId = $pgId
        resourceGroupId   = $rgId
        probes            = $probes
    }
    $verdict | ConvertTo-Json -Depth 6

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
