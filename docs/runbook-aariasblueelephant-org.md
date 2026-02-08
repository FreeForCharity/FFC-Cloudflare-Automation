# Runbook: aariasblueelephant.org GitHub Pages Setup

## Overview

This runbook documents the setup process for configuring the apex domain `aariasblueelephant.org` with GitHub Pages, leveraging the existing GitHub Actions workflows in the FFC-Cloudflare-Automation repository.

## Domain Information

- **Apex Domain**: aariasblueelephant.org
- **GitHub Organization**: FreeForCharity
- **Repository Name**: ffc-ex-aariasblueelephant.org
- **GitHub Pages URL**: freeforcharity.github.io/ffc-ex-aariasblueelephant.org
- **Repository Type**: Project Pages (project repository with GitHub Pages enabled)
- **Current Status**: Domain already in Cloudflare account

## Requirements

### DNS Configuration
- 4 A records pointing to GitHub Pages IP addresses
- 4 AAAA records for IPv6 (GitHub Pages standard)
- www CNAME record → freeforcharity.github.io

### SSL/TLS
- Enable HTTPS
- Enforce HTTPS

### Additional Subdomains
- www.aariasblueelephant.org

## Implementation Steps

### Step 1: Create GitHub Repository

Use the existing GitHub Actions workflow to create the repository from template.

**Workflow**: `.github/workflows/create-repo.yml` - "89. Repo - Create GitHub Repo [Repo]"

**Inputs**:
- **RepoName**: `ffc-ex-aariasblueelephant.org`
- **Description**: `GitHub Pages site for aariasblueelephant.org`
- **TemplateRepo**: `FreeForCharity/FFC-IN-Single_Page_Template_Jekell` (Note: "Jekell" is the actual repository name, not a typo)
- **Visibility**: `public`
- **EnableIssues**: `true`
- **EnablePages**: `true`
- **CNAME**: `aariasblueelephant.org`
- **DryRun**: `false` (set to `true` first to preview)

**Execution**:
1. Navigate to: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/create-repo.yml
2. Click "Run workflow"
3. Fill in the inputs as specified above
4. Run in dry-run mode first to preview
5. Review the preview output
6. Run again with DryRun=false to create the repository

**Expected Outcome**:
- New repository `FreeForCharity/ffc-ex-aariasblueelephant.org` created
- Repository cloned from template `FFC-IN-Single_Page_Template_Jekell`
- GitHub Pages enabled with build_type=workflow
- CNAME set to `aariasblueelephant.org`
- Issues enabled
- Auto-delete head branches enabled

### Step 2: Configure DNS Records (Cloudflare)

Use the existing GitHub Actions workflow to enforce the standard DNS configuration.

**Option A: Full Standard Enforcement (Recommended)**

**Workflow**: `.github/workflows/1-enforce-domain-standard.yml` - "03. Domain - Enforce Standard (GitHub Apex + M365) [CF+M365]"

This workflow will:
- Configure GitHub Pages DNS (A, AAAA, www CNAME)
- Configure Microsoft 365 DNS (if applicable)
- Set up DKIM selectors (if applicable)

**Inputs**:
- **domain**: `aariasblueelephant.org`
- **dry_run**: `true` (set to false after verifying preview)
- **dmarc_mgmt_debug**: `false`
- **issue_number**: (optional - can reference the original issue)

**Execution**:
1. Navigate to: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/1-enforce-domain-standard.yml
2. Click "Run workflow"
3. Enter `aariasblueelephant.org` as the domain
4. Set dry_run to `true` for preview
5. Review the output artifacts
6. Run again with dry_run=false to apply changes

**Option B: GitHub Pages DNS Only**

**Workflow**: `.github/workflows/2-enforce-standard.yml` - "06. DNS - Enforce Standard (DNS-only) [CF]"

This workflow configures only GitHub Pages DNS without Microsoft 365 records.

**Inputs**:
- **domain**: `aariasblueelephant.org`
- **dry_run**: `true` (set to false after verifying preview)
- **pages_only**: `true` (only enforce GitHub Pages records)
- **proxy_on**: `false` (keep DNS-only mode for GitHub to issue SSL)

**Execution**:
1. Navigate to: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/2-enforce-standard.yml
2. Click "Run workflow"
3. Fill in the inputs as specified above
4. Run in dry-run mode first to preview
5. Review the preview output
6. Run again with dry_run=false to apply changes

**Expected DNS Records Created**:

```
Type    Name    Content                     Proxied
A       @       185.199.108.153             No (DNS only)
A       @       185.199.109.153             No (DNS only)
A       @       185.199.110.153             No (DNS only)
A       @       185.199.111.153             No (DNS only)
AAAA    @       2606:50c0:8000::153         No (DNS only)
AAAA    @       2606:50c0:8001::153         No (DNS only)
AAAA    @       2606:50c0:8002::153         No (DNS only)
AAAA    @       2606:50c0:8003::153         No (DNS only)
CNAME   www     freeforcharity.github.io    No (DNS only)
```

