# GitHub Actions Automation

This guide explains how to use GitHub Secrets and GitHub Actions to automate Terraform deployments, eliminating the need for local `terraform.tfvars` files.

## Overview

GitHub Actions integration provides:
- ✅ **Secure token storage** in GitHub Secrets (encrypted at rest)
- ✅ **Automated deployments** via workflow dispatch
- ✅ **PR validation** with Terraform plan comments
- ✅ **No local credentials** needed
- ✅ **Audit trail** of all deployments
- ✅ **Team collaboration** without sharing tokens

## Architecture

### Two Deployment Methods

This repository supports **two approaches** for managing credentials:

| Method | Use Case | Token Storage | Best For |
|--------|----------|---------------|----------|
| **Local Terraform** | Manual deployments | `terraform.tfvars` (local file) | Individual developers, one-time setups |
| **GitHub Actions** | Automated CI/CD | GitHub Secrets (encrypted) | Teams, automated deployments, CI/CD |

Both methods are valid and can be used based on your needs.

## Setup GitHub Secrets

### Step 1: Add Cloudflare API Token to GitHub Secrets

1. Go to your repository on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add the following secret:
   - **Name**: `CLOUDFLARE_API_TOKEN`
   - **Value**: Your Cloudflare API token (from Cloudflare dashboard)
5. Click **Add secret**

⚠️ **Security Note**: The workflow automatically validates that this secret exists before use.

### Step 2: Verify Secret is Added

The secret should now appear in your repository secrets list (value will be hidden).

## GitHub Actions Workflows

Two workflows are included:

### 1. Terraform Plan (`.github/workflows/terraform-plan.yml`)

**Trigger**: Automatically runs on pull requests

**Purpose**: Validates Terraform configuration and posts plan to PR comments

**Features**:
- ✅ **Secret validation** - Verifies CLOUDFLARE_API_TOKEN exists before use
- ✅ Terraform format check
- ✅ Terraform initialization
- ✅ Terraform validation
- ✅ Terraform plan (uses GitHub Secret)
- ✅ Posts results as PR comment

**Example PR Comment**:
```
#### Terraform Format and Style 🖌 success
#### Terraform Initialization ⚙️ success
#### Terraform Validation 🤖 success
#### Terraform Plan 📖 success

Plan: 6 to add, 0 to change, 0 to destroy
```

### 2. Terraform Apply (`.github/workflows/terraform-apply.yml`)

**Trigger**: Manual workflow dispatch

**Purpose**: Deploys Cloudflare configuration to production

**Features**:
- ✅ **Secret validation** - Verifies CLOUDFLARE_API_TOKEN exists before use
- ✅ Manual approval required (workflow dispatch)
- ✅ Input domain names via UI
- ✅ Only runs on main/master branch
- ✅ Uses GitHub Secret for API token
- ✅ Outputs results

## How to Deploy Using GitHub Actions

### Method 1: Using Workflow Dispatch (Recommended)

1. Go to **Actions** tab in your GitHub repository
2. Select **Terraform Apply** workflow
3. Click **Run workflow**
4. Fill in the inputs:
   - **domain_name**: `ffcadmin.org`
   - **github_pages_domain**: `freeforcharity.github.io`
5. Click **Run workflow**
6. Wait for the workflow to complete
7. Check the outputs in the workflow logs

### Method 2: Push to Main Branch

When you push Terraform changes to the main branch:
1. **Terraform Plan** workflow runs automatically on PR
2. Review the plan in PR comments
3. Merge the PR
4. Manually trigger **Terraform Apply** workflow

## Environment Variables in GitHub Actions

The workflows use these environment variables:

```yaml
env:
  TF_VAR_cloudflare_api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}
  TF_VAR_domain_name: ${{ github.event.inputs.domain_name }}
  TF_VAR_github_pages_domain: ${{ github.event.inputs.github_pages_domain }}
```

Terraform automatically reads `TF_VAR_*` environment variables and maps them to input variables.

## Security Benefits of GitHub Secrets

### Why GitHub Secrets are Secure

1. **Encrypted at Rest**: Secrets are encrypted using AES-256-GCM
2. **Encrypted in Transit**: Uses TLS 1.2+ for transmission
3. **Access Control**: Only workflows in the same repository can access secrets
4. **Audit Logging**: All secret usage is logged
5. **No Local Storage**: Tokens never touch developer machines
6. **Automatic Redaction**: GitHub automatically redacts secrets in logs

### Security Comparison

| Feature | Local terraform.tfvars | GitHub Secrets |
|---------|----------------------|----------------|
| Encryption at rest | ❌ No (local file) | ✅ Yes (AES-256) |
| Shared across team | ❌ Manual sharing | ✅ Automatic |
| Audit trail | ❌ No | ✅ Yes |
| Rotation | Manual | Manual (but centralized) |
| Risk if laptop stolen | ⚠️ High | ✅ None |
| CI/CD ready | ❌ No | ✅ Yes |

## Updating GitHub Secrets

To rotate or update the API token:

1. Generate new Cloudflare API token
2. Go to **Settings** → **Secrets and variables** → **Actions**
3. Click on **CLOUDFLARE_API_TOKEN**
4. Click **Update secret**
5. Enter new token value
6. Click **Update secret**

