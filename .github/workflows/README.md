# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and support DNS
management operations.

To keep the Actions UI list stable and easy to scan, workflows are prefixed with a two-digit number
(e.g., `01.`). CI validates that every active workflow has a numeric prefix and that prefixes are
unique.

## Why some workflows are deprecated

Older workflows (including the legacy “Cloudflare DNS Run” and “Zone Add”) were created before the
repository moved to a safer, issue-based process and the current PowerShell-first automation.

We keep deprecated workflows as reference backups for two reasons:

1. **Stale links**: old docs/bookmarks/runbooks may still point to the legacy workflow file.
2. **Clarity**: the stub explains what replaced it and why.

Deprecated workflows are stored in `.github/workflows-deprecated/` so they **do not** show up in the
Actions UI and cannot be accidentally run.

## Operational workflows (Domain + DNS + M365)

These are the workflows administrators run manually (Actions → Run workflow) to report on or change
domain configuration.

### 01. Domain - Status (All Sources) [CF+M365]

- **Why**: quick read-only report across Cloudflare + M365.
- **When**: use first for any domain request or troubleshooting.
- **How**: run with `domain`, optionally provide `issue_number` to post results back.

### 02. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS]

- **Why**: common onboarding path (Cloudflare zone + optional WHMCS nameserver update).
- **When**: when a new domain should be added and pointed to the correct name servers.
- **How**: run with `domain`; optionally enable WHMCS update.

### 03. Domain - Enforce Standard (GitHub Apex + M365) [CF+M365]

- **Why**: applies the standard DNS configuration and can enable DKIM (when run LIVE).
- **When**: after reviewing status output and confirming the change is desired.
- **How**: run with `domain`; keep `dry_run` enabled until ready to apply changes; optionally set
  `issue_number` to post results back.

### 04. Domain - Export Inventory (All Sources) [CF+M365+WHMCS+WPMUDEV]

- **Why**: exports domain inventories from Cloudflare, M365, WHMCS, and WPMUDEV and produces a
  combined CSV.
- **When**: reconciliation, audits, or before/after large onboarding batches.

### 05–09 DNS workflows

- **05. DNS - Manage Record (Manual / Issue Label) [CF]**: create/update/delete one record (best for
  one-off changes).
  - Also supports an issue-triggered run when the label `run-dns-manage-record` is applied.
- **06. DNS - Enforce Standard (DNS-only) [CF]**: apply standard DNS configuration (DNS-only).
- **07. DNS - Audit Compliance (Report) [CF]**: report-only compliance check.
- **08. DNS - Export Cloudflare Zones (Report) [CF]**: export zone summaries for review/audit.
- **09. DNS - Create Zone (Admin) [CF]**: create a new Cloudflare zone (explicit account selection).

### 20–24 M365 workflows

- **20. M365 - Domain Preflight (Read-only) [M365+CF]**: onboarding checks.
- **21. M365 - List Tenant Domains [M365]**: discovery/listing.
- **22. M365 - Domain Status + DKIM (Toolbox) [M365]**: mixed utilities for domain and DKIM.
- **23. M365 - Enable DKIM (Exchange Online) [M365+CF]**: focused DKIM enable.
- **24. M365 - Add Tenant Domain (Admin) [M365]**: add a domain to the M365 tenant (Graph) and print
  DNS verification records.

### 30–33 WHMCS workflows

- **30. WHMCS - Export Domains (Report) [WHMCS]**: export WHMCS domains for reconciliation.
- **31. WHMCS - Export Products (Report) [WHMCS]**: export WHMCS products.
- **32. WHMCS - Export Payment Methods (Research) [WHMCS]**: export WHMCS payment methods.
- **33. WHMCS -> Zeffy Payments Import (Draft) [WHMCS]**: build a draft import CSV from WHMCS
  transactions.

### 40. WPMUDEV - Export Sites/Domains (Read-only) [WPMUDEV]

- Export hosted sites inventory from WPMUDEV Hub API for domain reconciliation. See
  [docs/wpmudev-domain-inventory.md](../../docs/wpmudev-domain-inventory.md) for details.

### 89–94 Repo workflows

- **89. Repo - Create GitHub Repo [Repo]**: repo bootstrap helper.
- **90. Repo - Deploy GitHub Pages [Repo]**: deploys the site from `main`.
- **91. Repo - CI Validate and Test [Repo]**: workflow linting, formatting checks, and PowerShell
  validation.
