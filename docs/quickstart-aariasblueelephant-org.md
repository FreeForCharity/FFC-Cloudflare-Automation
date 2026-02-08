# Quick Start: aariasblueelephant.org Setup

This is a quick reference guide for setting up aariasblueelephant.org. For detailed instructions, see [runbook-aariasblueelephant-org.md](runbook-aariasblueelephant-org.md).

## Overview

**Goal**: Configure aariasblueelephant.org with GitHub Pages using automated workflows

**Repository**: ffc-ex-aariasblueelephant.org  
**Domain**: aariasblueelephant.org

## Prerequisites

✅ Domain is already in Cloudflare account  
✅ GitHub Actions workflows are available in FFC-Cloudflare-Automation  
⚠️ Requires authorized access to run workflows

## Quick Steps

### 1. Create Repository (2 minutes)

1. Go to: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/create-repo.yml
2. Click "Run workflow"
3. Use these inputs:
   ```
   RepoName: ffc-ex-aariasblueelephant.org
   Description: GitHub Pages site for aariasblueelephant.org
   TemplateRepo: FreeForCharity/FFC-IN-Single_Page_Template_Jekell
   Visibility: public
   EnableIssues: true
   EnablePages: true
   CNAME: aariasblueelephant.org
   DryRun: true (first run to preview)
   ```
4. Review output, then run again with DryRun: false

### 2. Configure DNS (5 minutes)

1. Go to: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/2-enforce-standard.yml
2. Click "Run workflow"
3. Use these inputs:
   ```
   domain: aariasblueelephant.org
   dry_run: true (first run to preview)
   pages_only: true
   proxy_on: false
   ```
4. Review output, then run again with dry_run: false

### 3. Enable Custom Domain (5 minutes)

1. Go to: https://github.com/FreeForCharity/ffc-ex-aariasblueelephant.org/settings/pages
2. Enter custom domain: `aariasblueelephant.org`
3. Click "Save"
4. Wait for DNS check ✓
5. Enable "Enforce HTTPS"

### 4. Verify (2 minutes)

```bash
# Check DNS
dig aariasblueelephant.org A +short
dig www.aariasblueelephant.org CNAME +short

# Test site
curl -I https://aariasblueelephant.org
curl -I https://www.aariasblueelephant.org
```

## Expected DNS Records

After step 2, these records will be created:

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| A | @ | 185.199.108.153 | No |
| A | @ | 185.199.109.153 | No |
| A | @ | 185.199.110.153 | No |
| A | @ | 185.199.111.153 | No |
| AAAA | @ | 2606:50c0:8000::153 | No |
| AAAA | @ | 2606:50c0:8001::153 | No |
| AAAA | @ | 2606:50c0:8002::153 | No |
| AAAA | @ | 2606:50c0:8003::153 | No |
| CNAME | www | freeforcharity.github.io | No |

## Timeline

- Repository creation: ~2 minutes
- DNS configuration: ~5 minutes
- DNS propagation: 5-30 minutes
- GitHub DNS verification: 10-30 minutes
- SSL certificate: 1-24 hours (usually < 1 hour)

## Troubleshooting

**DNS check failing?**  
→ Wait 10-30 minutes for DNS propagation, ensure proxy is disabled

**HTTPS not working?**  
→ Wait up to 24 hours for SSL certificate, remove and re-add custom domain if needed

**www not redirecting?**  
→ Verify CNAME exists: `www` → `freeforcharity.github.io`

## Need Help?

- Full guide: [runbook-aariasblueelephant-org.md](runbook-aariasblueelephant-org.md)
- Automation guide: [automation-guide-github-pages.md](automation-guide-github-pages.md)
- Contact: Clarke Moyer (520-222-8104)
