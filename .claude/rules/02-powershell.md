# PowerShell Coding Rules

## Formatting

- Use **Invoke-Formatter** for code formatting (CI enforces it)
- Run `.\scripts\format-powershell.ps1` before committing
- CI will fail if formatted output differs from source

## Linting

- **PSScriptAnalyzer** is enforced in CI (errors fail the build)
- Run `.\scripts\analyze-powershell.ps1` to check locally
- Follow all PSScriptAnalyzer rules; suppress only with documented justification

## Script Conventions

- Use approved PowerShell verbs: Get-, Set-, New-, Remove-, Update-, Export-, Import-
- Parameters: Use `[Parameter(Mandatory)]` with `[ValidateNotNullOrEmpty()]`
- Support `-WhatIf` / `-DryRun` for any destructive operations
- Always validate API token presence before making API calls
- Error handling: Use `try/catch`, `Write-Error` for failures, `exit 1` on fatal errors

## API Tokens

- Reference via environment variables: `$env:CLOUDFLARE_API_TOKEN_FFC`, `$env:CLOUDFLARE_API_TOKEN_CM`
- Never hardcode tokens. Never echo tokens to output.
- Dual-account support: FFC account and CM (Clarke Moyer) account tokens

## CI Pipeline

- Prettier: JSON, YAML, Markdown, HTML, CSS formatting
- PSScriptAnalyzer: PowerShell linting (errors fail CI)
- Invoke-Formatter: PowerShell formatting (diff fails CI)
- actionlint: GitHub Actions YAML validation
- Sensitive file detection: Blocks _.pem, _.key, .env commits
