<#
.SYNOPSIS
  Provision a per-charity Google Tag Manager container (Wave 3, epic #508). Creates a container,
  seeds the FFC default tags (GA4 + optional Microsoft Clarity + optional Meta Pixel) on All Pages,
  publishes a version, and optionally delegates container Edit/Publish to the charity's POC so they
  can self-administer their own tags.

.DESCRIPTION
  Uses domain-wide delegation: the ffc-workspace-admin SA impersonates a Workspace admin (-Subject)
  to call the Tag Manager API. The SA key comes from Key Vault (see docs/google-api.md). All FFC
  charity containers live under ONE FFC-owned GTM account so FFC keeps account admin while each
  charity gets container-scoped access — the data-isolation model documented in docs/google-api.md.

  -DryRun prints the plan and makes no changes (default true in the calling workflow).

.EXAMPLE
  pwsh -File scripts/google-gtm-provision.ps1 -KeyPath sa.json -Subject clarkemoyer@freeforcharity.org `
    -AccountId 4702611686 -Domain newcharity.org -MeasurementId G-XXXX -GranteeEmail poc@newcharity.org
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$KeyPath,
    [Parameter(Mandatory)][string]$Subject,
    [Parameter(Mandatory)][string]$AccountId,
    [Parameter(Mandatory)][string]$Domain,
    [Parameter(Mandatory)][string]$MeasurementId,
    [string]$ClarityId,
    [string]$MetaPixelId,
    [string]$GranteeEmail,
    [switch]$DryRun,
    [string]$Scope = 'https://www.googleapis.com/auth/tagmanager.edit.containers https://www.googleapis.com/auth/tagmanager.publish https://www.googleapis.com/auth/tagmanager.manage.accounts https://www.googleapis.com/auth/tagmanager.manage.users'
)
$ErrorActionPreference = 'Stop'
$base = 'https://www.googleapis.com/tagmanager/v2'

function ConvertTo-B64Url([byte[]]$b) { [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_') }
function Get-DwdToken {
    param($Key, $Subject, $Scope)
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $h = @{ alg = 'RS256'; typ = 'JWT'; kid = $Key.private_key_id }; $c = @{ iss = $Key.client_email; sub = $Subject; scope = $Scope; aud = $Key.token_uri; iat = $now; exp = $now + 3600 }
    $si = "$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($h|ConvertTo-Json -Compress)))).$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($c|ConvertTo-Json -Compress))))"
    $rsa = [System.Security.Cryptography.RSA]::Create()
    try { $rsa.ImportFromPem($Key.private_key); $sig = $rsa.SignData([Text.Encoding]::ASCII.GetBytes($si), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1) } finally { $rsa.Dispose() }
    (Invoke-RestMethod -Method Post -Uri $Key.token_uri -ContentType 'application/x-www-form-urlencoded' -Body @{ grant_type = 'urn:ietf:params:oauth:grant-type:jwt-bearer'; assertion = "$si.$(ConvertTo-B64Url $sig)" }).access_token
}
function Invoke-Gtm {
    param([string]$Method = 'GET', [string]$Path, [object]$Body, [hashtable]$Auth)
    $p = @{ Method = $Method; Uri = "$base/$Path"; Headers = $Auth; ErrorAction = 'Stop' }
    if ($null -ne $Body) { $p.Body = ($Body | ConvertTo-Json -Depth 8); $p.ContentType = 'application/json' }
    Invoke-RestMethod @p
}

$key = Get-Content $KeyPath -Raw | ConvertFrom-Json
$auth = @{ Authorization = "Bearer $(Get-DwdToken -Key $key -Subject $Subject -Scope $Scope)" }
$acctPath = "accounts/$AccountId"

# 1. Find or create the container for this domain
$containers = (Invoke-Gtm -Path "$acctPath/containers" -Auth $auth).container
$container = $containers | Where-Object { $_.name -eq $Domain } | Select-Object -First 1
if ($container) {
    Write-Host "CONTAINER_EXISTS $($container.publicId) ($Domain)"
}
elseif ($DryRun) {
    Write-Host "[DRY-RUN] would CREATE container '$Domain' under $acctPath"
}
else {
    $container = Invoke-Gtm -Method Post -Path "$acctPath/containers" -Auth $auth -Body @{ name = $Domain; usageContext = @('web') }
    Write-Host "CONTAINER_CREATED $($container.publicId) ($Domain)"
}
if (-not $container -and $DryRun) { Write-Host '[DRY-RUN] stopping before tag/version steps (no container yet)'; return }

$ws = (Invoke-Gtm -Path "$($container.path)/workspaces" -Auth $auth).workspace | Where-Object { $_.name -eq 'Default Workspace' } | Select-Object -First 1
$existing = @((Invoke-Gtm -Path "$($ws.path)/tags" -Auth $auth).tag)

function Add-TagIfMissing {
    param([string]$Name, [string]$Type, [array]$Parameter, [array]$FiringTriggerId = @('2147479553'))
    $dupe = $existing | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if ($dupe) { Write-Host "  TAG_EXISTS $Name"; return }
    if ($DryRun) { Write-Host "  [DRY-RUN] would ADD tag '$Name' ($Type)"; return }
    $t = Invoke-Gtm -Method Post -Path "$($ws.path)/tags" -Auth $auth -Body @{ name = $Name; type = $Type; parameter = $Parameter; firingTriggerId = $FiringTriggerId }
    Write-Host "  TAG_CREATED $Name (id $($t.tagId))"
}

# 2. Seed the FFC default tags (All Pages)
Add-TagIfMissing -Name "GA4 - $MeasurementId" -Type 'googtag' -Parameter @(@{ type = 'template'; key = 'tagId'; value = $MeasurementId })
if ($ClarityId) {
    $clarityHtml = "<script type=`"text/javascript`">(function(c,l,a,r,i,t,y){c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};t=l.createElement(r);t.async=1;t.src=`"https://www.clarity.ms/tag/`"+i;y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);})(window,document,`"clarity`",`"script`",`"$ClarityId`");</script>"
    Add-TagIfMissing -Name "Microsoft Clarity - $ClarityId" -Type 'html' -Parameter @(@{ type = 'template'; key = 'html'; value = $clarityHtml })
}
if ($MetaPixelId) {
    $metaHtml = "<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','$MetaPixelId');fbq('track','PageView');</script>"
    Add-TagIfMissing -Name "Meta Pixel - $MetaPixelId" -Type 'html' -Parameter @(@{ type = 'template'; key = 'html'; value = $metaHtml })
}

# 3. Version + publish
if ($DryRun) {
    Write-Host '[DRY-RUN] would create a version and publish it'
}
else {
    $ver = Invoke-Gtm -Method Post -Path "$($ws.path):create_version" -Auth $auth -Body @{ name = "FFC provision $Domain" }
    $cv = $ver.containerVersion
    Invoke-Gtm -Method Post -Path "$($cv.path):publish" -Auth $auth | Out-Null
    Write-Host "PUBLISHED version=$($cv.containerVersionId)"
}

# 4. Delegate container access to the charity POC (Edit + Publish on THIS container only)
if ($GranteeEmail) {
    if ($DryRun) {
        Write-Host "[DRY-RUN] would grant $GranteeEmail container Edit/Publish on $($container.publicId)"
    }
    else {
        Invoke-Gtm -Method Post -Path "$acctPath/user_permissions" -Auth $auth -Body @{
            emailAddress    = $GranteeEmail
            accountAccess   = @{ permission = 'user' }
            containerAccess = @(@{ containerId = $container.containerId; permission = 'publish' })
        } | Out-Null
        Write-Host "DELEGATED $GranteeEmail -> $($container.publicId) (publish)"
    }
}

Write-Host "RESULT domain=$Domain container=$($container.publicId) measurementId=$MeasurementId"
