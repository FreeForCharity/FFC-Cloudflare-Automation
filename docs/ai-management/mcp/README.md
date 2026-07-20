# MCP Server Configurations

This directory contains Model Context Protocol (MCP) server configurations for AI tools
used across FFC repositories. Configurations are organized by AI tool.

## What Is MCP?

The Model Context Protocol (MCP) allows AI assistants to connect to external services
(APIs, databases, tools) via standardized server definitions. Each MCP server provides
the AI with access to specific capabilities -- querying GitHub, managing Cloudflare DNS,
running browser tests, etc.

## Directory Structure

```
mcp/
  claude/              Configs for Claude Code (.claude.json format)
    github.json        GitHub API access
    cloudflare.json    Cloudflare DNS and zone management
    sentry.json        Error tracking and monitoring
    playwright.json    Browser automation and testing
    microsoft-learn.json  Microsoft documentation search
  copilot/             Configs for GitHub Copilot (.copilot/mcp-config.json format)
    mcp-config-standard.json   Standard config (GitHub + MS Learn)
    mcp-config-full.json       Full config (all servers)
```

## Available MCP Servers

| Server | Tool | Purpose | Priority |
|--------|------|---------|----------|
| GitHub | Claude, Copilot | Repository operations, PRs, issues, Actions | Tier 1 (Essential) |
| Cloudflare | Claude, Copilot | DNS management, zone operations, WAF rules | Tier 1 (Essential for infra repos) |
| Playwright | Claude | Browser automation, site testing, screenshots | Tier 2 (Recommended) |
| Sentry | Claude, Copilot | Error tracking, performance monitoring | Tier 2 (Recommended) |
| Microsoft Learn | Claude, Copilot | Documentation search for M365, Azure, PowerShell | Tier 3 (Optional) |

## Priority Tiers

- **Tier 1 (Essential)**: Required for core FFC operations. GitHub is needed everywhere;
  Cloudflare is needed for infrastructure repos.
- **Tier 2 (Recommended)**: Significantly improves agent capabilities. Playwright enables
  site health checks; Sentry provides production error context.
- **Tier 3 (Optional)**: Nice to have. Microsoft Learn helps with M365/PowerShell
  documentation lookups.

## How Configs Are Deployed

### Claude Code

Claude Code MCP servers are configured in the project-level `.claude.json` file or the
user-level `~/.claude.json`. The sync script merges the appropriate configs based on
repo type.

### GitHub Copilot

Copilot reads MCP server config from `.copilot/mcp-config.json` in the repository root.
Choose the standard or full config based on the repo's needs.

## Authentication

MCP servers that require authentication use environment variables or OAuth flows:

| Server | Auth Method |
|--------|-------------|
| GitHub | `GITHUB_PERSONAL_ACCESS_TOKEN` env var or OAuth |
| Cloudflare | `CLOUDFLARE_API_TOKEN` env var |
| Sentry | `SENTRY_AUTH_TOKEN` env var |
| Playwright | No auth needed (local browser) |
| Microsoft Learn | No auth needed (public API) |

Never commit authentication tokens. Use environment variables or GitHub Secrets.
