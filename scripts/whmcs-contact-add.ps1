<#
.SYNOPSIS
    Add one or more additional points of contact to a WHMCS client (AddContact).

.DESCRIPTION
    Wraps the WHMCS 'AddContact' API action. A charity is the WHMCS client
    (see whmcs-client-add.ps1); each person who should receive mail or hold a
    portal login is attached here as a Contact. Supports per-contact email
    notification routing and an optional sub-account login.

    Two modes:
      1. Single contact via the -FirstName/-LastName/-Email (etc.) parameters.
      2. Bulk via -ContactsJson: a JSON array of contact objects, e.g.
         '[{"firstname":"A","lastname":"B","email":"a@x.org","domainemails":true,
            "supportemails":true},
           {"firstname":"C","lastname":"D","email":"c@x.org","invoiceemails":true}]'

    Emits JSON on stdout: { clientid, dryRun, contacts:[{contactid,email}] }.

    PRIVACY: these records are private to WHMCS and are never published to WHOIS.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$ClientId,

    [Parameter()]
    [string]$FirstName,

    [Parameter()]
    [string]$LastName,

    [Parameter()]
    [string]$Email,

    [Parameter()]
    [string]$CompanyName,

    [Parameter()]
    [string]$Address1,

    [Parameter()]
    [string]$City,

    [Parameter()]
    [string]$State,

    [Parameter()]
    [string]$Postcode,

    [Parameter()]
    [string]$Country,

    [Parameter()]
    [string]$PhoneNumber,

    # Per-contact email notification routing.
    [Parameter()][switch]$GeneralEmails,
    [Parameter()][switch]$InvoiceEmails,
    [Parameter()][switch]$SupportEmails,
    [Parameter()][switch]$ProductEmails,
    [Parameter()][switch]$DomainEmails,

    # Optional portal sub-account login for this contact.
    [Parameter()][switch]$SubAccount,
    [Parameter()][string]$Password,
    # Comma-separated permission keys, e.g. 'managedomains,managetickets'.
    [Parameter()][string]$Permissions,

    # Bulk mode: JSON array of contact objects (see .DESCRIPTION).
    [Parameter()]
    [string]$ContactsJson,

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

function Get-ContactList {
    # Normalizes either the single-contact params or -ContactsJson into an array
    # of hashtables of WHMCS AddContact field names.
    param()

    if (-not [string]::IsNullOrWhiteSpace($ContactsJson)) {
        $arr = $ContactsJson | ConvertFrom-Json -ErrorAction Stop
        if ($arr -isnot [System.Array]) { $arr = @($arr) }
        $out = @()
        foreach ($c in $arr) {
            $h = @{}
            foreach ($p in $c.PSObject.Properties) {
                $v = $p.Value
                if ($v -is [bool]) { if ($v) { $h[$p.Name.ToLowerInvariant()] = $true } }
                elseif ($null -ne $v -and -not [string]::IsNullOrWhiteSpace([string]$v)) { $h[$p.Name.ToLowerInvariant()] = [string]$v }
            }
            $out += , $h
        }
        return $out
    }

    if ([string]::IsNullOrWhiteSpace($FirstName) -or [string]::IsNullOrWhiteSpace($LastName) -or [string]::IsNullOrWhiteSpace($Email)) {
        throw 'Provide -FirstName, -LastName and -Email for a single contact, or use -ContactsJson for bulk.'
    }

    $h = @{ firstname = $FirstName; lastname = $LastName; email = $Email }
    if ($CompanyName) { $h.companyname = $CompanyName }
    if ($Address1) { $h.address1 = $Address1 }
    if ($City) { $h.city = $City }
    if ($State) { $h.state = $State }
    if ($Postcode) { $h.postcode = $Postcode }
    if ($Country) { $h.country = $Country }
    if ($PhoneNumber) { $h.phonenumber = $PhoneNumber }
    if ($GeneralEmails) { $h.generalemails = $true }
    if ($InvoiceEmails) { $h.invoiceemails = $true }
    if ($SupportEmails) { $h.supportemails = $true }
    if ($ProductEmails) { $h.productemails = $true }
    if ($DomainEmails) { $h.domainemails = $true }
    if ($SubAccount) { $h.subaccount = $true }
    if ($Password) { $h.password2 = $Password }
    if ($Permissions) { $h.permissions = $Permissions }
    return @(, $h)
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $contacts = Get-ContactList
    if (@($contacts).Count -eq 0) { throw 'No contacts to add.' }

    $results = @()
    foreach ($c in $contacts) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'AddContact'
            responsetype = 'json'
            clientid     = $ClientId
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
        foreach ($k in $c.Keys) { $body[$k] = $c[$k] }

        $email = if ($c.ContainsKey('email')) { [string]$c['email'] } else { $null }

        if ($DryRun) {
            $results += [pscustomobject]@{ contactid = $null; email = $email }
            continue
        }

        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $contactId = $null
        try { $contactId = [string]$resp.contactid } catch {}
        $results += [pscustomobject]@{ contactid = $contactId; email = $email }
    }

    [pscustomobject]@{ clientid = $ClientId; dryRun = [bool]$DryRun; contacts = $results } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
