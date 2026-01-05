# Security Policy

## Automated Security Scanning

This repository includes automated security scanning using industry-standard tools:

### Security Scanning Tools

| Tool           | Purpose                                  | Runs On            |
| -------------- | ---------------------------------------- | ------------------ |
| **CodeQL**     | Static analysis for code security issues | All PRs and pushes |
| **Dependabot** | Dependency vulnerability scanning        | Automated checks   |

### Security Workflow

Security scanning automatically runs on:

- Every push to `main` or `master` branch
- Every pull request targeting `main` or `master` branch
- Automated dependency checks

**Results** are uploaded to GitHub Security tab under Code Scanning alerts, where they can be:

- Reviewed and triaged
- Tracked over time
- Integrated with branch protection rules

---

## Supported Versions

We are committed to maintaining the security of this project. Currently, we support security updates
for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Reporting a Vulnerability

The Free For Charity team takes security vulnerabilities seriously. We appreciate your efforts to
responsibly disclose your findings.

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
- **Resolution**: We aim to release fixes for confirmed vulnerabilities as quickly as possible,
  depending on complexity

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
   - Environment files (`.env`, `.env.local`)

2. **Use secure coding practices**:

   - Follow the principle of least privilege
   - Validate all inputs
   - Keep dependencies updated

3. **Review the `.gitignore`**:
   - Ensure sensitive files are excluded
   - Never force-add ignored files

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

For sensitive security matters, please contact the Free For Charity team through the official
channels listed on the [Free For Charity website](https://freeforcharity.org).

## Attribution

We appreciate the security research community and will acknowledge researchers who report valid
vulnerabilities (with their permission).

## Sensitive Information Handling

### Cloudflare API Tokens

**⚠️ CRITICAL: Never commit API tokens to version control**

#### How to Securely Handle API Tokens

1. **From Cloudflare Dashboard**:

   - Log in to https://dash.cloudflare.com
   - Go to "My Profile" → "API Tokens"
   - Create a new token or use an existing one
   - Store it securely using environment variables

2. **Use Environment Variables**:

   ```powershell
   $env:CLOUDFLARE_API_TOKEN = "your-token-here"
   .\Update-CloudflareDns.ps1 -Zone example.org -Name staging -Type A -Content 203.0.113.42
   ```

3. **Use GitHub Secrets for CI/CD**:
   - Store tokens as repository secrets
   - Never expose them in workflow logs
   - Use minimal permissions

#### What to Do If a Token is Exposed

If an API token is accidentally committed or exposed:

1. **Immediately revoke the token** in Cloudflare dashboard
2. **Generate a new API token** with the same permissions
3. **Update your environment variables** with the new token
4. **Notify your team** of the security incident
5. **Review git history** and remove exposed tokens if possible

#### Best Practices

✅ **DO**:

- Store tokens in environment variables or GitHub Secrets
- Use scoped tokens with minimal required permissions
- Rotate tokens regularly (every 90 days recommended)
- Use different tokens for different environments (dev/staging/prod)
- Review `.gitignore` before committing changes

❌ **DON'T**:

- Commit tokens to git repositories
- Share tokens via email, Slack, or other unsecured channels
- Use tokens in documentation or README files
- Hard-code tokens in Python scripts
- Use the same token across multiple projects

---

## Security Checklist

### For Development:

- [ ] API tokens stored in environment variables or GitHub Secrets
- [ ] `.gitignore` includes sensitive files
- [ ] No tokens in documentation files
- [ ] No tokens in example files
- [ ] Git history doesn't contain exposed tokens
- [ ] Tokens have minimal required permissions
- [ ] Tokens are scoped to specific domains only

### For GitHub Actions/CI/CD:

- [ ] Cloudflare API tokens added to GitHub Secrets
- [ ] Branch protection enabled on main branch
- [ ] Required reviews configured
- [ ] Workflows don't echo secrets
- [ ] Team members trained on security practices

## Additional Resources

- [Cloudflare API Token Documentation](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Git Security Best Practices](https://git-scm.com/book/en/v2/GitHub-Account-Administration-and-Security)

---

**Remember**: Security is everyone's responsibility. When in doubt, ask before committing!
