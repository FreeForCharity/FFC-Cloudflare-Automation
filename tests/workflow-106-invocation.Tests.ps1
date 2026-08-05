# Binding tests for workflow 106's pwsh steps.
#
# WHY THIS EXISTS: 106 builds its call to Update-CloudflareDns.ps1 dynamically
# rather than as sixteen near-identical if/else branches. That is worth doing,
# but it moved the call into a data structure where a whole class of mistake
# becomes invisible to review, to PSScriptAnalyzer, and to every other test in
# this repo -- and only shows up when the step actually runs behind an approval
# gate.
#
# The mistake it shipped with: the parameters were built as an ARRAY and
# splatted. Splatting an array into an IN-PROCESS script call passes the
# elements POSITIONALLY, so '-Zone' arrives as a value rather than a parameter
# name, and binding dies with:
#
#     A positional parameter cannot be found that accepts argument '-Zone'.
#
# (103 splats an array too and works, because it shells out via `pwsh -File`,
# where a separate process re-parses argv. That difference is exactly why
# reading one workflow and copying its shape into another is not safe here.)
#
# So these tests extract the REAL run: blocks from the YAML and execute them
# against a stub Update-CloudflareDns.ps1 that records what it was bound to.
# Asserting on the received parameters -- not on the text of the step -- is what
# makes this catch the failure rather than a specific spelling of it.

