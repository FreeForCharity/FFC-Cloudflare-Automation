# FFC-Cloudflare-Automation
[![CodeQL](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml)
[![CI](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Automation utilities for Cloudflare tasks supporting Free For Charity.

## 🌐 GitHub Pages

View our automation tracking page: [FFC Cloudflare Automation Tracker](https://freeforcharity.github.io/FFC-Cloudflare-Automation-/)

The static site provides an overview of the Terraform automation, current status, key features, and helpful resources.

## Overview

This repository contains Infrastructure as Code (IaC) using Terraform to manage FreeForCharity's Cloudflare configuration and infrastructure, as well as Python utilities for DNS management.

## Features

### Terraform Infrastructure
- **Infrastructure as Code**: Declarative Cloudflare configuration using Terraform
- **GitHub Pages Integration**: Automated DNS setup for custom domains
- **SSL/TLS Management**: Automated security configuration
- **Multiple Deployment Methods**: Local Terraform or GitHub Actions
- **Automated Security Scanning**: CodeQL, tfsec, Checkov, and Trivy
- **Continuous Validation**: Automated Terraform validation and formatting checks
- **Version Control**: Full audit trail of infrastructure changes

### DNS Management Utilities
- **Python Script**: Flexible DNS record management for `clarkemoyer.com` zone
- **Create, update, search, and delete** DNS records
- **Supports A and CNAME** record types
- **Dry-run mode** to preview changes
- **Cloudflare proxy** (orange cloud) support
- **Secure token handling** via environment variable, argument, or prompt
- **PowerShell Alternative**: Quick staging subdomain updates

## Quick Start

### For Staging DNS Updates

The simplest way to update DNS records for `staging.clarkemoyer.com`:

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# Update staging subdomain IP address
python update_dns.py --name staging --type A --ip 203.0.113.42
```

You'll be prompted for your Cloudflare API token, or you can set it as an environment variable:

```bash
export CLOUDFLARE_API_TOKEN="your_token_here"
python update_dns.py --name staging --type A --ip 203.0.113.42
```

**👉 [See detailed staging subdomain guide →](STAGING_README.md)**

### For Terraform Automation

**Quick setup in 5 steps:**

1. Clone and configure:
   ```bash
   git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
   cd FFC-Cloudflare-Automation-
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Edit `terraform.tfvars` with your values

3. Initialize Terraform:
   ```bash
   terraform init
   ```

4. Apply configuration:
   ```bash
   terraform apply
   ```

5. Configure GitHub Pages custom domain

**👉 [See full setup guide →](SETUP_GUIDE.md)**  
**👉 [See quick start guide →](QUICK_START.md)**

## Prerequisites

### For Terraform
- [Terraform](https://www.terraform.io/downloads.html) (v1.6.0 or later)
- [Git](https://git-scm.com/downloads)
- Cloudflare account with domain added
- Cloudflare API token with DNS permissions

### For DNS Scripts
- Python 3.9+ (for `update_dns.py`)
- PowerShell 5.1+ (for `Update-StagingDns.ps1`)
- Cloudflare API token

## DNS Management Tool

The `update_dns.py` script provides flexible DNS record management for the `clarkemoyer.com` zone.

### Basic Examples

**Update or create an A record:**
```bash
python update_dns.py --name staging --type A --ip 203.0.113.42
```

**Update or create a CNAME record:**
```bash
python update_dns.py --name www --type CNAME --target example.com
```

**Search for existing records:**
```bash
python update_dns.py --name staging --type A --search
```

**Delete a specific record:**
```bash
python update_dns.py --record-id abc123xyz --delete
```

**Enable Cloudflare proxy (orange cloud):**
```bash
python update_dns.py --name staging --type A --ip 203.0.113.42 --proxied
```

**Dry run (preview changes without applying):**
```bash
python update_dns.py --name staging --type A --ip 203.0.113.42 --dry-run
```

### PowerShell Alternative

For staging subdomain updates only:

```powershell
./Update-StagingDns.ps1 -NewIp 203.0.113.42
```

**👉 [See PowerShell details in staging guide →](STAGING_README.md)**

## Terraform Deployment Methods

This repository supports **two deployment approaches**:

### Option 1: GitHub Actions (Recommended for Teams) 🚀

Use GitHub Secrets and automated workflows for secure, team-based deployments.

**Benefits**:
- ✅ Token stored securely in GitHub Secrets (encrypted)
- ✅ No credentials on local machines
- ✅ Automated PR validation with Terraform plans
- ✅ Audit trail of all deployments
- ✅ Team collaboration without sharing tokens

**👉 [See GitHub Actions setup guide →](GITHUB_ACTIONS.md)**

### Option 2: Local Terraform (For Individual Development)

Use local `terraform.tfvars` file for manual deployments.

**Benefits**:
- ✅ Simple setup for individual developers
- ✅ Direct control over deployments
- ✅ Good for learning and testing

**👉 [See setup guide →](SETUP_GUIDE.md)**

## Repository Structure

```
.
├── .github/
│   ├── workflows/          # GitHub Actions workflows
│   │   ├── ci.yml          # Continuous Integration
│   │   ├── codeql-analysis.yml  # Security scanning
│   │   └── README.md       # Workflow documentation
│   └── dependabot.yml      # Dependency update configuration
├── examples/               # Example Terraform configurations
├── CONTRIBUTING.md         # Contribution guidelines
├── DEPLOYMENT_CHECKLIST.md # Deployment checklist for ffcadmin.org
├── FFCADMIN_README.md      # Specific guide for ffcadmin.org
├── GITHUB_ACTIONS.md       # GitHub Actions deployment guide
├── LICENSE                 # GNU AGPL v3 license
├── QUICK_START.md          # 5-minute quick start guide
├── README.md               # This file
├── SECURITY.md             # Security policy
├── SETUP_GUIDE.md          # Detailed setup walkthrough
├── STAGING_README.md       # Staging subdomain management guide
├── TESTING.md              # Testing guide
├── main.tf                 # Main Terraform configuration
├── outputs.tf              # Terraform outputs
├── requirements.txt        # Python dependencies
├── update_dns.py           # Python DNS management script
├── Update-StagingDns.ps1   # PowerShell DNS script
├── variables.tf            # Terraform variables
└── versions.tf             # Terraform version constraints
```

## Security

Security is a top priority for this project. We implement multiple security measures:

- **Automated Security Scanning**: CodeQL, tfsec, Checkov, and Trivy analysis
- **Secret Detection**: GitHub secret scanning prevents credential exposure
- **Dependency Updates**: Dependabot keeps dependencies secure and up-to-date
- **CI Validation**: Automated checks for sensitive files and misconfigurations

For details on our security practices and how to report vulnerabilities, see [SECURITY.md](SECURITY.md).

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Code style and conventions
- Development workflow
- Pull request process
- Security requirements

## Workflows

This repository uses GitHub Actions for automation:

- **CI Workflow**: Validates Terraform configurations and checks for security issues
- **CodeQL Analysis**: Performs automated security scanning
- **Dependabot**: Keeps dependencies up-to-date

For more information, see [.github/workflows/README.md](.github/workflows/README.md).

## Best Practices

### Never Commit Sensitive Data

- **Do not commit**: API keys, tokens, credentials, `.tfvars` files with real values
- **Use instead**: Environment variables, Terraform Cloud, or secret management systems
- **Reference**: Check `.gitignore` to ensure sensitive files are excluded

### Terraform Conventions

- Use meaningful resource names
- Add descriptions to all variables
- Follow formatting standards (`terraform fmt`)
- Document complex configurations
- Use modules for reusable components

## Additional Resources

- **[Staging Subdomain Guide](STAGING_README.md)** - Detailed guide for managing staging.clarkemoyer.com
- **[Deployment Checklist](DEPLOYMENT_CHECKLIST.md)** - Checklist for ffcadmin.org deployment
- **[FFCAdmin Guide](FFCADMIN_README.md)** - Specific guide for ffcadmin.org domain
- **[Testing Guide](TESTING.md)** - Testing guide for deployments
- **[Configuration Examples](examples/README.md)** - Example Terraform configurations
- **[verify_old_token.json.README.md](verify_old_token.json.README.md)** - Information about the test token response file
- [GitHub Pages Custom Domain Documentation](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
- [Cloudflare DNS Documentation](https://developers.cloudflare.com/dns/)
- [Terraform Cloudflare Provider](https://registry.terraform.io/providers/cloudflare/cloudflare/latest/docs)
- [GitHub Pages IP Addresses](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site#configuring-an-apex-domain)

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues)
- **Documentation**: Check the guides linked above for detailed help
- **Security**: Report vulnerabilities via [SECURITY.md](SECURITY.md)

## About Free For Charity

Free For Charity is committed to using technology to support charitable giving. This infrastructure repository is part of our commitment to transparency and open-source development.

---

**Note**: This repository is under active development. Infrastructure configurations will be added as the project evolves.
