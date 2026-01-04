<#
.SYNOPSIS
    Cloudflare DNS Management Tool (PowerShell Edition)

.DESCRIPTION
    A comprehensive utility to manage Cloudflare DNS records via the API.
    Designed to replace update_dns.py for GitHub Actions and local Windows usage.
    Supports Create, Update (Upsert), Delete, and Get operations for A, AAAA, CNAME, MX, and TXT records.

.PARAMETER Zone
    The domain name (zone) to manage (e.g., "example.org").

.PARAMETER Name
    The DNS record name. Use "@" for the root domain.
    Example: "www" or "staging".

.PARAMETER Type
    DNS Record type. Supported: A, AAAA, CNAME, MX, TXT.

.PARAMETER Content
    The content of the record (IP address, Target domain, Text value).

.PARAMETER Priority
    Priority for MX records. Defaults to 10.

.PARAMETER Ttl
    Time to Live in seconds. Default is 1 (Auto).

.PARAMETER Proxied
    Switch to enable Cloudflare Proxy (Orange Cloud). Default is DNS Only (Grey Cloud).

.PARAMETER Token
    Cloudflare API Token. If omitted, the script will try both account tokens from the environment
    (recommended for GitHub Actions):
    - $env:CLOUDFLARE_API_TOKEN_FFC
    - $env:CLOUDFLARE_API_TOKEN_CM

.PARAMETER DryRun
    Preview changes without applying them.

.PARAMETER Remove
    Delete the specified record(s).

.PARAMETER List
    List records for the zone/name.

.EXAMPLE
    # Create/Update an A record (Grey Cloud)
    .\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42

.EXAMPLE
    # Create/Update WWW as Orange Cloud
    .\Update-CloudflareDns.ps1 -Zone example.org -Name www -Type CNAME -Content example.org -Proxied

.EXAMPLE
    # Create MX Record
    .\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type MX -Content mail.example.org -Priority 0

.EXAMPLE
    # Setup GitHub Pages (Apex)
    .\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.108.153

.EXAMPLE
    # Delete a record
    .\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Remove
#>
[CmdletBinding(DefaultParameterSetName = 'Set')]
param(
    [Parameter(Mandatory = $true, HelpMessage = "The Zone/Domain name (e.g. example.org)")]
    [string]$Zone,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Get')]
    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)]
    [string]$Name,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Get')]
    [Parameter(ParameterSetName = 'Remove')]
    [ValidateSet('A', 'AAAA', 'CNAME', 'MX', 'TXT')]
    [string]$Type,

    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Remove')]
    [string]$Content,

    [Parameter(ParameterSetName = 'Set')]
    [int]$Priority = 10,

    [Parameter(ParameterSetName = 'Set')]
    [int]$Ttl = 1, # 1 = Auto

    [Parameter(ParameterSetName = 'Set')]
    [switch]$Proxied,

    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)]
    [switch]$Remove,

    [Parameter(ParameterSetName = 'Get', Mandatory = $true)]
    [switch]$List,

    [Parameter(ParameterSetName = 'Audit', Mandatory = $true)]
    [switch]$Audit,

    [Parameter(ParameterSetName = 'Enforce', Mandatory = $true)]
    [switch]$EnforceStandard,

    [string]$Token,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://api.cloudflare.com/client/v4'

# --- Authenticate ---
function Get-AuthTokens {
    if ($Token) { return @($Token) }

    $tokens = @()
    if ($env:CLOUDFLARE_API_TOKEN_FFC) { $tokens += @($env:CLOUDFLARE_API_TOKEN_FFC) }
    if ($env:CLOUDFLARE_API_TOKEN_CM) { $tokens += @($env:CLOUDFLARE_API_TOKEN_CM) }

    if ($tokens.Count -eq 0) {
        throw "Cloudflare API token(s) not found. Set CLOUDFLARE_API_TOKEN_FFC and CLOUDFLARE_API_TOKEN_CM (recommended), or pass -Token."
    }

    return $tokens
}

function New-CfHeaders {
    param([Parameter(Mandatory = $true)][string]$AuthToken)
    return @{
        'Authorization' = "Bearer $AuthToken"
        'Content-Type'  = 'application/json'
    }
}

$AuthToken = $null
$Headers = $null

# --- Helper Functions ---

