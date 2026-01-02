# FFC-Cloudflare-Automation

[![CodeQL](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml)
[![CI](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Automation utilities for Cloudflare tasks supporting Free For Charity.

## 🌐 GitHub Pages

View our automation tracking page:
[FFC Cloudflare Automation Tracker](https://freeforcharity.github.io/FFC-Cloudflare-Automation-/)

The static site provides an overview of the automation, current status, key features, and helpful
resources.

## Overview

This repository contains automation utilities and scripts for managing Free For Charity's Cloudflare
DNS configuration. Administrators execute DNS changes based on structured issue requests, using
either the Cloudflare Dashboard for manual updates or Python scripts and Cloudflare API tools for
automated, consistent, and auditable domain management.

For details on the GitHub Actions standard enforcement workflow (including required GitHub Pages
AAAA records), see [docs/enforce-standard-workflow.md](docs/enforce-standard-workflow.md).

For Microsoft 365 domain status checks and DKIM helpers (work in progress), see
[docs/m365-domain-and-dkim.md](docs/m365-domain-and-dkim.md).

## Features

### Issue-Based Workflow

- **Structured Requests**: Use GitHub issue templates for domain management requests
- **Automated Tracking**: All DNS changes tracked via GitHub issues
- **Administrator Execution**: FFC Cloudflare administrators execute changes based on approved
  issues
- **Full Audit Trail**: Complete history of all domain operations
- **Standardized Procedures**: Consistent workflows for common operations

### DNS Management Utilities

- **Python Scripts**: Flexible DNS record management using Cloudflare API
- **Create, update, search, and delete** DNS records
- **Supports A, AAAA, and CNAME** record types for GitHub Pages configuration
- **Dry-run mode** to preview changes before execution
- **Cloudflare proxy** (orange cloud) support with explicit --no-proxy flag
- **Secure token handling** via environment variables
- **Export Tools**: Export DNS configurations for backup and analysis
- **Manual Option**: Administrators can also use Cloudflare Dashboard for manual DNS updates

### Supported Operations

1. **Purchase and add new .org domains** to the Cloudflare account
2. **Add existing domains** to Cloudflare from other providers
3. **Remove domains** from the Cloudflare account
4. **Configure GitHub Pages** for apex domains (e.g., example.org)
5. **Configure GitHub Pages** for subdomains (e.g., staging.example.org)

## Quick Start

### Requesting DNS Changes

To request a DNS change or domain operation:

1. Go to the
   [Issues](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues/new/choose) page
2. Select the appropriate issue template:
   - **Purchase and Add New .org Domain** - For new domain acquisitions
   - **Add Existing Domain to Cloudflare** - For migrating domains
   - **Remove Domain from Cloudflare** - For domain removal
   - **Configure Apex Domain for GitHub Pages** - For root domain setup
   - **Configure Subdomain for GitHub Pages** - For subdomain setup
3. Fill out all required information in the template
4. Submit the issue
5. An administrator will review and execute the request

### For Administrators: Executing DNS Changes

The simplest way to update DNS records using the PowerShell utilities:

```powershell
# Update DNS record (example: staging subdomain)
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42
```

You'll be prompted for your Cloudflare API token, or you can set it as an environment variable:

```powershell
$env:CLOUDFLARE_API_KEY_DNS_ONLY = "your_token_here"
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42
```

**👉 [See detailed staging subdomain guide →](STAGING_README.md)**

## Prerequisites

### For DNS Script Execution (Administrators)

- PowerShell 5.1+ (Windows) or PowerShell 7+ (cross-platform)
- Cloudflare API token with DNS edit permissions
- Access to FFC Cloudflare account

## DNS Management Tools

The PowerShell scripts in this repository provide flexible DNS record management for FFC domains.

### Basic Examples

**Update or create an A record:**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42
```

**Update or create an AAAA record (IPv6):**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name @ -Type AAAA -Content 2606:50c0:8000::153
```

**Update or create a CNAME record:**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name www -Type CNAME -Content example.org
```

**List existing records:**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -List
```

**Delete a specific record:**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42 -Remove
```

**Enable Cloudflare proxy (orange cloud):**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42 -Proxied
```

**Disable Cloudflare proxy (DNS only - gray cloud, required for GitHub Pages):**

```powershell
# Proxy is disabled by default, no flag needed
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42
```

**Dry run (preview changes without applying):**

```powershell
.\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42 -DryRun
```

### Quick Subdomain Updates

For quick staging subdomain updates, use the specialized script:

```powershell
.\Update-StagingDns.ps1 -NewIp 203.0.113.42
```

**👉 [See PowerShell details in staging guide →](STAGING_README.md)**

## GitHub Pages DNS Configuration

For configuring GitHub Pages with custom domains, please use the appropriate issue template:

- **Apex domain** (e.g., example.org): Use the "Configure Apex Domain for GitHub Pages" template
- **Subdomain** (e.g., staging.example.org): Use the "Configure Subdomain for GitHub Pages" template

The issue templates provide step-by-step instructions for administrators to configure DNS records
correctly.

### Quick Reference: GitHub Pages Setup

**For Apex Domains:**

```bash
# GitHub Pages requires 4 A records pointing to these IPs:
# 185.199.108.153
# 185.199.109.153
# 185.199.110.153
# 185.199.111.153
```

**For Subdomains:**

```bash
# Create CNAME record pointing to GitHub Pages
# Example: staging.example.org -> username.github.io
```

See the issue templates for detailed configuration instructions.

## DNS Summary Export

Use `Export-CloudflareDns.ps1` to export a CSV summarizing apex A/AAAA and `www` CNAME details for
specific zones.

### Usage

```powershell
# Run the export script
.\Export-CloudflareDns.ps1 -OutputFile zone_dns_summary.csv

# Provide your token via env
$env:CLOUDFLARE_API_KEY_DNS_ONLY = "<dns_only_token>"

# Or with explicit token
.\Export-CloudflareDns.ps1 -OutputFile zone_dns_summary.csv -Token "<your_token>"
```

### CSV Columns

- `zone`: zone name
- `apex_a_ips`: semicolon-separated apex A IPs
- `apex_a_proxied`: semicolon-separated proxied flags (true/false)
- `www_cname_target`: CNAME target for `www`
- `www_cname_proxied`: proxied flag for `www` CNAME
- `m365_compliant`: whether MX points to outlook.com (compliance check)

The script supports tokens with various permission levels via environment variables.

### GitHub Actions

- Secret: set `CLOUDFLARE_API_KEY_DNS_ONLY`.
- Workflow: `DNS Summary Export`.
  - Provide `zones` input to target specific zones, or set `all_zones=true` to export everything
    accessible to the token.

## Repository Structure

```
.
├── .github/
│   ├── ISSUE_TEMPLATE/     # Issue templates for domain management requests
│   │   ├── config.yml      # Issue template configuration
│   │   ├── 01-purchase-new-domain.yml
│   │   ├── 02-add-existing-domain.yml
│   │   ├── 03-remove-domain.yml
│   │   ├── 04-github-pages-apex.yml
│   │   └── 05-github-pages-subdomain.yml
│   ├── workflows/          # GitHub Actions workflows
│   │   ├── ci.yml          # Continuous Integration
│   │   ├── codeql-analysis.yml  # Security scanning
│   │   ├── 1-audit-compliance.yml  # [DNS] Report - Check Compliance
│   │   ├── 2-enforce-standard.yml  # [DNS] Fix - Enforce Standard
│   │   ├── 3-manage-record.yml     # [DNS] Manual - Manage Record
│   │   ├── 4-export-summary.yml    # [DNS] Report - Export All Domains
│   │   ├── 99-legacy-zone-add.yml  # Legacy zone-add (kept for reference)
│   │   └── README.md       # Workflow documentation
│   └── dependabot.yml      # Dependency update configuration
├── CONTRIBUTING.md         # Contribution guidelines
├── LICENSE                 # GNU AGPL v3 license
├── README.md               # This file
├── SECURITY.md             # Security policy
├── STAGING_README.md       # Staging subdomain management guide
├── Update-CloudflareDns.ps1   # Comprehensive PowerShell DNS management script
├── Update-StagingDns.ps1      # PowerShell staging subdomain script
└── Export-CloudflareDns.ps1   # PowerShell DNS configuration export tool
```

## Deprecated Features

**Python Scripts**: This repository previously used Python scripts (`update_dns.py`, 
`export_zone_dns_summary.py`) for DNS management. These have been replaced with PowerShell scripts 
for better Windows integration and simplified dependency management. All DNS operations are now 
performed using PowerShell scripts.

**Terraform**: This repository also previously used Terraform for infrastructure management. 
Terraform support has been removed in favor of PowerShell scripts and the Cloudflare API for DNS 
management.

## Security

Security is a top priority for this project. We implement multiple security measures:

- **Automated Security Scanning**: CodeQL analysis for code vulnerabilities
- **Secret Detection**: GitHub secret scanning prevents credential exposure
- **Dependency Updates**: Dependabot keeps dependencies secure and up-to-date
- **CI Validation**: Automated checks for sensitive files and misconfigurations
- **API Token Security**: Cloudflare API tokens stored securely in GitHub Secrets

### Protecting Cloudflare API Tokens in Workflows

- **Least privilege**: Use `CLOUDFLARE_API_KEY_DNS_ONLY` scoped to only the zones you intend to manage.
- **Environment approvals**: Store tokens as Environment secrets (e.g., `cloudflare-prod`) and
  require reviewers before jobs run.
- **Apply gating**: Workflows default to `--dry-run`; set `apply=true` to make changes. Applies are
  blocked unless running on `main`.
- **Actor allowlist**: Set repository variable `ALLOWED_ACTORS` (comma-separated usernames) to
  restrict who can dispatch destructive jobs.
- **Branch protections**: Require PR reviews and status checks on `main` to prevent unreviewed
  changes.
- **Rotation**: Set token expiration in Cloudflare and rotate regularly; remove unused tokens.

Where to set Environment secrets (for both `cloudflare-prod` and `m365-prod`):

- See [docs/github-actions-environments-and-secrets.md](docs/github-actions-environments-and-secrets.md).

For details on our security practices and how to report vulnerabilities, see
[SECURITY.md](SECURITY.md).

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Code style and conventions
- Development workflow
- Pull request process
- Security requirements

## Workflows

This repository uses GitHub Actions for automation:

- **CI Workflow**: Validates configurations and checks for security issues
- **CodeQL Analysis**: Performs automated security scanning
- **DNS Summary Export**: Exports DNS configurations for reporting
- **Dependabot**: Keeps dependencies up-to-date

For more information, see [.github/workflows/README.md](.github/workflows/README.md).

## Best Practices

### Never Commit Sensitive Data

- **Do not commit**: API keys, tokens, credentials, or configuration files with real values
- **Use instead**: Environment variables or GitHub Secrets for sensitive data
- **Reference**: Check `.gitignore` to ensure sensitive files are excluded

### DNS Management Best Practices

- Always use issue templates for requesting changes
- Test changes with `--dry-run` flag before applying
- Document all DNS changes in the corresponding issue
- Verify DNS propagation after making changes
- Keep DNS records well-documented and up-to-date
- Use Cloudflare proxy (orange cloud) appropriately - disable for GitHub Pages

## Additional Resources

- **[Staging Subdomain Guide](STAGING_README.md)** - Detailed guide for managing staging subdomains
- **[Enhancement Ideas](ENHANCEMENTS.md)** - Potential future improvements and features
- **[Issue Templates](.github/ISSUE_TEMPLATE/)** - Templates for domain management requests
- [GitHub Pages Custom Domain Documentation](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
- [Cloudflare DNS Documentation](https://developers.cloudflare.com/dns/)
- [Cloudflare API Documentation](https://developers.cloudflare.com/api/)
- [GitHub Pages IP Addresses](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site#configuring-an-apex-domain)

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the
[LICENSE](LICENSE) file for details.

## Support

- **Domain Requests**: Use
  [issue templates](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues/new/choose)
  for domain management
- **Issues**: Report bugs or request features via
  [GitHub Issues](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues)
- **Documentation**: Check the guides and issue templates for detailed help
- **Security**: Report vulnerabilities via [SECURITY.md](SECURITY.md)

## About Free For Charity

Free For Charity is committed to using technology to support charitable giving. This automation
repository is part of our commitment to transparency and open-source development.
