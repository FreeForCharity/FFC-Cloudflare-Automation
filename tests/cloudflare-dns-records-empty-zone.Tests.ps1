# Regression test for Get-AllDnsRecords in Update-CloudflareDns.ps1 on a zone
# with NO records.
#
# WHY: `return $records` on an empty array unrolls to $null at the caller, and
# every consumer of the result binds it to a
# [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Records - which
# accepts an empty collection and refuses null. A freshly created zone is
# exactly that case, so the first -EnforceStandard dry run and the first
# -ExportAll on newheightseducation.org (106 run 37008673446, 109 run
# 37009126539, 2026-10-02) both died with
#   Cannot bind argument to parameter 'Records' because it is null.
# before touching a single record. The fix is `return , $records`; this file
# pins that the caller receives an EMPTY ARRAY, never $null, and that the
# non-empty and paginated paths are unchanged.
#
# The function is extracted from the AST, as the sibling modules do, because
# the script itself resolves a zone against the live API on load. Invoke-CfApi
# is replaced by a scoped stub so no network is touched.

BeforeAll {
    $scriptPath = Join-Path $PSScriptRoot '..' 'Update-CloudflareDns.ps1'
    $script:SourcePath = (Resolve-Path $scriptPath).Path

    function Get-FunctionFromFile {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
        $fn = $ast.Find({
                param($n)
                $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $n.Name -eq $Name
            }, $true)
        if (-not $fn) { throw "$Name not found in $Path" }
        return $fn
    }

    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name 'Get-AllDnsRecords').Extent.Text))

    # The consumer shape every call site in the script hands the result to.
    function Test-RecordsBinder {
        param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Records)
        return @($Records).Count
    }

    # A page factory in the shape Cloudflare returns.
    function New-Page {
        param([object[]]$Result, [int]$TotalPages = 1)
        [pscustomobject]@{
            success     = $true
            result      = $Result
            result_info = [pscustomobject]@{ total_pages = $TotalPages }
        }
    }
}

Describe 'Get-AllDnsRecords on an EMPTY zone' {
    BeforeAll {
        function Invoke-CfApi { param($Method, $Uri, $Params) New-Page -Result @() }
    }

    It 'returns an empty ARRAY, not $null' {
        $r = Get-AllDnsRecords -ZoneId 'zone-empty'
        $null -ne $r | Should -BeTrue
        @($r).Count | Should -Be 0
    }

    It 'binds to a Mandatory [AllowEmptyCollection()] parameter without throwing' {
        # This is the exact binder the five call sites use; before the fix this
        # line is where 106 and 109 died.
        $r = Get-AllDnsRecords -ZoneId 'zone-empty'
        { Test-RecordsBinder -Records $r } | Should -Not -Throw
        Test-RecordsBinder -Records $r | Should -Be 0
    }
}

Describe 'Get-AllDnsRecords on a populated zone' {
    It 'returns every record on a single page' {
        function Invoke-CfApi {
            param($Method, $Uri, $Params)
            New-Page -Result @(
                [pscustomobject]@{ type = 'A'; name = 'example.org'; content = '1.2.3.4' },
                [pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'smtp.google.com' }
            )
        }
        $r = Get-AllDnsRecords -ZoneId 'zone-two'
        @($r).Count | Should -Be 2
        Test-RecordsBinder -Records $r | Should -Be 2
        @($r)[1].type | Should -Be 'MX'
    }

    It 'follows pagination and concatenates the pages in order' {
        function Invoke-CfApi {
            param($Method, $Uri, $Params)
            switch ([int]$Params.page) {
                1 { New-Page -Result @([pscustomobject]@{ name = 'p1' }) -TotalPages 2 }
                2 { New-Page -Result @([pscustomobject]@{ name = 'p2' }) -TotalPages 2 }
                default { throw "unexpected page $($Params.page)" }
            }
        }
        $r = Get-AllDnsRecords -ZoneId 'zone-paged'
        @($r).name | Should -Be @('p1', 'p2')
    }

    It 'a single record still arrives as a collection the binder accepts' {
        function Invoke-CfApi { param($Method, $Uri, $Params) New-Page -Result @([pscustomobject]@{ name = 'only' }) }
        $r = Get-AllDnsRecords -ZoneId 'zone-one'
        Test-RecordsBinder -Records $r | Should -Be 1
    }
}
