[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$AccessKey,

    [Parameter()]
    [string]$OutputFile = 'whmcs_transactions.csv',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [ValidateRange(1, 1000000)]
    [int]$MaxRows = 200000,

    [Parameter()]
    [datetime]$StartDate,

    [Parameter()]
    [datetime]$EndDate
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
        [string]$ApiUrl,

        [Parameter(Mandatory = $true)]
        [hashtable]$Body
    )

    $headers = @{
        'Accept'     = '*/*'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

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
    $s = [string]$Node
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    return $s
}

function ConvertTo-WhmcsDateParam {
    param([Nullable[datetime]]$Date)

    if ($null -eq $Date) { return $null }
    if ($Date.Value -eq [datetime]::MinValue) { return $null }

    return $Date.Value.ToString('yyyy-MM-dd')
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $rows = @()
    $seenTransactionIds = @{}

    $start = 0
    $total = $null

    while ($true) {
        if ($rows.Count -ge $MaxRows) {
            Write-Warning "Reached MaxRows=$MaxRows; stopping export early."
            break
        }

        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetTransactions'
            responsetype = 'xml'
            limitstart   = $start
            limitnum     = $PageSize
        }

        $sd = ConvertTo-WhmcsDateParam -Date $StartDate
        if ($sd) { $body.date = $sd }

        $ed = ConvertTo-WhmcsDateParam -Date $EndDate
        if ($ed) { $body.enddate = $ed }

        if ($accessKey) { $body.accesskey = $accessKey }

        $resp = Invoke-WhmcsApiXml -ApiUrl $api -Body $body

        if ($null -eq $total) {
            $totalText = Get-Text -Node $resp.totalresults
            if (-not [string]::IsNullOrWhiteSpace($totalText)) {
                $total = [int]$totalText
            }
        }

        $batch = @()
        if ($resp.transactions -and $resp.transactions.transaction) {
            $batch = @($resp.transactions.transaction)
        }

        $stopAfterThisBatch = $false
        if ($batch.Count -gt $PageSize) {
            Write-Warning "WHMCS GetTransactions returned $($batch.Count) rows with PageSize=$PageSize for limitstart=$start; pagination may be unsupported or ignored. De-duplicating and stopping after this request to avoid duplicates."
            $stopAfterThisBatch = $true
        }

        $addedThisBatch = 0

        foreach ($t in $batch) {
            $txId = Get-Text $t.id
            if (-not [string]::IsNullOrWhiteSpace($txId)) {
                if ($seenTransactionIds.ContainsKey($txId)) {
                    continue
                }
                $seenTransactionIds[$txId] = $true
            }

            $rows += [pscustomobject]@{
                transactionid = $txId
                userid        = Get-Text $t.userid
                date          = Get-Text $t.date
                gateway       = Get-Text $t.gateway
                amountin      = Get-Text $t.amountin
                fees          = Get-Text $t.fees
                amountout     = Get-Text $t.amountout
                transid       = Get-Text $t.transid
                invoiceid     = Get-Text $t.invoiceid
                description   = Get-Text $t.description
                currency      = Get-Text $t.currency
            }
            $addedThisBatch++
        }

        if ($addedThisBatch -eq 0) {
            Write-Warning "WHMCS GetTransactions returned no new rows for limitstart=$start; stopping to avoid an infinite loop and duplicates."
            break
        }

        if ($stopAfterThisBatch) { break }

        if ($batch.Count -lt $PageSize) { break }
        $start += $PageSize

        if ($total -and $start -ge $total) { break }
    }

    $rows | Export-Csv -NoTypeInformation -Encoding utf8 -Path $OutputFile

    Write-Host "Exported $($rows.Count) transactions to $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
