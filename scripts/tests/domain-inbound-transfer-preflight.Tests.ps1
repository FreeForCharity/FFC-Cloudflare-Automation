<#
.SYNOPSIS
    Pester (v5) tests for scripts/domain-inbound-transfer-preflight.ps1.

.DESCRIPTION
    The script is pure offline analysis -- no API calls, no secrets -- so every
    case here runs for real against the script rather than against mocks.

    The behaviour that matters is the registrar classification, because it is
    what decides whether a charity waits weeks for a registrar transfer or gets
    Cloudflare the same day:

      * Wix blocks nameserver delegation  -> transfer-required
      * everyone else permits it          -> ns-delegation

    and the corollary that a blocked *transfer* must never be reported as a
    blocked *nameserver change* for an NS-capable registrar.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:Preflight = Join-Path (Split-Path $PSScriptRoot -Parent) 'domain-inbound-transfer-preflight.ps1'

    # Evaluate a single domain and return the parsed result object.
    function Invoke-Preflight {
        param([hashtable]$Params)
        $json = & pwsh -NoProfile -File $script:Preflight @Params 2>$null
        return ($json | Out-String | ConvertFrom-Json)
    }

    $script:Today = Get-Date
}

Describe 'domain-inbound-transfer-preflight.ps1' {

    Context 'registrar classification' {

        It 'treats a Wix domain as transfer-required and NS-blocked' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.' }
            $r.registrarFamily | Should -Be 'wix'
            $r.nsDelegationAllowed | Should -BeFalse
            $r.inboundPath | Should -Be 'transfer-required'
            $r.bucket | Should -Be 'FOREIGN_TRANSFER_REQUIRED'
            $r.isForeignRegistrar | Should -BeTrue
        }

        It 'lets a Squarespace domain delegate nameservers without transferring' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'Squarespace Domains LLC' }
            $r.registrarFamily | Should -Be 'squarespace'
            $r.nsDelegationAllowed | Should -BeTrue
            $r.inboundPath | Should -Be 'ns-delegation'
            $r.bucket | Should -Be 'FOREIGN_NS_CAPABLE'
        }

        It 'lets a GoDaddy domain delegate nameservers without transferring' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'GoDaddy.com, LLC' }
            $r.registrarFamily | Should -Be 'godaddy'
            $r.inboundPath | Should -Be 'ns-delegation'
        }

        It 'assumes an unrecognized registrar is NS-capable but flags it for confirmation' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'SafeNames Ltd.' }
            $r.registrarFamily | Should -Be 'other'
            $r.nsDelegationAllowed | Should -BeTrue
            $r.reasons | Should -Match 'not in the known-behaviour list'
        }

        It 'reports an eNom domain as done and defers to the outbound pipeline' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'eNom, LLC' }
            $r.bucket | Should -Be 'AT_ENOM'
            $r.readiness | Should -Be 'done'
            $r.isForeignRegistrar | Should -BeFalse
            $r.reasons | Should -Match '115'
        }

        It 'reports a Cloudflare domain as done with nothing to do' {
            $r = Invoke-Preflight @{ Domain = 'example.org'; CurrentRegistrar = 'Cloudflare, Inc.' }
            $r.bucket | Should -Be 'AT_CLOUDFLARE'
            $r.readiness | Should -Be 'done'
            $r.inboundPath | Should -Be 'none'
        }

        It 'sends an unknown registrar to review rather than guessing a path' {
            $r = Invoke-Preflight @{ Domain = 'example.org' }
            $r.bucket | Should -Be 'UNKNOWN'
            $r.readiness | Should -Be 'review'
            $r.reasons | Should -Match 'RDAP'
        }
    }

    Context 'transfer blockers' {

        It 'blocks a domain inside the ICANN post-registration lock' {
            $recent = $script:Today.AddDays(-10).ToString('yyyy-MM-dd')
            $r = Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.'; RegistrationDate = $recent
            }
            $r.readiness | Should -Be 'blocked'
            $r.bucket | Should -Be 'BLOCKED'
            $r.postRegLockOk | Should -BeFalse
            $r.reasons | Should -Match '60-day'
        }

        It 'blocks an expired domain and says to renew first' {
            $past = $script:Today.AddDays(-5).ToString('yyyy-MM-dd')
            $r = Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.'; ExpiryDate = $past
            }
            $r.readiness | Should -Be 'blocked'
            $r.reasons | Should -Match 'renew'
        }

        It 'blocks a registry-locked domain' {
            $r = Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'GoDaddy.com, LLC'; TransferLocked = 'True'
            }
            $r.readiness | Should -Be 'blocked'
            $r.transferLocked | Should -BeTrue
            $r.reasons | Should -Match 'clientTransferProhibited'
        }

        <#
            The point of the whole exercise: a locked GoDaddy domain still
            reaches Cloudflare today, because delegating nameservers is not a
            transfer and no lock applies to it. Reporting this as simply
            "blocked" would strand a site that could have gone live.
        #>
        It 'notes that the nameserver change is still available when the transfer is blocked' {
            $r = Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'GoDaddy.com, LLC'; TransferLocked = 'True'
            }
            $r.readiness | Should -Be 'blocked'
            $r.reasons | Should -Match 'nameserver change to Cloudflare is not'
        }

        It 'does not offer that consolation for a Wix domain, where NS really is blocked' {
            $r = Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.'; TransferLocked = 'True'
            }
            $r.readiness | Should -Be 'blocked'
            $r.reasons | Should -Not -Match 'nameserver change to Cloudflare is not'
        }

        It 'treats a healthy, long-registered foreign domain as ready' {
            $r = Invoke-Preflight @{
                Domain           = 'example.org'
                CurrentRegistrar = 'Wix.com Ltd.'
                RegistrationDate = $script:Today.AddYears(-5).ToString('yyyy-MM-dd')
                ExpiryDate       = $script:Today.AddYears(1).ToString('yyyy-MM-dd')
            }
            $r.readiness | Should -Be 'ready'
            $r.bucket | Should -Be 'FOREIGN_TRANSFER_REQUIRED'
        }
    }

    Context 'runbook generation' {

        BeforeEach {
            $script:RunbookDir = Join-Path ([System.IO.Path]::GetTempPath()) ("inbound-rb-" + [guid]::NewGuid().ToString('N'))
        }

        AfterEach {
            if (Test-Path -LiteralPath $script:RunbookDir) {
                Remove-Item -LiteralPath $script:RunbookDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        It 'writes a four-stage runbook for a Wix domain' {
            Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.'; RunbookDir = $script:RunbookDir
            } | Out-Null
            $path = Join-Path $script:RunbookDir 'runbook-inbound-example.org.md'
            $path | Should -Exist
            $body = Get-Content -LiteralPath $path -Raw
            $body | Should -Match '## 1. At the losing registrar'
            $body | Should -Match '## 2. Transfer in to eNom'
            $body | Should -Match '## 3. Point nameservers at Cloudflare'
            $body | Should -Match '## 4. Later: eNom .* Cloudflare Registrar'
        }

        It 'skips the transfer stages for an NS-capable registrar' {
            Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'GoDaddy.com, LLC'; RunbookDir = $script:RunbookDir
            } | Out-Null
            $body = Get-Content -LiteralPath (Join-Path $script:RunbookDir 'runbook-inbound-example.org.md') -Raw
            $body | Should -Match '## 1. Point nameservers at Cloudflare'
            $body | Should -Not -Match 'Transfer in to eNom'
        }

        It 'always tells the operator to capture MX before touching DNS' {
            Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'Wix.com Ltd.'; RunbookDir = $script:RunbookDir
            } | Out-Null
            $body = Get-Content -LiteralPath (Join-Path $script:RunbookDir 'runbook-inbound-example.org.md') -Raw
            $body | Should -Match '## 0. Before touching anything'
            $body | Should -Match 'MX'
            $body | Should -Match '_dmarc'
            # The shell loop must survive PowerShell interpolation intact.
            $body | Should -Match 'dig \+short \$t example\.org'
        }

        <#
            The outbound preflight only writes runbooks for readiness 'ready',
            which is how non-eNom domains fell off the worklist entirely. A
            blocked domain must still get a runbook, with its blockers on top.
        #>
        It 'still writes a runbook for a blocked domain, with the blockers called out' {
            Invoke-Preflight @{
                Domain           = 'example.org'
                CurrentRegistrar = 'Wix.com Ltd.'
                TransferLocked   = 'True'
                RunbookDir       = $script:RunbookDir
            } | Out-Null
            $path = Join-Path $script:RunbookDir 'runbook-inbound-example.org.md'
            $path | Should -Exist
            (Get-Content -LiteralPath $path -Raw) | Should -Match 'Blocked for registrar transfer'
        }

        It 'writes no runbook for a domain already at eNom' {
            Invoke-Preflight @{
                Domain = 'example.org'; CurrentRegistrar = 'eNom, LLC'; RunbookDir = $script:RunbookDir
            } | Out-Null
            (Join-Path $script:RunbookDir 'runbook-inbound-example.org.md') | Should -Not -Exist
        }
    }

    Context 'CSV mode' {

        BeforeAll {
            $script:WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("inbound-csv-" + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Force -Path $script:WorkDir | Out-Null
            $script:InputCsv = Join-Path $script:WorkDir 'truth.csv'
            $script:OutCsv = Join-Path $script:WorkDir 'out.csv'

            @(
                [pscustomobject]@{ domain = 'wixsite.org'; registrarOfRecord = 'Wix.com Ltd.'; registryExpiry = '2030-01-01'; registryCreated = '2010-01-01'; transferLocked = 'False' }
                [pscustomobject]@{ domain = 'godaddysite.org'; registrarOfRecord = 'GoDaddy.com, LLC'; registryExpiry = '2030-01-01'; registryCreated = '2010-01-01'; transferLocked = 'False' }
                [pscustomobject]@{ domain = 'ffcsite.org'; registrarOfRecord = 'eNom, LLC'; registryExpiry = '2030-01-01'; registryCreated = '2010-01-01'; transferLocked = 'False' }
                [pscustomobject]@{ domain = 'donesite.org'; registrarOfRecord = 'Cloudflare, Inc.'; registryExpiry = '2030-01-01'; registryCreated = '2010-01-01'; transferLocked = 'False' }
                [pscustomobject]@{ domain = 'mystery.org'; registrarOfRecord = ''; registryExpiry = ''; registryCreated = ''; transferLocked = '' }
            ) | Export-Csv -Path $script:InputCsv -NoTypeInformation -Encoding utf8

            $script:Summary = (& pwsh -NoProfile -File $script:Preflight `
                    -RegistryTruthCsv $script:InputCsv -OutputFile $script:OutCsv 2>$null) |
                Out-String | ConvertFrom-Json
            $script:Rows = Import-Csv -LiteralPath $script:OutCsv
        }

        AfterAll {
            if (Test-Path -LiteralPath $script:WorkDir) {
                Remove-Item -LiteralPath $script:WorkDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        It 'counts every input row' {
            $script:Summary.total | Should -Be 5
        }

        It 'separates the transfer-required case from the NS-capable one' {
            $script:Summary.foreignTransferRequired | Should -Be 1
            $script:Summary.foreignNsCapable | Should -Be 1
        }

        It 'recognizes domains already under FFC control' {
            $script:Summary.atEnom | Should -Be 1
            $script:Summary.atCloudflare | Should -Be 1
        }

        It 'flags the row with no registrar of record' {
            $script:Summary.unknownRegistrar | Should -Be 1
        }

        It 'reads the RDAP column names from the registry-truth export' {
            $wix = $script:Rows | Where-Object { $_.domain -eq 'wixsite.org' }
            $wix.currentRegistrar | Should -Be 'Wix.com Ltd.'
            $wix.inboundPath | Should -Be 'transfer-required'
        }
    }

    Context 'usage contract' {

        It 'fails when neither a domain nor a CSV is supplied' {
            & pwsh -NoProfile -File $script:Preflight 2>&1 | Out-Null
            $LASTEXITCODE | Should -Be 1
        }

        It 'fails when the input CSV does not exist' {
            & pwsh -NoProfile -File $script:Preflight -RegistryTruthCsv '/nonexistent/nope.csv' 2>&1 | Out-Null
            $LASTEXITCODE | Should -Be 1
        }
    }
}
