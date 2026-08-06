# Binding tests for workflow 105's "Manage DNS Record" step.
#
# WHY THIS EXISTS: 105 gained a record_action input so a record can be REMOVED,
# not just set -- needed so a live write to a throwaway name is reversible.
# That turns one unconditional call into a branch, and a branch that chooses
# between "create this record" and "DELETE this record" is the worst place in
# the repo for an untested mistake.
#
# Two failure modes are specifically pinned:
#
#   1. The array-splat bug 106 shipped. Splatting an ARRAY into an IN-PROCESS
#      script call binds POSITIONALLY, so '-Zone' arrives as a value and
#      binding dies with "A positional parameter cannot be found that accepts
#      argument '-Zone'". 105 now builds a hashtable for the same reason.
#
#   2. A remove with no content. The script's -Remove deletes EVERY record of
#      that type at that name when -Content is absent. For a multi-value type
#      like TXT that means taking out unrelated records -- SPF and DMARC share
#      names with other TXT. The step must refuse rather than pass it through.
#
# As in the 106 tests, these extract the REAL run: block from the YAML and run
# it against a stub that records what it was bound to. Asserting on the
# received parameters, not on the text of the step, is what makes this catch
# the failure rather than one spelling of it.

BeforeAll {
    $script:WorkflowPath = (Resolve-Path (Join-Path $PSScriptRoot '..' '.github' 'workflows' '105-manage-record.yml')).Path

    function Get-StepRunBlock {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$StepName)

        $lines = [System.IO.File]::ReadAllLines($Path)
        $start = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*-\s+name:\s+$([regex]::Escape($StepName))\s*$") { $start = $i; break }
        }
        if ($start -lt 0) { throw "Step '$StepName' not found in $Path" }

        $runAt = -1
        for ($i = $start; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^\s*run:\s*\|\s*$') { $runAt = $i; break }
            if ($i -gt $start -and $lines[$i] -match '^\s*-\s+name:\s+') { break }
        }
        if ($runAt -lt 0) { throw "No 'run: |' block for step '$StepName'" }

        $indent = ($lines[$runAt] -replace '\S.*$', '').Length + 2
        $body = New-Object System.Collections.Generic.List[string]
        for ($i = $runAt + 1; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            if ($line.Trim() -eq '') { $body.Add(''); continue }
            $lineIndent = ($line -replace '\S.*$', '').Length
            if ($lineIndent -lt $indent) { break }
            $body.Add($line.Substring($indent))
        }
        if ($body.Count -eq 0) { throw "Empty run block for step '$StepName'" }
        return ($body -join "`n")
    }

    function Invoke-StepAgainstStub {
        param(
            [Parameter(Mandatory)][string]$StepBody,
            [Parameter(Mandatory)][hashtable]$Env
        )

        $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("ffc105-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        try {
            $capture = Join-Path $sandbox 'bound.json'

            # Mirrors the real script's Set/Remove parameter sets so binding
            # behaves the same way. Records $PSBoundParameters, which is what
            # lets the assertions check VALUES -- an array splat binds '-Zone'
            # as the first positional value, so Zone comes back wrong even
            # where binding does not throw outright.
            @'
[CmdletBinding(DefaultParameterSetName = 'Set')]
param(
    [Parameter(Mandatory = $true)][string]$Zone,
    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)]
    [string]$Name,
    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Remove')]
    [ValidateSet('A','AAAA','CNAME','MX','TXT')][string]$Type,
    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Remove')]
    [string]$Content,
    [Parameter(ParameterSetName = 'Set')][int]$Priority = 10,
    [Parameter(ParameterSetName = 'Set')][int]$Ttl = 1,
    [Parameter(ParameterSetName = 'Set')][switch]$Proxied,
    [Parameter(ParameterSetName = 'Remove', Mandatory = $true)][switch]$Remove,
    [switch]$DryRun
)
$out = @{}
foreach ($k in $PSBoundParameters.Keys) {
    $v = $PSBoundParameters[$k]
    $out[$k] = if ($v -is [switch]) { [bool]$v } else { [string]$v }
}
$out | ConvertTo-Json -Compress | Set-Content -Path (Join-Path $PSScriptRoot 'bound.json') -Encoding utf8
'@ | Set-Content -Path (Join-Path $sandbox 'Update-CloudflareDns.ps1') -Encoding utf8

            $prev = @{}
            foreach ($k in $Env.Keys) {
                $prev[$k] = [Environment]::GetEnvironmentVariable($k)
                [Environment]::SetEnvironmentVariable($k, $Env[$k])
            }
            $oldCwd = Get-Location
            try {
                Set-Location $sandbox
                & ([scriptblock]::Create($StepBody)) *> $null
            }
            finally {
                Set-Location $oldCwd
                foreach ($k in $Env.Keys) { [Environment]::SetEnvironmentVariable($k, $prev[$k]) }
            }

            if (-not (Test-Path $capture)) { throw 'Step did not reach Update-CloudflareDns.ps1 (binding failed or it threw)' }
            $json = Get-Content -Path $capture -Raw | ConvertFrom-Json
            $result = @{}
            foreach ($p in $json.PSObject.Properties) { $result[$p.Name] = $p.Value }
            return $result
        }
        finally {
            Remove-Item -Path $sandbox -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $script:ManageBody = Get-StepRunBlock -Path $script:WorkflowPath -StepName 'Manage DNS Record'

    $script:BaseEnv = @{
        REC_DOMAIN  = 'ffcworkingsite1.org'
        REC_TYPE    = 'TXT'
        REC_NAME    = '_dkimtest'
        REC_CONTENT = 'v=DKIM1; k=rsa; p=AAAA'
        REC_ACTION  = 'set'
    }
}

