# Unit tests for Resolve-CustomNsPatch in scripts/cloudflare-custom-nameservers.ps1.
#
# The decision about whether a zone is moved onto the FFC account custom
# nameservers (ns1/ns2.freeforcharity.org) is this one pure function; the
# HTTP around it (resolve zone, GET, PUT, re-read) needs a live zone. The
# refusal case carries the weight: the API reference was not reachable from
# the sandbox that wrote the script, so a response without `enabled` must be
# refused with the keys it DID carry named, never patched from memory.
#
# Extracted from the AST: the script has a mandatory -Zone and resolves it
# against the live API on load.

BeforeAll {
    $scriptPath = Join-Path $PSScriptRoot '..' 'scripts' 'cloudflare-custom-nameservers.ps1'
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

    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name 'Resolve-CustomNsPatch').Extent.Text))

    $script:Off = [pscustomobject]@{ enabled = $false; ns_set = 1 }
    $script:On = [pscustomobject]@{ enabled = $true; ns_set = 1 }
}

Describe 'Resolve-CustomNsPatch' {

    Context 'the FFC default: enable' {
        It 'patches a zone on its assigned pair onto the account custom nameservers' {
            $p = Resolve-CustomNsPatch -Current $script:Off -Desired 'enable'
            $p.Action | Should -Be 'patch'
            $p.Body.enabled | Should -BeTrue
            $p.Body.Keys | Should -Be @('enabled')
        }

        It 'is a no-op on a zone already on the custom nameservers' {
            $p = Resolve-CustomNsPatch -Current $script:On -Desired 'enable'
            $p.Action | Should -Be 'noop'
            $p.Body.Keys.Count | Should -Be 0
        }

        It 'never writes ns_set or any other bookkeeping field' {
            $p = Resolve-CustomNsPatch -Current $script:Off -Desired 'enable'
            $p.Body.ContainsKey('ns_set') | Should -BeFalse
        }
    }

    Context 'the opt-out: disable' {
        It 'patches an enabled zone back to the assigned pair' {
            $p = Resolve-CustomNsPatch -Current $script:On -Desired 'disable'
            $p.Action | Should -Be 'patch'
            $p.Body.enabled | Should -BeFalse
        }

        It 'is a no-op on a zone already on the assigned pair' {
            (Resolve-CustomNsPatch -Current $script:Off -Desired 'disable').Action | Should -Be 'noop'
        }
    }

    Context 'the shape it will not assume' {
        It 'REFUSES a response with no enabled field, naming the keys it has' {
            $odd = [pscustomobject]@{ ns_set = 1; status = 'ok' }
            $p = Resolve-CustomNsPatch -Current $odd -Desired 'enable'
            $p.Action | Should -Be 'refuse'
            $p.Body.Keys.Count | Should -Be 0
            $p.Reason | Should -Match 'enabled'
            $p.Reason | Should -Match 'ns_set'
            $p.Reason | Should -Match 'status'
        }

        It 'refuses a null response rather than treating it as "nothing to change"' {
            $p = Resolve-CustomNsPatch -Current $null -Desired 'enable'
            $p.Action | Should -Be 'refuse'
            $p.Reason | Should -Match 'no properties'
        }

        It 'rejects a desired state outside enable|disable at the parameter' {
            { Resolve-CustomNsPatch -Current $script:Off -Desired 'maybe' } | Should -Throw
        }
    }
}