- **92. Repo - CodeQL Security Analysis [Repo]**: CodeQL scanning for workflow security.
- **93. Repo - Initialize Labels [Repo]**: initial label creation from `.github/labels.yml`.
- **94. Repo - Sync Labels [Repo]**: keeps labels in sync when `.github/labels.yml` changes.

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

| Workflow                                   | Trigger                                   | Purpose                                                                          |
| ------------------------------------------ | ----------------------------------------- | -------------------------------------------------------------------------------- |
| 0-domain-status.yml                        | Manual (workflow_dispatch)                | 01. Domain: Status (all sources) [CF+M365]                                       |
| 14-domain-add-ffc-cloudflare-and-whmcs.yml | Manual (workflow_dispatch)                | 02. Domain: Add to FFC Cloudflare + WHMCS nameservers (admin) [CF+WHMCS]         |
| 1-enforce-domain-standard.yml              | Manual (workflow_dispatch)                | 03. Domain: Enforce standard (GitHub apex + M365) [CF+M365]                      |
| 4-domain-export-inventory.yml              | Manual (workflow_dispatch)                | 04. Domain: Export inventory (all sources) [CF+M365+WHMCS+WPMUDEV]               |
| 3-manage-record.yml                        | Manual + issue label                      | 05. DNS: Manage a single record (plus label-gated issue trigger) [CF]            |
| 2-enforce-standard.yml                     | Manual (workflow_dispatch)                | 06. DNS: Enforce standard (DNS-only) [CF]                                        |
| 1-audit-compliance.yml                     | Manual (workflow_dispatch)                | 07. DNS: Audit compliance (report-only) [CF]                                     |
| 4-export-summary.yml                       | Manual (workflow_dispatch)                | 08. DNS: Export Cloudflare zones [CF]                                            |
| 11-cloudflare-zone-create.yml              | Manual (workflow_dispatch)                | 09. DNS: Create zone (explicit account selection) [CF]                           |
| 15-website-provision.yml                   | Issue assigned                            | 15. Website: Provision (DNS + repo + content) [CF+Repo]                          |
| 7-m365-domain-preflight.yml                | Manual (workflow_dispatch)                | 20. M365: Domain preflight (Graph + Cloudflare audit)                            |
| 6-m365-list-domains.yml                    | Manual (workflow_dispatch)                | 21. M365: List tenant domains                                                    |
| 5-m365-domain-and-dkim.yml                 | Manual (workflow_dispatch)                | 22. M365: Domain status + DKIM helpers                                           |
| 8-m365-dkim-enable.yml                     | Manual (workflow_dispatch)                | 23. M365: Enable DKIM (Exchange Online)                                          |
| 12-m365-add-tenant-domain.yml              | Manual (workflow_dispatch)                | 24. M365: Add tenant domain and print verification DNS records                   |
| 7-whmcs-export-domains.yml                 | Manual (workflow_dispatch)                | 30. WHMCS: Export domains                                                        |
| 8-whmcs-export-products.yml                | Manual (workflow_dispatch)                | 31. WHMCS: Export products                                                       |
| 9-whmcs-export-payment-methods.yml         | Manual (workflow_dispatch)                | 32. WHMCS: Export payment methods                                                |
| 10-whmcs-zeffy-payments-import-draft.yml   | Manual (workflow_dispatch)                | 33. WHMCS -> Zeffy: Build draft import CSV                                       |
| 13-wpmudev-export-sites.yml                | Manual (workflow_dispatch)                | 40. WPMUDEV: Export sites/domains for reconciliation                             |
| create-repo.yml                            | Manual (workflow_dispatch)                | 89. Repo: Create GitHub repo                                                     |
| deploy-pages.yml                           | Pushes to `main` + manual                 | 90. Repo: Deploy GitHub Pages                                                    |
| ci.yml                                     | PRs and pushes to `main`                  | 91. Repo: Lint workflows, validate scripts, check formatting and sensitive files |
| codeql-analysis.yml                        | PRs, pushes to `main`, weekly, and manual | 92. Repo: CodeQL scanning                                                        |
| initialize-labels.yml                      | Manual (workflow_dispatch)                | 93. Repo: Initialize labels from `.github/labels.yml`                            |
| sync-labels.yml                            | Push to `main` (labels.yml) + manual      | 94. Repo: Sync labels when `.github/labels.yml` changes                          |

