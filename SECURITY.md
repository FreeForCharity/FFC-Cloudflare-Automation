# Security Policy

## Automated Security Scanning

This repository includes automated security scanning for Infrastructure-as-Code (Terraform) using multiple industry-standard tools:

### Security Scanning Tools

| Tool | Purpose | Runs On |
|------|---------|---------|
| **tfsec** | Static analysis for Terraform security issues | All PRs and pushes |
| **Checkov** | Policy-as-code scanner for IaC misconfigurations | All PRs and pushes |
| **Trivy** | Vulnerability scanner for IaC and container images | All PRs and pushes |
| **Terraform Validate** | Built-in validation for Terraform syntax and logic | All PRs and pushes |

### Security Workflow

The security scanning workflow (`.github/workflows/terraform-security.yml`) automatically runs on:
- Every push to `main` or `master` branch
- Every pull request targeting `main` or `master` branch
- Manual workflow dispatch

**Results** are uploaded to GitHub Security tab under Code Scanning alerts, where they can be:
- Reviewed and triaged
- Tracked over time
- Integrated with branch protection rules

### Running Security Scans Locally

You can run the same security checks locally before pushing:

#### tfsec
```bash
# Install
brew install tfsec  # macOS
# or download from https://github.com/aquasecurity/tfsec/releases

# Run
tfsec . --minimum-severity MEDIUM
```

#### Checkov
```bash
# Install
pip install checkov

# Run
checkov -d . --framework terraform
```

#### Trivy
```bash
# Install
brew install trivy  # macOS
# or download from https://github.com/aquasecurity/trivy/releases

# Run
trivy config .
```

#### Terraform Validate
```bash
terraform init -backend=false
terraform validate
```

---

## Sensitive Information Handling

### CloudFlare API Tokens

**⚠️ CRITICAL: Never commit API tokens to version control**

## Two Secure Storage Methods

This repository supports **two secure methods** for storing the CloudFlare API token:

### Method 1: Local terraform.tfvars (For Individual Development)

The CloudFlare API token is stored in the `terraform.tfvars` file, which is:
- ✅ Located on your local machine
- ✅ Excluded from git by `.gitignore`
- ✅ Never committed to the repository
- ✅ Never pushed to GitHub

**Use this method when**:
- Developing and testing locally
- One-time manual deployments
- Single developer projects

### Method 2: GitHub Secrets (For Team/CI/CD) ⭐ **RECOMMENDED**

The CloudFlare API token is stored as a GitHub Secret:
- ✅ Encrypted at rest (AES-256-GCM)
- ✅ Encrypted in transit (TLS 1.2+)
- ✅ Centrally managed by repository admins
- ✅ Never exposed to developers
- ✅ Audit trail of all usage
- ✅ Automatic credential rotation support

**Use this method when**:
- Multiple team members need access
- Automated CI/CD deployments
- Want deployment audit trail
- Tokens shouldn't be on local machines

See **[GITHUB_ACTIONS.md](GITHUB_ACTIONS.md)** for complete GitHub Secrets setup guide.

---

## Local terraform.tfvars Method

#### How to Securely Obtain the Token

1. **From CloudFlare Dashboard**:
   - Log in to https://dash.cloudflare.com
   - Go to "My Profile" → "API Tokens"
   - Create a new token or use an existing one
   - Copy the token and store it in `terraform.tfvars`

2. **From Your Team Lead**:
   - Request the API token through secure channels (not email, Slack, etc.)
   - Use a password manager or secure sharing tool
   - Delete any messages containing the token after retrieval

#### Configuration File Security

The `terraform.tfvars` file contains sensitive information:

```hcl
# This file should NEVER be committed to git
cloudflare_api_token = "your-actual-token-here"
domain_name          = "ffcadmin.org"
github_pages_domain  = "freeforcharity.github.io"
```

**Protection Mechanisms**:
- File is listed in `.gitignore`
- Git will not track or commit this file
- File remains on your local machine only

#### What to Do If a Token is Exposed

If an API token is accidentally committed or exposed:

1. **Immediately revoke the token** in CloudFlare dashboard
2. **Generate a new API token** with the same permissions
3. **Update `terraform.tfvars`** with the new token
4. **Notify your team** of the security incident
5. **Review git history** and remove exposed tokens if possible

#### Best Practices

✅ **DO**:
- Store tokens in `terraform.tfvars` (excluded by .gitignore)
- Use scoped tokens with minimal required permissions
- Rotate tokens regularly (every 90 days recommended)
- Use different tokens for different environments (dev/staging/prod)
- Review `.gitignore` before committing changes

