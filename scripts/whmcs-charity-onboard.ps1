<#
.SYNOPSIS
    Orchestrate WHMCS charity onboarding: create the client, attach real
    Contacts (board / primary / technical roster), and place the onboarding
    order (pre-501c3 or 501c3) with product custom fields.

.DESCRIPTION
    Composes the focused scripts (whmcs-client-add, whmcs-contact-add,
    whmcs-order-add) as child processes and chains their JSON output. Reads an
    intake object from -IntakeJsonPath or -IntakeJson. The product key resolves
    to a pid via config/whmcs-onboarding-products.json (or pass a numeric pid).

    Use -DryRun to preview every call without writing (no client/contacts/order
    are created). Emits a combined JSON summary on stdout.

    PRIVACY: the client + contacts are private to WHMCS and never published. The
    public WHOIS registrant is separate (config/ffc-registrant-contact.json).

    Intake shape:
    {
      "product": "pre501c3" | "501c3" | 33,
      "client":   { "firstName","lastName","email","companyName","address1","city","state","postcode","country","phoneNumber" },
      "contacts": [ { "firstName","lastName","email","phoneNumber",
                      "domainEmails":true,"supportEmails":true,"invoiceEmails":false,
                      "subAccount":false,"permissions":"managedomains,managetickets" } ],
      "customFields": { "<productCustomFieldId>": "value" }
    }
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$IntakeJsonPath,

    [Parameter()]
    [string]$IntakeJson,

    [Parameter()]
    [string]$ProductsConfigPath,

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
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Get-IntakeObject {
    if (-not [string]::IsNullOrWhiteSpace($IntakeJson)) {
        return $IntakeJson | ConvertFrom-Json -ErrorAction Stop
    }
    if (-not [string]::IsNullOrWhiteSpace($IntakeJsonPath)) {
        if (-not (Test-Path $IntakeJsonPath)) { throw "Intake file not found: $IntakeJsonPath" }
        return (Get-Content -Path $IntakeJsonPath -Raw) | ConvertFrom-Json -ErrorAction Stop
    }
    throw 'Provide -IntakeJsonPath or -IntakeJson.'
}

function Resolve-ProductId {
    param([Parameter(Mandatory = $true)]$Product)

    # Numeric pid passed directly.
    $pidOut = 0
    if ([int]::TryParse([string]$Product, [ref]$pidOut)) { return $pidOut }

    $cfgPath = if ($ProductsConfigPath) { $ProductsConfigPath } else { Join-Path $PSScriptRoot '..' 'config' 'whmcs-onboarding-products.json' }
    if (-not (Test-Path $cfgPath)) { throw "Products config not found: $cfgPath (or pass a numeric pid)." }
    $cfg = (Get-Content -Path $cfgPath -Raw) | ConvertFrom-Json -ErrorAction Stop
    $entry = $cfg.products.$Product
    if (-not $entry) { throw "Unknown product key '$Product'. Known: $(@($cfg.products.PSObject.Properties.Name) -join ', '), or pass a numeric pid." }
    return [int]$entry.pid
}

function Set-CredentialEnv {
    # Forward credentials to child scripts via inherited environment variables
    # (never on the command line, so secrets cannot leak via process listings or
    # failure output). Child scripts already resolve WHMCS_API_* from the env.
    if ($ApiUrl) { $env:WHMCS_API_URL = $ApiUrl }
    if ($Identifier) { $env:WHMCS_API_IDENTIFIER = $Identifier }
    if ($Secret) { $env:WHMCS_API_SECRET = $Secret }
    if ($AccessKey) { $env:WHMCS_API_ACCESS_KEY = $AccessKey }
    if ($CredentialsJson) { $env:WHMCS_API_CREDENTIALS_JSON = $CredentialsJson }
}

function Add-CommonArgs {
    # Only non-secret flags go on the child command line.
    param([System.Collections.Generic.List[string]]$ArgList)
    if ($DryRun) { $ArgList.Add('-DryRun') }
}

function Invoke-ChildScript {
    # Runs a child .ps1 in its own pwsh process (so its `exit` does not end this
    # script) and returns its parsed JSON stdout.
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[string]]$ArgList
    )
    $path = Join-Path $PSScriptRoot $Name
    if (-not (Test-Path $path)) { throw "Child script not found: $path" }

    $raw = & pwsh -NoProfile -File $path @ArgList 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed (exit $LASTEXITCODE): $($raw -join [Environment]::NewLine)"
    }
    $text = ($raw | Out-String).Trim()
    try { return $text | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$Name did not return parseable JSON. Output: $text" }
}

