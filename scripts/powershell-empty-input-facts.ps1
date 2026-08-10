<#
.SYNOPSIS
    Emit, as JSON, the facts a caller needs to decide whether an empty
    `$env:` reference can vanish from a PowerShell invocation (issue #1150).

.DESCRIPTION
    The second half of the #1080 remedy: once a dispatch input is moved out of
    the `run:` text into step-level `env:`, the body must fail closed when that
    mapping is empty. In a NATIVE command an empty `$env:X` renders as no
    argument at all, so `-Domain $env:DOMAIN -KeyPath $k` becomes
    `-Domain -KeyPath $k` and every later argument binds one position to the
    left (ledger L214).

    Deciding that needs three fact kinds, and all three need the real
    PowerShell AST rather than a regex, because the shapes in this tree span
    `+=` accumulation, `if` blocks and array splats:

      commands     every command, how it was invoked, and each argument
                   CLASSIFIED -- in particular which arguments are `$env:X`
                   references, which `scripts/powershell-invocation-facts.ps1`
                   deliberately renders as $null (it only cares about literal
                   flag strings).

      assignments  every assignment, with array elements classified the same
                   way, so `$a = @('-Domain', $env:DOMAIN)` and its `+=`
                   continuations can be read as an argument list. `envRefs`
                   carries the `$env:` names the right-hand side reads, which
                   is what makes `$d = $env:INPUT_DOMAIN` recognisable as a
                   LOCAL COPY -- the false-positive class named in #1150.

      conditions   the variable names read by every `if` / ternary / `??`
                   CONDITION, with the line it sits on. A body that tests a
                   variable before using it is guarded, whether the test exits
                   (`if (IsNullOrWhiteSpace($env:X)) { exit 1 }`), fills a
                   default, or gates the append (`if ($env:X) { $a += ... }`).
                   All three are live in this tree and all three are guards.

    Reporting only. The verdict lives in
    scripts/check-workflow-empty-input-guard.py; this file exists because only
    PowerShell can parse PowerShell.

.NOTES
    Nothing here executes workflow code -- `Parser::ParseInput` builds an AST
    and runs nothing. A separate file from powershell-invocation-facts.ps1 on
    purpose: that script's JSON is the contract of the #1051 guard, and
    widening it in place would put two guards on one shape.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BodyListFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-EnvName {
    <#
        The environment-variable name of `$env:FOO`, or $null.

        `IsDriveQualified` plus a drive of `env` is the property that
        identifies it, checked rather than a string prefix so `$environment`
        cannot match.
    #>
    param($Expression)

    if ($Expression -isnot [System.Management.Automation.Language.VariableExpressionAst]) { return $null }
    $path = $Expression.VariablePath
    if (-not $path.IsDriveQualified) { return $null }
    if ($path.DriveName -ne 'env') { return $null }
    return $path.UserPath.Substring(4)
}

function Get-Classified {
    <#
        One argument or array element, classified as the caller needs it:

          env       $env:FOO       -> text = FOO
          literal   'x' / 42       -> text = the value
          variable  $foo           -> text = foo
          other     anything else  -> text = $null

        An EXPANDABLE string that interpolates a variable ("$env:FOO/x") is
        deliberately `other`, not `literal`: it is one argument whatever its
        contents, so it cannot vanish the way a bare reference can.
    #>
    param($Expression)

    $result = [ordered]@{ kind = 'other'; text = $null; line = 0; offset = -1 }
    if ($null -eq $Expression) { return $result }
    $result.line = $Expression.Extent.StartLineNumber
    $result.offset = $Expression.Extent.StartOffset

    $envName = Get-EnvName -Expression $Expression
    if ($null -ne $envName) {
        $result.kind = 'env'
        $result.text = $envName
        return $result
    }

    if ($Expression -is [System.Management.Automation.Language.VariableExpressionAst]) {
        $result.kind = 'variable'
        $result.text = $Expression.VariablePath.UserPath
        return $result
    }

    if ($Expression -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
        $result.kind = 'literal'
        $result.text = $Expression.Value
        return $result
    }

    if ($Expression -is [System.Management.Automation.Language.ConstantExpressionAst]) {
        $result.kind = 'literal'
        $result.text = [string]$Expression.Value
        return $result
    }

    return $result
}

function Get-EnvReference {
    <#
        Every `$env:` name read anywhere inside a subtree. Used for two things:
        alias detection on an assignment's right-hand side, and the names a
        condition tests.
    #>
    param($Ast)

    $names = @()
    if ($null -eq $Ast) { return $names }
    foreach ($variable in $Ast.FindAll({ $args[0] -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
        $envName = Get-EnvName -Expression $variable
        if ($null -ne $envName) { $names += , $envName }
    }
    return $names
}

function Get-LocalReference {
    param($Ast)

    $names = @()
    if ($null -eq $Ast) { return $names }
    foreach ($variable in $Ast.FindAll({ $args[0] -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
        if ($null -ne (Get-EnvName -Expression $variable)) { continue }
        $names += , $variable.VariablePath.UserPath
    }
    return $names
}

function Get-ExpressionFromStatement {
    <#
        The right-hand side of an assignment as an expression, when it is one.
        A multi-element pipeline is left unclassified rather than guessed at --
        powershell-invocation-facts.ps1 draws the same line for the same reason.
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
    }
    return $null
}

function Get-ArrayElement {
    <#
        The element expressions of an array literal or `@( ... )`, in SOURCE
        ORDER -- order is what makes an element a parameter value, because the
        element before it is the flag. A scalar right-hand side is returned as
        a one-element list, which is how `$a += '-DryRun'` accumulates.
    #>
    param($Expression)

    if ($null -eq $Expression) { return $null }

    if ($Expression -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        return $Expression.Elements
    }
    if ($Expression -is [System.Management.Automation.Language.ArrayExpressionAst]) {
        $elements = @()
        foreach ($statement in $Expression.SubExpression.Statements) {
            $inner = Get-ExpressionFromStatement -Statement $statement
            if ($inner -is [System.Management.Automation.Language.ArrayLiteralAst]) {
                $elements += $inner.Elements
            }
            elseif ($null -ne $inner) {
                $elements += , $inner
            }
        }
        return $elements
    }
    return @($Expression)
}

function Get-BodyFactSet {
    param([string]$Id, [string]$Text)

    $errors = $null
    $tokens = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)

    $facts = [ordered]@{
        id          = $Id
        parseErrors = @()
        commands    = @()
        assignments = @()
        conditions  = @()
    }

    foreach ($parseError in $errors) {
        $facts.parseErrors += , ([ordered]@{
                line    = $parseError.Extent.StartLineNumber
                message = $parseError.Message
            })
    }
    # A body that does not parse is reported, never analysed: partial facts
    # from a broken parse would read as "no findings here".
    if ($facts.parseErrors.Count -gt 0) { return $facts }

    foreach ($command in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        $elements = $command.CommandElements
        if ($elements.Count -eq 0) { continue }

        $name = $null
        $first = Get-Classified -Expression $elements[0]
        if ($first.kind -eq 'literal') { $name = $first.text }
        elseif ($first.kind -eq 'variable') { $name = '$' + $first.text }
        elseif ($first.kind -eq 'env') { $name = '$env:' + $first.text }

        $arguments = @()
        for ($i = 1; $i -lt $elements.Count; $i++) {
            $element = $elements[$i]
            if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                # `-Domain:$env:X` carries its value on the parameter itself.
                $attached = $null
                if ($null -ne $element.Argument) {
                    $attached = Get-Classified -Expression $element.Argument
                }
                $arguments += , ([ordered]@{
                        index    = $i
                        kind     = 'parameter'
                        text     = $element.ParameterName
                        line     = $element.Extent.StartLineNumber
                        offset   = $element.Extent.StartOffset
                        attached = $attached
                    })
                continue
            }
            if ($element -is [System.Management.Automation.Language.VariableExpressionAst] -and $element.Splatted) {
                $arguments += , ([ordered]@{
                        index    = $i
                        kind     = 'splat'
                        text     = $element.VariablePath.UserPath
                        line     = $element.Extent.StartLineNumber
                        offset   = $element.Extent.StartOffset
                        attached = $null
                    })
                continue
            }
            $classified = Get-Classified -Expression $element
            $arguments += , ([ordered]@{
                    index    = $i
                    kind     = $classified.kind
                    text     = $classified.text
                    line     = $classified.line
                    offset   = $classified.offset
                    attached = $null
                })
        }

        $facts.commands += , ([ordered]@{
                name       = $name
                invocation = [string]$command.InvocationOperator
                line       = $command.Extent.StartLineNumber
                arguments  = @($arguments)
                text       = $command.Extent.Text
            })
    }

    foreach ($assignment in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        $left = $assignment.Left
        if ($left -isnot [System.Management.Automation.Language.VariableExpressionAst]) { continue }

        $right = Get-ExpressionFromStatement -Statement $assignment.Right
        $elements = @()
        foreach ($element in (Get-ArrayElement -Expression $right)) {
            $elements += , (Get-Classified -Expression $element)
        }

        $facts.assignments += , ([ordered]@{
                name     = $left.VariablePath.UserPath
                envName  = (Get-EnvName -Expression $left)
                operator = [string]$assignment.Operator
                line     = $assignment.Extent.StartLineNumber
                offset   = $assignment.Extent.StartOffset
                elements = @($elements)
                envRefs  = @(Get-EnvReference -Ast $assignment.Right)
                varRefs  = @(Get-LocalReference -Ast $assignment.Right)
            })
    }

    # Every place the body TESTS a value. An `if` clause, a ternary, and the
    # left operand of `??` / `??=` are the three spellings live in this tree.
    $conditionAsts = @()
    foreach ($ifStatement in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.IfStatementAst] }, $true)) {
        foreach ($clause in $ifStatement.Clauses) { $conditionAsts += , $clause.Item1 }
    }
    foreach ($ternary in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.TernaryExpressionAst] }, $true)) {
        $conditionAsts += , $ternary.Condition
    }
    foreach ($binary in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.BinaryExpressionAst] }, $true)) {
        if ([string]$binary.Operator -eq 'QuestionQuestion') { $conditionAsts += , $binary.Left }
    }
    foreach ($assignment in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        if ([string]$assignment.Operator -eq 'QuestionQuestionEquals') { $conditionAsts += , $assignment.Left }
    }

    foreach ($condition in $conditionAsts) {
        if ($null -eq $condition) { continue }
        $facts.conditions += , ([ordered]@{
                line    = $condition.Extent.StartLineNumber
                offset  = $condition.Extent.StartOffset
                envRefs = @(Get-EnvReference -Ast $condition)
                varRefs = @(Get-LocalReference -Ast $condition)
                text    = $condition.Extent.Text
            })
    }

    return $facts
}

$result = [ordered]@{ bodies = @() }
foreach ($unit in (Get-Content -LiteralPath $BodyListFile -Raw -Encoding utf8 | ConvertFrom-Json)) {
    $result.bodies += , (Get-BodyFactSet -Id $unit.id -Text $unit.text)
}

# Depth 10: bodies -> commands -> arguments -> attached. The default of 2
# truncates them to type names, silently.
$result | ConvertTo-Json -Depth 10 -Compress
