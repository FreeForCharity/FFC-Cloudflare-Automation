<#
.SYNOPSIS
    Emit, as JSON, the facts a caller needs to decide whether a PowerShell
    invocation of a repo script can bind (issue #1051).

.DESCRIPTION
    Two fact kinds, both read out of the real PowerShell AST rather than a
    regex, because the shapes in this tree span multiple lines, `&` invocation
    operators, pipelines and `+=` accumulation:

      -BodyListFile   a JSON array of { id, text } units -- one per workflow
                      `run:` block. For each unit this reports every command,
                      how it was invoked, which arguments were splatted, and how
                      each variable in that unit was assigned.

      -ScriptListFile a JSON array of repo-relative `.ps1` paths. For each this
                      reports the parameter names, aliases and parameter sets of
                      the script's `param()` block, resolved with `Get-Command`
                      (which reads the param block -- it does NOT execute the
                      script).

    Reporting only. The verdict lives in scripts/check-pwsh-workflow-invocations.py;
    this file exists because only PowerShell can parse PowerShell.

.NOTES
    Nothing here executes workflow code. `Get-Command -Type ExternalScript`
    compiles a script's parameter metadata without running its body -- that is
    the whole reason the binding half of the check is safe to run in CI.
#>
[CmdletBinding()]
param(
    [string]$BodyListFile,
    [string]$ScriptListFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ExpressionFromStatement {
    <#
        The right-hand side of an assignment is a StatementAst. `$x = @(1,2)`
        arrives as a bare CommandExpressionAst, while a piped RHS
        (`$x = Get-Thing | Where-Object ...`) arrives as a PipelineAst -- handle
        both, because assuming the pipeline wrapper classifies EVERY plain
        assignment as `unknown`, which is the direction that reads as "nothing
        to check here". Anything with more than one pipeline element is a
        command pipeline, not a literal, and is deliberately left unclassified.
    #>
    param($Statement)

    if ($null -eq $Statement) { return $null }
    if ($Statement -is [System.Management.Automation.Language.CommandExpressionAst]) {
        return $Statement.Expression
    }
    if ($Statement -is [System.Management.Automation.Language.PipelineAst]) {
        if ($Statement.PipelineElements.Count -ne 1) { return $null }
        $element = $Statement.PipelineElements[0]
        if ($element -is [System.Management.Automation.Language.CommandExpressionAst]) {
            return $element.Expression
        }
        return $null
    }
    return $null
}

function Get-ElementText {
    <#
        The literal text of an array element, or $null when it is not a
        constant. Only the constant elements matter: an array splat is a
        finding precisely when it carries FLAG-shaped strings ('-Zone'), and a
        flag is always a literal.
    #>
    param($Expression)

    if ($Expression -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
        return $Expression.Value
    }
    if ($Expression -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) {
        return $Expression.Value
    }
    if ($Expression -is [System.Management.Automation.Language.ConstantExpressionAst]) {
        return [string]$Expression.Value
    }
    return $null
}

function Get-ValueShape {
    <#
        Classify an assignment's value as the splat operator sees it:

          hashtable  @{ Zone = $d }        -> binds BY NAME   (correct for a script call)
          array      @('-Zone', $d)        -> binds POSITIONALLY (the #1050 defect)
          unknown    anything else

        `unknown` is never silently treated as safe -- the caller reports it.
    #>
    param($Expression)

    $shape = [ordered]@{ kind = 'unknown'; elements = @() }
    if ($null -eq $Expression) { return $shape }

    # A bare constant matters for the `+=` accumulation form: `$a += '-DryRun'`
    # is how half this tree finishes building an argument list, and dropping it
    # would classify the variable as unknown at the moment it gained the flag.
    if ($Expression -is [System.Management.Automation.Language.ConstantExpressionAst] -or
        $Expression -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) {
        $shape.kind = 'scalar'
        $shape.elements = @((Get-ElementText -Expression $Expression))
        return $shape
    }

    if ($Expression -is [System.Management.Automation.Language.HashtableAst]) {
        $shape.kind = 'hashtable'
        $keys = @()
        foreach ($pair in $Expression.KeyValuePairs) {
            $key = Get-ElementText -Expression $pair.Item1
            $keys += , $key
        }
        $shape.elements = $keys
        return $shape
    }

    $elementAsts = $null
    if ($Expression -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        # `$a = 1, 2` and the inner literal of `@(1, 2)`.
        $elementAsts = $Expression.Elements
    }
    elseif ($Expression -is [System.Management.Automation.Language.ArrayExpressionAst]) {
        # `@( ... )`. The sub-expression may hold several statements; only the
        # simple single-pipeline form is classified by element.
        $elementAsts = @()
        foreach ($statement in $Expression.SubExpression.Statements) {
            $inner = Get-ExpressionFromStatement -Statement $statement
            if ($inner -is [System.Management.Automation.Language.ArrayLiteralAst]) {
                $elementAsts += $inner.Elements
            }
            elseif ($null -ne $inner) {
                $elementAsts += , $inner
            }
        }
    }

    if ($null -ne $elementAsts) {
        $shape.kind = 'array'
        $texts = @()
        foreach ($element in $elementAsts) {
            $texts += , (Get-ElementText -Expression $element)
        }
        $shape.elements = $texts
    }

    return $shape
}

function Get-BodyFactSet {
    param([string]$Id, [string]$Text)

    $errors = $null
    $tokens = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)

    $facts = [ordered]@{
        id          = $Id
        parseErrors = @()
        assignments = @()
        commands    = @()
    }

    foreach ($parseError in $errors) {
        $facts.parseErrors += , ([ordered]@{
                line    = $parseError.Extent.StartLineNumber
                message = $parseError.Message
            })
    }
    # A body that does not parse is reported, never analysed: partial facts from
    # a broken parse would read as "no findings here".
    if ($facts.parseErrors.Count -gt 0) { return $facts }

    foreach ($assignment in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        $left = $assignment.Left
        $target = $null
        $memberKey = $null

        if ($left -is [System.Management.Automation.Language.VariableExpressionAst]) {
            $target = $left.VariablePath.UserPath
        }
        elseif ($left -is [System.Management.Automation.Language.MemberExpressionAst] -and
            $left.Expression -is [System.Management.Automation.Language.VariableExpressionAst]) {
            # `$params.DryRun = $true` -- a KEY ADDED to an existing hashtable.
            # 112 and 120 both build their splat this way, so ignoring it would
            # under-report the key set the target script has to bind.
            $target = $left.Expression.VariablePath.UserPath
            $memberKey = Get-ElementText -Expression $left.Member
        }
        elseif ($left -is [System.Management.Automation.Language.IndexExpressionAst] -and
            $left.Target -is [System.Management.Automation.Language.VariableExpressionAst]) {
            # `$params['DryRun'] = $true` -- the same thing, indexer spelling.
            $target = $left.Target.VariablePath.UserPath
            $memberKey = Get-ElementText -Expression $left.Index
        }
        if ($null -eq $target) { continue }

        if ($null -ne $memberKey) {
            $facts.assignments += , ([ordered]@{
                    name     = $target
                    operator = 'MemberEquals'
                    kind     = 'key'
                    elements = @($memberKey)
                    line     = $assignment.Extent.StartLineNumber
                })
            continue
        }

        $shape = Get-ValueShape -Expression (Get-ExpressionFromStatement -Statement $assignment.Right)
        $facts.assignments += , ([ordered]@{
                name     = $target
                operator = [string]$assignment.Operator
                kind     = $shape.kind
                elements = @($shape.elements)
                line     = $assignment.Extent.StartLineNumber
            })
    }

    foreach ($command in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        $elements = $command.CommandElements
        if ($elements.Count -eq 0) { continue }

        $name = Get-ElementText -Expression $elements[0]
        if ($null -eq $name -and $elements[0] -is [System.Management.Automation.Language.VariableExpressionAst]) {
            # `& $tool` -- the name is not in the source. Reported as such so the
            # caller can say "unverifiable" rather than "clean".
            $name = '$' + $elements[0].VariablePath.UserPath
        }

        # ORDER IS LOAD-BEARING, so arguments stay one ordered list rather than
        # three buckets. `& pwsh -NoProfile -File .\x.ps1 @a` and `& .\x.ps1 @a`
        # differ only in where the script path sits: in the first the splat
        # belongs to a NATIVE command (array splatting is correct), in the
        # second to the PowerShell binder (array splatting is the #1050 defect).
        # Bucketing by kind throws away exactly the fact that separates them.
        $arguments = @()
        for ($i = 1; $i -lt $elements.Count; $i++) {
            $element = $elements[$i]
            $argument = [ordered]@{
                index = $i
                kind  = 'positional'
                text  = $null
                line  = $element.Extent.StartLineNumber
            }
            if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                $argument.kind = 'parameter'
                $argument.text = $element.ParameterName
            }
            elseif ($element -is [System.Management.Automation.Language.VariableExpressionAst] -and $element.Splatted) {
                $argument.kind = 'splat'
                $argument.text = $element.VariablePath.UserPath
            }
            else {
                $argument.text = Get-ElementText -Expression $element
            }
            $arguments += , $argument
        }

        $facts.commands += , ([ordered]@{
                name       = $name
                # 'Unknown' = bare invocation, 'Ampersand' = `& x`, 'Dot' = `. x`.
                invocation = [string]$command.InvocationOperator
                arguments  = @($arguments)
                line       = $command.Extent.StartLineNumber
                text       = $command.Extent.Text
            })
    }

    return $facts
}

function Get-ScriptFactSet {
    param([string]$Path)

    $facts = [ordered]@{
        path          = $Path
        resolved      = $false
        error         = $null
        parameters    = @()
        parameterSets = @()
    }

    try {
        $command = Get-Command -Name (Resolve-Path -LiteralPath $Path).Path -CommandType ExternalScript -ErrorAction Stop
    }
    catch {
        $facts.error = $_.Exception.Message
        return $facts
    }

    $facts.resolved = $true
    foreach ($entry in $command.Parameters.GetEnumerator()) {
        $facts.parameters += , ([ordered]@{
                name    = $entry.Key
                aliases = @($entry.Value.Aliases)
                type    = $entry.Value.ParameterType.FullName
            })
    }
    foreach ($set in $command.ParameterSets) {
        $names = @()
        $mandatory = @()
        $positional = @()
        foreach ($parameter in $set.Parameters) {
            $names += , $parameter.Name
            if ($parameter.IsMandatory) { $mandatory += , $parameter.Name }
            # Position is Int32.MinValue for a parameter that can only be bound
            # by name. The count of real positions is what decides whether a
            # positionally-bound (array-splatted) call could ever bind.
            if ($parameter.Position -ge 0) { $positional += , $parameter.Name }
        }
        $facts.parameterSets += , ([ordered]@{
                name          = $set.Name
                isDefault     = $set.IsDefault
                parameters    = @($names)
                mandatory     = @($mandatory)
                positionalArg = @($positional)
            })
    }

    return $facts
}

$result = [ordered]@{ bodies = @(); scripts = @() }

if ($BodyListFile) {
    foreach ($unit in (Get-Content -LiteralPath $BodyListFile -Raw -Encoding utf8 | ConvertFrom-Json)) {
        $result.bodies += , (Get-BodyFactSet -Id $unit.id -Text $unit.text)
    }
}

if ($ScriptListFile) {
    foreach ($path in (Get-Content -LiteralPath $ScriptListFile -Raw -Encoding utf8 | ConvertFrom-Json)) {
        $result.scripts += , (Get-ScriptFactSet -Path $path)
    }
}

# Depth 10: commands -> splats/parameters -> ordered dictionaries. The default
# of 2 truncates them to type names, silently.
$result | ConvertTo-Json -Depth 10 -Compress
