# Contributing to FFC Cloudflare Automation

Thank you for your interest in contributing to Free For Charity's Cloudflare automation infrastructure! This document provides guidelines for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Development Workflow](#development-workflow)
- [Terraform Guidelines](#terraform-guidelines)
- [Security Guidelines](#security-guidelines)
- [Pull Request Process](#pull-request-process)
- [Questions and Support](#questions-and-support)

## Code of Conduct

Free For Charity is committed to fostering an open and welcoming environment. We expect all contributors to:

- Be respectful and inclusive
- Exercise empathy and kindness
- Focus on what is best for the community
- Accept constructive criticism gracefully
- Show courtesy and respect to other contributors

## Getting Started

### Prerequisites

Before contributing, ensure you have:

- Git installed and configured
- Terraform CLI (v1.6.0 or later)
- A GitHub account
- Basic understanding of Infrastructure as Code (IaC)
- Familiarity with Cloudflare (helpful but not required)

### Fork and Clone

1. Fork this repository to your GitHub account
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/FFC-Cloudflare-Automation-.git
   cd FFC-Cloudflare-Automation-
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
   ```

## How to Contribute

### Types of Contributions

We welcome various types of contributions:

- **Infrastructure Code**: Terraform configurations for Cloudflare resources
- **Documentation**: Improvements to README, guides, and inline comments
- **Bug Fixes**: Corrections to existing infrastructure code
- **Security Improvements**: Enhancements to security practices
- **Automation Scripts**: Helper scripts and tooling
- **CI/CD**: Improvements to GitHub Actions workflows

### Before You Start

1. **Check existing issues**: Look for related issues or discussions
2. **Create an issue**: If no issue exists, create one to discuss your proposed changes
3. **Wait for feedback**: Get agreement on the approach before starting significant work
4. **Assign yourself**: Assign the issue to yourself to avoid duplicate work

## Development Workflow

### 1. Create a Branch

Create a descriptive branch for your changes:

```bash
git checkout -b feature/descriptive-name
# or
git checkout -b fix/issue-description
```

### 2. Make Your Changes

- Write clear, documented code
- Follow existing code style and conventions
- Add comments for complex logic
- Update documentation as needed

### 3. Test Your Changes

```bash
# Format Terraform code
terraform fmt -recursive

# Validate Terraform configuration
terraform init -backend=false
terraform validate

# Plan changes (if applicable)
terraform plan
```

### 4. Commit Your Changes

Write clear, concise commit messages:

```bash
git add .
git commit -m "Add: Brief description of what was added/changed"
```

Commit message prefixes:
- `Add:` - New features or files
- `Fix:` - Bug fixes
- `Update:` - Updates to existing features
- `Refactor:` - Code refactoring
- `Docs:` - Documentation changes
- `Security:` - Security improvements

### 5. Keep Your Branch Updated

```bash
git fetch upstream
git rebase upstream/main
```

### 6. Push and Create Pull Request

```bash
git push origin your-branch-name
```

Then create a pull request on GitHub.

## Terraform Guidelines

### Code Style

- Use consistent formatting (enforced by `terraform fmt`)
- Use meaningful resource names
- Add descriptions to variables and outputs
- Group related resources together
- Use modules for reusable components

### Variables

```hcl
variable "example_var" {
  description = "Clear description of what this variable does"
  type        = string
  default     = "default-value"
  
  validation {
    condition     = length(var.example_var) > 0
    error_message = "Variable cannot be empty."
  }
}
```

### Resources

```hcl
resource "cloudflare_record" "example" {
  name    = "example"
  type    = "A"
  value   = "192.0.2.1"
  proxied = true
  
  # Clear comments for complex configurations
  comment = "Example DNS record"
}
```

### Documentation

- Document all modules with a README
- Include usage examples
- Document input variables and outputs
- Explain any non-obvious decisions

## Security Guidelines

**Critical**: Never commit sensitive data!

### What NOT to Commit

- API keys or tokens
- Passwords or credentials
- Private keys or certificates
- Actual `.tfvars` files with real values
- `.env` files with secrets

### What TO Do

- Use `.tfvars.example` files with placeholder values
- Document required environment variables
- Use Terraform Cloud/Enterprise for sensitive values
- Reference the `.gitignore` file
- Use GitHub Secrets for CI/CD credentials

### Security Checklist

Before submitting a PR:

- [ ] No hardcoded credentials or secrets
- [ ] `.tfvars` files are gitignored
- [ ] Sensitive variables are properly marked
- [ ] No accidentally committed sensitive files
- [ ] Security best practices followed

## Pull Request Process

### PR Requirements

1. **Clear title and description**: Explain what and why
2. **Reference issues**: Link to related issues
3. **Pass all CI checks**: Ensure workflows pass
4. **No merge conflicts**: Rebase if needed
5. **Security review**: No sensitive data committed

### PR Template

When creating a PR, include:

```markdown
## Description
Brief description of changes

## Related Issue
Fixes #123

## Changes Made
- Change 1
- Change 2
- Change 3

## Testing
Describe how you tested these changes

## Checklist
- [ ] Code follows project style guidelines
- [ ] Tests pass locally
- [ ] Documentation updated
- [ ] No sensitive data committed
- [ ] Security guidelines followed
```

### Review Process

1. A maintainer will review your PR
2. Address any feedback or requested changes
3. Once approved, a maintainer will merge your PR
4. Your contribution will be acknowledged!

## Questions and Support

### Getting Help

- **Issues**: Open an issue for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions
- **Documentation**: Check existing documentation first

### Response Times

- We aim to respond to issues within 1 week
- PRs are typically reviewed within 2 weeks
- Security issues receive priority attention

## Recognition

We value all contributions! Contributors will be:

- Acknowledged in release notes
- Added to the contributors list (if they wish)
- Thanked in the community
- Recognized on GitHub contributors page
- Featured in project documentation (for major features)

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (GNU AGPL v3).

---

Thank you for contributing to Free For Charity! Your efforts help us build better infrastructure for charitable giving.
