# Sentry MCP Setup Guide

The Sentry MCP server provides AI coding tools with access to error monitoring, issue triage, and root cause analysis for applications instrumented with Sentry.

## What It Provides

- **Error Event Search**: Query errors by project, time range, and frequency
- **Stack Trace Analysis**: View full stack traces and error context
- **Root Cause Analysis**: AI-assisted identification of error causes
- **Performance Data**: Transaction traces and performance metrics
- **Release Tracking**: Correlate errors with specific releases

## Server Details

| Detail | Value |
|--------|-------|
| Remote URL | `https://mcp.sentry.dev/mcp` |
| Transport | Streamable HTTP |
| Authentication | OAuth (Sentry account) |

## Setup for Claude Code

### Option A: Quick add

```bash
claude mcp add sentry --transport sse https://mcp.sentry.dev/mcp
```

### Option B: Manual configuration

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "sentry": {
      "type": "sse",
      "url": "https://mcp.sentry.dev/mcp"
    }
  }
}
```

### Authentication Flow

Sentry MCP uses OAuth. When you first interact with the Sentry MCP tools in Claude Code:

1. Claude Code will provide a URL to authorize with Sentry.
2. Open the URL in your browser.
3. Log in to your Sentry account (or create one).
4. Authorize the MCP integration.
5. Return to Claude Code -- the connection will be established.

The OAuth token is cached locally, so you should only need to do this once per machine.

## Setup for GitHub Copilot Agent

1. Navigate to your repository on GitHub.
2. Go to **Settings** > **Copilot** > **MCP Servers**.
3. Add a new MCP server:

```json
{
  "type": "sse",
  "url": "https://mcp.sentry.dev/mcp"
}
```

4. Sentry MCP uses OAuth, so no static token is needed. The authentication flow will occur when Copilot Agent first tries to use Sentry tools.

**Note**: For Copilot Agent, OAuth may require a Sentry auth token stored as an environment secret if the OAuth flow is not supported in the Copilot Agent context. Check Sentry's documentation for the latest authentication options.

Alternative with auth token:

```json
{
  "type": "sse",
  "url": "https://mcp.sentry.dev/mcp",
  "headers": {
    "Authorization": "Bearer ${SENTRY_AUTH_TOKEN}"
  }
}
```

Add `SENTRY_AUTH_TOKEN` to the `copilot` environment secrets if needed.

## Creating a Sentry Auth Token (Alternative to OAuth)

If you need a static auth token instead of OAuth:

1. Log in to [sentry.io](https://sentry.io).
2. Go to **Settings** > **Account** > **API** > **Auth Tokens**.
3. Click **Create New Token**.
4. Select these scopes:
   - `event:read`
   - `project:read`
   - `org:read`
   - `issue:read`
5. Create the token and store it securely.

## Verification

### In Claude Code

```
Using Sentry, list recent errors for the FFC project.
```

If you do not have a Sentry project set up yet, start with:

```
Using Sentry, list the organizations and projects I have access to.
```

### Expected behavior

- First use: OAuth prompt appears; authenticate in browser
- Subsequent uses: Tools work immediately with cached credentials
- Returns: Error events with timestamps, stack traces, and frequency data

## Troubleshooting

| Issue | Solution |
|-------|----------|
| OAuth flow does not complete | Ensure your browser can reach sentry.io; check for popup blockers |
| "No projects found" | Verify your Sentry account has at least one project configured |
| Auth token rejected | Regenerate the token and ensure it has the required scopes |
| MCP server unreachable | Verify `https://mcp.sentry.dev/mcp` is accessible from your network |
| Stale OAuth token | Delete the cached token and re-authenticate (check Claude Code's MCP cache) |
