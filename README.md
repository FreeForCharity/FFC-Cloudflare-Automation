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
