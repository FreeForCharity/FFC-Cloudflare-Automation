# Security Policy

## Supported Versions

We are committed to maintaining the security of this project. Currently, we support security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Reporting a Vulnerability

The Free For Charity team takes security vulnerabilities seriously. We appreciate your efforts to responsibly disclose your findings.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities by:

1. **GitHub Security Advisories** (Preferred):
   - Navigate to the Security tab of this repository
   - Click "Report a vulnerability"
   - Fill out the form with details about the vulnerability

2. **Email**:
   - Send an email to the Free For Charity security team
   - Include detailed information about the vulnerability
   - Include steps to reproduce if possible

### What to Include

When reporting a vulnerability, please include:

- Type of vulnerability (e.g., exposed credentials, insecure configuration)
- Full paths of affected source files
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

### Response Timeline

- **Initial Response**: Within 48 hours of receiving your report
- **Status Update**: Within 7 days with our evaluation and expected resolution timeline
- **Resolution**: We aim to release fixes for confirmed vulnerabilities as quickly as possible, depending on complexity

### Safe Harbor

We support safe harbor for security researchers who:

- Make a good faith effort to avoid privacy violations, data destruction, and service interruptions
- Only interact with accounts you own or with explicit permission of the account holder
- Do not exploit a security issue beyond what is necessary to demonstrate it
- Report vulnerabilities as soon as possible after discovery
- Keep vulnerability details confidential until we've had a reasonable time to address them

## Security Best Practices

When contributing to or using this repository:

### For Contributors

1. **Never commit sensitive data**:
   - API keys, tokens, or credentials
   - Private keys or certificates
   - `.tfvars` files with actual values
   - Environment files (`.env`, `.env.local`)

2. **Use secure coding practices**:
   - Follow the principle of least privilege
   - Validate all inputs
   - Use parameterized queries
   - Keep dependencies updated

3. **Review the `.gitignore`**:
   - Ensure sensitive files are excluded
   - Never force-add ignored files

4. **Use Terraform best practices**:
   - Store sensitive values in Terraform Cloud/Enterprise
   - Use input variables for configuration
   - Encrypt state files
   - Use remote backends with encryption

### For Users

1. **Protect your credentials**:
   - Never commit credentials to version control
   - Use environment variables or secret management systems
   - Rotate credentials regularly
   - Use unique credentials per environment

2. **Review permissions**:
   - Follow least privilege principles
   - Regularly audit access
   - Remove unused credentials

3. **Keep up to date**:
   - Monitor security advisories
   - Update dependencies regularly
   - Subscribe to repository notifications

## Automated Security

This repository uses several automated security measures:

- **CodeQL Analysis**: Automated code scanning for security vulnerabilities
- **Dependency Scanning**: Automated checks for vulnerable dependencies (Dependabot)
- **Secret Scanning**: GitHub's secret scanning to detect committed secrets
- **CI Validation**: Automated checks for sensitive files in pull requests

## Security Contacts

For sensitive security matters, please contact the Free For Charity team through the official channels listed on the [Free For Charity website](https://freeforcharity.org).

## Attribution

We appreciate the security research community and will acknowledge researchers who report valid vulnerabilities (with their permission).
