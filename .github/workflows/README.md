# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and support DNS
management operations.

## Why some workflows are deprecated

Older workflows (including the legacy “Cloudflare DNS Run” and “Zone Add”) were created before the
repository moved to a safer, issue-based process and the current PowerShell-first automation.

We keep deprecated workflows as **stubs** for two reasons:

1. **Stale links**: old docs/bookmarks/runbooks may still point to the legacy workflow file.
2. **Clarity**: the stub explains what replaced it and why.

If you see a workflow named like `.github/workflows/...`, that typically means the workflow did not
have a `name:` at the time it was created. GitHub uses the file path as the display name, which
sorts to the top. Stubs exist to prevent that confusion.

## Operational workflows (DNS + M365)

These are the workflows administrators run manually (Actions → Run workflow) to report on or change
domain configuration.

### 01. Domain - Status (Check)

- **Why**: quick read-only report across Cloudflare + M365.
- **When**: use first for any domain request or troubleshooting.
- **How**: run with `domain`, optionally provide `issue_number` to post results back.

### 02. Domain - Enforce Standard (Fix)

- **Why**: applies the standard DNS configuration and can enable DKIM (when run LIVE).
- **When**: after reviewing status output and confirming the change is desired.
- **How**: run with `domain`; keep `dry_run` enabled until ready to apply changes; optionally set
  `issue_number` to post results back.

### 03–07 DNS workflows

- **03. DNS - Add Domain (Create Zone) (Admin)**: create a new Cloudflare zone and output assigned
  name servers.
- **04. DNS - Manage Record (Manual)**: create/update/delete one record (best for one-off changes).
- **05. DNS - Audit Compliance (Report)**: report-only compliance check.
- **06. DNS - Enforce Standard (Fix)**: apply standard DNS configuration (DNS-only).
- **07. DNS - Export All Domains (Report)**: export summaries for review/audit.

### 08–12 M365 workflows

- **08. M365 - Add Tenant Domain (Admin)**: add a new domain in the tenant and print required DNS
  verification records.
- **09. M365 - Domain Status + DKIM (Toolbox)**: mixed utilities for domain and DKIM.
- **10. M365 - Enable DKIM (Exchange Online)**: focused DKIM enable.
- **11. M365 - Domain Preflight (Read-only)**: onboarding checks.
- **12. M365 - List Tenant Domains**: discovery/listing.

## ci.yml - Continuous Integration

Runs automated validation and security checks on all pull requests and pushes to main branch.

### When it runs:

- On pull requests targeting `main` branch
- On pushes to `main` branch

### What it does:

**Validate Repository Job:**

1. Checks out the code
2. Lints GitHub Actions workflows (actionlint)
3. Checks formatting for supported files (Prettier)
4. Validates PowerShell scripts for syntax errors (PowerShell parser)
5. Lints PowerShell scripts (PSScriptAnalyzer)
6. Checks PowerShell formatting (Invoke-Formatter)
7. Scans for accidentally committed sensitive files (_.pem, _.key, .env)
8. Verifies README.md exists

This workflow ensures that:

- GitHub Actions workflow YAML is well-formed and consistent (actionlint)
- Common file formats (YAML/Markdown/JSON/CSS/HTML) stay consistently formatted (Prettier)
- PowerShell scripts are syntactically correct (parser)
- PowerShell code quality rules are enforced (PSScriptAnalyzer; CI fails on errors)
- PowerShell formatting is consistent (Invoke-Formatter; CI fails if formatting differs)
- No sensitive data is accidentally committed
- Documentation exists

### Local usage

- Prettier (check): `npx --yes prettier@3.3.3 --check . --ignore-unknown`
- Prettier (write): `npx --yes prettier@3.3.3 --write . --ignore-unknown`
- actionlint (requires Go):
  - Install: `go install github.com/rhysd/actionlint/cmd/actionlint@latest`
  - Run: `actionlint`
- PowerShell formatting (repo helper): run
  [scripts/format-powershell.ps1](../../scripts/format-powershell.ps1)
- PowerShell lint summary (repo helper): run
  [scripts/analyze-powershell.ps1](../../scripts/analyze-powershell.ps1)

Notes:

- Prettier configuration lives in [.prettierrc.json](../../.prettierrc.json) and
  [.prettierignore](../../.prettierignore).
- Prettier does not format PowerShell; PowerShell formatting is handled by Invoke-Formatter.

## codeql-analysis.yml - Security Scanning

Performs automated security analysis using GitHub's CodeQL engine to detect security issues in
GitHub Actions workflows.

### When it runs:

- On pull requests targeting `main` branch
- On pushes to `main` branch
- Scheduled: Every Monday at 6:00 AM UTC

### What it does:

1. Checks out the code
2. Initializes CodeQL for GitHub Actions workflow analysis
3. Performs security analysis
4. Uploads results to GitHub Security tab

### Required Permissions:

- `actions: read` - Read workflow information
- `contents: read` - Read repository contents
- `security-events: write` - Upload security scan results

This workflow helps identify security vulnerabilities early in the development process, including:

- Workflow injection risks (untrusted inputs used in shell commands)
- Dangerous trigger patterns (e.g., unsafe use of privileged contexts)
- Excessive permissions or missing least-privilege settings

