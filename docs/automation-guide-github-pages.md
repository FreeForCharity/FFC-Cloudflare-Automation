# Automation Guide: GitHub Pages DNS Setup Using GitHub Actions

## Overview

This guide explains how to use the existing GitHub Actions workflows in the FFC-Cloudflare-Automation repository to automate the complete setup of GitHub Pages with custom domains, including repository creation and DNS configuration.

## Workflows Available

The FFC-Cloudflare-Automation repository provides the following workflows for GitHub Pages automation:

### 1. Repository Creation Workflow

**File**: `.github/workflows/create-repo.yml`  
**Name**: "89. Repo - Create GitHub Repo [Repo]"

Creates a new GitHub repository from a template with GitHub Pages pre-configured.

**Features**:
- Creates repository from specified template
- Configures GitHub Pages with workflow-based deployment
- Sets custom domain (CNAME)
- Enables/disables Issues, Projects, Wiki
- Configures merge strategies
- Enables auto-delete of head branches

### 2. DNS Enforcement Workflows

#### Option A: Full Standard Enforcement (GitHub Pages + M365)

**File**: `.github/workflows/1-enforce-domain-standard.yml`  
**Name**: "03. Domain - Enforce Standard (GitHub Apex + M365) [CF+M365]"

Enforces the complete DNS standard including:
- GitHub Pages DNS (A, AAAA, www CNAME)
- Microsoft 365 DNS (MX, TXT, SRV, CNAME)
- DKIM configuration

#### Option B: DNS-Only Enforcement (Flexible)

**File**: `.github/workflows/2-enforce-standard.yml`  
**Name**: "06. DNS - Enforce Standard (DNS-only) [CF]"

Enforces DNS configuration with flexible options:
- Full standard or GitHub Pages only
- Configurable proxy settings
- No M365 DKIM integration

## Step-by-Step Automation Process

### Process 1: Using GitHub Actions Workflows (Recommended)

This is the fully automated approach using the web interface.

#### Step 1: Create Repository

1. Navigate to the [Create Repo Workflow](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/create-repo.yml)
2. Click **"Run workflow"**
3. Fill in the workflow inputs:
   - **RepoName**: `ffc-ex-<domainname>` (e.g., `ffc-ex-example.org`)
   - **Description**: Short description of the site
   - **TemplateRepo**: `FreeForCharity/FFC-IN-Single_Page_Template_Jekell` (Note: "Jekell" is the actual repository name)
   - **Visibility**: `public`
   - **EnableIssues**: `true`
   - **EnablePages**: `true`
   - **CNAME**: Your apex domain (e.g., `example.org`)
   - **DryRun**: `true` (for preview) or `false` (to execute)
4. Click **"Run workflow"**
5. Monitor the workflow execution
6. Review the output logs
7. If DryRun was true, run again with DryRun=false to create the repository

**Workflow Output**:
- New repository created in FreeForCharity organization
- GitHub Pages enabled with workflow deployment
- CNAME configured (if provided)
- Repository settings configured as specified

#### Step 2: Configure DNS Records

**Option A: For GitHub Pages + M365 Domains**

1. Navigate to the [Enforce Domain Standard Workflow](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/1-enforce-domain-standard.yml)
2. Click **"Run workflow"**
3. Fill in the workflow inputs:
   - **domain**: Your apex domain (e.g., `example.org`)
   - **dry_run**: `true` (for preview) or `false` (to execute)
   - **dmarc_mgmt_debug**: `false` (unless debugging)
   - **issue_number**: Optional - issue number to post results back to
4. Click **"Run workflow"**
5. Review the workflow artifacts:
   - Download "cloudflare-domain-enforce" artifact
   - Review `cloudflare-enforce.txt` for actions taken
   - Review `cloudflare-audit-after.txt` for compliance check
6. If dry_run was true and results look good, run again with dry_run=false

**Option B: For GitHub Pages Only (No M365)**

1. Navigate to the [Enforce Standard DNS Workflow](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/2-enforce-standard.yml)
2. Click **"Run workflow"**
3. Fill in the workflow inputs:
   - **domain**: Your apex domain (e.g., `example.org`)
   - **dry_run**: `true` (for preview) or `false` (to execute)
   - **pages_only**: `true` (to only configure GitHub Pages DNS)
   - **proxy_on**: `false` (must be false for GitHub Pages SSL)
   - **dmarc_mgmt_debug**: `false`
4. Click **"Run workflow"**
5. Review the output
6. If dry_run was true and results look good, run again with dry_run=false