All future workflow runs will use the new token automatically.

## Workflow File Locations

```
.github/
└── workflows/
    ├── terraform-plan.yml   # Runs on PRs
    └── terraform-apply.yml  # Manual deployment
```

## Customizing Workflows

### Adding More Variables

To add additional configuration options:

1. **Add to workflow inputs** (in `terraform-apply.yml`):
```yaml
inputs:
  ssl_mode:
    description: 'SSL mode (off/flexible/full/strict)'
    required: false
    default: 'full'
```

2. **Pass as environment variable**:
```yaml
env:
  TF_VAR_ssl_mode: ${{ github.event.inputs.ssl_mode }}
```

### Running on Different Branches

To allow deployment from feature branches, modify:

```yaml
if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/your-branch'
```

### Adding Slack Notifications

Add to the end of `terraform-apply.yml`:

```yaml
- name: Notify Slack
  if: always()
  uses: 8398a7/action-slack@v3
  with:
    status: ${{ job.status }}
    webhook_url: ${{ secrets.SLACK_WEBHOOK }}
```

## Local Development vs. GitHub Actions

### When to Use Local terraform.tfvars

✅ **Use local approach when**:
- Developing and testing locally
- One-time manual deployments
- Learning Terraform
- No CI/CD pipeline needed
- Single developer project

### When to Use GitHub Actions

✅ **Use GitHub Actions when**:
- Multiple team members need to deploy
- Automated deployments required
- CI/CD pipeline exists
- Want deployment audit trail
- Need approval workflows
- Tokens shouldn't be on local machines

### Using Both Approaches

You can use both methods in the same project:
- **Development**: Use local `terraform.tfvars` for testing
- **Production**: Use GitHub Actions for actual deployments

## Troubleshooting

### Secret Not Found

**Error**: `Error: Required variable not set: cloudflare_api_token`

**Solution**:
1. Verify secret name is exactly `CLOUDFLARE_API_TOKEN`
2. Check secret is added to the repository (not organization)
3. Ensure secret has a value set

### Workflow Not Running

**Problem**: Workflow doesn't trigger on PR

**Solution**:
1. Check workflow files are in `.github/workflows/`
2. Verify branch name matches trigger (main vs master)
3. Check file paths match the trigger paths

### Permission Denied

**Error**: `Error: Error acquiring the state lock`

**Solution**:
1. Ensure only one workflow runs at a time
2. Check API token has correct permissions
3. Wait for previous run to complete

## Migration from Local to GitHub Actions

If you're currently using local `terraform.tfvars`:

1. **Add secret to GitHub**:
   ```bash
   # Copy your token from terraform.tfvars
   # Add to GitHub Secrets as CLOUDFLARE_API_TOKEN
   ```

2. **Test with workflow dispatch**:
   - Run Terraform Apply workflow manually
   - Verify it works with GitHub Secret

3. **Optional: Remove local file**:
   ```bash
   rm terraform.tfvars
   # .gitignore ensures it's not tracked anyway
   ```

4. **Use GitHub Actions going forward**:
   - All deployments via Actions
   - Token managed centrally
   - Team can deploy without sharing tokens

## Best Practices

### For GitHub Actions

✅ **DO**:
- Use workflow dispatch for production deployments
- Review Terraform plan before applying
- Use branch protection rules
- Enable required reviews for main branch
- Monitor workflow runs
- Rotate secrets regularly (every 90 days)

❌ **DON'T**:
- Hardcode secrets in workflow files
- Allow automatic apply without review
- Give everyone write access to secrets
- Use the same token for dev and prod
- Skip PR reviews

### For Local Development

✅ **DO**:
- Keep `terraform.tfvars` in .gitignore
- Use for testing and development
- Document local setup process
- Test changes locally before pushing

❌ **DON'T**:
- Commit `terraform.tfvars` to git
- Share your local token file
- Use production tokens locally
- Skip validation steps

## Example Workflow Run

1. **Developer opens PR** with Terraform changes
2. **Terraform Plan workflow** runs automatically
3. **GitHub posts plan** as PR comment
4. **Team reviews** the planned changes
5. **PR is merged** to main branch
6. **Admin triggers** Terraform Apply workflow
7. **Workflow runs** using GitHub Secret
8. **Cloudflare updated** automatically
9. **Team notified** of successful deployment

## Support

For issues with GitHub Actions:
- Check workflow run logs in Actions tab
- Verify secret is properly configured
- Review [GitHub Actions documentation](https://docs.github.com/en/actions)
- Check [Terraform GitHub Actions guide](https://www.terraform.io/docs/github-actions/getting-started.html)

## For AI Agents

**If you are an AI agent working on this repository:**

📖 **REQUIRED**: Read [AI Agent Instructions](.github/agents/AI_AGENT_INSTRUCTIONS.md)

This document contains mandatory security guidelines to prevent secret exposure when modifying workflows or documentation.

---

**Summary**: Both local and GitHub Actions approaches are valid. Use GitHub Secrets for team environments and automated deployments. Use local files for individual development and learning.