BeforeAll {
    $script:WorkflowPath = (Resolve-Path (Join-Path $PSScriptRoot '..' '.github' 'workflows' '106-enforce-standard.yml')).Path

    # Pull a step's `run: |` block scalar out of the YAML by text. The repo has
    # no YAML module available to Pester, and a regex over the block scalar is
    # enough: we only need the body, and a wrong extraction fails loudly when
    # the extracted script does not run.
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
            # Stop if we walked into the next step.
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

    # Runs a step body in a sandbox whose Update-CloudflareDns.ps1 is a stub that
    # writes its bound parameters to JSON. Returns those parameters.
    function Invoke-StepAgainstStub {
        param(
            [Parameter(Mandatory)][string]$StepBody,
            [Parameter(Mandatory)][hashtable]$Env
        )

        $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("ffc106-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        try {
            $capture = Join-Path $sandbox 'bound.json'

            # The stub mirrors the real script's Enforce/Audit parameter names so
            # binding behaves the same way. It records $PSBoundParameters, which
            # is what lets the assertions check VALUES -- an array splat binds
            # '-Zone' as the value of the first positional parameter, so Zone
            # comes back wrong even in the cases where binding does not throw.
            @'
[CmdletBinding(DefaultParameterSetName = 'Set')]
param(
    [Parameter(Mandatory = $true)][string]$Zone,
    [Parameter(ParameterSetName = 'Set')][string]$Name,
    [Parameter(ParameterSetName = 'Set')][string]$Type,
    [Parameter(ParameterSetName = 'Set')][string]$Content,
    [Parameter(ParameterSetName = 'Audit', Mandatory = $true)][switch]$Audit,
    [Parameter(ParameterSetName = 'Enforce', Mandatory = $true)][switch]$EnforceStandard,
    [Parameter(ParameterSetName = 'Enforce')][switch]$GitHubPagesOnly,
    [Parameter(ParameterSetName = 'Enforce')][Parameter(ParameterSetName = 'Audit')][switch]$ProxyGitHubPages,
    [Parameter(ParameterSetName = 'Enforce')][Parameter(ParameterSetName = 'Audit')][ValidateSet('Microsoft365','Google','Unchanged')][string]$MailProvider = 'Unchanged',
    [Parameter(ParameterSetName = 'Enforce')][Parameter(ParameterSetName = 'Audit')][string]$MailProviderRegistry,
    [Parameter(ParameterSetName = 'Enforce')][string]$ConfirmMailProviderChange,
    [string]$Token,
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

    $script:EnforceBody = Get-StepRunBlock -Path $script:WorkflowPath -StepName 'Enforce FFC Standard Configuration'
    $script:AuditBody = Get-StepRunBlock -Path $script:WorkflowPath -StepName 'Post-Enforce Compliance Audit'

    $script:BaseEnv = @{
        REC_DOMAIN              = 'slopestohope.org'
        REC_DRY_RUN             = 'true'
        REC_PAGES_ONLY          = 'false'
        REC_PROXY_ON            = 'false'
        REC_MAIL_PROVIDER       = 'from-registry'
        REC_CONFIRM_MAIL_CHANGE = ''
        FFC_CF_DMARCMGMT_DEBUG  = '0'
    }
}

Describe '106 enforce step binds its parameters' {
    It 'passes the domain as -Zone, not positionally' {
        # THE regression. Under the array splat this came back as '-Zone'
        # (or failed to bind at all).
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $script:BaseEnv
        $b['Zone'] | Should -Be 'slopestohope.org'
        $b['EnforceStandard'] | Should -BeTrue
        $b['DryRun'] | Should -BeTrue
    }

    It 'omits -MailProvider for from-registry so the script resolves per-domain' {
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $script:BaseEnv
        $b.ContainsKey('MailProvider') | Should -BeFalse
    }

    It 'passes an explicit provider override through' {
        $e = $script:BaseEnv.Clone(); $e['REC_MAIL_PROVIDER'] = 'google'
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e
        $b['MailProvider'] | Should -Be 'Google'
    }

    It 'passes the typed confirmation through verbatim' {
        $e = $script:BaseEnv.Clone(); $e['REC_CONFIRM_MAIL_CHANGE'] = 'slopestohope.org'
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e
        $b['ConfirmMailProviderChange'] | Should -Be 'slopestohope.org'
    }

    It 'omits the confirmation entirely when blank' {
        # A blank must not arrive as an empty string, which could later be
        # mistaken for "something was supplied".
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $script:BaseEnv
        $b.ContainsKey('ConfirmMailProviderChange') | Should -BeFalse
    }

    It 'drops -DryRun on a live run' {
        $e = $script:BaseEnv.Clone(); $e['REC_DRY_RUN'] = 'false'
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e
        $b.ContainsKey('DryRun') | Should -BeFalse
    }

    It 'uses -GitHubPagesOnly and no provider when pages_only is set' {
        $e = $script:BaseEnv.Clone(); $e['REC_PAGES_ONLY'] = 'true'; $e['REC_MAIL_PROVIDER'] = 'google'
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e
        $b['GitHubPagesOnly'] | Should -BeTrue
        $b.ContainsKey('MailProvider') | Should -BeFalse
    }

    It 'adds -ProxyGitHubPages only when asked' {
        $b = Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $script:BaseEnv
        $b.ContainsKey('ProxyGitHubPages') | Should -BeFalse
        $e = $script:BaseEnv.Clone(); $e['REC_PROXY_ON'] = 'true'
        (Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e)['ProxyGitHubPages'] | Should -BeTrue
    }

    It 'throws on an unrecognised mail_provider rather than defaulting' {
        $e = $script:BaseEnv.Clone(); $e['REC_MAIL_PROVIDER'] = 'fastmail'
        { Invoke-StepAgainstStub -StepBody $script:EnforceBody -Env $e } | Should -Throw
    }
}

Describe '106 audit step binds its parameters' {
    It 'passes the domain as -Zone and requests -Audit' {
        $b = Invoke-StepAgainstStub -StepBody $script:AuditBody -Env $script:BaseEnv
        $b['Zone'] | Should -Be 'slopestohope.org'
        $b['Audit'] | Should -BeTrue
    }

    It 'audits against the provider that was enforced' {
        $e = $script:BaseEnv.Clone(); $e['REC_MAIL_PROVIDER'] = 'google'
        (Invoke-StepAgainstStub -StepBody $script:AuditBody -Env $e)['MailProvider'] | Should -Be 'Google'
    }

    It 'omits -MailProvider for from-registry' {
        $b = Invoke-StepAgainstStub -StepBody $script:AuditBody -Env $script:BaseEnv
        $b.ContainsKey('MailProvider') | Should -BeFalse
    }

    It 'omits -MailProvider entirely when pages_only is set' {
        $e = $script:BaseEnv.Clone(); $e['REC_PAGES_ONLY'] = 'true'; $e['REC_MAIL_PROVIDER'] = 'google'
        $b = Invoke-StepAgainstStub -StepBody $script:AuditBody -Env $e
        $b.ContainsKey('MailProvider') | Should -BeFalse
    }
}
