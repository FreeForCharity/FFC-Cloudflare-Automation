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

The simplest way to update DNS records using the Python utilities:

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# Update DNS record (example: staging subdomain)
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42
```

You'll be prompted for your Cloudflare API token, or you can set it as an environment variable:

```bash
export CLOUDFLARE_API_TOKEN="your_token_here"
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42
```

**👉 [See detailed staging subdomain guide →](STAGING_README.md)**

## Prerequisites

### For DNS Script Execution (Administrators)

- Python 3.9+
- PowerShell 5.1+ (optional, for `Update-StagingDns.ps1`)
- Cloudflare API token with DNS edit permissions
- Access to FFC Cloudflare account

## DNS Management Tools

The Python scripts in this repository provide flexible DNS record management for FFC domains.

### Basic Examples

**Update or create an A record:**

```bash
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42
```

**Update or create an AAAA record (IPv6):**

```bash
python update_dns.py --zone example.org --name @ --type AAAA --ip 2606:50c0:8000::153
```

**Update or create a CNAME record:**

```bash
python update_dns.py --zone example.org --name www --type CNAME --target example.org
```

**Search for existing records:**

```bash
python update_dns.py --zone example.org --name staging --type A --search
```

**Delete a specific record:**

```bash
python update_dns.py --zone example.org --record-id abc123xyz --delete
```

**Enable Cloudflare proxy (orange cloud):**

```bash
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --proxied
```

**Disable Cloudflare proxy (DNS only - required for GitHub Pages):**

```bash
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --no-proxy
```

**Dry run (preview changes without applying):**

```bash
python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --dry-run
```

### PowerShell Alternative

For quick subdomain updates:

```powershell
./Update-StagingDns.ps1 -NewIp 203.0.113.42
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

Use `export_zone_dns_summary.py` to export a CSV summarizing apex A/AAAA and `www` CNAME details for
specific zones. This tool is friendly to DNS-only tokens by accepting explicit zone names.

### Usage

```powershell
# Activate venv (if not already)
python -m venv .venv; .\.venv\Scripts\activate; pip install -r requirements.txt

# Provide your token via env (prefers CLOUDFLARE_API_KEY_READ_ALL, then CLOUDFLARE_API_KEY_DNS_ONLY)
$env:CLOUDFLARE_API_KEY_READ_ALL = "<read_all_token>"  # can list zones
# or
$env:CLOUDFLARE_API_KEY_DNS_ONLY = "<dns_only_token>"  # needs explicit zones/IDs

# Export for selected zones
python export_zone_dns_summary.py --zones ffcworkingsite1.org,freedomrisingusa.org,legioninthewoods.org,pagbooster.org --output zone_dns_summary.csv

# Or read zones from a file (one zone per line)
python export_zone_dns_summary.py --zones-file .\zones.txt --output zone_dns_summary.csv

# Export for all zones if your token can read zones
python export_zone_dns_summary.py --all-zones --output zone_dns_summary.csv

# If your token cannot read zone details, provide zone IDs directly
# (Get zone ID from Cloudflare Dashboard → Zone Overview)
python export_zone_dns_summary.py --zones ffcworkingsite1.org --zone-ids ffcworkingsite1.org=<zone_id> --output zone_dns_summary.csv
python export_zone_dns_summary.py --zones-file .\zones.txt --zone-id-file .\zone_ids.csv --output zone_dns_summary.csv
```

### CSV Columns

- `zone`: zone name
- `apex_a_ips`: semicolon-separated apex A IPs
- `apex_a_ttls`: semicolon-separated TTLs for apex A
- `apex_a_proxied`: semicolon-separated proxied flags (true/false)
- `apex_aaaa_ips`: semicolon-separated apex AAAA IPs
- `apex_aaaa_ttls`: semicolon-separated TTLs for apex AAAA
- `apex_aaaa_proxied`: semicolon-separated proxied flags
- `www_cname_target`: CNAME target for `www`
- `www_cname_ttl`: TTL for `www` CNAME
- `www_cname_proxied`: proxied flag for `www` CNAME
- `other_a_count`: count of non-apex A records
- `other_aaaa_count`: count of non-apex AAAA records
- `other_cname_count`: count of non-apex/`www` CNAME records

If your token lacks permission to list all zones, supply explicit zones with
`--zones`/`--zones-file`.

### GitHub Actions

- Secret: set `CLOUDFLARE_API_KEY_READ_ALL` (preferred) or `CLOUDFLARE_API_KEY_DNS_ONLY`.
- Workflow: `DNS Summary Export`.
  - Provide `zones` input to target specific zones, or set `all_zones=true` to export everything
    accessible to the token.
  - The workflow prefers `CLOUDFLARE_API_KEY_READ_ALL` and falls back to
    `CLOUDFLARE_API_KEY_DNS_ONLY`.

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
├── requirements.txt        # Python dependencies
├── update_dns.py           # Python DNS management script
├── export_zone_dns_summary.py  # DNS configuration export tool
├── export_zone_a_records.py    # A record export tool
└── Update-StagingDns.ps1   # PowerShell DNS script
```

## Deprecated Features

**Terraform**: This repository previously used Terraform for infrastructure management. Terraform
support has been removed in favor of Python scripts and the Cloudflare API for DNS management.

## Security

Security is a top priority for this project. We implement multiple security measures:

- **Automated Security Scanning**: CodeQL analysis for code vulnerabilities
- **Secret Detection**: GitHub secret scanning prevents credential exposure
- **Dependency Updates**: Dependabot keeps dependencies secure and up-to-date
- **CI Validation**: Automated checks for sensitive files and misconfigurations
- **API Token Security**: Cloudflare API tokens stored securely in GitHub Secrets

### Protecting Cloudflare API Tokens in Workflows

- **Least privilege**: Use `CLOUDFLARE_API_KEY_READ_ALL` for read-only workflows; use
  `CLOUDFLARE_API_KEY_DNS_ONLY` scoped to specific zones for DNS edits.
- **Environment approvals**: Store tokens as Environment secrets (e.g., `cloudflare-prod`) and
  require reviewers before jobs run.
- **Apply gating**: Workflows default to `--dry-run`; set `apply=true` to make changes. Applies are
  blocked unless running on `main`.
- **Actor allowlist**: Set repository variable `ALLOWED_ACTORS` (comma-separated usernames) to
  restrict who can dispatch destructive jobs.
- **Branch protections**: Require PR reviews and status checks on `main` to prevent unreviewed
  changes.
- **Rotation**: Set token expiration in Cloudflare and rotate regularly; remove unused tokens.

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