### Step 3: Configure Custom Domain in GitHub Pages

**Manual Steps** (performed in the repository):

1. Navigate to: https://github.com/FreeForCharity/ffc-ex-aariasblueelephant.org/settings/pages
2. Under "Custom domain", enter: `aariasblueelephant.org`
3. Click "Save"
4. Wait for DNS check to complete (green checkmark)
5. Once verified, check "Enforce HTTPS"

**Note**: The CNAME file will be automatically created in the repository root by GitHub.

### Step 4: Verify Configuration

**DNS Verification**:
```bash
# Check A records
dig aariasblueelephant.org A +short

# Expected output:
# 185.199.108.153
# 185.199.109.153
# 185.199.110.153
# 185.199.111.153

# Check AAAA records
dig aariasblueelephant.org AAAA +short

# Expected output:
# 2606:50c0:8000::153
# 2606:50c0:8001::153
# 2606:50c0:8002::153
# 2606:50c0:8003::153

# Check www CNAME
dig www.aariasblueelephant.org CNAME +short

# Expected output:
# freeforcharity.github.io.
```

**Website Verification**:
```bash
# Test apex domain
curl -I https://aariasblueelephant.org

# Test www subdomain
curl -I https://www.aariasblueelephant.org
```

**Checklist**:
- [ ] Repository `ffc-ex-aariasblueelephant.org` created
- [ ] GitHub Pages enabled in repository settings
- [ ] All 4 A records created in Cloudflare
- [ ] All 4 AAAA records created in Cloudflare
- [ ] www CNAME created in Cloudflare
- [ ] All DNS records set to "DNS only" (gray cloud, proxy disabled)
- [ ] DNS propagation verified
- [ ] Custom domain `aariasblueelephant.org` configured in GitHub Pages
- [ ] DNS check passed (green checkmark in GitHub Pages settings)
- [ ] CNAME file present in repository root
- [ ] HTTPS enabled and enforced
- [ ] Website loads at https://aariasblueelephant.org
- [ ] Website loads at https://www.aariasblueelephant.org
- [ ] SSL certificate valid (issued by GitHub)

## Cloudflare Settings

Recommended Cloudflare settings for GitHub Pages:

- **SSL/TLS Mode**: Full (not Full Strict)
- **Always Use HTTPS**: On
- **Automatic HTTPS Rewrites**: On
- **Minimum TLS Version**: 1.2 or higher
- **Proxy Status**: DNS Only (gray cloud) ⚠️ **Critical** - Must be disabled for GitHub Pages SSL to work

## Timeline

- Repository creation: ~1-2 minutes
- DNS configuration via workflow: ~5 minutes
- DNS propagation: 5-30 minutes
- GitHub DNS verification: 10-30 minutes
- SSL certificate provisioning: 1-24 hours (typically within 1 hour)

## Troubleshooting

### DNS Check Failing in GitHub Pages

If GitHub Pages shows "DNS check unsuccessful":
1. Verify all 4 A records are created correctly
2. Ensure proxy is disabled (DNS only / gray cloud)
3. Wait 10-30 minutes for DNS propagation
4. Try removing and re-adding the custom domain

### SSL Certificate Not Provisioning

If HTTPS is not available after 24 hours:
1. Verify DNS records are correct
2. Ensure proxy is disabled in Cloudflare
3. Remove custom domain in GitHub Pages, wait 5 minutes, add it back
4. Check that "Enforce HTTPS" was enabled after DNS check passed

### www Subdomain Not Working

If www subdomain doesn't redirect to apex:
1. Verify CNAME record exists: `www` → `freeforcharity.github.io`
2. Ensure proxy is disabled for CNAME
3. Wait for DNS propagation
4. GitHub Pages handles the redirect automatically

## Alternative: Manual PowerShell Script Method

If GitHub Actions workflows are unavailable, DNS records can be configured manually using PowerShell scripts:

```powershell
# Set Cloudflare API token
$env:CLOUDFLARE_API_TOKEN_FFC = "your-token-here"

# Create A records for GitHub Pages
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type A -Content 185.199.108.153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type A -Content 185.199.109.153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type A -Content 185.199.110.153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type A -Content 185.199.111.153

# Create AAAA records for GitHub Pages (IPv6)
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type AAAA -Content 2606:50c0:8000::153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type AAAA -Content 2606:50c0:8001::153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type AAAA -Content 2606:50c0:8002::153
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name @ -Type AAAA -Content 2606:50c0:8003::153

# Create www CNAME
.\Update-CloudflareDns.ps1 -Zone aariasblueelephant.org -Name www -Type CNAME -Content freeforcharity.github.io
```

## References

- GitHub Pages Documentation: https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site
- Cloudflare DNS Documentation: https://developers.cloudflare.com/dns/
- FFC-Cloudflare-Automation Repository: https://github.com/FreeForCharity/FFC-Cloudflare-Automation
- Issue Template: `.github/ISSUE_TEMPLATE/04-github-pages-apex.yml`
- Enforce Standard Workflow Documentation: `docs/enforce-standard-workflow.md`
