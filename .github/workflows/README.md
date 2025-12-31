# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality and support DNS management operations.

## CI/CD Workflows

### ci.yml - Continuous Integration

Runs automated validation and security checks on all pull requests and pushes to main branch.

**When it runs:**
- On pull requests targeting `main` branch
- On pushes to `main` branch

**What it does:**
1. Checks out the code
2. Validates PowerShell scripts for syntax errors
3. Scans for accidentally committed sensitive files (*.pem, *.key, .env)
4. Verifies README.md exists

This workflow ensures that:
- PowerShell scripts are syntactically correct
- No sensitive data is accidentally committed
- Documentation exists

### deploy-pages.yml - GitHub Pages Deployment

Deploys the repository content to GitHub Pages for documentation and tracking.

**When it runs:**
- On pushes to `main` branch
- Manual trigger (workflow_dispatch)

**What it does:**
1. Checks out the code
2. Configures GitHub Pages
3. Uploads the site artifact
4. Deploys to GitHub Pages

## DNS Management Workflows

### 1-audit-compliance.yml - DNS Compliance Report

Checks DNS configuration compliance for a specified domain.

**When it runs:** Manual trigger (workflow_dispatch)

**Inputs:**
- `domain` (required): Domain name to audit (e.g., example.org)

**What it does:**
Runs DNS compliance audit using the PowerShell script to verify domain configuration against FFC standards.

### 2-enforce-standard.yml - DNS Standard Enforcement

Enforces FFC standard DNS configuration on a domain.

**When it runs:** Manual trigger (workflow_dispatch)

**Inputs:**
- `domain` (required): Domain name to enforce standards on
- `dry_run` (optional, default: true): Preview changes before applying

**What it does:**
1. Enforces standard DNS configuration (dry-run or live mode)
2. Runs post-enforcement compliance audit

### 3-manage-record.yml - Manual DNS Record Management

Manages individual DNS records for a domain.

**When it runs:**
- Manual trigger (workflow_dispatch)
- Issue events (opened, edited, labeled)

**Inputs:**
- `domain` (required): Domain name (zone)
- `dry_run` (optional, default: true): No changes mode
- `record_type` (optional, default: TXT): Record type (TXT, A, CNAME, MX)
- `record_name` (optional, default: @): Record name or subdomain
- `record_content` (optional): Record content value

**What it does:**
1. Resolves input context (manual or issue-based)
2. Validates zone exists in Cloudflare
3. Manages DNS record (if not dry-run)
4. Runs post-change compliance audit

### 4-export-summary.yml - Export Domain Summary

Exports DNS configuration summary for all domains.

**When it runs:** Manual trigger (workflow_dispatch)

**What it does:**
1. Runs DNS export script
2. Generates CSV summary of all domains
3. Uploads CSV as workflow artifact

## Workflow Summary

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| ci.yml | PRs and pushes to main | Validate PowerShell scripts and check for sensitive files |
| deploy-pages.yml | Pushes to main, manual | Deploy GitHub Pages site |
| 1-audit-compliance.yml | Manual (workflow_dispatch) | Report: Check DNS compliance |
| 2-enforce-standard.yml | Manual (workflow_dispatch) | Fix: Enforce standard DNS configuration |
| 3-manage-record.yml | Manual (workflow_dispatch) | Manual: Manage a single DNS record |
| 4-export-summary.yml | Manual (workflow_dispatch) | Report: Export all domains summary |

## Current Workflow

This repository uses an **issue-based workflow** for domain management:

1. **Users** submit requests using GitHub issue templates
2. **Administrators** review requests and execute DNS changes using:
   - PowerShell scripts (Update-CloudflareDns.ps1, Export-CloudflareDns.ps1)
   - Cloudflare API
   - GitHub Actions workflows for automation and auditing
3. **Changes are tracked** via GitHub issues for full audit trail

## Required Setup

The workflows require the following secrets to be configured:

1. **CLOUDFLARE_API_KEY_DNS_ONLY** - DNS edit permissions for managing records
2. **CLOUDFLARE_API_KEY_READ_ALL** (optional) - Read-only access for exports

Repository variables:
- **ALLOWED_ACTORS** (optional) - Comma-separated list of GitHub usernames allowed to run workflows

### Getting the Most Value

1. **Review workflow results:**
   - Check the Actions tab for workflow runs
   - Address any failures before merging PRs

2. **Configure branch protection:**
   - Require status checks to pass before merging
   - Require up-to-date branches before merging

3. **Use environment protection:**
   - Configure the `cloudflare-prod` environment with required reviewers
   - Restrict who can approve DNS changes

## Best Practices

- Never commit sensitive data like API keys, passwords, or private keys
- Use environment variables or GitHub Secrets for sensitive values
- Review the `.gitignore` file to ensure sensitive files are excluded
- Use issue templates for all domain management requests
- Document DNS changes in the corresponding GitHub issue
- Test DNS changes with dry-run mode before applying (enabled by default)
- Review compliance audits after making changes