function Invoke-CfApi {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Body = $null,
        [hashtable]$Params = $null
    )

    $requestParams = @{
        Method      = $Method
        Uri         = "$ApiBase$Uri"
        Headers     = $Headers
        ContentType = 'application/json'
        TimeoutSec  = 30
    }

    if ($Body) { $requestParams['Body'] = ($Body | ConvertTo-Json -Depth 10 -Compress) }
    
    # Append Query Params
    if ($Params) {
        $queryString = ($Params.Keys | ForEach-Object { "$_=$($Params[$_])" }) -join '&'
        $requestParams['Uri'] += "?$queryString"
    }

    try {
        $response = Invoke-RestMethod @requestParams
        if (-not $response.success) {
            $err = $response.errors | Select-Object -ExpandProperty message -ErrorAction SilentlyContinue
            throw "API Error: $err"
        }
        return $response
    }
    catch {
        Write-Error "Request Failed: $($_.Exception.Message)"
        if ($_.Exception.Response) {
            # Try to read the error stream
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = [System.IO.StreamReader]::new($stream)
            $body = $reader.ReadToEnd()
            Write-Error "API Error Body: $body"
        }
        throw
    }
}

function Get-ZoneId {
    param([string]$ZoneName)
    Write-Verbose "Looking up Zone ID for $ZoneName..."
    $resp = Invoke-CfApi -Method 'GET' -Uri '/zones' -Params @{ name = $ZoneName }
    $zones = $resp.result
    if ($zones.Count -eq 0) { throw "Zone '$ZoneName' not found." }
    return $zones[0].id
}

function Try-ResolveZoneContext {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ZoneName,
        [Parameter(Mandatory = $true)]
        [string[]]$Tokens
    )

    foreach ($t in $Tokens) {
        if ([string]::IsNullOrWhiteSpace($t)) { continue }
        $script:AuthToken = $t
        $script:Headers = New-CfHeaders -AuthToken $t

        try {
            $resp = Invoke-CfApi -Method 'GET' -Uri '/zones' -Params @{ name = $ZoneName }
            $zones = @($resp.result)
            if ($zones.Count -gt 0) {
                return [pscustomobject]@{ Token = $t; ZoneId = $zones[0].id }
            }
        }
        catch {
            # Ignore and try next token
        }
    }

    return $null
}

function Get-AllDnsRecords {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ZoneId
    )

    $page = 1
    $perPage = 100
    $records = @()

    while ($true) {
        $resp = Invoke-CfApi -Method 'GET' -Uri "/zones/$ZoneId/dns_records" -Params @{ per_page = $perPage; page = $page }
        if ($resp.result) { $records += $resp.result }

        $totalPages = $null
        if ($resp.PSObject.Properties.Name -contains 'result_info' -and $resp.result_info -and ($resp.result_info.PSObject.Properties.Name -contains 'total_pages')) {
            $totalPages = [int]$resp.result_info.total_pages
        }

        if ($totalPages -and $page -ge $totalPages) { break }
        if (-not $totalPages -and ($resp.result.Count -lt $perPage)) { break }

        $page++
    }

    return $records
}

function Normalize-TxtContent {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return '' }
    $trimmed = $Value.Trim()
    if ($trimmed.Length -ge 2 -and $trimmed.StartsWith('"') -and $trimmed.EndsWith('"')) {
        return $trimmed.Substring(1, $trimmed.Length - 2)
    }
    return $trimmed
}

function Is-TxtQuoted {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return $false }
    $trimmed = $Value.Trim()
    return ($trimmed.Length -ge 2 -and $trimmed.StartsWith('"') -and $trimmed.EndsWith('"'))
}

function Quote-TxtContent {
    param([AllowNull()][string]$Value)
    $normalized = Normalize-TxtContent -Value $Value
    return '"' + $normalized + '"'
}

function Get-DmarcRuaMailtos {
    param([AllowNull()][string]$DmarcContent)
    $normalized = Normalize-TxtContent -Value $DmarcContent
    if (-not $normalized) { return @() }

    $tags = $normalized -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $ruaTag = $tags | Where-Object { $_ -match '^(?i)rua\s*=' } | Select-Object -First 1
    if (-not $ruaTag) { return @() }

    $ruaValue = ($ruaTag -split '=', 2)[1].Trim()
    if (-not $ruaValue) { return @() }

    return ($ruaValue -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -like 'mailto:*' })
}

