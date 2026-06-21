<#
.SYNOPSIS
    Create a WHMCS client (AddClient). Intended for charity onboarding where the
    charity itself is the WHMCS client and additional people are attached as
    Contacts (see whmcs-contact-add.ps1).

.DESCRIPTION
    Wraps the WHMCS 'AddClient' API action following the same credential / error
    conventions as the other scripts in this repo. Emits a single JSON object on
    stdout: { action, dryRun, clientid, email }. Use -DryRun to preview the call
    (no write) - the would-be request body is returned (secrets stripped).

    PRIVACY: charity client + contact details live only inside WHMCS (private,
    admin-side) and are never published. The public WHOIS registrant for FFC
    domains is set separately (see config/ffc-registrant-contact.json) - it is
    NOT derived from these client records.

.NOTES
    Custom fields: WHMCS expects client custom fields as a base64-encoded PHP
    serialized array keyed by the client-custom-field id, e.g. {"1":"value"}.
    Pass them via -CustomFieldsJson '{"1":"EIN-123","2":"pre-501c3"}'. Discover
    the field ids with the products/clients export reports.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FirstName,

    [Parameter(Mandatory = $true)]
    [string]$LastName,

    [Parameter(Mandatory = $true)]
    [string]$Email,

    [Parameter()]
    [string]$CompanyName,

    [Parameter()]
    [string]$Address1,

    [Parameter()]
    [string]$Address2,

    [Parameter()]
    [string]$City,

    [Parameter()]
    [string]$State,

    [Parameter()]
    [string]$Postcode,

    [Parameter()]
    [string]$Country = 'US',

    [Parameter()]
    [string]$PhoneNumber,

    [Parameter()]
    [int]$ClientGroupId,

    [Parameter()]
    [string]$Password,

    # JSON object of { "<clientCustomFieldId>": "value", ... }
    [Parameter()]
    [string]$CustomFieldsJson,

    # Suppress the WHMCS welcome email during bulk/automated onboarding.
    [Parameter()]
    [switch]$NoWelcomeEmail,

    # Skip WHMCS new-client validation (use sparingly).
    [Parameter()]
    [switch]$SkipValidation,

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

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'AddClient'
        responsetype = 'json'
        firstname    = $FirstName
        lastname     = $LastName
        email        = $Email
        country      = $Country
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

    if ($CompanyName) { $body.companyname = $CompanyName }
    if ($Address1) { $body.address1 = $Address1 }
    if ($Address2) { $body.address2 = $Address2 }
    if ($City) { $body.city = $City }
    if ($State) { $body.state = $State }
    if ($Postcode) { $body.postcode = $Postcode }
    if ($PhoneNumber) { $body.phonenumber = $PhoneNumber }
    if ($PSBoundParameters.ContainsKey('ClientGroupId')) { $body.groupid = $ClientGroupId }

    # AddClient requires a password2; generate a strong random one if not supplied.
    $pw = if ($Password) { $Password } else { ([guid]::NewGuid().ToString('N') + 'Aa1!') }
    $body.password2 = $pw

    if ($NoWelcomeEmail) { $body.noemail = $true }
    if ($SkipValidation) { $body.skipvalidation = $true }

    if (-not [string]::IsNullOrWhiteSpace($CustomFieldsJson)) {
        $body.customfields = ConvertTo-WhmcsCustomFields -Json $CustomFieldsJson
    }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey', 'password2', 'customfields')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = 'AddClient'; dryRun = $true; clientid = $null; email = $Email; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $clientId = $null
    try { $clientId = [string]$resp.clientid } catch {}

    [pscustomobject]@{ action = 'AddClient'; dryRun = $false; clientid = $clientId; email = $Email } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
