# FFC-Cloudflare-Automation-
A repository for the documentation and automation via Terraform supporting FreeForCharity and our use of Cloudflare.

## 🌐 GitHub Pages

View our automation tracking page: [FFC Cloudflare Automation Tracker](https://freeforcharity.github.io/FFC-Cloudflare-Automation-/)

The static site provides an overview of the Terraform automation, current status, key features, and helpful resources.

## 📋 About

This repository contains Infrastructure as Code (IaC) using Terraform to manage FreeForCharity's Cloudflare configuration and infrastructure.
# FFC Cloudflare Automation

[![CodeQL](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/codeql-analysis.yml)
[![CI](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml/badge.svg)](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

A repository for the documentation and automation via Terraform supporting Free For Charity and our use of Cloudflare.

## Overview

This repository contains Infrastructure as Code (IaC) configurations for managing Free For Charity's Cloudflare resources. Using Terraform, we automate the provisioning, configuration, and maintenance of our Cloudflare infrastructure.

## Features

- **Infrastructure as Code**: Declarative configuration using Terraform
- **Automated Security Scanning**: CodeQL security analysis on every commit
- **Continuous Validation**: Automated Terraform validation and formatting checks
- **Dependency Management**: Automated dependency updates via Dependabot
- **Version Control**: Full audit trail of infrastructure changes

## Getting Started

### Prerequisites

- [Terraform](https://www.terraform.io/downloads.html) (v1.6.0 or later)
- [Git](https://git-scm.com/downloads)
- Access to Cloudflare account (for contributors)

### Local Development

1. Clone the repository:
   ```bash
   git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
   cd FFC-Cloudflare-Automation-
   ```

2. Initialize Terraform (when Terraform files are added):
   ```bash
   terraform init
   ```

3. Validate configurations:
   ```bash
   terraform validate
   ```

4. Format code:
   ```bash
   terraform fmt -recursive
   ```

## Repository Structure

```
.
├── .github/
│   ├── workflows/          # GitHub Actions workflows
│   │   ├── ci.yml          # Continuous Integration
│   │   ├── codeql-analysis.yml  # Security scanning
│   │   └── README.md       # Workflow documentation
│   └── dependabot.yml      # Dependency update configuration
├── CONTRIBUTING.md         # Contribution guidelines
├── LICENSE                 # GNU AGPL v3 license
├── README.md              # This file
└── SECURITY.md            # Security policy
```

## Security

Security is a top priority for this project. We implement multiple security measures:

- **Automated Security Scanning**: CodeQL analysis runs on every commit
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

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues)
- **Documentation**: Check [CONTRIBUTING.md](CONTRIBUTING.md) for development help
- **Security**: Report vulnerabilities via [SECURITY.md](SECURITY.md)

## About Free For Charity

Free For Charity is committed to using technology to support charitable giving. This infrastructure repository is part of our commitment to transparency and open-source development.

---

**Note**: This repository is under active development. Infrastructure configurations will be added as the project evolves.
# FFC-Cloudflare-Automation

A Terraform automation repository for configuring CloudFlare DNS settings to work with GitHub Pages custom domains. This automation simplifies the process of pointing a CloudFlare-managed domain to a GitHub Pages site.

## Overview

This Terraform configuration automatically sets up all the necessary DNS records and CloudFlare settings required for GitHub Pages custom domain support, including:

- **A Records**: Creates 4 A records pointing to GitHub Pages IP addresses
- **CNAME Record**: Creates a www subdomain CNAME record
- **SSL/TLS Settings**: Configures optimal SSL/TLS settings for GitHub Pages
- **HTTPS Redirect**: Sets up automatic HTTPS redirects
- **Optional CAA Records**: For SSL certificate management

### Security Scanning

This repository includes automated security scanning for Infrastructure-as-Code:
- **tfsec** - Static analysis for Terraform security issues
- **Checkov** - Policy-as-code scanner for IaC misconfigurations
- **Trivy** - Vulnerability scanner for IaC
- **Terraform Validate** - Built-in validation for syntax and logic

Security scans run automatically on all pull requests and pushes. See [SECURITY.md](SECURITY.md) for more details.

## Prerequisites

Before using this automation, you need:

1. **Terraform**: Install Terraform (version 1.6.0 or later)
   - Download from: https://www.terraform.io/downloads
   - Or use a package manager (e.g., `brew install terraform` on macOS)

2. **CloudFlare Account**: With a domain already added to CloudFlare
   - Your domain's nameservers must be pointing to CloudFlare

3. **CloudFlare API Token**: Create an API token with the following permissions:
   - Zone: DNS: Edit
   - Zone: Zone Settings: Edit
   - Zone: Zone: Read
   
   Create a token at: https://dash.cloudflare.com/profile/api-tokens

4. **GitHub Pages Site**: A GitHub Pages site that you want to configure the custom domain for

## Deployment Methods

This repository supports **two deployment approaches**:

### Option 1: GitHub Actions (Recommended for Teams) 🚀

Use GitHub Secrets and automated workflows for secure, team-based deployments.

**Benefits**:
- ✅ Token stored securely in GitHub Secrets (encrypted)
- ✅ No credentials on local machines
- ✅ Automated PR validation with Terraform plans
- ✅ Audit trail of all deployments
- ✅ Team collaboration without sharing tokens

**Quick Start**: See **[GITHUB_ACTIONS.md](GITHUB_ACTIONS.md)** for complete setup

### Option 2: Local Terraform (For Individual Development)

Use local `terraform.tfvars` file for manual deployments.

**Benefits**:
- ✅ Simple setup for individual developers
- ✅ Direct control over deployments
- ✅ Good for learning and testing

**Quick Start**: See instructions below

---

## Quick Start (Local Deployment)

### 1. Clone this repository

```bash
git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
cd FFC-Cloudflare-Automation-
```

### 2. Configure your variables

Copy the example variables file and edit it with your values:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set your values:

```hcl
cloudflare_api_token = "your-cloudflare-api-token-here"
domain_name         = "example.com"
github_pages_domain = "username.github.io"
```

### 3. Initialize Terraform

```bash
terraform init
```

This downloads the CloudFlare provider and initializes the Terraform working directory.

### 4. Review the planned changes

```bash
terraform plan
```

This shows you what changes Terraform will make without actually making them.

### 5. Apply the configuration

```bash
terraform apply
```

Review the planned changes and type `yes` to proceed. Terraform will:
- Create DNS A records for GitHub Pages
- Create a www CNAME record
- Configure SSL/TLS settings
- Set up HTTPS redirect rules

### 6. Configure GitHub Pages

After Terraform completes:

1. Go to your GitHub repository settings
2. Navigate to **Pages** section
3. Under **Custom domain**, enter your domain (e.g., `example.com`)
4. Wait for DNS check to complete (may take a few minutes)
5. Enable **Enforce HTTPS** once DNS is verified

## Configuration Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `cloudflare_api_token` | CloudFlare API token | `"abc123..."` |
| `domain_name` | Your domain managed by CloudFlare | `"example.com"` |
| `github_pages_domain` | Your GitHub Pages domain | `"username.github.io"` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `github_pages_ips` | GitHub Pages IP addresses | `["185.199.108.153", ...]` |
| `dns_ttl` | DNS record TTL (1 = automatic) | `1` |
| `proxied` | Proxy through CloudFlare CDN | `false` |
| `create_www_record` | Create www subdomain | `true` |
| `create_https_redirect` | Create HTTPS redirect rule | `true` |
| `configure_ssl_settings` | Configure SSL/TLS settings | `true` |
| `ssl_mode` | SSL mode (off/flexible/full/strict) | `"full"` |
| `always_use_https` | Always redirect to HTTPS | `true` |
| `automatic_https_rewrites` | Auto HTTPS rewrites | `true` |
| `min_tls_version` | Minimum TLS version | `"1.2"` |
| `create_caa_records` | Create CAA records | `false` |

## Terraform Commands

### View current state

```bash
terraform show
```

### View outputs

```bash
terraform output
```

### Update configuration

After modifying variables or configuration:

```bash
terraform plan
terraform apply
```

### Destroy resources

To remove all CloudFlare settings created by this automation:

```bash
terraform destroy
```

**Warning**: This will delete all DNS records and settings created by Terraform.

## DNS Records Created

This automation creates the following DNS records:

### A Records (Root Domain)

Four A records pointing to GitHub Pages IP addresses:
- `185.199.108.153`
- `185.199.109.153`
- `185.199.110.153`
- `185.199.111.153`

### CNAME Record (www subdomain)

- `www.example.com` → `username.github.io`

## Troubleshooting

### DNS not propagating

DNS changes can take time to propagate. You can check DNS records using:

```bash
dig example.com A
dig www.example.com CNAME
```

Or use online tools like https://dnschecker.org

### GitHub Pages DNS check failing

1. Ensure all A records are created correctly
2. Wait 10-15 minutes for DNS propagation
3. Try removing and re-adding the custom domain in GitHub
4. Check that your domain's nameservers are pointing to CloudFlare

### SSL Certificate issues

- Make sure `ssl_mode` is set to `"full"` (not `"flexible"`)
- Ensure HTTPS is enabled in GitHub Pages settings
- Wait for CloudFlare's Universal SSL certificate to activate (can take up to 24 hours)

### CloudFlare API errors

- Verify your API token has the correct permissions
- Check that the token hasn't expired
- Ensure the domain exists in your CloudFlare account

## Security Best Practices

1. **Never commit `terraform.tfvars`**: This file contains your API token
2. **Use environment variables**: Alternative to `terraform.tfvars`:
   ```bash
   export TF_VAR_cloudflare_api_token="your-token"
   export TF_VAR_domain_name="example.com"
   export TF_VAR_github_pages_domain="username.github.io"
   terraform apply
   ```
3. **Use scoped API tokens**: Only grant necessary permissions
4. **Rotate tokens regularly**: Create new tokens periodically
5. **Enable two-factor authentication**: On your CloudFlare account

## Advanced Usage

### Using with Terraform Cloud

Store your API token securely:

1. Create a Terraform Cloud workspace
2. Add `cloudflare_api_token` as a sensitive variable
3. Connect your repository
4. Configure automatic runs

### Multiple Domains

To manage multiple domains, create separate directories:

```
domain1/
  main.tf
  terraform.tfvars
domain2/
  main.tf
  terraform.tfvars
```

Or use Terraform workspaces:

```bash
terraform workspace new domain1
terraform workspace new domain2
terraform workspace select domain1
terraform apply
```

### Custom IP Addresses

If GitHub Pages IP addresses change, override them:

```hcl
github_pages_ips = [
  "new.ip.address.1",
  "new.ip.address.2",
  "new.ip.address.3",
  "new.ip.address.4"
]
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues or questions:
- Open an issue in this repository
- Check GitHub Pages documentation: https://docs.github.com/en/pages
- Check CloudFlare documentation: https://developers.cloudflare.com

## License

See [LICENSE](LICENSE) file for details.

## Additional Resources

- [GitHub Pages Custom Domain Documentation](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
- [CloudFlare DNS Documentation](https://developers.cloudflare.com/dns/)
- [Terraform CloudFlare Provider](https://registry.terraform.io/providers/cloudflare/cloudflare/latest/docs)
- [GitHub Pages IP Addresses](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site#configuring-an-apex-domain)

## About Free For Charity

This repository supports Free For Charity and our use of CloudFlare for domain management and automation.
