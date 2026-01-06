# Contributing to FFC Cloudflare Automation

Thank you for your interest in contributing to Free For Charity's Cloudflare automation
infrastructure! This document provides guidelines for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Tooling](#tooling)
- [How to Contribute](#how-to-contribute)
- [Agent-First Contribution Policy](#agent-first-contribution-policy)
- [Development Workflow](#development-workflow)
- [Copilot Review Expectations](#copilot-review-expectations)
- [Agent Operating Guidelines](#agent-operating-guidelines)
- [Working with Agents](#working-with-agents)
- [PowerShell Guidelines](#powershell-guidelines)
- [Security Guidelines](#security-guidelines)
- [Pull Request Process](#pull-request-process)
- [Questions and Support](#questions-and-support)

## Code of Conduct

Free For Charity is committed to fostering an open and welcoming environment. We expect all
contributors to:

- Be respectful and inclusive
- Exercise empathy and kindness
- Focus on what is best for the community
- Accept constructive criticism gracefully
- Show courtesy and respect to other contributors

## Getting Started

### Prerequisites

Before contributing, ensure you have:

- Git installed and configured
- PowerShell 5.1+ (Windows) or PowerShell 7+ (cross-platform)
- A GitHub account
- Familiarity with Cloudflare (helpful but not required)

### Fork and Clone

1. Fork this repository to your GitHub account
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/FFC-Cloudflare-Automation.git
   cd FFC-Cloudflare-Automation
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/FreeForCharity/FFC-Cloudflare-Automation.git
   ```

## Tooling

### Recommended: GitHub Copilot Pro (or Business)

This repository increasingly uses automation and agents to accelerate development. We recommend
**GitHub Copilot Pro** (or GitHub Copilot Business) as the minimum tooling for effective
contribution:

- **Agent Mode**: Use Copilot in VS Code or GitHub UI to assist with code changes, documentation,
  and routine tasks
- **PR Reviews**: Copilot can provide automated review suggestions on pull requests
- **Iterative Fixes**: Agents can help iterate on feedback and fix issues quickly

**Note**: Contributors without GitHub Copilot can still contribute successfully, but must follow the
same process and quality standards. Maintainers expect agent-capable workflows (review suggestions,
generated diffs, iterative fixes) to be the norm.

## How to Contribute

### Types of Contributions

We welcome various types of contributions:

- **PowerShell Scripts**: DNS management scripts and utilities
- **Documentation**: Improvements to README, guides, and inline comments
- **Bug Fixes**: Corrections to existing code
- **Security Improvements**: Enhancements to security practices
- **Automation Scripts**: Helper scripts and tooling
- **CI/CD**: Improvements to GitHub Actions workflows

### Before You Start

1. **Check existing issues**: Look for related issues or discussions
2. **Create an issue**: If no issue exists, create one to discuss your proposed changes
3. **Wait for feedback**: Get agreement on the approach before starting significant work
4. **Assign yourself**: Assign the issue to yourself to avoid duplicate work

## Agent-First Contribution Policy

This repository embraces automation and AI-assisted development to accelerate contributions while
maintaining quality and safety.

### Agents Are Encouraged

We welcome and encourage the use of AI agents (like GitHub Copilot in Agent Mode) for:

- **Documentation updates**: README, guides, inline comments
- **Script improvements**: PowerShell utilities, automation scripts
- **Workflow enhancements**: GitHub Actions, CI/CD pipelines
- **Routine maintenance**: Formatting, linting fixes, dependency updates
- **Bug fixes**: Addressing issues with clear scope

### Humans Stay in Control

While agents accelerate work, humans remain responsible for:

- **Defining scope**: Clear problem statements and acceptance criteria
- **Approval decisions**: Final review and merge decisions
- **Quality standards**: Ensuring changes meet security and maintainability bars
- **Strategic direction**: Architecture and design choices

### Small, Reviewable Increments

Whether using agents or working manually:

- **Single-purpose PRs**: Each PR should address one issue or feature
- **Avoid scope creep**: Resist the temptation to fix unrelated issues
- **Reviewable diffs**: Keep changes small enough for effective human review
- **Iterative approach**: Prefer multiple small PRs over one large rewrite

## Development Workflow

### Required Workflow: Issue → PR → Review → Merge

All contributions must follow this workflow:

1. **Open an issue first** (GitHub UI or CLI):
   ```bash
   gh issue create --title "Brief description" --body "Detailed context"
   ```
2. **Create a branch** for your work (never push directly to `main`)
3. **Open a pull request** linking to the issue (`Fixes #NNN` or `Refs #NNN`)
4. **Before merge**, ensure:
   - ✅ CI checks pass (linting, formatting, tests)
   - ✅ GitHub Copilot PR review requested and feedback addressed
   - ✅ Human maintainer approval (as applicable)

### Pull Requests Required

All changes must be made through a pull request (PR). **Never push directly to `main`**, even for
small fixes, so reviews and checks are consistently applied.

### Issues Before PRs

**You must create (or reference) a GitHub issue before opening a PR.**

- **Small changes**: The issue can be lightweight, but it must exist
- **Large changes**: The issue should include context, acceptance criteria, and discussion
- **PRs must reference their issue** in the description (e.g., `Fixes #123` or `Refs #123`)

You can create issues via:

- GitHub web UI
- GitHub CLI: `gh issue create --title "..." --body "..."`
- GitHub mobile app

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

```powershell
# Test PowerShell scripts
Get-Help .\Update-CloudflareDns.ps1 -Detailed
Get-Help .\Update-StagingDns.ps1 -Detailed
Get-Help .\Export-CloudflareDns.ps1 -Detailed
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

## Copilot Review Expectations

Before merging any PR, you must request and address feedback from GitHub Copilot's PR review
feature. This is an additional automated reviewer that helps catch issues early.

### How to Request Copilot Review

1. Open your PR on GitHub
2. Request a review from GitHub Copilot (if available in your organization)
3. Wait for Copilot to analyze the changes and provide feedback
4. Address any valid concerns raised

### What Copilot Review Checks

Treat Copilot review as an additional automated reviewer that checks for:

- **Correctness**: Logic errors, edge cases, potential bugs
- **Security**: Secrets in code, logging sensitive data, unsafe patterns
- **Best practices**: Code style, formatting, CI compliance
- **Maintainability**: Code clarity, documentation, test coverage

### Important Notes

- ✅ **Copilot review is in addition to CI/tests**, not a replacement
- ✅ **Address valid feedback** before requesting human review
- ✅ **Use judgment**: Not all Copilot suggestions may be applicable
- ✅ **Human review still required**: Copilot review does not replace human maintainer approval

## Agent Operating Guidelines

These guidelines help AI agents (and humans using agents) contribute effectively and safely.

### Keep PRs Small and Scoped

- **Single purpose**: Address one issue or feature per PR
- **Avoid drive-by refactors**: Don't fix unrelated issues unless they're in scope
- **Reviewable size**: Keep diffs small enough for effective human review

### Prefer Root Cause Fixes

- **Fix the underlying issue**: Don't just patch symptoms
- **Avoid scope creep**: Don't add "nice to have" features unless explicitly in scope
- **Stay focused**: Complete the task as specified, then stop

### Follow Established Patterns

- **Read existing docs first**: Check README, CONTRIBUTING, and related docs before editing
- **Match existing style**: Follow patterns already in the codebase
- **Be consistent**: Use the same approaches as similar existing code

### Security Best Practices

- **Never print or log secrets**: Redact tokens, API keys, passwords in diagnostics
- **No hardcoded credentials**: Always use environment variables or secrets management
- **Validate inputs**: Sanitize user inputs, especially in workflows and scripts

### Workflow-Specific Guidelines

When changing GitHub Actions workflows:

- **Least-privilege permissions**: Only grant permissions actually needed
- **Avoid unsafe interpolation**: Never use `${{ github.event.issue.title }}` or similar untrusted
  inputs directly in shell commands
- **Deterministic outputs**: Prefer predictable, reproducible builds and artifacts
- **Test workflow changes**: Use workflow_dispatch or test branches when possible

### PowerShell-Specific Guidelines

When changing PowerShell scripts:

- **Run formatters**: Use `scripts/format-powershell.ps1` to format code
- **Run linters**: Use `scripts/analyze-powershell.ps1` to check for issues
- **PSScriptAnalyzer compliance**: Fix errors, address warnings when practical
- **Invoke-Formatter compliance**: Ensure code passes formatting checks

## Working with Agents

Guidance for humans collaborating with AI agents to get the best results.

### Provide Clear Scope

- **Crisp acceptance criteria**: Define what "done" looks like before starting
- **Explicit constraints**: Call out what should NOT be changed
- **Example inputs/outputs**: Show expected behavior when possible

### Ask for a Plan First

When scope is unclear or complex:

- **Request a plan**: Ask the agent to outline its approach before coding
- **Review the plan**: Ensure it aligns with your goals
- **Iterate on scope**: Adjust the plan before implementation begins

### Require Summaries

- **Changes made**: Ask the agent to summarize what was changed and why
- **Risks identified**: Request a list of potential risks or side effects
- **Testing performed**: Confirm what validation was done

### Prefer Reviewable Diffs

- **Incremental changes**: Request multiple small PRs over one large rewrite
- **Focused iterations**: Review and approve each step before proceeding
- **Rollback-friendly**: Smaller changes are easier to revert if needed

## PowerShell Guidelines

### Code Style

- Follow PowerShell best practices
- Use approved verbs (Get-_, Set-_, New-_, Remove-_, etc.)
- Use meaningful variable and function names
- Add comment-based help to functions
- Add comments for complex logic
- Use proper parameter validation

### Error Handling

```powershell
try {
    # Code that might fail
    $result = Invoke-RestMethod -Uri $uri -Method Get
} catch {
    Write-Error "Error: $_"
    exit 1
}
```

### Documentation

- Document all scripts with comment-based help
- Include usage examples
- Document parameters with proper descriptions
- Explain any non-obvious decisions

## Security Guidelines

**Critical**: Never commit sensitive data!

### What NOT to Commit

- API keys or tokens
- Passwords or credentials
- Private keys or certificates
- `.env` files with real secrets

### What TO Do

- Use `.env.example` files with placeholder values
- Document required environment variables
- Reference the `.gitignore` file
- Use GitHub Secrets for CI/CD credentials

### Security Checklist

Before submitting a PR:

- [ ] No hardcoded credentials or secrets
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

By contributing, you agree that your contributions will be licensed under the same license as the
project (GNU AGPL v3).

---

Thank you for contributing to Free For Charity! Your efforts help us build better infrastructure for
charitable giving.