**Workflow Output**:
- DNS records created/updated in Cloudflare
- Audit report showing compliance status
- Optional comment posted to GitHub issue (if issue_number provided)

#### Step 3: Configure Custom Domain in GitHub Pages

This step must be done manually in the repository:

1. Navigate to the repository's **Settings** → **Pages**
2. Under **"Custom domain"**, enter your apex domain (e.g., `example.org`)
3. Click **"Save"**
4. Wait for the DNS check to complete (green checkmark appears)
5. Once verified, check **"Enforce HTTPS"**

**Note**: The CNAME file will be automatically created in the repository root.

#### Step 4: Verify the Setup

Use command-line tools to verify:

```bash
# Check A records
dig example.org A +short

# Check AAAA records  
dig example.org AAAA +short

# Check www CNAME
dig www.example.org CNAME +short

# Test HTTPS on apex domain
curl -I https://example.org

# Test HTTPS on www subdomain
curl -I https://www.example.org
```

### Process 2: Using PowerShell Scripts (Manual Alternative)

If GitHub Actions are not available or you prefer command-line execution:

#### Step 1: Create Repository Using GitHub CLI

The `Create-GitHubRepo.ps1` script can be run locally:

```powershell
# Set GitHub token
$env:GH_TOKEN = "your-github-token"

# Run the script
.\scripts\Create-GitHubRepo.ps1 `
    -RepoName "ffc-ex-example.org" `
    -Description "GitHub Pages site for example.org" `
    -TemplateRepo "FreeForCharity/FFC-IN-Single_Page_Template_Jekell" `
    -Visibility "public" `
    -EnableIssues $true `
    -EnablePages `
    -CNAME "example.org" `
    -DryRun  # Remove -DryRun to execute

# Note: "Jekell" in the template repository name is intentional, not a typo
```

#### Step 2: Configure DNS Using PowerShell Script

The `Update-CloudflareDns.ps1` script can be run locally:

**Option A: Use Enforce Standard Mode**

```powershell
# Set Cloudflare API tokens
$env:CLOUDFLARE_API_TOKEN_FFC = "your-ffc-token"
$env:CLOUDFLARE_API_TOKEN_CM = "your-cm-token"

# Preview changes (dry run)
.\Update-CloudflareDns.ps1 -Zone example.org -EnforceStandard -GitHubPagesOnly -DryRun

# Apply changes
.\Update-CloudflareDns.ps1 -Zone example.org -EnforceStandard -GitHubPagesOnly
```

**Option B: Create Records Individually**

```powershell
# Set Cloudflare API token
$env:CLOUDFLARE_API_TOKEN_FFC = "your-token"

# Create A records
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.108.153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.109.153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.110.153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type A -Content 185.199.111.153

# Create AAAA records
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type AAAA -Content 2606:50c0:8000::153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type AAAA -Content 2606:50c0:8001::153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type AAAA -Content 2606:50c0:8002::153
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type AAAA -Content 2606:50c0:8003::153

# Create www CNAME
.\Update-CloudflareDns.ps1 -Zone example.org -Name www -Type CNAME -Content freeforcharity.github.io
```

## Standard GitHub Pages DNS Configuration

The workflows automatically create the following DNS records:

### A Records (IPv4)
```
Type: A, Name: @, Content: 185.199.108.153, Proxied: No
Type: A, Name: @, Content: 185.199.109.153, Proxied: No
Type: A, Name: @, Content: 185.199.110.153, Proxied: No
Type: A, Name: @, Content: 185.199.111.153, Proxied: No
```

### AAAA Records (IPv6)
```
Type: AAAA, Name: @, Content: 2606:50c0:8000::153, Proxied: No
Type: AAAA, Name: @, Content: 2606:50c0:8001::153, Proxied: No
Type: AAAA, Name: @, Content: 2606:50c0:8002::153, Proxied: No
Type: AAAA, Name: @, Content: 2606:50c0:8003::153, Proxied: No
```

### CNAME Record (www subdomain)
```
Type: CNAME, Name: www, Content: freeforcharity.github.io, Proxied: No
```

**Important**: All records must have Cloudflare proxy **disabled** (DNS only / gray cloud) to allow GitHub to issue SSL certificates.

## Workflow Execution Tips

### Dry Run First

Always run workflows in dry-run mode first:
- Repository creation: Set `DryRun: true`
- DNS enforcement: Set `dry_run: true`

This allows you to:
- Preview what will be created/changed
- Verify the configuration is correct
- Catch errors before making changes

### Review Artifacts

