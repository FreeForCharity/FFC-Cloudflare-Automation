# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and support DNS management operations.

## ci.yml - Continuous Integration

Runs automated validation and security checks on all pull requests and pushes to main branch.

### When it runs:
- On pull requests targeting `main` branch
- On pushes to `main` branch

### What it does:

**Validate Repository Job:**
1. Checks out the code
2. Sets up Terraform CLI (v1.6.0) - for legacy file validation
3. Runs Terraform format check (`terraform fmt -check -recursive`) on legacy files
4. Initializes Terraform (if Terraform files exist) - legacy validation
5. Validates Terraform configuration (if Terraform files exist) - legacy validation
6. Scans for accidentally committed sensitive files (*.tfvars, *.pem, *.key, .env)
7. Verifies README.md exists

This workflow ensures that:
- Legacy Terraform code follows proper formatting standards
- Infrastructure configurations remain valid
- No sensitive data is accidentally committed
- Documentation exists

**Note**: Terraform validation is maintained for legacy files but is not the active deployment method.

## codeql-analysis.yml - Security Scanning

Performs automated security analysis using GitHub's CodeQL engine to detect security vulnerabilities in JavaScript/TypeScript code.

### When it runs:
- On pull requests targeting `main` branch
- On pushes to `main` branch
- Scheduled: Every Monday at 6:00 AM UTC

### What it does:
1. Checks out the code
2. Initializes CodeQL for JavaScript/TypeScript analysis
3. Automatically builds the project
4. Performs security analysis
5. Uploads results to GitHub Security tab

### Required Permissions:
- `actions: read` - Read workflow information
- `contents: read` - Read repository contents
- `security-events: write` - Upload security scan results

This workflow helps identify security vulnerabilities early in the development process, including:
- SQL injection vulnerabilities
- Cross-site scripting (XSS)
- Path traversal issues
- Hardcoded credentials
- Use of weak cryptography
- And many other security issues

## Workflow Summary

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| ci.yml | PRs and pushes to main | Validate configurations and check for sensitive files |
| codeql-analysis.yml | PRs, pushes to main, and weekly | Security vulnerability scanning |
| dns-summary-export.yml | Manual (workflow_dispatch) | Export DNS configuration summaries |

## Current Workflow

This repository uses an **issue-based workflow** for domain management:

1. **Users** submit requests using GitHub issue templates
2. **Administrators** review requests and execute DNS changes using:
   - Python scripts (update_dns.py, export_zone_dns_summary.py)
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
- Keep code formatting consistent using `terraform fmt` (for legacy files)
- Address security alerts from CodeQL promptly
- Use issue templates for all domain management requests
- Document DNS changes in the corresponding GitHub issue
- Test DNS changes with dry-run mode before applying
