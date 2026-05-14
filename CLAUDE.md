# Claude Code Instructions: FFC-Cloudflare-Automation

Welcome, Claude! This document provides specific instructions for working on FFC-Cloudflare-Automation.

**Project:** FFC-Cloudflare-Automation -- a Free For Charity nonprofit website

See **AGENTS.md** for the full project reference including architecture, commands, conventions, and security rules. This file covers what is different or specific to your capabilities as Claude Code.

---

## Terminal & Tool Usage

You have full terminal access via the Bash tool. Use it for all CLI operations.

**File editing:** Prefer the Edit tool over `sed` or `awk`. Always read a file before editing it.

**File search:** Use Grep and Glob tools instead of `grep`, `find`, or `rg` bash commands.

---

## Timeouts

**Set timeout to 180+ seconds** for these commands:

| Command            | Why                                                |
| ------------------ | -------------------------------------------------- |
| `npm run build`    | Static export can take 30-60s; do not cancel early |
| `npm run test:e2e` | Playwright launches browsers; needs time           |
| `npm install`      | Network-dependent; can be slow on first run        |

**NEVER CANCEL a running build, test, or install command.** Let it finish. If it fails, read the error output.

---

## Pre-Commit Checklist

Run these in order before committing:

```bash
npm run format    # Fix formatting
npm run lint      # Check for lint errors
npm test          # Run unit tests
npm run build     # Verify static export
npm run test:e2e  # Run E2E tests
```

If any step fails, fix the issue and re-run from that step forward.

---

## MCP Servers

You may have access to these MCP servers. Use them when available:

| Server             | What It Provides                                         |
| ------------------ | -------------------------------------------------------- |
| **Playwright MCP** | Browser automation, screenshots, accessibility snapshots |
| **GitHub MCP**     | Issue/PR management, repository operations               |
| **Cloudflare MCP** | DNS records, Pages deployments, Workers                  |
| **Sentry MCP**     | Error tracking, performance monitoring                   |

Check your available tools at the start of each session. If an MCP server is available, prefer it over CLI alternatives for that domain.

---

## Custom Agents

Check `.claude/agents/` for custom agent definitions. Common agents include:

| Agent         | Purpose                               |
| ------------- | ------------------------------------- |
| `dns-audit`   | Audit DNS records for correctness     |
| `site-health` | Check site availability, SSL, headers |
| `pr-reviewer` | Automated PR review checklist         |
| `onboarding`  | New repo setup and configuration      |

Invoke these when the task matches their purpose. If no matching agent exists, proceed with your general capabilities.

---

## Workflow Reminders

- **Always create a branch.** Never commit directly to `main`.
- **Link PRs to issues** with `Fixes #NNN` or `Refs #NNN` in the PR body.
- **Commit messages** use Conventional Commits: `feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`
- **kebab-case** for all route folder names (SEO requirement).
- **Use `assetPath()`** for all image and asset references (GitHub Pages compatibility).

# PowerShell Infrastructure Overlay for CLAUDE.md

> Append this content to the base CLAUDE.md when deploying to PowerShell/infrastructure repos
> such as FFC-Cloudflare-Automation.

---

## PowerShell Infrastructure Context

This repository is a PowerShell-based infrastructure automation toolset. It manages DNS,
email authentication, domain billing, and website provisioning for ~30 charity domains
across the Free For Charity network.

### PowerShell Conventions

- **Formatting**: All `.ps1` files must pass `Invoke-Formatter` (from PSScriptAnalyzer). The CI
  pipeline compares original vs. formatted output and fails on any difference. Run locally before
  committing:
  ```powershell
  Import-Module PSScriptAnalyzer
  $original = Get-Content -Path .\MyScript.ps1 -Raw
  $formatted = Invoke-Formatter -ScriptDefinition $original
  Set-Content -Path .\MyScript.ps1 -Value $formatted -NoNewline
  ```
- **Linting**: PSScriptAnalyzer runs in CI with `-Severity @('Error','Warning')`. Errors fail the
  build; warnings are reported but tolerated.
- **Approved verbs**: Use standard PowerShell verbs: `Get-`, `Set-`, `New-`, `Remove-`, `Update-`,
  `Export-`, `Import-`, `Invoke-`, `Test-`.