DNS enforcement workflows produce artifacts:
- Download the workflow artifacts
- Review the text files to see exactly what was done
- Verify the post-audit shows no issues

### Use Issue Integration

The Domain Enforce Standard workflow supports posting results back to an issue:
- Provide the `issue_number` input
- Workflow will comment results on that issue
- Useful for tracking and documentation

### Monitor Workflow Logs

Watch the workflow execution logs in real-time:
- Click on a running workflow to see live logs
- Check for errors or warnings
- Verify API calls are successful

## Troubleshooting Workflows

### Workflow Fails with "Secret not set" Error

Ensure the required secrets are configured in the repository/environment:
- For repository creation: `CBM_TOKEN` (GitHub token with repo creation permissions)
- For DNS enforcement: `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` and/or `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`

Secrets are configured in:
- Repository: Settings → Secrets and variables → Actions
- Environment: Settings → Environments → [environment name] → Secrets

### Workflow Completes but Changes Not Applied

Check if dry-run mode was enabled:
- Repository creation: `DryRun` input
- DNS enforcement: `dry_run` input

If dry-run was true, the workflow only previewed changes. Run again with dry-run=false.

### DNS Records Not Created

Possible causes:
1. Workflow ran in dry-run mode
2. API token doesn't have permissions for the zone
3. Zone not found in Cloudflare account
4. API rate limits reached

Check the workflow logs for specific error messages.

### Repository Created but Pages Not Enabled

The create-repo workflow may fail to enable Pages if:
1. The template repository doesn't have the correct structure
2. GitHub Actions deployment is not configured in the template
3. The workflow failed partway through

Manually enable Pages in the repository settings as a fallback.

## Best Practices

### 1. Use Dry Run Mode

Always preview changes before applying them.

### 2. Follow Naming Conventions

Use the `ffc-ex-<domainname>` format for repository names to maintain consistency.

### 3. Verify DNS Propagation

Wait 10-30 minutes after DNS changes before testing the website.

### 4. Document in Issues

Create a GitHub issue for each new domain setup to track the process and results.

### 5. Test HTTPS Last

Only enable "Enforce HTTPS" in GitHub Pages after the DNS check passes.

### 6. Keep Proxy Disabled

Never enable Cloudflare proxy (orange cloud) for GitHub Pages DNS records.

### 7. Monitor SSL Certificate

SSL certificates can take up to 24 hours to provision, but typically complete within 1 hour.

## Workflow Inputs Reference

### Create Repository Workflow

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| RepoName | string | Yes | - | Repository name (e.g., ffc-ex-example.org) |
| Description | string | No | "Created via automation" | Repository description |
| TemplateRepo | string | Yes | FreeForCharity/FFC-IN-Single_Page_Template_Jekell | Template repository |
| Visibility | choice | Yes | public | public, private, or internal |
| EnableIssues | boolean | No | true | Enable Issues |
| EnablePages | boolean | No | false | Enable GitHub Pages |
| CNAME | string | No | - | Custom domain (auto-detected from repo name if empty) |
| DryRun | boolean | No | false | Preview only |

### Enforce Domain Standard Workflow (CF+M365)

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| domain | string | Yes | - | Domain name (e.g., example.org) |
| dry_run | boolean | No | true | Preview only |
| dmarc_mgmt_debug | boolean | No | false | Debug DMARC Management API |
| issue_number | number | No | - | Issue number to post results to |

### Enforce Standard DNS Workflow (DNS-only)

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| domain | string | Yes | - | Domain name (e.g., example.org) |
| dry_run | boolean | No | true | Preview only |
| pages_only | boolean | No | false | Only enforce GitHub Pages DNS |
| proxy_on | boolean | No | false | Enable Cloudflare proxy (should be false for GitHub Pages) |
| dmarc_mgmt_debug | boolean | No | false | Debug DMARC Management API |

## Related Documentation

- [Enforce Standard Workflow Documentation](enforce-standard-workflow.md)
- [GitHub Actions Environments and Secrets](github-actions-environments-and-secrets.md)
- [GitHub Pages Apex Domain Issue Template](../.github/ISSUE_TEMPLATE/04-github-pages-apex.yml)
- [GitHub Pages Subdomain Issue Template](../.github/ISSUE_TEMPLATE/05-github-pages-subdomain.yml)

## External References

- [GitHub Pages Documentation](https://docs.github.com/en/pages)
- [Configuring a custom domain for GitHub Pages](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
- [GitHub Pages IP Addresses](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site#configuring-an-apex-domain)
- [Cloudflare DNS Documentation](https://developers.cloudflare.com/dns/)
