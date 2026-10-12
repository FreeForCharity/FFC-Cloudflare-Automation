<#
.SYNOPSIS
    Pester (v5) unit tests for whmcs-ticket-template.ps1 (workflow 205's `template` input)
    and for the shipped config/whmcs-ticket-templates.json.

.DESCRIPTION
    Offline: no WHMCS call. Loads the REAL config, so a template edit that makes
    preview_ready staff-only, drops its subject, or puts contact details into a
    client-visible body is caught here.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $scriptsDir = Split-Path $PSScriptRoot -Parent
    . (Join-Path $scriptsDir 'whmcs-ticket-template.ps1')
    $script:ConfigPath = Join-Path (Split-Path $scriptsDir -Parent) 'config/whmcs-ticket-templates.json'
    $script:Config = Get-Content -LiteralPath $script:ConfigPath -Raw | ConvertFrom-Json

    function ConvertTo-TemplateConfigFile {
        param([string]$Json)
        $path = Join-Path ([System.IO.Path]::GetTempPath()) ('ticket-tpl-{0}.json' -f [guid]::NewGuid())
        [System.IO.File]::WriteAllText($path, $Json)
        return $path
    }
}

Describe 'config/whmcs-ticket-templates.json' {
    It 'ships preview_ready as a client-visible template that can open a ticket' {
        $t = $script:Config.templates.preview_ready
        $t | Should -Not -BeNullOrEmpty
        [bool]$t.internal | Should -BeFalse
        [string]$t.subject | Should -Not -BeNullOrEmpty
        [bool]$t.extra_message_required | Should -BeTrue
    }

    It 'asks the charity to check phone and desktop and to reply with changes or "approved"' {
        $body = [string]$script:Config.templates.preview_ready.body
        $body | Should -Match 'phone'
        $body | Should -Match 'computer'
        $body | Should -Match '"approved"'
        $body | Should -Match 'changes'
    }

    It 'keeps every client-visible body free of contact details (no PII)' {
        foreach ($p in $script:Config.templates.PSObject.Properties) {
            if ([bool]$p.Value.internal) { continue }
            $body = [string]$p.Value.body
            $body | Should -Not -Match '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -Because "$($p.Name) is client-visible"
            $body | Should -Not -Match '\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}' -Because "$($p.Name) is client-visible"
        }
    }

    It 'gives a subject only to client-visible templates' {
        foreach ($p in $script:Config.templates.PSObject.Properties) {
            if (-not [string]::IsNullOrWhiteSpace([string]$p.Value.subject)) {
                [bool]$p.Value.internal | Should -BeFalse -Because "$($p.Name) can open a client ticket"
            }
        }
    }
}

Describe 'Resolve-WhmcsOpenTicketTemplate (shipped config)' {
    It 'builds the preview_ready subject and appends the preview URL to the body' {
        $r = Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name 'preview_ready' -ExtraMessage 'Preview: https://freeforcharity.github.io/FFC-EX-example.org/'
        $r.Subject | Should -Be '[Website Preview] Your new website is ready to review'
        $r.Message | Should -BeLike "$([string]$script:Config.templates.preview_ready.body)*"
        $r.Message | Should -Match "`n`nPreview: https://freeforcharity\.github\.io/FFC-EX-example\.org/$"
    }

    It 'refuses preview_ready without the preview URL' {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name 'preview_ready' -ExtraMessage '  ' } | Should -Throw '*requires extra_message*'
    }

    It 'refuses a reply-only template (no subject)' {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name 'need_info' -ExtraMessage '' } | Should -Throw '*reply-only*'
    }

    It 'refuses an internal note' {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name 'internal_note' -ExtraMessage '' } | Should -Throw '*internal*'
    }

    It 'refuses an unknown template and names the known ones' {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name 'does_not_exist' -ExtraMessage '' } | Should -Throw '*Known:*preview_ready*'
    }

    It 'refuses a key that is not a template name (<_>)' -ForEach @('preview-ready', 'Preview_Ready', '../x', 'preview_ready; rm') {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:ConfigPath -Name $_ -ExtraMessage 'x' } | Should -Throw
    }
}

Describe 'Resolve-WhmcsOpenTicketTemplate (fixtures)' {
    AfterEach {
        if ($script:Tmp) { Remove-Item -LiteralPath $script:Tmp -ErrorAction SilentlyContinue }
    }

    It 'omits a blank prefix and leaves the body alone when no extra message is given' {
        $script:Tmp = ConvertTo-TemplateConfigFile '{"templates":{"hello":{"internal":false,"subject_prefix":"","subject":"Hi","body":"Body text."}}}'
        $r = Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:Tmp -Name 'hello' -ExtraMessage ''
        $r.Subject | Should -Be 'Hi'
        $r.Message | Should -Be 'Body text.'
    }

    It 'refuses an empty body' {
        $script:Tmp = ConvertTo-TemplateConfigFile '{"templates":{"hello":{"internal":false,"subject":"Hi","body":"  "}}}'
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:Tmp -Name 'hello' -ExtraMessage '' } | Should -Throw '*empty body*'
    }

    It 'refuses an internal template even when it has a subject' {
        $script:Tmp = ConvertTo-TemplateConfigFile '{"templates":{"hello":{"internal":true,"subject":"Hi","body":"x"}}}'
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath $script:Tmp -Name 'hello' -ExtraMessage '' } | Should -Throw '*internal*'
    }

    It 'throws when the config file is missing' {
        { Resolve-WhmcsOpenTicketTemplate -ConfigPath (Join-Path ([System.IO.Path]::GetTempPath()) 'no-such-ticket-config.json') -Name 'x' -ExtraMessage '' } | Should -Throw '*not found*'
    }
}