function Add-StringArg {
    param([System.Collections.Generic.List[string]]$ArgList, [string]$Name, $Value)
    if ($null -ne $Value -and -not [string]::IsNullOrWhiteSpace([string]$Value)) {
        $ArgList.Add($Name); $ArgList.Add([string]$Value)
    }
}

try {
    $intake = Get-IntakeObject
    if (-not $intake.client) { throw 'Intake must include a "client" object.' }
    if (-not $intake.product) { throw 'Intake must include a "product" key (e.g. "pre501c3", "501c3", or a numeric pid).' }
    Set-CredentialEnv
    $productId = Resolve-ProductId -Product $intake.product

    $c = $intake.client

    # --- 1. Client ---------------------------------------------------------
    $clientArgs = [System.Collections.Generic.List[string]]::new()
    Add-StringArg $clientArgs '-FirstName'   $c.firstName
    Add-StringArg $clientArgs '-LastName'    $c.lastName
    Add-StringArg $clientArgs '-Email'       $c.email
    Add-StringArg $clientArgs '-CompanyName' $c.companyName
    Add-StringArg $clientArgs '-Address1'    $c.address1
    Add-StringArg $clientArgs '-City'        $c.city
    Add-StringArg $clientArgs '-State'       $c.state
    Add-StringArg $clientArgs '-Postcode'    $c.postcode
    Add-StringArg $clientArgs '-Country'     $c.country
    Add-StringArg $clientArgs '-PhoneNumber' $c.phoneNumber
    $clientArgs.Add('-NoWelcomeEmail')
    Add-CommonArgs $clientArgs
    $clientResult = Invoke-ChildScript -Name 'whmcs-client-add.ps1' -ArgList $clientArgs

    $clientId = if ($clientResult.clientid) { [int]$clientResult.clientid } else { 0 }
    if (-not $DryRun -and $clientId -le 0) { throw 'Client creation did not return a clientid.' }

    # --- 2. Contacts -------------------------------------------------------
    $contactResults = @()
    foreach ($ct in @($intake.contacts)) {
        if (-not $ct) { continue }
        $caArgs = [System.Collections.Generic.List[string]]::new()
        $caArgs.Add('-ClientId'); $caArgs.Add([string]$clientId)
        Add-StringArg $caArgs '-FirstName'   $ct.firstName
        Add-StringArg $caArgs '-LastName'    $ct.lastName
        Add-StringArg $caArgs '-Email'       $ct.email
        Add-StringArg $caArgs '-PhoneNumber' $ct.phoneNumber
        if ($ct.domainEmails) { $caArgs.Add('-DomainEmails') }
        if ($ct.supportEmails) { $caArgs.Add('-SupportEmails') }
        if ($ct.invoiceEmails) { $caArgs.Add('-InvoiceEmails') }
        if ($ct.productEmails) { $caArgs.Add('-ProductEmails') }
        if ($ct.generalEmails) { $caArgs.Add('-GeneralEmails') }
        if ($ct.subAccount) { $caArgs.Add('-SubAccount') }
        Add-StringArg $caArgs '-Permissions' $ct.permissions
        Add-CommonArgs $caArgs
        $contactResults += (Invoke-ChildScript -Name 'whmcs-contact-add.ps1' -ArgList $caArgs)
    }

    # --- 3. Order ----------------------------------------------------------
    $orderArgs = [System.Collections.Generic.List[string]]::new()
    $orderArgs.Add('-ClientId'); $orderArgs.Add([string]$clientId)
    $orderArgs.Add('-ProductId'); $orderArgs.Add([string]$productId)
    if ($intake.customFields) {
        $cfJson = $intake.customFields | ConvertTo-Json -Compress -Depth 6
        $orderArgs.Add('-CustomFieldsJson'); $orderArgs.Add($cfJson)
    }
    Add-CommonArgs $orderArgs
    $orderResult = Invoke-ChildScript -Name 'whmcs-order-add.ps1' -ArgList $orderArgs

    [pscustomobject]@{
        dryRun   = [bool]$DryRun
        pid      = $productId
        client   = $clientResult
        contacts = $contactResults
        order    = $orderResult
    } | ConvertTo-Json -Depth 10
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
