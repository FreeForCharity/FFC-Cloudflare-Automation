# GitHub MCP Setup Guide

The GitHub MCP server provides comprehensive access to the GitHub API with 51 tools spanning repositories, issues, pull requests, Actions, code security, and more.

## Available Toolsets

The GitHub MCP server organizes its tools into toolsets that can be selectively enabled:

| Toolset | Tools | Description |
|---------|-------|-------------|
| `repos` | Create/read/update repos, branches, files, commits | Core repository operations |
| `issues` | Create/read/update/search issues, comments, labels | Issue tracking |
| `pull_requests` | Create/read/update PRs, reviews, merge | Pull request management |
| `actions` | List/trigger/cancel workflows, view run logs | CI/CD pipeline management |
| `code_security` | Query code scanning alerts, Dependabot alerts | Security vulnerability tracking |
| `users` | User profile lookup, search | User information |
| `notifications` | List/manage notifications | GitHub notification management |
| `experiments` | Experimental/preview tools | Early access features |

## Setup for Claude Code

### Option A: Plugin (if available)

```bash
claude mcp add github-mcp --transport sse https://api.github.com/mcp
```

### Option B: Manual configuration

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "github": {
      "type": "sse",
      "url": "https://api.github.com/mcp",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}",
        "X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security"
      }
    }
  }
}
```

Set the environment variable:

```powershell
$env:GITHUB_TOKEN = "ghp_your-personal-access-token"
```

### Toolset Selection

Use the `X-MCP-Toolsets` header to control which tool groups are available. This reduces noise and keeps the tool list focused:

```json
"X-MCP-Toolsets": "repos,issues,pull_requests"
```

To enable all toolsets:

```json
"X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security,users,notifications,experiments"
```

**Recommendation**: Start with `repos,issues,pull_requests` and add more as needed.

## Setup for GitHub Copilot Agent

GitHub Copilot Agent has built-in access to GitHub functionality, so the GitHub MCP is often not needed separately. However, for advanced or custom configurations:

1. Go to your repository **Settings** > **Copilot** > **MCP Servers**.
2. Add the GitHub MCP server:

```json
{
  "type": "sse",
  "url": "https://api.github.com/mcp",
  "headers": {
    "Authorization": "Bearer ${GITHUB_TOKEN}",
    "X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security"
  }
}
```

3. Add `GITHUB_TOKEN` as an environment secret in the `copilot` environment.

**Note**: Copilot Agent already has native GitHub access for many operations. The MCP server adds cross-organization capabilities and advanced toolsets like `code_security` and `actions`.

## PAT Token Requirements

Create a Fine-Grained Personal Access Token (recommended) or Classic token:

### Fine-Grained Token (Recommended)

1. Go to [GitHub Settings > Developer Settings > Personal Access Tokens > Fine-grained tokens](https://github.com/settings/tokens?type=beta).
2. Click **Generate new token**.
3. Set **Resource owner** to the organization (e.g., `FreeForCharity`).
4. Set **Repository access** to "All repositories" or select specific repos.
5. Grant these permissions:

| Permission | Access Level | Required For |
|------------|-------------|--------------|
| Contents | Read & Write | File operations, branches |
| Issues | Read & Write | Issue management |
| Pull requests | Read & Write | PR creation and review |
| Actions | Read | Workflow monitoring |
| Code scanning alerts | Read | Security toolset |
| Metadata | Read | Required (always) |

6. Click **Generate token** and store it securely.

### Classic Token

1. Go to [GitHub Settings > Developer Settings > Personal Access Tokens > Tokens (classic)](https://github.com/settings/tokens).
2. Click **Generate new token**.
3. Select scopes:
   - `repo` (full control of private repositories)
   - `workflow` (if you need Actions management)
   - `read:org` (for organization operations)
4. Generate and store securely.

## Verification

### In Claude Code

```
Using the GitHub MCP, list the repositories in the FreeForCharity organization.
```

Expected: A list of repositories with names, descriptions, and visibility.

### Test specific toolsets

```
List open pull requests on FreeForCharity/FFC-EX-legioninthewoods.org
```

```
Show the latest GitHub Actions runs for FreeForCharity/FFC-Cloudflare-Automation
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Bad credentials" | Regenerate your PAT and update the environment variable |
| "Resource not accessible by integration" | Your token lacks the required permission scope |
| Only some tools appear | Check the `X-MCP-Toolsets` header value |
| Rate limiting (403) | GitHub API has rate limits; use a PAT for higher limits (5000 req/hr) |
| Cross-org access fails | Ensure your token's resource owner includes the target org, or use a classic token with `repo` + `read:org` |
