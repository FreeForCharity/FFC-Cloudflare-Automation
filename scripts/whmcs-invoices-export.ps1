[CmdletBinding(DefaultParameterSetName = 'Workflow')]
param(
    # Workflow-compatible parameters (used by .github/workflows/10-whmcs-zeffy-payments-import-draft.yml)
    [Parameter(ParameterSetName = 'Workflow')]
    [string]$ApiUrl,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$Identifier,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$Secret,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$AccessKey,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$OutputFile = 'whmcs_invoices.csv',

    [Parameter(ParameterSetName = 'Workflow')]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter(ParameterSetName = 'Workflow')]
    [ValidateRange(0, 1000000)]
    [int]$MaxRows = 0,

    [Parameter(ParameterSetName = 'Workflow')]
    [datetime]$StartDate,

    [Parameter(ParameterSetName = 'Workflow')]
    [datetime]$EndDate,

    # Backward-compatible parameters (older versions of this script)
    [Parameter(Mandatory, ParameterSetName = 'Legacy')]
    [string]$WhmcsApiUrl,

    [Parameter(Mandatory, ParameterSetName = 'Legacy')]
    [string]$WhmcsIdentifier,

    [Parameter(Mandatory, ParameterSetName = 'Legacy')]
    [string]$WhmcsSecret,

    [Parameter(Mandatory, ParameterSetName = 'Legacy')]
    [string]$OutCsv,

    [Parameter(ParameterSetName = 'Legacy')]
    [string]$StartDateLegacy,

    [Parameter(ParameterSetName = 'Legacy')]
    [string]$EndDateLegacy,

    [Parameter(ParameterSetName = 'Legacy')]
    [ValidateRange(1, 250)]
    [int]$Limit = 250
)

$ErrorActionPreference = 'Stop'

function Resolve-WhmcsCredentials {
    param(
        [string]$IdentifierParam,
        [string]$SecretParam
    )

    $id = if ($IdentifierParam) { $IdentifierParam } else { $env:WHMCS_API_IDENTIFIER }
    $sec = if ($SecretParam) { $SecretParam } else { $env:WHMCS_API_SECRET }

    if (-not [string]::IsNullOrWhiteSpace($id) -and -not [string]::IsNullOrWhiteSpace($sec)) {
        return @{ Identifier = $id; Secret = $sec }
    }

    throw 'Missing WHMCS credentials. Provide -Identifier/-Secret or set WHMCS_API_IDENTIFIER/WHMCS_API_SECRET.'
}

function Resolve-WhmcsApiUrl {
    param([string]$ApiUrlParam)

    if ($ApiUrlParam) { return $ApiUrlParam }
    if ($env:WHMCS_API_URL) { return $env:WHMCS_API_URL }

    return 'https://freeforcharity.org/hub/includes/api.php'
}

function Resolve-WhmcsAccessKey {
    param([string]$AccessKeyParam)

    if ($AccessKeyParam) { return $AccessKeyParam }
    if ($env:WHMCS_API_ACCESS_KEY) { return $env:WHMCS_API_ACCESS_KEY }

    return $null
}

function New-DirectoryForFile {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function ConvertFrom-WhmcsXml {
    param([Parameter(Mandatory = $true)][string]$RawXml)

    $clean = $RawXml -replace "[\x00-\x08\x0B\x0C\x0E-\x1F]", ''
    try {
        return [xml]$clean
    }
    catch {
        $snippet = if ($clean.Length -gt 400) { $clean.Substring(0, 400) + '...' } else { $clean }
        throw "Failed to parse WHMCS XML response. Snippet: $snippet"
    }
}

function Invoke-WhmcsApiXml {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedApiUrl,

        [Parameter(Mandatory = $true)]
        [hashtable]$Body
    )

    $headers = @{
        'Accept'     = '*/*'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ResolvedApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($resp -is [xml]) {
        $xml = $resp
    }
    elseif ($resp -is [string]) {
        $xml = ConvertFrom-WhmcsXml -RawXml $resp
    }
    else {
        $raw = $resp | Out-String
        $xml = ConvertFrom-WhmcsXml -RawXml $raw
    }

    $root = $xml.whmcsapi
    if (-not $root) {
        throw 'WHMCS API returned XML but did not contain <whmcsapi> root.'
    }

    if ($root.result -ne 'success') {
        $msg = $null
        if ($root.message) { $msg = [string]$root.message }
        elseif ($root.errormessage) { $msg = [string]$root.errormessage }
        if ([string]::IsNullOrWhiteSpace($msg)) { $msg = 'Unknown WHMCS API error.' }
        throw "WHMCS API error: $msg"
    }

    return $root
}

function Get-Text {
    param($Node)

    if (-not $Node) { return $null }
    if ($Node -is [System.Xml.XmlNode]) {
        $s = $Node.InnerText
    }
    else {
        $s = [string]$Node
    }
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return $s
}

function ConvertTo-WhmcsDateParam {
    param([Nullable[datetime]]$Date)

    if ($null -eq $Date) { return $null }
    if ($Date.Value -eq [datetime]::MinValue) { return $null }

    return $Date.Value.ToString('yyyy-MM-dd')
}

function ConvertTo-WhmcsLegacyDate {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }

    $dt = $null
    try { $dt = [datetime]::Parse($Value) } catch { $dt = $null }
    return $dt
}

