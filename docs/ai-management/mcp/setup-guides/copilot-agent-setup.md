# Copilot Agent MCP Setup Guide

This guide walks through the complete setup for configuring MCP servers in GitHub Copilot Agent mode. Copilot Agent can connect to external MCP servers (Cloudflare, Sentry, etc.) to extend its capabilities beyond code generation.

## Overview

GitHub Copilot Agent supports MCP servers through repository-level configuration. You configure the servers in the GitHub UI, store authentication tokens as environment secrets, and Copilot Agent connects to them during agent sessions.

## Prerequisites

- GitHub Copilot Business or Enterprise license with Agent mode enabled
- Repository admin access (for settings and environment configuration)
- API tokens for each MCP server you want to connect

## Step 1: Navigate to Repository Settings

1. Open your repository on GitHub (e.g., `github.com/FreeForCharity/FFC-EX-legioninthewoods.org`).
2. Click **Settings** (gear icon in the repository tab bar).
3. In the left sidebar, find **Copilot** (under the "Code and automation" section).

## Step 2: Create the Copilot Environment

MCP server authentication tokens are stored as **environment secrets** in a special `copilot` environment.

1. Go to **Settings** > **Environments**.
2. Click **New environment**.
3. Name it `copilot` (this exact name is required).
4. Click **Configure environment**.

## Step 3: Add Environment Secrets

For each MCP server that requires authentication, add the token as a secret:

1. In the `copilot` environment configuration, scroll to **Environment secrets**.
2. Click **Add secret**.
3. Add the following secrets as needed:

| Secret Name | Description | Where to Get It |
|-------------|-------------|-----------------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with DNS/Zone permissions | Cloudflare Dashboard > My Profile > API Tokens |
| `GITHUB_TOKEN` | GitHub PAT (if cross-org access needed) | GitHub Settings > Developer Settings > PATs |
| `SENTRY_AUTH_TOKEN` | Sentry auth token (if not using OAuth) | Sentry > Settings > API > Auth Tokens |

**Note**: Do not add secrets you do not need. Only add tokens for MCP servers you plan to configure.

## Step 4: Configure MCP Servers

### Option A: Via GitHub UI

1. Go to **Settings** > **Copilot** > **MCP Servers**.
2. Click **Add MCP Server** (or equivalent button).
3. Paste the JSON configuration for each server.

### Option B: Via .copilot/mcp-config.json

Create a file `.copilot/mcp-config.json` in the repository root:

```json
{
  "mcpServers": {
    "cloudflare": {
      "type": "sse",
      "url": "https://api.cloudflare.com/mcp",
      "headers": {
        "Authorization": "Bearer ${CLOUDFLARE_API_TOKEN}"
      }
    },
    "github": {
      "type": "sse",
      "url": "https://api.github.com/mcp",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}",
        "X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security"
      }
    },
    "sentry": {
      "type": "sse",
      "url": "https://mcp.sentry.dev/mcp"
    }
  }
}
```

Commit this file to the repository. Environment variable references (e.g., `${CLOUDFLARE_API_TOKEN}`) are resolved from the `copilot` environment secrets at runtime.

## Step 5: Validate Configuration

### Quick validation

1. Open the repository in GitHub (web interface).
2. Start a Copilot Agent chat (click the Copilot icon or open the Copilot panel).
3. Ask Copilot Agent to use an MCP tool:

```
Using the Cloudflare MCP, list the DNS zones in my account.
```

If the MCP server is configured correctly, Copilot Agent should execute the request and return results.

### Validation checklist

- [ ] `copilot` environment exists under Settings > Environments
- [ ] Required secrets are added to the `copilot` environment
- [ ] MCP server configuration is either in the UI or in `.copilot/mcp-config.json`
- [ ] Environment variable references in the config match the secret names exactly
- [ ] Copilot Agent can successfully call at least one tool from each configured server

## Complete Configuration Examples

### Minimal: Cloudflare only

**Environment secrets**:
- `CLOUDFLARE_API_TOKEN`

**MCP config** (`.copilot/mcp-config.json`):
```json
{
  "mcpServers": {
    "cloudflare": {
      "type": "sse",
      "url": "https://api.cloudflare.com/mcp",
      "headers": {
        "Authorization": "Bearer ${CLOUDFLARE_API_TOKEN}"
      }
    }
  }
}
```

### Full stack: All Tier 1 servers

**Environment secrets**:
- `CLOUDFLARE_API_TOKEN`
- `GITHUB_TOKEN`
- `SENTRY_AUTH_TOKEN`

**MCP config** (`.copilot/mcp-config.json`):
```json
{
  "mcpServers": {
    "cloudflare": {
      "type": "sse",
      "url": "https://api.cloudflare.com/mcp",
      "headers": {
        "Authorization": "Bearer ${CLOUDFLARE_API_TOKEN}"
      }
    },
    "github": {
      "type": "sse",
      "url": "https://api.github.com/mcp",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}",
        "X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security"
      }
    },
    "sentry": {
      "type": "sse",
      "url": "https://mcp.sentry.dev/mcp",
      "headers": {
        "Authorization": "Bearer ${SENTRY_AUTH_TOKEN}"
      }
    }
  }
}
```

## Applying to Multiple Repos

Rather than configuring each repo manually, use the sync pipeline:

1. The MCP config template lives in `templates/base/.copilot/mcp-config.json` in this repository.
2. Run `Sync-AIConfigs.ps1` to deploy it across all FFC repos via PR.
3. Environment secrets still need to be set per-repo (or use org-level secrets if available).

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "MCP server not found" | Verify the URL is correct and accessible |
| "Unauthorized" or "Forbidden" | Check that the secret name matches the `${}` reference exactly |
| Copilot Agent does not show MCP tools | Ensure Agent mode is enabled for the repo; refresh the session |
| Secret not resolving | Verify the secret is in the `copilot` environment specifically, not repo-level |
| Tools work but return empty results | The API token may lack required permissions; regenerate with correct scopes |
| Changes to config not taking effect | Copilot Agent may cache configurations; start a new chat session |
