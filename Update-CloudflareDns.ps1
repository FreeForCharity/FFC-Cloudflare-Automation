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
    Cloudflare API Token. Defaults to $env:CLOUDFLARE_API_TOKEN.

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
[CmdletBinding(DefaultParameterSetName='Set')]
param(
    [Parameter(Mandatory=$true, HelpMessage="The Zone/Domain name (e.g. example.org)")]
    [string]$Zone,

    [Parameter(ParameterSetName='Set', Mandatory=$true)]
    [Parameter(ParameterSetName='Get')]
    [Parameter(ParameterSetName='Remove', Mandatory=$true)]
    [string]$Name,

    [Parameter(ParameterSetName='Set', Mandatory=$true)]
    [Parameter(ParameterSetName='Get')]
    [Parameter(ParameterSetName='Remove')]
    [ValidateSet('A', 'AAAA', 'CNAME', 'MX', 'TXT')]
    [string]$Type,

    [Parameter(ParameterSetName='Set', Mandatory=$true)]
    [Parameter(ParameterSetName='Remove')]
    [string]$Content,

    [Parameter(ParameterSetName='Set')]
    [int]$Priority = 10,

    [Parameter(ParameterSetName='Set')]
    [int]$Ttl = 1, # 1 = Auto

    [Parameter(ParameterSetName='Set')]
    [switch]$Proxied,

    [Parameter(ParameterSetName='Remove', Mandatory=$true)]
    [switch]$Remove,

    [Parameter(ParameterSetName='Get', Mandatory=$true)]
    [switch]$List,

    [Parameter(ParameterSetName='Audit', Mandatory=$true)]
    [switch]$Audit,

    [Parameter(ParameterSetName='Enforce', Mandatory=$true)]
    [switch]$EnforceStandard,

    [string]$Token,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://api.cloudflare.com/client/v4'

# --- Authenticate ---
function Get-AuthToken {
    if ($Token) { return $Token }
    if ($env:CLOUDFLARE_API_KEY_DNS_ONLY) { return $env:CLOUDFLARE_API_KEY_DNS_ONLY }
    if ($env:CLOUDFLARE_API_TOKEN) { return $env:CLOUDFLARE_API_TOKEN }
    
    # Non-interactive mode: Fail if no token
    throw "Cloudflare API Token not found. Set CLOUDFLARE_API_KEY_DNS_ONLY or CLOUDFLARE_API_TOKEN environment variable, or pass -Token parameter."
}

$AuthToken = Get-AuthToken
if (-not $AuthToken) { Write-Error "No Cloudflare API Token provided."; exit 1 }

$Headers = @{
    'Authorization' = "Bearer $AuthToken"
    'Content-Type'  = 'application/json'
}

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


# --- Main Logic ---

try {
    # 1. Resolve Zone ID
    $ZoneId = Get-ZoneId -ZoneName $Zone
    Write-Verbose "Found Zone ID: $ZoneId"

    # 2. Resolve Full Record Name
    if ($Name -eq '@') {
        $RecordName = $Zone
    } else {
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
        } else {
            Write-Host "CNAME inventory:" -ForegroundColor DarkCyan
            foreach ($rec in $cnameRecords) {
                Write-Host (" - {0} -> {1} (proxied={2}, ttl={3})" -f $rec.name, $rec.content, $rec.proxied, $rec.ttl)
            }
        }

        # 0b. Required CNAMEs (M365/Teams/Intune + GitHub Pages)
        $requiredCnames = @(
            @{ Name = "autodiscover.$Zone";         Content = 'autodiscover.outlook.com';                        Proxied = $false },
            @{ Name = "enterpriseenrollment.$Zone"; Content = 'enterpriseenrollment-s.manage.microsoft.com';     Proxied = $false },
            @{ Name = "enterpriseregistration.$Zone"; Content = 'enterpriseregistration.windows.net';           Proxied = $false },
            @{ Name = "lyncdiscover.$Zone";         Content = 'webdir.online.lync.com';                         Proxied = $false },
            @{ Name = "sip.$Zone";                 Content = 'sipdir.online.lync.com';                         Proxied = $false },
            @{ Name = "www.$Zone";                 Content = 'freeforcharity.github.io';                       Proxied = $true }
        )

        foreach ($req in $requiredCnames) {
            $candidates = $allRecords | Where-Object { $_.type -eq 'CNAME' -and $_.name -eq $req.Name }
            $match = $candidates | Where-Object { $_.content -eq $req.Content -and $_.proxied -eq $req.Proxied }

            if ($match) {
                Write-Host "[OK] Required CNAME present: $($req.Name)" -ForegroundColor Green
            } elseif ($candidates) {
                $example = $candidates | Select-Object -First 1
                Write-Warning "[DIFFERS] Required CNAME $($req.Name): found '$($example.content)' (proxied=$($example.proxied)), expected '$($req.Content)' (proxied=$($req.Proxied))"
            } else {
                Write-Warning "[MISSING] Required CNAME $($req.Name) -> $($req.Content) (proxied=$($req.Proxied))"
            }
        }
        
        # 1. Microsoft 365 MX
        $mx = $allRecords | Where-Object { $_.type -eq 'MX' -and $_.content -like '*.mail.protection.outlook.com' }
        if ($mx) { Write-Host "[OK] M365 MX Record found ($($mx.content))" -ForegroundColor Green }
        else { Write-Warning "[MISSING] M365 MX Record (*.mail.protection.outlook.com)" }

        # 2. SPF
        $spf = $allRecords | Where-Object { $_.type -eq 'TXT' -and $_.content -like '*include:spf.protection.outlook.com*' }
        if ($spf) { Write-Host "[OK] M365 SPF Record found" -ForegroundColor Green }
        else { Write-Warning "[MISSING] M365 SPF Record (include:spf.protection.outlook.com)" }

        # 3. DMARC
        $dmarc = $allRecords | Where-Object { $_.type -eq 'TXT' -and $_.name -like "_dmarc.$Zone" }
        if ($dmarc) { Write-Host "[OK] DMARC Record found" -ForegroundColor Green }
        else { Write-Warning "[MISSING] DMARC Record (_dmarc.$Zone)" }

        # 4. GitHub Pages (A + AAAA Records)
        $ghV4Ips = @('185.199.108.153', '185.199.109.153', '185.199.110.153', '185.199.111.153')
        $ghV6Ips = @('2606:50c0:8000::153', '2606:50c0:8001::153', '2606:50c0:8002::153', '2606:50c0:8003::153')

        $aRecords = $allRecords | Where-Object { $_.type -eq 'A' -and $_.name -eq $Zone }
        $missingV4 = $ghV4Ips | Where-Object { $_ -notin $aRecords.content }
        if ($missingV4.Count -eq 0 -and $aRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages A Records found" -ForegroundColor Green
        } else {
            Write-Warning "[MISSING/PARTIAL] GitHub Pages A Records. Missing: $($missingV4 -join ', ')"
        }

        $aaaaRecords = $allRecords | Where-Object { $_.type -eq 'AAAA' -and $_.name -eq $Zone }
        $missingV6 = $ghV6Ips | Where-Object { $_ -notin $aaaaRecords.content }
        if ($missingV6.Count -eq 0 -and $aaaaRecords.Count -ge 4) {
            Write-Host "[OK] GitHub Pages AAAA Records found" -ForegroundColor Green
        } else {
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
            @{ Type='MX'; Name='@'; Content=$m365MxTarget; Priority=0 },
            @{ Type='TXT'; Name='@'; Content='v=spf1 include:spf.protection.outlook.com -all' },
            @{ Type='TXT'; Name='_dmarc'; Content='v=DMARC1; p=none; rua=mailto:dmarc-rua@freeforcharity.org' },

            # Microsoft 365 / Teams / Intune (Unproxied)
            @{ Type='CNAME'; Name='autodiscover';          Content='autodiscover.outlook.com';                    Proxied=$false },
            @{ Type='CNAME'; Name='enterpriseenrollment';  Content='enterpriseenrollment-s.manage.microsoft.com'; Proxied=$false },
            @{ Type='CNAME'; Name='enterpriseregistration'; Content='enterpriseregistration.windows.net';         Proxied=$false },
            @{ Type='CNAME'; Name='lyncdiscover';          Content='webdir.online.lync.com';                     Proxied=$false },
            @{ Type='CNAME'; Name='sip';                   Content='sipdir.online.lync.com';                     Proxied=$false },
            
            # GitHub Pages (Apex)
            @{ Type='A'; Name='@'; Content='185.199.108.153'; Proxied=$true },
            @{ Type='A'; Name='@'; Content='185.199.109.153'; Proxied=$true },
            @{ Type='A'; Name='@'; Content='185.199.110.153'; Proxied=$true },
            @{ Type='A'; Name='@'; Content='185.199.111.153'; Proxied=$true },

            # GitHub Pages (Apex IPv6)
            @{ Type='AAAA'; Name='@'; Content='2606:50c0:8000::153'; Proxied=$true },
            @{ Type='AAAA'; Name='@'; Content='2606:50c0:8001::153'; Proxied=$true },
            @{ Type='AAAA'; Name='@'; Content='2606:50c0:8002::153'; Proxied=$true },
            @{ Type='AAAA'; Name='@'; Content='2606:50c0:8003::153'; Proxied=$true },
            
            # GitHub Pages (WWW)
            @{ Type='CNAME'; Name='www'; Content=$wwwTarget; Proxied=$true }
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
                    if ($std.Name -eq '@' -and $stdContent -like 'v=spf1*') {
                        # Avoid duplicates: SPF is "present" if it includes the M365 include, even if it has extra mechanisms.
                        $foundRecord = $candidates | Where-Object { $_.content -like '*include:spf.protection.outlook.com*' }
                    } elseif ($std.Name -eq '_dmarc') {
                        $foundRecord = $candidates | Where-Object { $_.content -like 'v=DMARC1*' }
                        if (-not $foundRecord -and $candidates) {
                            # Prefer updating an existing _dmarc TXT rather than creating duplicates.
                            $updateCandidate = $candidates | Select-Object -First 1
                        }
                    } else {
                        $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                    }
                }
                'A' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    if (-not $foundRecord -and $candidates) {
                        $updateCandidate = ($candidates | Where-Object { $_.content -eq $stdContent } | Select-Object -First 1)
                        if (-not $updateCandidate) { $updateCandidate = $candidates | Select-Object -First 1 }
                    }
                }
                'AAAA' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    if (-not $foundRecord -and $candidates) {
                        $updateCandidate = ($candidates | Where-Object { $_.content -eq $stdContent } | Select-Object -First 1)
                        if (-not $updateCandidate) { $updateCandidate = $candidates | Select-Object -First 1 }
                    }
                }
                'CNAME' {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent -and ($null -eq $desiredProxied -or $_.proxied -eq $desiredProxied) }
                    if (-not $foundRecord -and $candidates) {
                        # Prefer updating existing CNAME rather than creating a second one.
                        $updateCandidate = ($candidates | Where-Object { $_.content -eq $stdContent } | Select-Object -First 1)
                        if (-not $updateCandidate) { $updateCandidate = $candidates | Select-Object -First 1 }
                    }
                }
                default {
                    $foundRecord = $candidates | Where-Object { $_.content -eq $stdContent }
                }
            }

            if ($foundRecord) {
                Write-Host " [OK]" -ForegroundColor Green
            } else {
                if ($updateCandidate) {
                    Write-Host " [DIFFERS] -> Updating..." -ForegroundColor Yellow

                    $updatePayload = @{
                        type    = $std.Type
                        name    = $recName
                        content = $stdContent
                        ttl     = 1
                    }
                    if ($std.Type -eq 'MX') { $updatePayload['priority'] = $std.Priority }
                    if ($std.Type -in @('A', 'AAAA', 'CNAME') -and $null -ne $desiredProxied) { $updatePayload['proxied'] = $desiredProxied }

                    if (-not $DryRun) {
                        try {
                            $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($updateCandidate.id)" -Body $updatePayload
                            Write-Host "UPDATED" -ForegroundColor Green
                        } catch {
                            Write-Error "Failed to update $($std.Type) $recName (ID: $($updateCandidate.id))"
                        }
                    } else {
                        Write-Host "[DRY-RUN] PUT $recName" -ForegroundColor DarkGray
                    }
                    continue
                }

                Write-Host " [MISSING] -> Creating..." -ForegroundColor Yellow
                
                # Payload
                $newPayload = @{
                    type    = $std.Type
                    content = $stdContent
                    ttl     = 1 # Auto
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
                     } catch {
                        Write-Error "Failed to create $($std.Type) $recName"
                     }
                } else {
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
            } else {
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
        } else {
            # No exact match -> CREATE (Append)
            if ($DryRun) {
                Write-Host "[DRY-RUN] Would CREATE new record: $Type $RecordName -> $Content" -ForegroundColor Yellow
            } else {
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
        } else {
            Write-Host "Creating new record: $Type $RecordName -> $Content..." -NoNewline
            $null = Invoke-CfApi -Method 'POST' -Uri "/zones/$ZoneId/dns_records" -Body $payload
            Write-Host " DONE" -ForegroundColor Green
        }
    } else {
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
            } else {
                Write-Host "Updating record $($rec.id)..." -NoNewline
                $null = Invoke-CfApi -Method 'PUT' -Uri "/zones/$ZoneId/dns_records/$($rec.id)" -Body $payload
                Write-Host " DONE" -ForegroundColor Green
            }
        }
    }

} catch {
    Write-Error $_
    exit 1
}
