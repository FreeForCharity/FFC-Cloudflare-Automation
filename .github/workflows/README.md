# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and support DNS
management operations.

To keep the Actions UI list stable and easy to scan, workflows are prefixed with a three-digit
number whose first digit is the system the workflow targets (e.g., `101.` = Cloudflare, `201.` =
WHMCS — see the auto-generated catalog below). CI validates that every active workflow has a
numeric prefix, that prefixes are unique, and that the catalog is regenerated.

**New here?** Start with these two guides:

- [docs/charity-onboarding-lifecycle.md](../../docs/charity-onboarding-lifecycle.md) — the
  end-to-end order for bringing a charity online (domain → DNS/M365 → website → WHMCS → support).
- [docs/workflow-safety-and-approvals.md](../../docs/workflow-safety-and-approvals.md) — which
  workflows write live data, what `dry_run` and the environment approval gates protect, and the
  credential/PII guarantees.

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

### 101. Domain - Status (All Sources) [CF+M365]

- **Why**: quick read-only report across Cloudflare + M365.
- **When**: use first for any domain request or troubleshooting.
- **How**: run with `domain`, optionally provide `issue_number` to post results back.

### 102. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS]

- **Why**: common onboarding path (Cloudflare zone + optional WHMCS nameserver update).
- **When**: when a new domain should be added and pointed to the correct name servers.
- **How**: run with `domain`; optionally enable WHMCS update.

### 103. Domain - Enforce Standard (GitHub Apex + M365) [CF+M365]

- **Why**: applies the standard DNS configuration and can enable DKIM (when run LIVE).
- **When**: after reviewing status output and confirming the change is desired.
- **How**: run with `domain`; keep `dry_run` enabled until ready to apply changes; optionally set
  `issue_number` to post results back.

### 104. Domain - Export Inventory (All Sources) [CF+M365+WHMCS+WPMUDEV]

- **Why**: exports domain inventories from Cloudflare, M365, WHMCS, and WPMUDEV and produces a
  combined CSV.
- **When**: reconciliation, audits, or before/after large onboarding batches.

### 105–111 DNS workflows

- **105. DNS - Manage Record (Manual / Issue Label) [CF]**: create/update/delete one record (best
  for one-off changes).
  - Also supports an issue-triggered run when the label `run-dns-manage-record` is applied.
- **106. DNS - Enforce Standard (DNS-only) [CF]**: apply standard DNS configuration (DNS-only).
- **107. DNS - Audit Compliance (Report) [CF]**: report-only compliance check.
- **108. DNS - Export Cloudflare Zones (Report) [CF]**: export zone summaries for review/audit.
- **110. DNS - Create Zone (Admin) [CF]**: create a new Cloudflare zone (explicit account
  selection).
- **111. DNS - Create Redirect Rule (Admin) [CF]**: configure a 301/302/307/308 Cloudflare Single
  Redirect rule on a source zone, pointing at a target domain. Idempotent — re-running updates the
  same rule. Defaults to `dry_run=true`; flip it off to actually apply, which gates on
  `cloudflare-prod-write`.

### 113–118 Registrar + Domain transfer workflows

