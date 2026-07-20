# Cloudflare MCP Setup Guide

The Cloudflare MCP server provides AI coding tools with direct access to Cloudflare's API for DNS management, zone analytics, WAF rules, and more.

## What It Provides

- **DNS Management**: List, create, update, and delete DNS records across all FFC zones
- **Zone Analytics**: Traffic stats, cache hit ratios, threat summaries
- **WAF / Page Rules**: Inspect and modify firewall and page rules
- **Workers**: Deploy and manage Cloudflare Workers
- **SSL/TLS**: Check certificate status and configuration

## Setup for Claude Code

### Option A: Quick add (recommended)

```bash
claude mcp add cloudflare --transport sse https://api.cloudflare.com/mcp
```

### Option B: Manual configuration

Add to `.claude/settings.json` in the repo (or your global settings):

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

Set the environment variable before launching Claude Code:

```powershell
$env:CLOUDFLARE_API_TOKEN = "your-token-here"
```

Or add it to your `.env` file (never commit this):

```
CLOUDFLARE_API_TOKEN=your-token-here
```

### Option C: OAuth (browser-based)

Cloudflare MCP supports OAuth authentication. When configured without a token, it will prompt you to authenticate via browser:

```json
{
  "mcpServers": {
    "cloudflare": {
      "type": "sse",
      "url": "https://api.cloudflare.com/mcp"
    }
  }
}
```

## Setup for GitHub Copilot Agent

1. Navigate to your repository on GitHub.
2. Go to **Settings** > **Copilot** > **MCP Servers** (or the equivalent Copilot Agent configuration).
3. Add a new MCP server configuration:

```json
{
  "type": "sse",
  "url": "https://api.cloudflare.com/mcp",
  "headers": {
    "Authorization": "Bearer ${CLOUDFLARE_API_TOKEN}"
  }
}
```

4. Add `CLOUDFLARE_API_TOKEN` as an **Environment Secret** in the `copilot` environment:
   - Go to **Settings** > **Environments** > **copilot**
   - Add secret: `CLOUDFLARE_API_TOKEN`

## Authentication: Creating a Cloudflare API Token

1. Log in to the [Cloudflare Dashboard](https://dash.cloudflare.com).
2. Go to **My Profile** > **API Tokens** > **Create Token**.
3. Use the **Edit zone DNS** template, or create a custom token with these permissions:
   - **Zone**: DNS: Edit
   - **Zone**: Zone: Read
   - **Zone**: Analytics: Read
4. Set **Zone Resources** to "All zones" (or restrict to specific FFC zones).
5. Copy the token and store it securely.

**Important**: The token should have the minimum permissions needed. For read-only auditing, use DNS: Read and Zone: Read only.

## Verification

After configuration, verify the connection works:

### In Claude Code

Ask Claude Code to list DNS records:

```
List the DNS records for legioninthewoods.org
```

Expected: Claude should return a list of DNS records without errors.

### In Copilot Agent

Open a Copilot Agent chat and ask:

```
Using the Cloudflare MCP, list the DNS zones in my account.
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Unauthorized" error | Check that your API token is valid and has the correct permissions |
| "Zone not found" | Ensure the token has access to the zone in question |
| MCP server not appearing | Restart Claude Code or reload the Copilot Agent session |
| Timeout errors | Cloudflare API may be rate-limiting; wait and retry |