## Workflow Summary

| Workflow                      | Trigger                         | Purpose                                                                                                   |
| ----------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------- |
| ci.yml                        | PRs and pushes to main          | Lint workflows, validate scripts, and check for sensitive files                                           |
| codeql-analysis.yml           | PRs, pushes to main, and weekly | Security scanning of GitHub Actions workflows                                                             |
| 0-domain-status.yml           | Manual (workflow_dispatch)      | 01. Domain: Status check (Cloudflare + M365)                                                              |
| 1-enforce-domain-standard.yml | Manual (workflow_dispatch)      | 02. Domain: Enforce standard (Cloudflare + M365; supports issue post-back)                                |
| 1-audit-compliance.yml        | Manual (workflow_dispatch)      | Report: Check DNS compliance                                                                              |
| 2-enforce-standard.yml        | Manual (workflow_dispatch)      | Fix: Enforce standard DNS configuration                                                                   |
| 3-manage-record.yml           | Manual (workflow_dispatch)      | Manual: Manage a single DNS record                                                                        |
| 4-export-summary.yml          | Manual (workflow_dispatch)      | Report: Export all domains summary                                                                        |
| 5-m365-domain-and-dkim.yml    | Manual (workflow_dispatch)      | M365: Domain status + DKIM helpers (Graph + Exchange Online)                                              |
| 6-m365-list-domains.yml       | Manual (workflow_dispatch)      | M365: List tenant domains (Graph)                                                                         |
| 7-m365-domain-preflight.yml   | Manual (workflow_dispatch)      | M365: Domain onboarding preflight (two jobs: Graph in `m365-prod`, Cloudflare audit in `cloudflare-prod`) |
| 11-cloudflare-zone-add.yml    | Manual (workflow_dispatch)      | DNS: Add domain by creating a Cloudflare zone (admin-only) + output assigned name servers                 |
| 12-m365-add-domain.yml        | Manual (workflow_dispatch)      | M365: Add tenant domain (Graph; admin-only) + print verification DNS records                              |

## Deprecated workflows (kept as stubs)

These workflows are **not** needed anymore because the repo moved to:

- safer least-privilege tokens (DNS-only for Cloudflare)
- issue-based change tracking (with optional post-back)
- clearer split between reporting, enforcement, and manual record edits

### Cloudflare Zone Add (removed)

- **Why not needed**: creating a Cloudflare zone generally requires **account-level** permissions
  (and often additional setup like plan/ownership validation). We avoid automating that because it
  increases blast radius and is rarely repeatable in a safe “DNS-only” token.
- **What replaces it**: zone creation is done in the Cloudflare dashboard by an account admin; once
  the zone exists, use **01/02** (preferred) or the **04–07** DNS workflows to manage records and
  apply standards.

If you prefer to run zone creation from Actions (still admin-only), use:

- **03. DNS - Add Domain (Create Zone) (Admin)**

### Legacy Cloudflare DNS update / run

- **What it used to do**: a monolithic “do DNS automation” flow.
- **What replaces it**: use **04** for single-record work, **05** for audit/reporting, **06** for
  applying the standard, **07** for exports — or use **01/02** for the simplified domain flow.

### Legacy DNS summary export

- **What it used to do**: export summaries via old tooling.
- **What replaces it**: **07. DNS - Export All Domains (Report)**.

## Required secrets for admin workflows

- **03. DNS - Add Domain (Create Zone) (Admin)**
  - `CLOUDFLARE_API_KEY_DNS_ONLY`: Cloudflare API token. Must have permissions to create zones (per
    Cloudflare API: at least one of Zone:Edit or Zone:DNS:Edit) and be scoped to allow zone
    creation.
  - `CLOUDFLARE_ACCOUNT_ID`: Cloudflare account id where zones are created
- **08. M365 - Add Tenant Domain (Admin)**
  - Uses the existing `m365-prod` environment secrets (`FFC_AZURE_CLIENT_ID`, `FFC_AZURE_TENANT_ID`)

## Current Workflow

This repository uses an **issue-based workflow** for domain management:

1. **Users** submit requests using GitHub issue templates
2. **Administrators** review requests and execute DNS changes using:
   - PowerShell scripts in this repo
   - Cloudflare API
   - GitHub Actions workflows for exports and automation
3. **Changes are tracked** via GitHub issues for full audit trail

## Required Setup

No additional setup is required for these workflows to run. However, to get the most value:

1. **Enable CodeQL scanning in repository settings:**

   - Go to Settings > Security > Code scanning
   - CodeQL results will appear in the Security tab

2. **Review workflow results:**

   - Check the Actions tab for workflow runs
   - Address any failures before merging PRs

3. **Configure branch protection:**
   - Require status checks to pass before merging
   - Require up-to-date branches before merging

## Best Practices

- Never commit sensitive data like API keys, passwords, or private keys
- Use environment variables or GitHub Secrets for sensitive values
- Review the `.gitignore` file to ensure sensitive files are excluded
- Address security alerts from CodeQL promptly
- Use issue templates for all domain management requests
- Document DNS changes in the corresponding GitHub issue
- Test DNS changes with dry-run mode before applying