## 15-website-provision.yml - Website provisioning (DNS + repo + content)

Provisions a charity website end-to-end after a website request issue is assigned.

### When it runs

- Trigger: `issues.assigned`
- Gate: issue title starts with `[WEBSITE REQUEST]` **or** the issue has the `website-request`
  label.

### What it does

1. **Parse issue** body sections written by the issue form.
2. **Cloudflare source-of-truth check** in `cloudflare-prod` to determine whether the domain is in
   FFC-controlled Cloudflare (FFC/CM accounts).
3. **Comment start** on the issue (includes run URL + target repo + Cloudflare check result).
4. **DNS enforcement (conditional)** in `cloudflare-prod` (only when the Cloudflare check indicates
   the zone is in FFC-controlled Cloudflare):

- Runs `Update-CloudflareDns.ps1 -Zone <domain> -EnforceStandard -GitHubPagesOnly`
- Uploads enforcement + audit outputs as artifacts.

5. **Repo provisioning** in `github-prod`:

- Creates a new repo from the configured template and enables GitHub Pages.
- If the zone is controlled in Cloudflare, sets the Pages custom domain (`CNAME`) to the apex
  domain.
- Adds the issue requester and Technical POC GitHub username as repo maintainers.

6. **Content application** in `github-prod`:
   - Clones the new repo.
   - Writes `ffc-content.json` (audit/traceability record).
   - Runs `scripts/Apply-WebsiteReactTemplate.ps1` to patch the React template:
     - Footer component content
     - Leadership/team section via JSON (`src/data/team/*.json` + `src/data/team.ts`)
   - Commits + pushes to the new repo’s `main` branch.
7. **Comment completion** with a marker for idempotency.

### Idempotency

The workflow writes a completion marker comment:

- `<!-- website-provision:completed -->`

If the marker is already present, the workflow skips provisioning.

### Required environments / secrets

- Environment: `cloudflare-prod` (required only when DNS is controlled in Cloudflare)
  - `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
  - `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
- Environment: `github-prod`
  - `CBM_TOKEN`

### Optional repository variables

- `FFC_WEBSITE_TARGET_ORG` (default: `FreeForCharity`)
- `FFC_WEBSITE_TEMPLATE_REPO` (default: `FreeForCharity/FFC_Single_Page_Template`)

## Deprecated workflows (backups only)

These workflows are **not** needed anymore because the repo moved to:

- safer least-privilege tokens (DNS-only for Cloudflare)
- issue-based change tracking (with optional post-back)
- clearer split between reporting, enforcement, and manual record edits

### Cloudflare Zone Add (removed)

- **Why removed**: the legacy workflow required a separate secret and had unclear safety guardrails.
- **What replaces it**: use **09. DNS - Create Zone (Admin) [CF]**.
  - It requires explicit account selection (FFC/CM) to reduce accidental duplicates.
  - It refuses to create a zone if the domain already exists in the other account.
  - It uses the `cloudflare-prod` environment secrets.

## Admin workflow test notes

These workflows are higher-blast-radius and should be tested with a domain you control.

### 09. DNS - Create Zone (Admin) [CF]

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

### 24. M365 - Add Tenant Domain (Admin) [M365]

- Ensure the `m365-prod` environment has:
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
- Run the workflow with `domain` set to a safe test domain.
- Confirm output includes `verificationDnsRecords` and `serviceConfigurationRecords`.

### 02. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS]

- This is the highest-blast-radius workflow (Cloudflare + WHMCS).
- Prefer testing with a domain you control; keep `enforce_dry_run=false` unless you explicitly want
  enforcement changes.

### Legacy Cloudflare DNS update / run

- **What it used to do**: a monolithic “do DNS automation” flow.
- **What replaces it**: use **05** for single-record work, **07** for audit/reporting, **06** for
  applying the standard, **08** for exports — or use **01–04** for the simplified domain flow.

### Legacy DNS summary export

- **What it used to do**: export summaries via old tooling.
- **What replaces it**: **08. DNS - Export Cloudflare Zones (Report) [CF]**.

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