Describe '105 manage step binds its parameters' {
    It 'passes the domain as -Zone, not positionally' {
        $b = Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $script:BaseEnv
        $b['Zone'] | Should -Be 'ffcworkingsite1.org'
        $b['Name'] | Should -Be '_dkimtest'
        $b['Type'] | Should -Be 'TXT'
        $b['Content'] | Should -Be 'v=DKIM1; k=rsa; p=AAAA'
    }

    It 'sets Ttl and does NOT pass -Remove on a set' {
        $b = Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $script:BaseEnv
        $b['Ttl'] | Should -Be 1
        $b.ContainsKey('Remove') | Should -BeFalse
    }

    It 'defaults to set when the action is blank' {
        # Runs dispatched before record_action existed send an empty string.
        $e = $script:BaseEnv.Clone(); $e['REC_ACTION'] = ''
        $b = Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $e
        $b.ContainsKey('Remove') | Should -BeFalse
        $b['Ttl'] | Should -Be 1
    }

    It 'passes -Remove and no Ttl on a remove' {
        $e = $script:BaseEnv.Clone(); $e['REC_ACTION'] = 'remove'
        $b = Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $e
        $b['Remove'] | Should -BeTrue
        $b['Content'] | Should -Be 'v=DKIM1; k=rsa; p=AAAA'
        $b.ContainsKey('Ttl') | Should -BeFalse -Because 'Ttl is not in the Remove parameter set'
    }

    It 'REFUSES a remove with no content rather than deleting every record at the name' {
        # Without -Content the script deletes EVERY record of that type at that
        # name. Passing a blank through would be a fleet-scale footgun.
        $e = $script:BaseEnv.Clone(); $e['REC_ACTION'] = 'remove'; $e['REC_CONTENT'] = ''
        { Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $e } | Should -Throw
    }

    It 'refuses a blank content only for remove, not for set' {
        # A blank on a set falls back to the documented SPF default, which is
        # existing behaviour and must not start throwing.
        $e = $script:BaseEnv.Clone(); $e['REC_CONTENT'] = ''
        $b = Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $e
        $b['Content'] | Should -Be 'v=spf1 include:spf.protection.outlook.com -all'
    }

    It 'throws on an unrecognised action rather than falling through to set' {
        $e = $script:BaseEnv.Clone(); $e['REC_ACTION'] = 'destroy'
        { Invoke-StepAgainstStub -StepBody $script:ManageBody -Env $e } | Should -Throw
    }
}