- **Parameters**: Follow existing patterns -- `[Parameter(Mandatory = $true)]` with
  `[ValidateNotNullOrEmpty()]` or `[ValidateSet()]` as appropriate.
- **Destructive operations**: Support `-DryRun` or `-WhatIf` switches. Preview changes and print
  what would happen without applying them.
- **Error handling**: Use `try/catch` blocks. Call `Write-Error` for failures. Use `exit 1` for
  fatal errors that should stop the pipeline.
- **API token validation**: Always check that the required API token environment variable is
  present before making any API call. Fail early with a clear message if missing.

### GitHub Actions Workflows

This repo has 24+ GitHub Actions workflows using a numbered naming convention for UI ordering:

| Prefix | Category | Examples |
|--------|----------|---------|
| 0-* | Status/reporting | `0-domain-status.yml` |
| 1-* | Standards enforcement | `1-enforce-domain-standard.yml`, `1-audit-compliance.yml` |
| 2-* | Enforcement | `2-enforce-standard.yml` |
| 3-* | DNS record management | `3-manage-record.yml` |
| 4-* | Export/inventory | `4-domain-export-inventory.yml`, `4-export-summary.yml` |
| 5-8 | M365 operations | `5-m365-domain-and-dkim.yml`, `6-m365-list-domains.yml` |
| 7-8 | WHMCS operations | `7-whmcs-export-domains.yml`, `8-whmcs-export-products.yml` |
| 9-10 | WHMCS billing | `9-whmcs-export-payment-methods.yml`, `10-whmcs-zeffy-payments-import-draft.yml` |
| 11-15 | Provisioning | `11-cloudflare-zone-create.yml`, `15-website-provision.yml` |

Workflow names in YAML must start with a two-digit prefix (e.g., `name: '05. M365 - DKIM Setup'`).
The CI pipeline validates that prefixes are present and unique.

### CI Pipeline Checks

The `ci.yml` workflow runs on every PR and push to main:

1. **Workflow name prefix validation** -- two-digit prefixes, unique across all workflows
2. **actionlint** -- validates all workflow YAML syntax and expressions
3. **Prettier** -- checks formatting of YAML, JSON, Markdown files
4. **PowerShell syntax validation** -- parses all `.ps1` files for syntax errors
5. **PSScriptAnalyzer** -- lints PowerShell for errors and warnings
6. **Invoke-Formatter** -- checks PowerShell formatting matches expected output
7. **Sensitive file detection** -- scans for `.pem`, `.key`, `.env`, `.env.local` files

### Cloudflare Integration

- **Dual-account support**: FFC account and CM (Clarke Moyer) account, each with separate API tokens
- Environment variables: `CLOUDFLARE_API_TOKEN_FFC` and `CLOUDFLARE_API_TOKEN_CM`
- GitHub Secrets: `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` and `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
- Scripts auto-detect which token can access a given zone
- Operations: zone creation, DNS record CRUD, DNS export, domain status reporting

### M365 Integration

- **Graph API via OIDC**: Federated credentials, no stored client secrets
- Domain operations: add domain to tenant, verify ownership via TXT record
- Email authentication: DKIM enable, SPF/DKIM/DMARC record creation in Cloudflare
- Standard MX record: `0 <domain>.mail.protection.outlook.com`
- Standard SPF: `v=spf1 include:spf.protection.outlook.com -all`
- DKIM: Two CNAME selectors pointing to `*.domainkey.<domain>.onmicrosoft.com`
- DMARC: `v=DMARC1; p=reject; rua=mailto:dmarc@<domain>`

### WHMCS Integration

- Domain billing exports and lookups
- Nameserver management (set Cloudflare NS on registered domains)
- Client, invoice, and transaction reporting
- Payment method exports

### WPMUDEV Integration

- WordPress hosting site inventory exports
- Site health monitoring data

### GitHub Environment Secrets

Workflows that need secrets must use GitHub Environments:

| Environment | Secrets Available |
|-------------|------------------|
| `cloudflare-prod` | `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`, `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` |
| `m365-prod` | Azure AD OIDC credentials for Graph API |
| `github-prod` | `GH_PAT` for cross-repo operations |
| `wpmudev-prod` | `WPMUDEV_API_KEY` |
| `whmcs-prod` | `WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET`, `WHMCS_API_URL` |
