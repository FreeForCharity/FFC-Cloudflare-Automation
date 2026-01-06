# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and support DNS
management operations.

## Why some workflows are deprecated

Older workflows (including the legacy “Cloudflare DNS Run” and “Zone Add”) were created before the
repository moved to a safer, issue-based process and the current PowerShell-first automation.

We keep deprecated workflows as reference backups for two reasons:

1. **Stale links**: old docs/bookmarks/runbooks may still point to the legacy workflow file.
2. **Clarity**: the stub explains what replaced it and why.

Deprecated workflows are stored in `.github/workflows-deprecated/` so they **do not** show up in the
Actions UI and cannot be accidentally run.

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

### 03–06 DNS workflows

- **03. DNS - Manage Record (Manual / Issue Label)**: create/update/delete one record (best for
  one-off changes).
  - Also supports an issue-triggered run when the label `run-dns-manage-record` is applied.
- **04. DNS - Audit Compliance (Report)**: report-only compliance check.
- **05. DNS - Enforce Standard (Fix)**: apply standard DNS configuration (DNS-only).
- **06. DNS - Export All Domains (Report)**: export summaries for review/audit.

### 20–24 M365 workflows

- **20. M365 - List Tenant Domains**: discovery/listing.
- **21. M365 - Domain Preflight (Read-only)**: onboarding checks.
- **22. M365 - Domain Status + DKIM (Toolbox)**: mixed utilities for domain and DKIM.
- **23. M365 - Enable DKIM (Exchange Online)**: focused DKIM enable.
- **24. M365 - Add Tenant Domain (Admin)**: add a domain to the M365 tenant (Graph) and print DNS
  verification records.

### 30–33 WHMCS workflows

- **30. WHMCS - Export Domains (Report)**: export WHMCS domains for reconciliation.
- **31. WHMCS - Export Products (Report)**: export WHMCS products.
- **32. WHMCS - Export Payment Methods (Research)**: export WHMCS payment methods.
- **33. WHMCS -> Zeffy Payments Import (Draft)**: build a draft import CSV from WHMCS transactions.

### Admin and inventory workflows

- **11. DNS - Add Domain (Create Zone) (Admin)**: create a new Cloudflare zone (explicit account
  selection)
- **14. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin)**: create the zone, enforce the
  baseline, and optionally update WHMCS nameservers.
- **40. WPMUDEV - Export Sites/Domains (Read-only)**: export hosted sites inventory from WPMUDEV Hub
  API for domain reconciliation. See
  [docs/wpmudev-domain-inventory.md](../../docs/wpmudev-domain-inventory.md) for details.

### 89–94 Repo workflows

- **89. Repo - Create GitHub Repo**: repo bootstrap helper.
- **90. Repo - Deploy GitHub Pages**: deploys the site from `main`.
- **91. Repo - CI Validate and Test**: workflow linting, formatting checks, and PowerShell
  validation.
- **92. Repo - CodeQL Security Analysis**: CodeQL scanning for workflow security.
- **93. Repo - Initialize Labels**: initial label creation from `.github/labels.yml`.
- **94. Repo - Sync Labels**: keeps labels in sync when `.github/labels.yml` changes.

### Deprecated workflow backups

These are kept in `.github/workflows-deprecated/` for historical reference:

- Cloudflare DNS Update (legacy)
- Cloudflare DNS Run (legacy)
- DNS Summary Export (legacy)
- Cloudflare Zone Add (removed)

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

