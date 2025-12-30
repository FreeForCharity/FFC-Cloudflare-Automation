# GitHub Actions Workflows

This repository uses GitHub Actions workflows to ensure code quality, security, and infrastructure validation.

## ci.yml - Continuous Integration

Runs automated validation and security checks on all pull requests and pushes to main branch.

### When it runs:
- On pull requests targeting `main` branch
- On pushes to `main` branch

### What it does:

**Validate Repository Job:**
1. Checks out the code
2. Sets up Terraform CLI (v1.6.0)
3. Runs Terraform format check (`terraform fmt -check -recursive`)
4. Initializes Terraform (if Terraform files exist)
5. Validates Terraform configuration (if Terraform files exist)
6. Scans for accidentally committed sensitive files (*.tfvars, *.pem, *.key, .env)
7. Verifies README.md exists

This workflow ensures that:
- Terraform code follows proper formatting standards
- Infrastructure configurations are valid
- No sensitive data is accidentally committed
- Documentation exists

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
| ci.yml | PRs and pushes to main | Validate Terraform and check for sensitive files |
| codeql-analysis.yml | PRs, pushes to main, and weekly | Security vulnerability scanning |

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
- Use Terraform variables and environment variables for sensitive values
- Review the `.gitignore` file to ensure sensitive files are excluded
- Keep Terraform formatting consistent using `terraform fmt`
- Address security alerts from CodeQL promptly
