Set-StrictMode -Version Latest

function ConvertTo-Bool {
    param([object]$Value)

    if ($null -eq $Value) { return $false }
    if ($Value -is [bool]) { return [bool]$Value }

    $s = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($s)) { return $false }

    $normalized = $s.ToLowerInvariant()
    $boolMap = @{
        'true'  = $true
        'false' = $false
        'yes'   = $true
        'no'    = $false
        '1'     = $true
        '0'     = $false
    }

    if ($boolMap.ContainsKey($normalized)) {
        return $boolMap[$normalized]
    }

    return $false
}

function Normalize-Domain {
    param([string]$Domain)

    if ([string]::IsNullOrWhiteSpace($Domain)) { return $null }

    $value = $Domain.Trim()
    $host = $null

    try {
        $uri = [uri]$value
        if ($uri.Host) {
            $host = $uri.Host
        }
    }
    catch {
    }

    if (-not $host) {
        $host = $value.Split('/')[0]
    }

    if ([string]::IsNullOrWhiteSpace($host)) { return $null }

    return $host.ToLowerInvariant().TrimEnd('.')
}

function Protect-CsvCell {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) { return $null }

    $s = [string]$Value
    if ($s -match '^[\s]*[=+\-@]') {
        return "'$s"
    }

    return $s
}

Export-ModuleMember -Function ConvertTo-Bool, Normalize-Domain, Protect-CsvCell