| Workflow                                   | Trigger                                   | Purpose                                                                                        |
| ------------------------------------------ | ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 0-domain-status.yml                        | Manual (workflow_dispatch)                | 01. Domain: Status check (Cloudflare + M365)                                                   |
| 1-enforce-domain-standard.yml              | Manual (workflow_dispatch)                | 02. Domain: Enforce standard (Cloudflare + M365; supports issue post-back)                     |
| 1-audit-compliance.yml                     | Manual (workflow_dispatch)                | 04. DNS: Audit compliance (report-only)                                                        |
| 2-enforce-standard.yml                     | Manual (workflow_dispatch)                | 05. DNS: Enforce standard (DNS-only)                                                           |
| 3-manage-record.yml                        | Manual + issue label                      | 03. DNS: Manage a single record (plus label-gated issue trigger)                               |
| 4-export-summary.yml                       | Manual (workflow_dispatch)                | 06. DNS: Export all domains summary                                                            |
| 11-cloudflare-zone-create.yml              | Manual (workflow_dispatch)                | 11. DNS: Create a new Cloudflare zone (explicit account selection)                             |
| 14-domain-add-ffc-cloudflare-and-whmcs.yml | Manual (workflow_dispatch)                | 14. Admin: Add domain to FFC Cloudflare, enforce baseline, optionally update WHMCS nameservers |
| 5-m365-domain-and-dkim.yml                 | Manual (workflow_dispatch)                | 22. M365: Domain status + DKIM helpers                                                         |
| 6-m365-list-domains.yml                    | Manual (workflow_dispatch)                | 20. M365: List tenant domains                                                                  |
| 7-m365-domain-preflight.yml                | Manual (workflow_dispatch)                | 21. M365: Domain onboarding preflight (Graph + Cloudflare audit)                               |
| 8-m365-dkim-enable.yml                     | Manual (workflow_dispatch)                | 23. M365: Enable DKIM (Exchange Online)                                                        |
| 12-m365-add-tenant-domain.yml              | Manual (workflow_dispatch)                | 24. M365: Add tenant domain and print verification DNS records                                 |
| 7-whmcs-export-domains.yml                 | Manual (workflow_dispatch)                | 30. WHMCS: Export domains                                                                      |
| 8-whmcs-export-products.yml                | Manual (workflow_dispatch)                | 31. WHMCS: Export products                                                                     |
| 9-whmcs-export-payment-methods.yml         | Manual (workflow_dispatch)                | 32. WHMCS: Export payment methods                                                              |
| 10-whmcs-zeffy-payments-import-draft.yml   | Manual (workflow_dispatch)                | 33. WHMCS -> Zeffy: Build draft import CSV                                                     |
| 13-wpmudev-export-sites.yml                | Manual (workflow_dispatch)                | 40. WPMUDEV: Export sites/domains for reconciliation                                           |
| create-repo.yml                            | Manual (workflow_dispatch)                | 89. Repo: Create GitHub repo                                                                   |
| deploy-pages.yml                           | Pushes to `main` + manual                 | 90. Repo: Deploy GitHub Pages                                                                  |
| ci.yml                                     | PRs and pushes to `main`                  | 91. Repo: Lint workflows, validate scripts, check formatting and sensitive files               |
| codeql-analysis.yml                        | PRs, pushes to `main`, weekly, and manual | 92. Repo: CodeQL scanning                                                                      |
| initialize-labels.yml                      | Manual (workflow_dispatch)                | 93. Repo: Initialize labels from `.github/labels.yml`                                          |
| sync-labels.yml                            | Push to `main` (labels.yml) + manual      | 94. Repo: Sync labels when `.github/labels.yml` changes                                        |

## Deprecated workflows (backups only)

These workflows are **not** needed anymore because the repo moved to:

- safer least-privilege tokens (DNS-only for Cloudflare)
- issue-based change tracking (with optional post-back)
- clearer split between reporting, enforcement, and manual record edits

### Cloudflare Zone Add (removed)

- **Why removed**: the legacy workflow required a separate secret and had unclear safety guardrails.
- **What replaces it**: use **11. DNS - Add Domain (Create Zone) (Admin)**.
  - It requires explicit account selection (FFC/CM) to reduce accidental duplicates.
  - It refuses to create a zone if the domain already exists in the other account.
  - It uses the `cloudflare-prod` environment secrets.

## Admin workflow test notes

These workflows are higher-blast-radius and should be tested with a domain you control.

### 11. DNS - Add Domain (Create Zone) (Admin)

- Ensure the `cloudflare-prod` environment has both secrets:
  - `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
  - `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
- Run the workflow with:
  - `domain`: a safe test domain
  - `account`: the intended owning account
  - `zone_type`: usually `full`
- Confirm output includes:
  - Zone ID
  - Assigned name servers

### 24. M365 - Add Tenant Domain (Admin)

- Ensure the `m365-prod` environment has:
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
- Run the workflow with `domain` set to a safe test domain.
- Confirm output includes `verificationDnsRecords` and `serviceConfigurationRecords`.

### 14. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin)

- This is the highest-blast-radius workflow (Cloudflare + WHMCS).
- Prefer testing with a domain you control; keep `enforce_dry_run=false` unless you explicitly want
  enforcement changes.

### Legacy Cloudflare DNS update / run

- **What it used to do**: a monolithic “do DNS automation” flow.
- **What replaces it**: use **03** for single-record work, **04** for audit/reporting, **05** for
  applying the standard, **06** for exports — or use **01/02** for the simplified domain flow.

### Legacy DNS summary export

- **What it used to do**: export summaries via old tooling.
- **What replaces it**: **06. DNS - Export All Domains (Report)**.

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