❌ **DON'T**:
- Commit tokens to git repositories
- Share tokens via email, Slack, or other unsecured channels
- Use tokens in documentation or README files
- Hard-code tokens in Terraform files
- Use the same token across multiple projects

#### Alternative: Environment Variables

For additional security, you can use environment variables instead of `terraform.tfvars`:

```bash
export TF_VAR_cloudflare_api_token="your-token-here"
export TF_VAR_domain_name="ffcadmin.org"
export TF_VAR_github_pages_domain="freeforcharity.github.io"

terraform apply
```

This keeps the token out of files entirely.

#### Verification

To verify your token is not tracked by git:

```bash
# This should NOT show terraform.tfvars
git ls-files | grep terraform.tfvars

# This should show terraform.tfvars.example only
git ls-files | grep tfvars
```

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do NOT** open a public issue
2. Contact the repository maintainers privately
3. Provide details about the vulnerability
4. Wait for confirmation before disclosing publicly

## GitHub Secrets Method (Recommended for Teams)

### Setup GitHub Secrets

1. **Navigate to Repository Settings**:
   - Go to your repository on GitHub
   - Click **Settings** → **Secrets and variables** → **Actions**

2. **Add CloudFlare API Token**:
   - Click **New repository secret**
   - Name: `CLOUDFLARE_API_TOKEN`
   - Value: Your CloudFlare API token
   - Click **Add secret**

3. **Deploy Using GitHub Actions**:
   - Go to **Actions** tab
   - Select **Terraform Apply** workflow
   - Click **Run workflow**
   - Enter domain details
   - Token is automatically injected from secrets

### Security Benefits

| Feature | Local File | GitHub Secrets |
|---------|------------|----------------|
| Encryption at rest | ❌ | ✅ (AES-256) |
| Shared securely | ❌ | ✅ |
| Audit trail | ❌ | ✅ |
| Auto redaction | ❌ | ✅ |
| CI/CD ready | ❌ | ✅ |

### Best Practices for GitHub Secrets

✅ **DO**:
- Use GitHub Secrets for production deployments
- Limit access to secrets (repository admins only)
- Enable branch protection for main branch
- Require PR reviews before merge
- Rotate secrets every 90 days
- Use workflow dispatch for manual deployments

❌ **DON'T**:
- Echo secrets in workflow logs
- Pass secrets as workflow inputs
- Use secrets in public repositories
- Share repository admin access unnecessarily

See **[GITHUB_ACTIONS.md](GITHUB_ACTIONS.md)** for complete setup guide.

---

## Security Checklist

### For Local Development:

- [ ] API token stored in `terraform.tfvars` (not committed)
- [ ] `.gitignore` includes `*.tfvars` (except `*.tfvars.example`)
- [ ] No tokens in documentation files
- [ ] No tokens in example files
- [ ] Git history doesn't contain exposed tokens
- [ ] Token has minimal required permissions
- [ ] Token is scoped to specific domains only

### For GitHub Actions/CI/CD:

- [ ] CloudFlare API token added to GitHub Secrets
- [ ] Secret named exactly `CLOUDFLARE_API_TOKEN`
- [ ] Branch protection enabled on main branch
- [ ] Required reviews configured
- [ ] Workflow dispatch used for production deployments
- [ ] Workflows don't echo secrets
- [ ] Team members trained on GitHub Actions usage

## Additional Resources

- [CloudFlare API Token Documentation](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Terraform Sensitive Variables](https://www.terraform.io/docs/language/values/variables.html#suppressing-values-in-cli-output)
- [Git Security Best Practices](https://git-scm.com/book/en/v2/GitHub-Account-Administration-and-Security)

## For AI Agents

**If you are an AI agent (GitHub Copilot, ChatGPT, Claude, etc.) working on this repository:**

📖 **REQUIRED READING**: [AI Agent Instructions](.github/agents/AI_AGENT_INSTRUCTIONS.md)

This document contains mandatory security rules for AI agents to prevent secret exposure. All AI agents must follow these instructions when working on this codebase.

**Key Points:**
- NEVER write actual API tokens in any file
- ALWAYS use GitHub Secrets or environment variables
- ALWAYS validate secrets exist before use
- ALWAYS use placeholders in documentation

---

**Remember**: Security is everyone's responsibility. When in doubt, ask before committing!