function Set-DmarcRuaMailtos {
    param(
        [Parameter(Mandatory = $true)][string]$DmarcContent,
        [Parameter(Mandatory = $true)][string[]]$RuaMailtos
    )

    $normalized = Normalize-TxtContent -Value $DmarcContent
    $tags = @()
    if ($normalized) {
        $tags = $normalized -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }

    # Remove existing rua tag(s)
    $tags = $tags | Where-Object { $_ -notmatch '^(?i)rua\s*=' }

    # Ensure v and p are present and normalized
    if (-not ($tags | Where-Object { $_ -match '^(?i)v\s*=\s*DMARC1$' })) {
        # Remove any existing v tag then re-add
        $tags = $tags | Where-Object { $_ -notmatch '^(?i)v\s*=' }
        $tags = @('v=DMARC1') + $tags
    }
    if (-not ($tags | Where-Object { $_ -match '^(?i)p\s*=' })) {
        $tags += 'p=none'
    }

    $uniqueRua = @($RuaMailtos | Where-Object { $_ } | Select-Object -Unique)
    if ($uniqueRua.Count -gt 0) {
        $tags += ('rua=' + ($uniqueRua -join ','))
    }

    return ($tags -join '; ')
}


# --- Main Logic ---

try {
    # 1. Resolve which account owns the zone (tries both configured tokens)
    $tokens = Get-AuthTokens
    $ctx = Try-ResolveZoneContext -ZoneName $Zone -Tokens $tokens
    if (-not $ctx) {
        throw "Zone '$Zone' not found in any configured Cloudflare account (tokens tried: $($tokens.Count))."
    }

    $AuthToken = $ctx.Token
    $Headers = New-CfHeaders -AuthToken $AuthToken
    $ZoneId = $ctx.ZoneId

    Write-Verbose "Resolved zone '$Zone' to Zone ID: $ZoneId"

    # 2. Resolve Full Record Name
    if ($Name -eq '@') {
        $RecordName = $Zone
    }
    else {
        # If user passed full fqdn (e.g. staging.example.org) use it, otherwise append zone
        if ($Name.EndsWith($Zone)) { $RecordName = $Name }
        else { $RecordName = "$Name.$Zone" }
    }

    # 3. Search for existing records
    $queryParams = @{
        name = $RecordName
    }
    if ($Type) { $queryParams['type'] = $Type }

    $existing = (Invoke-CfApi -Method 'GET' -Uri "/zones/$ZoneId/dns_records" -Params $queryParams).result

    # --- LIST Operation ---
    if ($List) {
        if ($existing.Count -eq 0) { Write-Warning "No records found for $RecordName" }
        else {
            $existing | Select-Object id, type, name, content, proxied, ttl | Format-Table -AutoSize
        }
        return
    }

    # --- AUDIT Operation ---
    if ($Audit) {
        Write-Host "Running Compliance Audit for Zone: $Zone" -ForegroundColor Cyan
        
        # Helper to find
        $allRecords = Get-AllDnsRecords -ZoneId $ZoneId

        # 0. CNAME Inventory (helps identify other required CNAMEs beyond WWW)
        $cnameRecords = $allRecords | Where-Object { $_.type -eq 'CNAME' } | Sort-Object name
        if (-not $cnameRecords -or $cnameRecords.Count -eq 0) {
            Write-Host "[INFO] No CNAME records found in zone." -ForegroundColor DarkGray
        }
        else {
            Write-Host "CNAME inventory:" -ForegroundColor DarkCyan
            foreach ($rec in $cnameRecords) {
                Write-Host (" - {0} -> {1} (proxied={2}, ttl={3})" -f $rec.name, $rec.content, $rec.proxied, $rec.ttl)
            }
        }

        # 0b. Required CNAMEs (M365/Teams/Intune + GitHub Pages)
        $requiredCnames = @(
            @{ Name = "autodiscover.$Zone"; Content = 'autodiscover.outlook.com'; Proxied = $false },
            @{ Name = "enterpriseenrollment.$Zone"; Content = 'enterpriseenrollment-s.manage.microsoft.com'; Proxied = $false },
            @{ Name = "enterpriseregistration.$Zone"; Content = 'enterpriseregistration.windows.net'; Proxied = $false },
            @{ Name = "lyncdiscover.$Zone"; Content = 'webdir.online.lync.com'; Proxied = $false },
            @{ Name = "sip.$Zone"; Content = 'sipdir.online.lync.com'; Proxied = $false },
            @{ Name = "www.$Zone"; Content = 'freeforcharity.github.io'; Proxied = $true }
        )

        foreach ($req in $requiredCnames) {
            $candidates = $allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq $req.Name }
            $match = $candidates | Where-Object { $_.content -eq $req.Content -and $_.proxied -eq $req.Proxied }

            if ($match) {
                Write-Host "[OK] Required CNAME present: $($req.Name)" -ForegroundColor Green
            }
            elseif ($candidates) {
                $example = $candidates | Select-Object -First 1
                Write-Warning "[DIFFERS] Required CNAME $($req.Name): found '$($example.content)' (proxied=$($example.proxied)), expected '$($req.Content)' (proxied=$($req.Proxied))"
            }
            else {
                Write-Warning "[MISSING] Required CNAME $($req.Name) -> $($req.Content) (proxied=$($req.Proxied))"
            }
        }

        # 0c. Required SRV records (Microsoft 365 / Teams)
        $requiredSrvs = @(
            @{ Name = "_sip._tls.$Zone"; Priority = 100; Weight = 1; Port = 443; Target = 'sipdir.online.lync.com' },
            @{ Name = "_sipfederationtls._tcp.$Zone"; Priority = 100; Weight = 1; Port = 5061; Target = 'sipfed.online.lync.com' }
        )

        foreach ($req in $requiredSrvs) {
            $candidates = $allRecords | Where-Object { $_.type -eq 'SRV' -and $_.name -eq $req.Name }
            $match = $candidates | Where-Object {
                $_.data -and
                [int]$_.data.priority -eq [int]$req.Priority -and
                [int]$_.data.weight -eq [int]$req.Weight -and
                [int]$_.data.port -eq [int]$req.Port -and
                $_.data.target -eq $req.Target
            }

            if ($match) {
                Write-Host "[OK] Required SRV present: $($req.Name)" -ForegroundColor Green
            }
            elseif ($candidates) {
                $example = $candidates | Select-Object -First 1
                Write-Warning "[DIFFERS] Required SRV $($req.Name): found '$($example.data.priority) $($example.data.weight) $($example.data.port) $($example.data.target)', expected '$($req.Priority) $($req.Weight) $($req.Port) $($req.Target)'"
            }
            else {
                Write-Warning "[MISSING] Required SRV $($req.Name) -> $($req.Priority) $($req.Weight) $($req.Port) $($req.Target)"
            }
        }
        
        # 1. Microsoft 365 MX
        $mx = $allRecords | Where-Object { $_.type -eq 'MX' -and $_.content -like '*.mail.protection.outlook.com' }
        if ($mx) { Write-Host "[OK] M365 MX Record found ($($mx.content))" -ForegroundColor Green }
        else { Write-Warning "[MISSING] M365 MX Record (*.mail.protection.outlook.com)" }

        # 2. SPF
        # Note: Cloudflare's API/UI frequently normalizes TXT quoting. Treat normalized content as authoritative
        # to avoid false diffs and unnecessary rewrites.
        $spf = $allRecords | Where-Object { $_.type -eq 'TXT' -and (Normalize-TxtContent -Value $_.content) -like '*include:spf.protection.outlook.com*' }
        if ($spf) {
            Write-Host "[OK] M365 SPF Record found" -ForegroundColor Green
        }
        else { Write-Warning "[MISSING] M365 SPF Record (include:spf.protection.outlook.com)" }

        # 3. DMARC
        $dmarc = $allRecords | Where-Object { $_.type -eq 'TXT' -and $_.name -like "_dmarc.$Zone" }
        $dmarcValid = $dmarc | Where-Object { (Normalize-TxtContent -Value $_.content) -like 'v=DMARC1*' }
        if ($dmarcValid) {
            $normalizedDmarc = Normalize-TxtContent -Value ($dmarcValid | Select-Object -First 1).content
            $ruaMailtos = Get-DmarcRuaMailtos -DmarcContent $normalizedDmarc

            $hasInternalRua = $ruaMailtos -contains 'mailto:dmarc-rua@freeforcharity.org'
            $hasCloudflareRua = [bool]($ruaMailtos | Where-Object { $_ -like 'mailto:*@dmarc-reports.cloudflare.net' } | Select-Object -First 1)

            if ($hasInternalRua) {
                if ($hasCloudflareRua) {
                    Write-Host "[OK] DMARC Record found (internal+Cloudflare rua)" -ForegroundColor Green
                }
                else {
                    Write-Host "[OK] DMARC Record found (internal rua only)" -ForegroundColor Green
                    Write-Warning "[OPTIONAL] DMARC Record has no Cloudflare rua; enable Cloudflare DMARC Management to add it"
                }
            }
            else {
                if (-not $hasInternalRua) { Write-Warning "[DIFFERS] DMARC Record missing internal rua (mailto:dmarc-rua@freeforcharity.org)" }
                if (-not $hasCloudflareRua) { Write-Warning "[OPTIONAL] DMARC Record has no Cloudflare rua; enable Cloudflare DMARC Management to add it" }
            }
        }
        else { Write-Warning "[MISSING] DMARC Record (_dmarc.$Zone)" }

        # 4. GitHub Pages (A + AAAA Records)
        $ghV4Ips = @('185.199.108.153', '185.199.109.153', '185.199.110.153', '185.199.111.153')
        $ghV6Ips = @('2606:50c0:8000::153', '2606:50c0:8001::153', '2606:50c0:8002::153', '2606:50c0:8003::153')

        $aRecords = $allRecords | Where-Object { $_.type -eq 'A' -and $_.name -eq $Zone }
        $missingV4 = $ghV4Ips | Where-Object { $_ -notin $aRecords.content }
        if ($missingV4.Count -eq 0 -and $aRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages A Records found" -ForegroundColor Green
        }
        else {
            Write-Warning "[MISSING/PARTIAL] GitHub Pages A Records. Missing: $($missingV4 -join ', ')"
        }

        $aaaaRecords = $allRecords | Where-Object { $_.type -eq 'AAAA' -and $_.name -eq $Zone }
        $missingV6 = $ghV6Ips | Where-Object { $_ -notin $aaaaRecords.content }
        if ($missingV6.Count -eq 0 -and $aaaaRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages AAAA Records found" -ForegroundColor Green
        }
        else {
            Write-Warning "[MISSING/PARTIAL] GitHub Pages AAAA Records. Missing: $($missingV6 -join ', ')"
        }

        # 5. WWW CNAME
        $www = $allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq "www.$Zone" }
        if ($www) { Write-Host "[OK] WWW CNAME found ($($www.content))" -ForegroundColor Green }
        else { Write-Warning "[MISSING] WWW CNAME record" }

        return
    }

    # --- ENFORCE STANDARD Operation ---
    if ($EnforceStandard) {
        Write-Host "Enforcing FFC Standard Configuration for Zone: $Zone" -ForegroundColor Cyan

        # IMPORTANT: Enforce needs a full record inventory. Without this, it will treat everything as missing.
        $allRecords = Get-AllDnsRecords -ZoneId $ZoneId

        $m365MxTarget = (($Zone -replace '\.', '-') + '.mail.protection.outlook.com')
        $wwwTarget = 'freeforcharity.github.io'
        
        # Define Standard Records
        $standards = @(
            # Microsoft 365 Email
            @{ Type = 'MX'; Name = '@'; Content = $m365MxTarget; Priority = 0 },
            # SPF: preserve existing SPF content; only create if missing.
            @{ Type = 'TXT'; Name = '@'; Content = 'v=spf1 include:spf.protection.outlook.com -all'; MatchContains = 'include:spf.protection.outlook.com' },
            # DMARC: preserve Cloudflare-generated rua (if present) and always include internal rua.
            @{ Type = 'TXT'; Name = '_dmarc'; Content = 'v=DMARC1; p=none'; EnsureInternalRua = $true; PreserveCloudflareRua = $true; InternalRua = 'mailto:dmarc-rua@freeforcharity.org' },

            # Microsoft 365 / Teams / Intune (Unproxied)
            @{ Type = 'CNAME'; Name = 'autodiscover'; Content = 'autodiscover.outlook.com'; Proxied = $false },
            @{ Type = 'CNAME'; Name = 'enterpriseenrollment'; Content = 'enterpriseenrollment-s.manage.microsoft.com'; Proxied = $false },
            @{ Type = 'CNAME'; Name = 'enterpriseregistration'; Content = 'enterpriseregistration.windows.net'; Proxied = $false },
            @{ Type = 'CNAME'; Name = 'lyncdiscover'; Content = 'webdir.online.lync.com'; Proxied = $false },
            @{ Type = 'CNAME'; Name = 'sip'; Content = 'sipdir.online.lync.com'; Proxied = $false },

            # Microsoft 365 / Teams (SRV)
            @{ Type = 'SRV'; Name = '_sip._tls'; Data = @{ service = '_sip'; proto = '_tls'; name = $Zone; priority = 100; weight = 1; port = 443; target = 'sipdir.online.lync.com' } },
            @{ Type = 'SRV'; Name = '_sipfederationtls._tcp'; Data = @{ service = '_sipfederationtls'; proto = '_tcp'; name = $Zone; priority = 100; weight = 1; port = 5061; target = 'sipfed.online.lync.com' } },
            
            # GitHub Pages (Apex)
            @{ Type = 'A'; Name = '@'; Content = '185.199.108.153'; Proxied = $true },
            @{ Type = 'A'; Name = '@'; Content = '185.199.109.153'; Proxied = $true },
            @{ Type = 'A'; Name = '@'; Content = '185.199.110.153'; Proxied = $true },
            @{ Type = 'A'; Name = '@'; Content = '185.199.111.153'; Proxied = $true },

            # GitHub Pages (Apex IPv6)
            @{ Type = 'AAAA'; Name = '@'; Content = '2606:50c0:8000::153'; Proxied = $true },
            @{ Type = 'AAAA'; Name = '@'; Content = '2606:50c0:8001::153'; Proxied = $true },
            @{ Type = 'AAAA'; Name = '@'; Content = '2606:50c0:8002::153'; Proxied = $true },
            @{ Type = 'AAAA'; Name = '@'; Content = '2606:50c0:8003::153'; Proxied = $true },
            
            # GitHub Pages (WWW)
            @{ Type = 'CNAME'; Name = 'www'; Content = $wwwTarget; Proxied = $true }
        )

        foreach ($std in $standards) {
            $recName = if ($std.Name -eq '@') { $Zone } else { "$($std.Name).$Zone" }
            
            # Re-use the existing logic by calling the script recursively or refactoring.
            # For simplicity and safety within this function, we will call the logic we just verified.
            # We construct the arguments to simulate a "Single Record Ensure" call.
            
            Write-Host "Checking standard record: $($std.Type) $($std.Name)..." -NoNewline
            
            # We can't easily recurse inside the same script execution context without dot-sourcing, 
            # so we will use the logic we built for internal function usage? 
            # Actually, `Update-CloudflareDns.ps1` is a script, so we can call it.
            # But calling it 8 times might be slow on auth. 
            # BETTER: We implement the "Check & Create" logic right here, reusing the helper variables.

            # 1. Check existence (and identify update candidates)
            $stdContent = $std.Content
            $desiredData = $null
            if ($std.Type -eq 'SRV' -and $std.ContainsKey('Data')) { $desiredData = $std.Data }
            $desiredProxied = $null
            if ($std.Type -in @('A', 'AAAA', 'CNAME')) {
                $desiredProxied = $true
                if ($std.ContainsKey('Proxied')) { $desiredProxied = [bool]$std.Proxied }
            }
            $candidates = $allRecords | Where-Object { $_.type -eq $std.Type -and $_.name -eq $recName }

            $foundRecord = $null
            $updateCandidate = $null

            switch ($std.Type) {
                'MX' {
                    # Cloudflare stores MX target in `content` and priority separately.
                    # Treat as present if any apex MX points at *.mail.protection.outlook.com with desired priority.
                    $foundRecord = $candidates | Where-Object {
                        $_.content -like '*.mail.protection.outlook.com' -and
                        ($null -eq $std.Priority -or $_.priority -eq $std.Priority)
                    }
                }
                'TXT' {
                    if ($std.ContainsKey('MatchContains') -and $std.MatchContains) {
                        # SPF: present if it includes the M365 include; do not overwrite other mechanisms.
                        $foundRecord = $candidates | Where-Object { (Normalize-TxtContent -Value $_.content) -like ("*" + $std.MatchContains + "*") }
                        if ($foundRecord) {
                            $spfCandidate = $foundRecord | Select-Object -First 1
                            $stdContent = $spfCandidate.content
                        }
                    }
                    elseif ($std.Name -eq '_dmarc') {
                        $foundRecord = $candidates | Where-Object { (Normalize-TxtContent -Value $_.content) -like 'v=DMARC1*' }
                        $ensureInternalRua = $false
                        $preserveCloudflareRua = $false
                        $internalRua = 'mailto:dmarc-rua@freeforcharity.org'
                        if ($std.ContainsKey('EnsureInternalRua')) { $ensureInternalRua = [bool]$std.EnsureInternalRua }
                        if ($std.ContainsKey('PreserveCloudflareRua')) { $preserveCloudflareRua = [bool]$std.PreserveCloudflareRua }
                        if ($std.ContainsKey('InternalRua') -and $std.InternalRua) { $internalRua = [string]$std.InternalRua }

                        if ($foundRecord) {
                            $dmarcCandidate = $foundRecord | Select-Object -First 1
                            $normalized = Normalize-TxtContent -Value $dmarcCandidate.content
                            $existingRua = Get-DmarcRuaMailtos -DmarcContent $normalized

                            $hasInternalRua = $existingRua -contains $internalRua
                            $hasCloudflareRua = $false
                            if ($preserveCloudflareRua) {
                                $hasCloudflareRua = [bool]($existingRua | Where-Object { $_ -like 'mailto:*@dmarc-reports.cloudflare.net' } | Select-Object -First 1)
                            }
                            # Only update when needed: missing required internal rua.
                            if ($ensureInternalRua -and -not $hasInternalRua) {
                                $updateCandidate = $dmarcCandidate
                                $desiredRua = @($existingRua)
                                $desiredRua += $internalRua
                                $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $normalized -RuaMailtos $desiredRua
                                $stdContent = $desiredNormalized
                                $foundRecord = $null
                            }
                            else {
                                # Already compliant; do not rewrite just to normalize formatting/order.
                                $stdContent = $dmarcCandidate.content
                            }
                        }
                        elseif ($candidates) {
                            # Prefer updating an existing _dmarc TXT rather than creating duplicates.
                            $updateCandidate = $candidates | Select-Object -First 1
                            $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $stdContent -RuaMailtos @($internalRua)
                            $stdContent = $desiredNormalized
                        }
                        else {
                            $desiredNormalized = Set-DmarcRuaMailtos -DmarcContent $stdContent -RuaMailtos @($internalRua)
                            $stdContent = $desiredNormalized
                        }
                    }
                    else {
                        $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                    }
                }
                'A' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    # A records are multi-value in our standard (GitHub Pages needs multiple A records at the apex).
                    # If a required value is missing, CREATE it rather than updating an arbitrary existing A record,
                    # which can "swap" values and leave one required IP missing.
                }
                'AAAA' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    # AAAA records are multi-value in our standard (GitHub Pages needs multiple AAAA records at the apex).
                    # If a required value is missing, CREATE it rather than updating an arbitrary existing AAAA record.
                }
                'CNAME' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    if (-not $foundRecord -and $candidates) {
                        # Prefer updating existing CNAME rather than creating a second one.
                        $updateCandidate = ($candidates | Where-Object { $_.content -eq $stdContent } | Select-Object -First 1)
                        if (-not $updateCandidate) { $updateCandidate = $candidates | Select-Object -First 1 }
                    }
                }
                'SRV' {
                    if (-not $desiredData) { break }
                    $foundRecord = $candidates | Where-Object {
                        $_.data -and
                        ($null -eq $_.data.service -or $_.data.service -eq $desiredData.service) -and
                        ($null -eq $_.data.proto -or $_.data.proto -eq $desiredData.proto) -and
                        ($null -eq $_.data.name -or $_.data.name -eq $desiredData.name) -and
                        [int]$_.data.priority -eq [int]$desiredData.priority -and
                        [int]$_.data.weight -eq [int]$desiredData.weight -and
                        [int]$_.data.port -eq [int]$desiredData.port -and
                        $_.data.target -eq $desiredData.target
                    }
                    if (-not $foundRecord -and $candidates) {
                        $updateCandidate = $candidates | Select-Object -First 1
                    }
                }
                default {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                }
            }

            if ($foundRecord) {
                Write-Host " [OK]" -ForegroundColor Green
            }
            else {
                if ($updateCandidate) {
                    Write-Host " [DIFFERS] -> Updating..." -ForegroundColor Yellow

                    $updatePayload = @{
                        type = $std.Type
                        name = $recName
                        ttl  = 1
                    }
                    if ($std.Type -eq 'SRV') {
                        $updatePayload['data'] = $desiredData
                    }
                    else {
                        $updatePayload['content'] = $stdContent
                    }
                    if ($std.Type -eq 'MX') { $updatePayload['priority'] = $std.Priority }
                    if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) { $updatePayload['proxied'] = $desiredProxied }

                    if (-not $DryRun) {
                        try {
                            $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($updateCandidate.id)" -Body $updatePayload
                            Write-Host "UPDATED" -ForegroundColor Green
                        }
                        catch {
                            Write-Error "Failed to update $($std.Type) $recName (ID: $($updateCandidate.id))"
                        }
                    }
                    else {
                        Write-Host "[DRY-RUN] PUT $recName" -ForegroundColor DarkGray
                    }
                    continue
                }

                Write-Host " [MISSING] -> Creating..." -ForegroundColor Yellow
                
                # Payload
                $newPayload = @{
                    type = $std.Type
                    ttl  = 1 # Auto
                }
                if ($std.Type -eq 'SRV') {
                    $newPayload['data'] = $desiredData
                }
                else {
                    $newPayload['content'] = $stdContent
                }
                if ($std.Type -eq 'MX') { $newPayload['priority'] = $std.Priority }
                # Proxied? Standard FFC: Pages A/CNAME = Proxied? Usually Yes for SSL. 
                # M365 = No.
                if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) { $newPayload['proxied'] = $desiredProxied }
                
                # Name must be FQDN for the API
                $newPayload['name'] = $recName

                if (-not $DryRun) {
                    try {
                        $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $newPayload
                        Write-Host "CREATED" -ForegroundColor Green
                    }
                    catch {
                        Write-Error "Failed to create $($std.Type) $recName"
                    }
                }
                else {
                    Write-Host "[DRY-RUN] POST $recName" -ForegroundColor DarkGray
                }
            }
        }
        return
    }

    # --- REMOVE Operation ---
    if ($Remove) {
        if ($existing.Count -eq 0) {
            Write-Warning "No records found to delete for $RecordName ($Type)"
            return
        }
        foreach ($rec in $existing) {
            # Safety: If Content is specified, only delete matching records
            if ($Content -and $rec.content -ne $Content) {
                Write-Verbose "Skipping record $($rec.id) (Content mismatch: '$($rec.content)' != '$Content')"
                continue
            }

            if ($DryRun) {
                Write-Host "[DRY-RUN] Would DELETE record: $($rec.type) $RecordName -> $($rec.content) (ID: $($rec.id))" -ForegroundColor Yellow
            }
            else {
                Write-Host "Deleting record: $($rec.type) $RecordName -> $($rec.content)..." -NoNewline
                $null = Invoke-CfApi -Method 'DELETE' -Uri "/zones/$ZoneId/dns_records/$($rec.id)"
                Write-Host " DONE" -ForegroundColor Green
            }
        }
        return
    }

    # --- SET (Create/Update) Operation ---
    
    # Prepare payload
    $payload = @{
        type    = $Type
        name    = $RecordName
        content = $Content
        ttl     = $Ttl
    }

    # Only add proxied for record types that support it
    if ($Type -notin @('MX', 'TXT')) {
        $payload['proxied'] = $Proxied.IsPresent
    }

    # Add priority for MX records
    if ($Type -eq 'MX') {
        $payload['priority'] = $Priority
    }

    # Check for matches (Same Type)
    $existingSameType = $existing | Where-Object { $_.type -eq $Type }

    # --- LOGIC BRANCH: Multi-Value Types (MX, TXT) ---
    # These types allow multiple records with the same name.
    # Logic: Ensure ONE record exists with this content. Do not overwrite others.
    if ($Type -in @('MX', 'TXT')) {
        $exactMatch = $existingSameType | Where-Object { 
            $_.content -eq $Content -and 
            ($Type -ne 'MX' -or $_.priority -eq $Priority)
        }

        if ($exactMatch) {
            Write-Verbose "Exact match found (ID: $($exactMatch[0].id))."
            Write-Host "  [Skip] $Type $RecordName matches desired state." -ForegroundColor DarkGray
        }
        else {
            # No exact match -> CREATE (Append)
            if ($DryRun) {
                Write-Host "[DRY-RUN] Would CREATE new record: $Type $RecordName -> $Content" -ForegroundColor Yellow
            }
            else {
                Write-Host "Creating new record: $Type $RecordName -> $Content..." -NoNewline
                $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
                Write-Host " DONE" -ForegroundColor Green
            }
        }
        return
    }

    # --- LOGIC BRANCH: Single-Value / Managed Types (A, AAAA, CNAME) ---
    # These often imply a single state for a subdomain (e.g. www points to X).
    # Logic: Update ALL matching records to the new state.
    
    if ($existingSameType.Count -eq 0) {
        # CREATE
        if ($DryRun) {
            Write-Host "[DRY-RUN] Would CREATE new record: $Type $RecordName -> $Content (Proxied: $Proxied)" -ForegroundColor Yellow
        }
        else {
            Write-Host "Creating new record: $Type $RecordName -> $Content..." -NoNewline
            $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
            Write-Host " DONE" -ForegroundColor Green
        }
    }
    else {
        # UPDATE
        foreach ($rec in $existingSameType) {
            $needsUpdate = $false
            
            if ($rec.content -ne $Content) { $needsUpdate = $true }
            
            # Check proxy status
            if ([bool]$rec.proxied -ne $Proxied) { $needsUpdate = $true } 

            if (-not $needsUpdate) {
                Write-Verbose "Record $($rec.id) is up to date."
                Write-Host "  [Skip] $Type $RecordName matches desired state." -ForegroundColor DarkGray
                continue
            }

            if ($DryRun) {
                Write-Host "[DRY-RUN] Would UPDATE record $($rec.id): $Content (Proxied: $Proxied)" -ForegroundColor Yellow
            }
            else {
                Write-Host "Updating record $($rec.id)..." -NoNewline
                $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($rec.id)" -Body $payload
                Write-Host " DONE" -ForegroundColor Green
            }
        }
    }

}
catch {
    Write-Error $_
    exit 1
}