These cover buying new domains via Cloudflare Registrar and transferring existing eNOM domains to
Cloudflare Registrar (project #157). See
[docs/domain-transfer-automation.md](../../docs/domain-transfer-automation.md).

- **113. Domain - Registrar Search / Check / Register (Admin, DRAFT) [CF]**: search for available
  names (`mode=search`), check availability/pricing, or register a brand-new domain via the
  Registrar API. Distinct from transfers. Applying the `domain-purchase-approved` label to a
  purchase request issue runs a **check-only** pass and comments the result back; a live purchase is
  gated behind `mode=execute-register` + a typed `confirm_domain`. See
  [docs/cloudflare-domain-registration.md](../../docs/cloudflare-domain-registration.md).
- **114. Domain - Validate Cloudflare Registrar API Access (Read-only) [CF]**: probe whether the
  selected token has Registrar read/write rights. Never charges.
- **115. Domain - Transfer Readiness Preflight (Report) [WHMCS]**: read-only/offline. Exports WHMCS
  domains and classifies each as ready/blocked/review/done for transfer, emitting per-domain
  dashboard runbooks.
- **116. Domain - Transfer EPP/Auth Code Probe (Admin) [WHMCS]**: determines whether a domain's
  EPP/auth code is returned inline (copy-pasteable) or only emailed. `dry-run` has no side effects;
  `execute` calls `DomainRequestEPP`.
- **117. Domain - Post-Transfer Verification (Report) [CF]**: read-only confirmation that a transfer
  landed (registrar = Cloudflare, nameservers on Cloudflare, site reachable).

### 301–305 M365 workflows

- **301. M365 - Domain Preflight (Read-only) [M365+CF]**: onboarding checks.
- **302. M365 - List Tenant Domains [M365]**: discovery/listing.
- **303. M365 - Domain Status + DKIM (Toolbox) [M365]**: mixed utilities for domain and DKIM.
- **304. M365 - Enable DKIM (Exchange Online) [M365+CF]**: focused DKIM enable.
- **305. M365 - Add Tenant Domain (Admin) [M365]**: add a domain to the M365 tenant (Graph) and
  print DNS verification records.

### 201–203, 213 WHMCS export workflows

- **201. WHMCS - Export Domains (Report) [WHMCS]**: export WHMCS domains for reconciliation.
- **202. WHMCS - Export Products (Report) [WHMCS]**: export WHMCS products.
- **203. WHMCS - Export Payment Methods (Research) [WHMCS]**: export WHMCS payment methods.
- **213. WHMCS -> Zeffy Payments Import (Draft) [WHMCS]**: build a draft import CSV from WHMCS
  transactions.

### 204–212 WHMCS support, orders & products

Onboarding / support (34–37) plus the support-triage, orders, and product-catalog automation
(38–43). All run on `windows-latest` under the `whmcs-prod` environment and read credentials from
Key Vault via the `whmcs-secrets-from-kv` action. Write workflows default to `dry_run=true`.

- **204. WHMCS - Charity Onboard [WHMCS]**: AddClient + AddContact + AddOrder from an intake JSON.
- **205. WHMCS - Open Ticket [WHMCS]** / **206. WHMCS - Issue to Ticket [WHMCS]** / **208. WHMCS -
  Export Tickets [WHMCS]**: manual ticket open, issue→ticket (one-way), and ticket export.
- **209. WHMCS - Tickets Triage [WHMCS]**: read-only. Surfaces Open/Customer-Reply tickets into the
  job summary + CSV artifact; can upsert a rolling `whmcs:triage` tracking issue. See
  [docs/whmcs-support-tickets.md](../../docs/whmcs-support-tickets.md).
- **207. WHMCS - Ticket Respond [WHMCS]**: post a templated reply/internal note to one ticket
  (`config/whmcs-ticket-templates.json`); dry-run by default, live reply is human-gated.
- **210. WHMCS - Orders Triage [WHMCS]**: read-only. Summarizes orders by status (Pending/Fraud/
  Active) and lists actionable Pending orders. See
  [docs/whmcs-orders.md](../../docs/whmcs-orders.md).
- **211. WHMCS - Order Update [WHMCS]**: accept/cancel/fraud one order; dry-run by default, no bulk
  automation.
- **212. WHMCS - Product Add [WHMCS]**: create a catalog product from
  `config/whmcs-catalog-products.json`; dry-run by default. See
  [docs/whmcs-product-catalog.md](../../docs/whmcs-product-catalog.md).

### 601. WPMUDEV - Export Sites/Domains (Read-only) [WPMUDEV]

- Export hosted sites inventory from WPMUDEV Hub API for domain reconciliation. See
  [docs/wpmudev-domain-inventory.md](../../docs/wpmudev-domain-inventory.md) for details.

### 401–403 Zeffy - Export [ZEFFY]

Read-only pulls from the Zeffy public API via OIDC → Key Vault (key loaded by
`zeffy-secrets-from-kv`; no key stored in GitHub). **One workflow per endpoint**, all
**dispatch-only** (no schedule) while we evaluate the API, and **none write donor PII** to an
artifact. Complements the one-way WHMCS→Zeffy import (workflow 213). See
[docs/zeffy-api.md](../../docs/zeffy-api.md).

- **401. Zeffy - Campaigns Export [ZEFFY]**: campaigns/events only (`GET /api/v1/campaigns`). This
  endpoint returns no personal data, so artifact `zeffy_campaigns` is inherently safe on this public
  repo.
- **402. Zeffy - Payments Export (PII masked) [ZEFFY]**: payments (`GET /api/v1/payments`) with
  donor PII **masked** (buyer email/name/company and receipt URL blanked). Artifact
  `zeffy_payments`.
- **403. Zeffy - Contacts Export (PII masked) [ZEFFY]**: donors (`GET /api/v1/contacts`) with PII
  **masked** (email/name/phone/address blanked; only the pseudonymous UUID, giving totals/counts,
  and country kept). Artifact `zeffy_contacts`.

The `-IncludePii` switch on the payments/contacts scripts is for local/private runs only; no
workflow passes it.

### 720–730 Repo workflows

- **720. Repo - Create GitHub Repo [Repo]**: repo bootstrap helper.
- **721. Repo - Deploy GitHub Pages [Repo]**: deploys the site from `main`.
- **722. Repo - CI Validate and Test [Repo]**: workflow linting, formatting checks, and PowerShell
  validation.
- **723. Repo - CodeQL Security Analysis [Repo]**: CodeQL scanning for workflow security.
- **724. Repo - Initialize Labels [Repo]**: initial label creation from `.github/labels.yml`.
- **725. Repo - Sync Labels [Repo]**: keeps labels in sync when `.github/labels.yml` changes.
- **729. Repo - Add Collaborator [Repo]**: adds (or updates) a GitHub user as a collaborator on a
  repo at a chosen permission level (`pull`/`triage`/`push`/`maintain`/`admin`). Reusable: run it
  manually (`workflow_dispatch`) or call it from another workflow (`workflow_call`). Runs with
  `secrets.CBM_TOKEN` (environment `github-prod`), so callers need no `actions: write`.

### Deprecated workflow backups

These are kept in `.github/workflows-deprecated/` for historical reference:

- Cloudflare DNS Update (legacy)
- Cloudflare DNS Run (legacy)
- DNS Summary Export (legacy)
- Cloudflare Zone Add (removed)

## 722-ci.yml - Continuous Integration

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

## 723-codeql-analysis.yml - Security Scanning

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

| Workflow                                    | Trigger                                   | Purpose                                                                           |
| ------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------- |
| 101-domain-status.yml                       | Manual (workflow_dispatch)                | 101. Domain: Status (all sources) [CF+M365]                                       |
| 102-domain-add-ffc-cloudflare-and-whmcs.yml | Manual (workflow_dispatch)                | 102. Domain: Add to FFC Cloudflare + WHMCS nameservers (admin) [CF+WHMCS]         |
| 103-enforce-domain-standard.yml             | Manual (workflow_dispatch)                | 103. Domain: Enforce standard (GitHub apex + M365) [CF+M365]                      |
| 104-domain-export-inventory.yml             | Manual (workflow_dispatch)                | 104. Domain: Export inventory (all sources) [CF+M365+WHMCS+WPMUDEV]               |
| 105-manage-record.yml                       | Manual + issue label                      | 105. DNS: Manage a single record (plus label-gated issue trigger) [CF]            |
| 106-enforce-standard.yml                    | Manual (workflow_dispatch)                | 106. DNS: Enforce standard (DNS-only) [CF]                                        |
| 107-audit-compliance.yml                    | Manual (workflow_dispatch)                | 107. DNS: Audit compliance (report-only) [CF]                                     |
| 108-export-summary.yml                      | Manual (workflow_dispatch)                | 108. DNS: Export Cloudflare zones [CF]                                            |
| 110-cloudflare-zone-create.yml              | Manual (workflow_dispatch)                | 110. DNS: Create zone (explicit account selection) [CF]                           |
| 701-website-provision.yml                   | Issue assigned + manual                   | 701. Website: Provision (DNS + repo + content) [CF+Repo]                          |
| 301-m365-domain-preflight.yml               | Manual (workflow_dispatch)                | 301. M365: Domain preflight (Graph + Cloudflare audit)                            |
| 302-m365-list-domains.yml                   | Manual (workflow_dispatch)                | 302. M365: List tenant domains                                                    |
| 303-m365-domain-and-dkim.yml                | Manual (workflow_dispatch)                | 303. M365: Domain status + DKIM helpers                                           |
| 304-m365-dkim-enable.yml                    | Manual (workflow_dispatch)                | 304. M365: Enable DKIM (Exchange Online)                                          |
| 305-m365-add-tenant-domain.yml              | Manual (workflow_dispatch)                | 305. M365: Add tenant domain and print verification DNS records                   |
| 201-whmcs-export-domains.yml                | Manual (workflow_dispatch)                | 201. WHMCS: Export domains                                                        |
| 202-whmcs-export-products.yml               | Manual (workflow_dispatch)                | 202. WHMCS: Export products                                                       |
| 203-whmcs-export-payment-methods.yml        | Manual (workflow_dispatch)                | 203. WHMCS: Export payment methods                                                |
| 213-whmcs-zeffy-payments-import-draft.yml   | Manual (workflow_dispatch)                | 213. WHMCS -> Zeffy: Build draft import CSV                                       |
| 209-whmcs-tickets-triage.yml                | Manual + schedule                         | 209. WHMCS: Tickets triage (read-only)                                            |
| 207-whmcs-ticket-respond.yml                | Manual (workflow_dispatch)                | 207. WHMCS: Ticket respond (templated, dry-run default)                           |
| 210-whmcs-orders-triage.yml                 | Manual + schedule                         | 210. WHMCS: Orders triage (read-only)                                             |
| 211-whmcs-order-update.yml                  | Manual (workflow_dispatch)                | 211. WHMCS: Order update accept/cancel/fraud (dry-run default)                    |
| 212-whmcs-product-add.yml                   | Manual (workflow_dispatch)                | 212. WHMCS: Product add (catalog, dry-run default)                                |
| 601-wpmudev-export-sites.yml                | Manual (workflow_dispatch)                | 601. WPMUDEV: Export sites/domains for reconciliation                             |
| 401-zeffy-campaigns-export.yml              | Manual (workflow_dispatch)                | 401. Zeffy: Campaigns export (read-only, no PII)                                  |
| 402-zeffy-payments-export.yml               | Manual (workflow_dispatch)                | 402. Zeffy: Payments export (read-only, PII masked)                               |
| 403-zeffy-contacts-export.yml               | Manual (workflow_dispatch)                | 403. Zeffy: Contacts export (read-only, PII masked)                               |
| 720-create-repo.yml                         | Manual (workflow_dispatch)                | 720. Repo: Create GitHub repo                                                     |
| 721-deploy-pages.yml                        | Pushes to `main` + manual                 | 721. Repo: Deploy GitHub Pages                                                    |
| 722-ci.yml                                  | PRs and pushes to `main`                  | 722. Repo: Lint workflows, validate scripts, check formatting and sensitive files |
| 723-codeql-analysis.yml                     | PRs, pushes to `main`, weekly, and manual | 723. Repo: CodeQL scanning                                                        |
| 724-initialize-labels.yml                   | Manual (workflow_dispatch)                | 724. Repo: Initialize labels from `.github/labels.yml`                            |
| 725-sync-labels.yml                         | Push to `main` (labels.yml) + manual      | 725. Repo: Sync labels when `.github/labels.yml` changes                          |
| 729-repo-add-collaborator.yml               | Manual + reusable (workflow_call)         | 729. Repo: Add a user as a collaborator at a chosen permission level              |

## 701-website-provision.yml - Website provisioning (DNS + repo + content)

Provisions a charity website end-to-end after a website request issue is assigned.

### When it runs

- Trigger: `issues.assigned`
  - Gate: issue title starts with `[WEBSITE REQUEST]` **or** the issue has the `website-request`
    label.
  - **Admin-minimal mode**: if the issue also carries the `admin-provision` label (use the **07.
    [ADMIN ONLY] Provision Website (Minimal)** issue template), validation mirrors manual dispatch —
    only the domain is required and all charity/footer/leadership/social fields are optional. Footer
    content patching is skipped; the repo (from the FFC template) is still created and apex GitHub
    Pages DNS is still enforced when the zone is controlled in Cloudflare. Use this right after
    registering a domain when you only need the repo + apex DNS.
- Trigger: `workflow_dispatch` (manual)
  - This supports “best-effort” provisioning when only a domain is known.

### What it does

1. **Resolve inputs**

- Issue mode: parses the issue form sections.
- Manual mode: reads `workflow_dispatch` inputs.

2. **Cloudflare source-of-truth check (best-effort)** in `cloudflare-prod`

- Determines whether the domain is in FFC-controlled Cloudflare (FFC/CM accounts).
- If Cloudflare tokens are missing, the workflow treats the domain as **not controlled** and
  continues.

3. **Comment start (issue mode only)**

- Includes run URL + target repo + Cloudflare check result.

4. **DNS enforcement (conditional)** in `cloudflare-prod` (only when the zone is controlled):

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
   - Runs `scripts/Apply-WebsiteReactTemplate.ps1` (best-effort) to patch the React template when
     enough information is provided:
     - Footer component content
     - Leadership/team section via JSON (`src/data/team/*.json` + `src/data/team.ts`)
   - Commits + pushes to the new repo’s `main` branch.

7. **Comment completion (issue mode only)** with a marker for idempotency.

### Manual run inputs (workflow_dispatch)

- Required
  - `domain`
- Optional (best-effort)
  - `charity_name`, `footer_email`, `footer_phone`, `footer_address`, `footer_ein`
  - `website_situation`, `current_website_url`
  - `requester_github_username`, `technical_poc_github_username`
  - `social_links` (one per line: `platform: https://...`)
  - `leadership_lines` (one per line: `Name | Title | LinkedIn URL`)

Manual runs do not post issue comments (there is no issue to attach to).

### Manual run quick examples

Minimal (domain-only) run:

- `domain`: `example.org`

More complete run (also applies the React template footer):

- `domain`: `example.org`
- `charity_name`: `Example Charity`
- `footer_email`: `info@example.org`
- `technical_poc_github_username`: `some-user`

### Best-effort behavior

- Repo creation always runs as long as `domain` is provided.
- If Cloudflare zone is not controlled (or Cloudflare tokens are missing):
  - DNS enforcement is skipped
  - GitHub Pages is enabled without setting a custom domain
- If `charity_name` or `footer_email` is missing:
  - `ffc-content.json` is still written
  - React template patching is skipped
- If requester/POC GitHub usernames are missing:
  - Collaborator addition skips missing values (repo still provisions)

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

### Hardcoded configuration

For simplicity, this workflow currently hardcodes:

- Target org: `FreeForCharity`
- Template repo: `FreeForCharity/FFC_Single_Page_Template`

## Deprecated workflows (backups only)

These workflows are **not** needed anymore because the repo moved to:

- safer least-privilege tokens (DNS-only for Cloudflare)
- issue-based change tracking (with optional post-back)
- clearer split between reporting, enforcement, and manual record edits

### Cloudflare Zone Add (removed)

- **Why removed**: the legacy workflow required a separate secret and had unclear safety guardrails.
- **What replaces it**: use **110. DNS - Create Zone (Admin) [CF]**.
  - It requires explicit account selection (FFC/CM) to reduce accidental duplicates.
  - It refuses to create a zone if the domain already exists in the other account.
  - It uses the `cloudflare-prod` environment secrets.

## Admin workflow test notes

These workflows are higher-blast-radius and should be tested with a domain you control.

### 110. DNS - Create Zone (Admin) [CF]

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

### 305. M365 - Add Tenant Domain (Admin) [M365]

- Ensure the `m365-prod` environment has:
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
- Run the workflow with `domain` set to a safe test domain.
- Confirm output includes `verificationDnsRecords` and `serviceConfigurationRecords`.

### 102. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS]

- This is the highest-blast-radius workflow (Cloudflare + WHMCS).
- Prefer testing with a domain you control; keep `enforce_dry_run=false` unless you explicitly want
  enforcement changes.

### Legacy Cloudflare DNS update / run

- **What it used to do**: a monolithic “do DNS automation” flow.
- **What replaces it**: use **05** for single-record work, **07** for audit/reporting, **06** for
  applying the standard, **08** for exports — or use **01–04** for the simplified domain flow.

### Legacy DNS summary export

- **What it used to do**: export summaries via old tooling.
- **What replaces it**: **108. DNS - Export Cloudflare Zones (Report) [CF]**.

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

<!-- catalog:begin -->

## Complete workflow catalog (auto-generated)

> Regenerate with `python3 scripts/generate-workflow-catalog.py` — do not hand-edit
> this section. Machine-readable version: `docs/workflow-catalog.json`.

### 1xx — Cloudflare / DNS / Domain

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 101 | Domain - Status (All Sources) [CF+M365] | `101-domain-status.yml` | workflow_dispatch | Reads | cloudflare-prod-read / ✅ m365-prod |
| 102 | Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS] | `102-domain-add-ffc-cloudflare-and-whmcs.yml` | workflow_dispatch | Writes (gated) | ✅ cloudflare-prod-write / ✅ whmcs-prod |
| 103 | Domain - Enforce Standard (GitHub Apex + M365) [CF+M365] | `103-enforce-domain-standard.yml` | workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write / ✅ m365-prod |
| 104 | Domain - Export Inventory (All Sources) [CF+M365+WHMCS+WPMUDEV] | `104-domain-export-inventory.yml` | workflow_dispatch | Reads | ✅ whmcs-prod / ✅ m365-prod / ✅ wpmudev-prod (+ cf-read) |
| 105 | DNS - Manage Record (Manual / Issue Label) [CF] | `105-manage-record.yml` | issues, workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write |
| 106 | DNS - Enforce Standard (DNS-only) [CF] | `106-enforce-standard.yml` | workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write |
| 107 | DNS - Audit Compliance (Report) [CF] | `107-audit-compliance.yml` | workflow_dispatch | Reads | cloudflare-prod-read |
| 108 | DNS - Export Cloudflare Zones (Report) [CF] | `108-export-summary.yml` | workflow_dispatch | Reads | cloudflare-prod-read |
| 109 | DNS - Export All Records (Full, per-record) [CF] | `109-dns-export-all-records.yml` | workflow_dispatch | Reads | cloudflare-prod-read |
| 110 | DNS - Create Zone (Admin) [CF] | `110-cloudflare-zone-create.yml` | workflow_dispatch | Writes (gated) | ✅ cloudflare-prod-write |
| 111 | DNS - Create Redirect Rule (Admin) [CF] | `111-dns-create-redirect-rule.yml` | workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write |
| 112 | DNS - Bulk Replace A-record IP (All Zones) [CF] | `112-dns-bulk-replace-a-ip.yml` | workflow_dispatch | Writes (gated) | ✅ cloudflare-prod-write |
| 113 | Domain - Registrar Search / Check / Register (Admin, DRAFT) [CF] | `113-cloudflare-domain-register.yml` | issues, workflow_dispatch | Writes (gated) | ✅ cloudflare-prod-write |
| 114 | Domain - Validate Cloudflare Registrar API Access (Read-only) [CF] | `114-cloudflare-registrar-access-check.yml` | workflow_dispatch | Reads | ✅ cloudflare-prod-write |
| 115 | Domain - Transfer Readiness Preflight (Report) [WHMCS] | `115-domain-transfer-preflight.yml` | workflow_dispatch | Reads | ✅ whmcs-prod |
| 116 | Domain - Transfer EPP/Auth Code Probe (Admin) [WHMCS] | `116-domain-transfer-epp-probe.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 117 | Domain - Post-Transfer Verification (Report) [CF] | `117-domain-transfer-verify.yml` | workflow_dispatch | Reads | cloudflare-prod-read |
| 118 | Domain - Registrar Lock / Unlock [WHMCS] | `118-whmcs-domain-lock.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 119 | DNS - Bulk Staging CNAME -> GitHub Pages (FFC-EX) [CF] | `119-bulk-staging-cname-github-pages.yml` | workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write |
| 120 | DNS + GH Pages - Bulk Cutover staging -> Apex (FFC-EX) [CF+GH] | `120-bulk-cutover-to-github-pages.yml` | workflow_dispatch | Writes (dry-run default) | ✅ cloudflare-prod-write / ✅ github-prod |
### 2xx — WHMCS

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 201 | WHMCS - Export Domains (Report) [WHMCS] | `201-whmcs-export-domains.yml` | workflow_dispatch | Reads | ✅ whmcs-prod |
| 202 | WHMCS - Export Products (Report) [WHMCS] | `202-whmcs-export-products.yml` | workflow_dispatch | Reads | ✅ whmcs-prod |
| 203 | WHMCS - Export Payment Methods (Research) [WHMCS] | `203-whmcs-export-payment-methods.yml` | workflow_dispatch | Reads | ✅ whmcs-prod |
| 204 | WHMCS - Charity Onboard (client + contacts + order) [WHMCS] | `204-whmcs-charity-onboard.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 205 | WHMCS - Open Ticket (manual) [WHMCS] | `205-whmcs-ticket-open.yml` | workflow_dispatch | Writes (gated) | ✅ whmcs-prod |
| 206 | WHMCS - Issue to Ticket (one-way) [WHMCS] | `206-whmcs-issue-to-ticket.yml` | issues | Writes (gated) | ✅ whmcs-prod |
| 207 | WHMCS - Ticket Respond (templated) [WHMCS] | `207-whmcs-ticket-respond.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 208 | WHMCS - Export Tickets (Report) [WHMCS] | `208-whmcs-tickets-export.yml` | workflow_dispatch | Reads | ✅ whmcs-prod |
| 209 | WHMCS - Tickets Triage (Open/Customer-Reply) [WHMCS] | `209-whmcs-tickets-triage.yml` | schedule, workflow_dispatch | Reads | ✅ whmcs-prod |
| 210 | WHMCS - Orders Triage (Pending/Fraud/Active) [WHMCS] | `210-whmcs-orders-triage.yml` | schedule, workflow_dispatch | Reads | ✅ whmcs-prod |
| 211 | WHMCS - Order Update (accept/cancel/fraud) [WHMCS] | `211-whmcs-order-update.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 212 | WHMCS - Product Add (catalog) [WHMCS] | `212-whmcs-product-add.yml` | workflow_dispatch | Writes (dry-run default) | ✅ whmcs-prod |
| 213 | WHMCS -> Zeffy Payments Import (Draft) [WHMCS] | `213-whmcs-zeffy-payments-import-draft.yml` | workflow_dispatch | Reads (builds a file) | ✅ whmcs-prod |
### 3xx — Microsoft (M365 / Azure / Graph)

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 301 | M365 - Domain Preflight (Read-only) [M365+CF] | `301-m365-domain-preflight.yml` | workflow_dispatch | Reads | cloudflare-prod-read / ✅ m365-prod |
| 302 | M365 - List Tenant Domains [M365] | `302-m365-list-domains.yml` | workflow_dispatch | Reads | ✅ m365-prod |
| 303 | M365 - Domain Status + DKIM (Toolbox) [M365] | `303-m365-domain-and-dkim.yml` | workflow_dispatch | Reads | ✅ m365-prod |
| 304 | M365 - Enable DKIM (Exchange Online) [M365+CF] | `304-m365-dkim-enable.yml` | workflow_dispatch | Writes (gated) | ✅ cloudflare-prod-write / ✅ m365-prod |
| 305 | M365 - Add Tenant Domain (Admin) [M365] | `305-m365-add-tenant-domain.yml` | workflow_dispatch | Writes (dry-run default) | ✅ m365-prod |
| 306 | Discover - Uncaptured Comms (M365, PII masked) [M365] | `306-discover-uncaptured-comms.yml` | workflow_dispatch | Reads | ✅ m365-prod |
### 4xx — Zeffy

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 401 | Zeffy - Campaigns Export [ZEFFY] | `401-zeffy-campaigns-export.yml` | workflow_dispatch | Reads | zeffy-prod |
| 402 | Zeffy - Payments Export (PII masked) [ZEFFY] | `402-zeffy-payments-export.yml` | workflow_dispatch | Reads | zeffy-prod |
| 403 | Zeffy - Contacts Export (PII masked) [ZEFFY] | `403-zeffy-contacts-export.yml` | workflow_dispatch | Reads | zeffy-prod |
### 5xx — Google

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 501 | Google - API Smoke (GA4 connectivity) [GOOGLE] | `501-google-api-smoke.yml` | workflow_call, workflow_dispatch | Reads | google-prod-read |
| 502 | Google - Analytics Report (GA4 -> JSON) [GOOGLE] | `502-google-analytics-report.yml` | schedule, workflow_dispatch | Reads | google-prod-read |
| 503 | Google - GTM Provision (per-charity container) [GOOGLE] | `503-google-gtm-provision.yml` | workflow_dispatch | Writes (dry-run default) | ✅ google-prod-write |
### 6xx — WPMUDEV

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 601 | WPMUDEV - Export Sites/Domains (Read-only) [WPMUDEV] | `601-wpmudev-export-sites.yml` | workflow_dispatch | Reads | ✅ wpmudev-prod |
### 7xx — GitHub (Website + Repo)

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 701 | Website - Provision (Issue Assigned) [CF+Repo] | `701-website-provision.yml` | issues | Writes (gated) | ✅ cloudflare-prod-write / ✅ github-prod |
| 702 | Domain - Deploy Static Clone to FFC-EX Repo | `702-ffc-ex-clone-deploy.yml` | workflow_dispatch | Writes (gated) | ✅ github-prod |
| 703 | Sites List - Generate (CSV + JSON) [GH] | `703-sites-list-generate.yml` | schedule, workflow_dispatch | (repo plumbing) | — |
| 720 | Repo - Create GitHub Repo [Repo] | `720-create-repo.yml` | workflow_dispatch | (repo plumbing) | — |
| 721 | Repo - Deploy GitHub Pages [Repo] | `721-deploy-pages.yml` | push, workflow_dispatch | (repo plumbing) | — |
| 722 | Repo - CI Validate and Test [Repo] | `722-ci.yml` | merge_group, pull_request, push | (repo plumbing) | — |
| 723 | Repo - CodeQL Security Analysis [Repo] | `723-codeql-analysis.yml` | merge_group, pull_request, push, schedule, workflow_dispatch | (repo plumbing) | — |
| 724 | Repo - Initialize Labels [Repo] | `724-initialize-labels.yml` | workflow_dispatch | (repo plumbing) | — |
| 725 | Repo - Sync Labels [Repo] | `725-sync-labels.yml` | push, workflow_dispatch | (repo plumbing) | — |
| 726 | Repo - Rulesets + Settings Drift Audit [Org] | `726-repo-rulesets-drift-audit.yml` | schedule, workflow_dispatch | Reads | — |
| 727 | Repo - Phantom Revert Guard [Repo] | `727-phantom-revert-guard.yml` | merge_group, pull_request, workflow_dispatch | (repo plumbing) | — |
| 728 | Repo - AI Agent Hooks Validate [Repo] | `728-ai-agent-hooks-validate.yml` | pull_request, push | (repo plumbing) | — |
| 729 | Repo - Add Collaborator [Repo] | `729-repo-add-collaborator.yml` | workflow_dispatch | Writes (**live default**) | ✅ github-prod |
| 730 | Repo - Audit Environment Approval Gates [Repo] | `730-repo-audit-environment-gates.yml` | push, workflow_dispatch | Reads | — |
### 8xx — Reserved

| # | Workflow | File | Triggers | Safety | Approval env |
| --- | --- | --- | --- | --- | --- |
| 801 | Candid - Charity Check (EIN) [CANDID] | `801-candid-charity-check.yml` | workflow_dispatch | Reads | candid-prod-read |
| 802 | Candid - Essentials Search [CANDID] | `802-candid-essentials-search.yml` | workflow_dispatch | Reads | candid-prod-read |

<!-- catalog:end -->