try {
    $resolvedApiUrl = $null
    $creds = $null
    $resolvedAccessKey = $null

    $resolvedOutput = $null
    $resolvedPageSize = $null
    $resolvedMaxRows = $null
    $resolvedStartDate = $null
    $resolvedEndDate = $null

    if ($PSCmdlet.ParameterSetName -eq 'Legacy') {
        $resolvedApiUrl = Resolve-WhmcsApiUrl -ApiUrlParam $WhmcsApiUrl
        $creds = Resolve-WhmcsCredentials -IdentifierParam $WhmcsIdentifier -SecretParam $WhmcsSecret
        $resolvedAccessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

        $resolvedOutput = $OutCsv
        $resolvedPageSize = $Limit
        $resolvedMaxRows = 0
        $resolvedStartDate = ConvertTo-WhmcsLegacyDate -Value $StartDateLegacy
        $resolvedEndDate = ConvertTo-WhmcsLegacyDate -Value $EndDateLegacy
    }
    else {
        $resolvedApiUrl = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
        $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
        $resolvedAccessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

        $resolvedOutput = $OutputFile
        $resolvedPageSize = $PageSize
        $resolvedMaxRows = $MaxRows
        $resolvedStartDate = $StartDate
        $resolvedEndDate = $EndDate
    }

    New-DirectoryForFile -Path $resolvedOutput

    $rows = @()

    $start = 0
    $total = $null

    while ($true) {
        if ($resolvedMaxRows -gt 0 -and $rows.Count -ge $resolvedMaxRows) {
            Write-Warning "Reached MaxRows=$resolvedMaxRows; stopping export early."
            break
        }

        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetInvoices'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $resolvedPageSize
        }

        $sd = ConvertTo-WhmcsDateParam -Date $resolvedStartDate
        if ($sd) { $body.datecreated = $sd }

        $ed = ConvertTo-WhmcsDateParam -Date $resolvedEndDate
        if ($ed) { $body.datecreatedend = $ed }

        if ($resolvedAccessKey) { $body.accesskey = $resolvedAccessKey }

        $resp = Invoke-WhmcsApiXml -ResolvedApiUrl $resolvedApiUrl -Body $body

        if ($null -eq $total) {
            $totalText = Get-Text -Node $resp.totalresults
            if (-not [string]::IsNullOrWhiteSpace($totalText)) {
                $total = [int]$totalText
            }
        }

        $batch = @()
        if ($resp.invoices -and $resp.invoices.invoice) {
            $batch = @($resp.invoices.invoice)
        }

        if ($batch.Count -eq 0) { break }

        foreach ($inv in $batch) {
            $rows += [pscustomobject]@{
                invoiceid     = Get-Text $inv.id
                userid        = Get-Text $inv.userid
                status        = Get-Text $inv.status
                date          = Get-Text $inv.date
                duedate       = Get-Text $inv.duedate
                total         = Get-Text $inv.total
                paymentmethod = Get-Text $inv.paymentmethod
            }

            if ($resolvedMaxRows -gt 0 -and $rows.Count -ge $resolvedMaxRows) { break }
        }

        if ($resolvedMaxRows -gt 0 -and $rows.Count -ge $resolvedMaxRows) { break }
        if ($batch.Count -lt $resolvedPageSize) { break }

        $start += $resolvedPageSize

        if ($total -and $start -ge $total) { break }
    }

    $rows | Export-Csv -NoTypeInformation -Encoding utf8 -Path $resolvedOutput

    Write-Host "Exported $($rows.Count) invoices to $resolvedOutput"
}
catch {
    Write-Error $_
    exit 1
}
