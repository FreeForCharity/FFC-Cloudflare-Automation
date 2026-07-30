<#
.SYNOPSIS
    Emits, as JSON, the command-resolution facts for a set of PowerShell files.

.DESCRIPTION
    Fact extractor behind scripts/check-powershell-command-resolution.py (issue #930).

    It uses the PowerShell language parser itself -- NOT a regex -- so that command
    position, splatting, pipelines, here-strings and comments are decided by the
    same engine that will run the script. A regex scan of this repo reports
    findings on `$x = 'Get-Thing'` and on Verb-Noun names inside comment blocks;
    the AST does not, and a guard that cries wolf gets switched off (ledger L09).

    For each file it reports:
      - functions   : every function defined in the file, at any nesting depth
                      (functions defined inside if/try blocks included -- a
                      top-level scan would miss them and report false findings)
      - dotSources  : each dot-source (`. <path>`), resolved to a repo-relative
                      path when every path segment is a literal, or resolved=false
                      when any segment is computed (the caller then skips that
                      file loudly rather than guessing its resolution set)
      - imports     : module names from a literal `Import-Module <Name>`
      - commands    : every command invocation whose name is statically known,
                      with its line number
      - parseErrors : parser diagnostics; the caller treats a non-empty list as
                      an error, never as a skip

    It also emits `inventory`: the command names available to the pwsh running
    this script (Get-Command). The caller unions that with the committed floor
    snapshot, so the allowlist is generated rather than hand-maintained.

    Deliberately out of scope, because it is not statically resolvable:
      - `& $variable` / `& $scriptblock` invocation (GetCommandName() returns null)
      - Invoke-Expression string bodies
      - command names assembled at runtime
      - bare external executables on PATH (`az`, `gh`, `git`) -- no Verb-Noun form

.PARAMETER PathListFile
    A UTF-8 text file holding one path to analyze per line. A list file rather
    than an argument array keeps the caller clear of per-platform command-line
    length limits when the whole repo (100+ scripts) is scanned at once.

.PARAMETER InventoryOnly
    Emit only the `inventory` array. Used to regenerate the committed floor
    snapshot config/powershell-cmdlet-inventory.json.

.EXAMPLE
    pwsh -NoProfile -File scripts/powershell-command-facts.ps1 -PathListFile files.txt

.EXAMPLE
    pwsh -NoProfile -File scripts/powershell-command-facts.ps1 -InventoryOnly
#>
[CmdletBinding(DefaultParameterSetName = 'Scan')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Scan')]
    [string]$PathListFile,

    [Parameter(Mandatory = $true, ParameterSetName = 'Inventory')]
    [switch]$InventoryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-CommandInventory {
    <#
        .SYNOPSIS
            Names of every command this PowerShell can resolve.
        .DESCRIPTION
            Cmdlets, functions and aliases from every module on PSModulePath.
            Module discovery is what makes this generated rather than curated:
            adding a module to CI adds its commands here without an edit.
    #>
    Get-Command -CommandType Cmdlet, Function, Alias -All -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name -Unique |
        Sort-Object -Unique
}

if ($InventoryOnly) {
    [pscustomobject]@{
        psVersion = $PSVersionTable.PSVersion.ToString()
        modules   = @(Get-Module -ListAvailable | Select-Object -ExpandProperty Name -Unique | Sort-Object)
        inventory = @(Get-CommandInventory)
    } | ConvertTo-Json -Depth 4
    return
}

function Resolve-DotSourceTarget {
    <#
        .SYNOPSIS
            Repo-relative target of a dot-source, or $null when it is computed.
        .DESCRIPTION
            Handles the shapes this repo uses, all anchored on $PSScriptRoot (the
            directory of the dot-sourcing file):

                . "$PSScriptRoot/lib.ps1"
                . (Join-Path $PSScriptRoot 'lib.ps1')
                . (Join-Path $PSScriptRoot '..' 'scripts' 'lib.ps1')
                . (Join-Path (Split-Path $PSScriptRoot -Parent) 'lib.ps1')

            Every literal segment is kept, in order, so a `..` hop is honoured
            rather than collapsed onto the file's own directory. Anything whose
            path depends on a variable returns $null.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.Language.Ast]$ArgumentAst
    )

    $text = $ArgumentAst.Extent.Text
    if ($text -notmatch '\$PSScriptRoot') {
        return $null
    }

    # Any variable other than $PSScriptRoot makes the path non-literal.
    $withoutRoot = $text -replace '\$PSScriptRoot', ''
    if ($withoutRoot -match '\$') {
        return $null
    }

    $segments = @()
    if ($text -match 'Split-Path\s+\$PSScriptRoot\s+-Parent') {
        $segments += '..'
    }
    foreach ($m in [regex]::Matches($withoutRoot, '[''"]([^''"]+)[''"]')) {
        $segments += ($m.Groups[1].Value -replace '\\', '/').Trim('/')
    }
    if ($segments.Count -eq 0) {
        return $null
    }
    if ($segments[-1] -notmatch '\.ps(m?)1$') {
        return $null
    }
    return ($segments -join '/')
}

$results = @()
$paths = Get-Content -LiteralPath $PathListFile | Where-Object { $_.Trim().Length -gt 0 }

foreach ($file in $paths) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path -LiteralPath $file).ProviderPath, [ref]$tokens, [ref]$errors)

    $parseErrors = @()
    foreach ($e in $errors) {
        $parseErrors += [pscustomobject]@{ line = $e.Extent.StartLineNumber; message = $e.Message }
    }

    $functions = @()
    foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        $functions += $fn.Name
    }

    $commands = @()
    $dotSources = @()
    $imports = @()

    foreach ($cmd in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        if ($cmd.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Dot) {
            $target = Resolve-DotSourceTarget -ArgumentAst $cmd.CommandElements[0]
            $dotSources += [pscustomobject]@{
                line     = $cmd.Extent.StartLineNumber
                text     = $cmd.Extent.Text
                target   = $target
                resolved = ($null -ne $target)
            }
            continue
        }

        # GetCommandName() returns $null for `& $var` and other dynamic
        # invocations -- exactly the cases documented as out of scope.
        $name = $cmd.GetCommandName()
        if (-not $name) { continue }

        if ($name -eq 'Import-Module' -and $cmd.CommandElements.Count -ge 2) {
            $moduleArg = $cmd.CommandElements[1]
            if ($moduleArg -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
                $imports += $moduleArg.Value
            }
        }

        $commands += [pscustomobject]@{ line = $cmd.Extent.StartLineNumber; name = $name }
    }

    $results += [pscustomobject]@{
        file        = $file
        parseErrors = $parseErrors
        functions   = $functions
        dotSources  = $dotSources
        imports     = $imports
        commands    = $commands
    }
}

[pscustomobject]@{
    inventory = @(Get-CommandInventory)
    files     = $results
} | ConvertTo-Json -Depth 6 -Compress
