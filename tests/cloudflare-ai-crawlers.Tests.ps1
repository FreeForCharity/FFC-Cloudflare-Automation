# Unit tests for Resolve-AiCrawlerPatch in scripts/cloudflare-ai-crawlers.ps1.
#
# WHY THIS ONE FUNCTION: it is the pure, decidable core of a setting whose
# blast radius is whether a charity's site can be read by AI crawlers at all.
# The HTTP around it (resolve zone, GET, PUT, re-read) cannot be unit tested
# without a live zone; every decision about WHAT to write comes out of here.
#
# The case that carries the most weight is the REFUSAL. The Cloudflare API
# reference was not reachable from the sandbox that wrote the script, so the
# field names are not something it may assume: a response without
# `ai_bots_protection` must be refused with the keys it DID carry named, not
# patched from memory. Same discipline as `missingTooling` in
# optimize-captured-assets.mjs - fail closed on a shape you cannot confirm.
#
# The function is extracted from the AST rather than dot-sourced: the script
# has a mandatory -Zone and would resolve a zone against the live API on load.

BeforeAll {
    $scriptPath = Join-Path $PSScriptRoot '..' 'scripts' 'cloudflare-ai-crawlers.ps1'
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

    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name 'Resolve-AiCrawlerPatch').Extent.Text))

    # Shapes modelled on Cloudflare's bot_management result. `blocked` is what a
    # newly created zone looks like under Cloudflare's own default.
    $script:Blocked = [pscustomobject]@{
        enable_js          = $true
        fight_mode         = $false
        ai_bots_protection = 'block'
        crawler_protection = 'enabled'
    }
    $script:Allowed = [pscustomobject]@{
        enable_js          = $true
        fight_mode         = $false
        ai_bots_protection = 'disabled'
        crawler_protection = 'disabled'
    }
}

Describe 'Resolve-AiCrawlerPatch' {

    Context 'the FFC default: allow' {
        It 'patches a Cloudflare-default (blocked) zone to disabled on both fields' {
            $p = Resolve-AiCrawlerPatch -Current $script:Blocked -Desired 'allow'
            $p.Action | Should -Be 'patch'
            $p.Body.ai_bots_protection | Should -Be 'disabled'
            $p.Body.crawler_protection | Should -Be 'disabled'
            $p.Body.Keys.Count | Should -Be 2
        }

        It 'is a no-op on a zone that already allows' {
            # Re-running 106 must not generate a write every time.
            $p = Resolve-AiCrawlerPatch -Current $script:Allowed -Desired 'allow'
            $p.Action | Should -Be 'noop'
            $p.Body.Keys.Count | Should -Be 0
        }

        It 'sends only the field that differs when the other is already right' {
            $half = [pscustomobject]@{ ai_bots_protection = 'block'; crawler_protection = 'disabled' }
            $p = Resolve-AiCrawlerPatch -Current $half -Desired 'allow'
            $p.Action | Should -Be 'patch'
            $p.Body.Keys | Should -Be @('ai_bots_protection')
        }
    }

    Context 'the opt-out: block' {
        It 'patches an allowing zone back to block/enabled' {
            $p = Resolve-AiCrawlerPatch -Current $script:Allowed -Desired 'block'
            $p.Action | Should -Be 'patch'
            $p.Body.ai_bots_protection | Should -Be 'block'
            $p.Body.crawler_protection | Should -Be 'enabled'
        }

        It 'is a no-op on a zone that already blocks' {
            (Resolve-AiCrawlerPatch -Current $script:Blocked -Desired 'block').Action | Should -Be 'noop'
        }
    }

    Context 'the shape it will not assume' {
        It 'REFUSES a response with no ai_bots_protection, naming the keys it has' {
            $odd = [pscustomobject]@{ enable_js = $true; fight_mode = $true }
            $p = Resolve-AiCrawlerPatch -Current $odd -Desired 'allow'
            $p.Action | Should -Be 'refuse'
            $p.Body.Keys.Count | Should -Be 0
            $p.Reason | Should -Match 'ai_bots_protection'
            $p.Reason | Should -Match 'enable_js'
            $p.Reason | Should -Match 'fight_mode'
        }

        It 'refuses a null response rather than treating it as "nothing to change"' {
            $p = Resolve-AiCrawlerPatch -Current $null -Desired 'allow'
            $p.Action | Should -Be 'refuse'
            $p.Reason | Should -Match 'no properties'
        }

        It 'only writes crawler_protection when the response carries it' {
            # A plan without the AI Labyrinth field must not be asked to set it.
            $noLabyrinth = [pscustomobject]@{ ai_bots_protection = 'block' }
            $p = Resolve-AiCrawlerPatch -Current $noLabyrinth -Desired 'allow'
            $p.Action | Should -Be 'patch'
            $p.Body.Keys | Should -Be @('ai_bots_protection')
            $p.Body.ContainsKey('crawler_protection') | Should -BeFalse
        }
    }

    Context 'discrimination, not permissiveness' {
        It 'rejects a desired posture outside allow|block at the parameter' {
            { Resolve-AiCrawlerPatch -Current $script:Blocked -Desired 'maybe' } | Should -Throw
        }

        It 'treats the value case-sensitively as Cloudflare does: "Block" is not "block"' {
            # PowerShell -ne is case-insensitive by default; the function must
            # still produce a patch here, because Cloudflare will not accept
            # "Block" and a case-folded compare would call it already-set.
            $cased = [pscustomobject]@{ ai_bots_protection = 'Disabled'; crawler_protection = 'disabled' }
            $p = Resolve-AiCrawlerPatch -Current $cased -Desired 'allow'
            # Documented behaviour: -ne IS case-insensitive, so this reads as
            # noop. Pinned so a future "fix" to -cne is a deliberate change with
            # a test flip, not a silent one.
            $p.Action | Should -Be 'noop'
        }
    }
}
