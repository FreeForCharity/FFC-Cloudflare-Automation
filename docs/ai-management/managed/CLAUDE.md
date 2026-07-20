# Free For Charity (FFC) - Global Claude Code Context

You are working on a **Free For Charity (FFC)** project.

## Mission

Free For Charity provides free websites and domain management for verified 501(c)(3) nonprofit organizations. FFC volunteers build, host, and maintain websites so nonprofits can focus on their missions instead of technology overhead.

## Core Services and Platforms

| Service | Purpose | Notes |
|---------|---------|-------|
| **Cloudflare** | DNS management, CDN, SSL, Workers | All FFC domains route through Cloudflare |
| **Microsoft 365** | Email (Exchange Online), collaboration | Nonprofit tenant via M365 for Nonprofits |
| **WHMCS** | Billing, domain registration tracking | Internal admin tool |
| **WPMUDEV** | WordPress hosting and plugins | Used for WordPress-based charity sites |
| **GitHub** | Source repos, GitHub Pages, Actions | All code lives in FreeForCharity org |

## Security Rules

- **NEVER** expose API tokens, secrets, or credentials in code or output.
- Use **GitHub Secrets** for CI/CD pipelines and **`.env` files** (git-ignored) for local development.
- Do not commit `.env`, `credentials.json`, or any file containing tokens.
- If you need a secret value, ask the operator to configure it — do not generate placeholder tokens.

## Project-Specific Instructions

Always check the **repo's own `CLAUDE.md`** file for project-specific instructions that supplement or override these global defaults. The repo-level file takes precedence for any conflicting guidance.

## Common Conventions (All FFC Repos)

- **Folder naming**: kebab-case (e.g., `site-assets`, `page-templates`)
- **Branching**: Feature branches off `main`, merged via pull request
- **Pre-commit checks**: Lint and format checks run before commits where configured
- **Commit messages**: Conventional Commits style (`feat:`, `fix:`, `chore:`, `docs:`)
- **PR descriptions**: Include a summary and test plan

## Custom Agents

Custom agents may be defined in `.claude/agents/` within individual repos. These are markdown files that provide specialized behavior (e.g., DNS auditing, site health checks). Invoke them with `/agent <name>` if available.

## MCP Servers

The following MCP servers may be configured for this environment:

- **Cloudflare MCP** - DNS record management, zone analytics, WAF rules
- **GitHub MCP** - Repository management, issues, PRs, Actions workflows
- **Sentry MCP** - Error monitoring, issue triage, root cause analysis
- **Playwright** - Browser automation and visual testing (Claude Code plugin)
- **Microsoft Learn MCP** - M365 documentation lookup

Check `.claude/settings.json` or the project's MCP configuration for which servers are active.
