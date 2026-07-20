# MCP Server Reference

Model Context Protocol (MCP) servers extend AI coding tools with access to external services. This document catalogs every MCP server relevant to FFC operations.

## Tier 1: Available Now

These servers are production-ready and can be configured today.

---

### Cloudflare MCP

**Purpose**: DNS record management, zone analytics, WAF rule inspection, and general Cloudflare account operations.

| Detail | Value |
|--------|-------|
| Source | [developers.cloudflare.com](https://developers.cloudflare.com) |
| Transport | Remote (SSE / streamable HTTP) |
| Auth | Cloudflare API Token or OAuth |
| Supports | Claude Code, Copilot Agent |

**Key capabilities**:
- List and manage DNS records across all FFC zones
- Query zone analytics (traffic, cache hit ratio, threats blocked)
- Inspect and modify WAF rules and page rules
- Manage Workers and Workers KV
- SSL/TLS certificate status

**FFC use case**: Auditing DNS records across all charity domains, verifying CNAME/A records point to the correct GitHub Pages or WordPress hosts, checking SSL status.

---

### GitHub MCP

**Purpose**: Full GitHub API access including repos, issues, pull requests, Actions workflows, code security, and notifications.

| Detail | Value |
|--------|-------|
| Source | [github/github-mcp-server](https://github.com/github/github-mcp-server) |
| Transport | Remote (streamable HTTP) |
| Auth | GitHub PAT or OAuth |
| Supports | Claude Code, Copilot Agent (built-in) |
| Toolsets | 51 tools across repos, issues, pull_requests, code_security, actions, users, notifications, experiments |

**Key capabilities**:
- Create, read, update repos and branches
- Manage issues and pull requests
- Trigger and monitor GitHub Actions workflows
- Query code scanning and Dependabot alerts
- Search code, issues, and users

**FFC use case**: Cross-repo operations from AI agents -- creating PRs, checking CI status, querying security alerts, managing issues across the FreeForCharity org.

---

### Sentry MCP

**Purpose**: Error monitoring, issue triage, and root cause analysis for applications instrumented with Sentry.

| Detail | Value |
|--------|-------|
| Source | [mcp.sentry.dev](https://mcp.sentry.dev) |
| Transport | Remote (streamable HTTP) |
| URL | `https://mcp.sentry.dev/mcp` |
| Auth | OAuth (Sentry account) |
| Supports | Claude Code, Copilot Agent |

**Key capabilities**:
- List and search error events
- Analyze stack traces and error frequency
- Root cause analysis suggestions
- Performance monitoring data
- Release tracking

**FFC use case**: Diagnosing production errors on FFC web properties, understanding error patterns, and getting AI-assisted root cause analysis.

---

### Playwright MCP

**Purpose**: Browser automation, visual testing, and web scraping through Playwright's headless browser engine.

| Detail | Value |
|--------|-------|
| Source | Built-in Claude Code plugin |
| Transport | Local (stdio) |
| Auth | None (runs locally) |
| Supports | Claude Code (plugin) |

**Key capabilities**:
- Navigate to URLs and take screenshots
- Click, type, fill forms in web pages
- Capture accessibility snapshots
- Run arbitrary Playwright scripts
- Network request interception

**FFC use case**: Testing charity websites visually, verifying deployments, checking responsive layouts, and automating form-based workflows.

---

### Microsoft Learn MCP

**Purpose**: Access to Microsoft 365 documentation, API references, and best practices.

| Detail | Value |
|--------|-------|
| Source | [learn.microsoft.com/api/mcp](https://learn.microsoft.com/api/mcp) |
| Transport | Remote |
| Auth | None (public documentation) |
| Supports | Claude Code, Copilot Agent |

**Key capabilities**:
- Search Microsoft documentation
- Look up M365 API references
- Query Exchange Online, Azure AD, and SharePoint docs
- Best practices and configuration guides

**FFC use case**: Looking up M365 admin procedures for the FFC nonprofit tenant, Exchange Online mail flow rules, and Azure AD configuration for email domains.

---

## Tier 2: Future / Custom Build Required

These servers are planned but not yet available. They require custom development.

---

### WordPress MCP Adapter

**Purpose**: Manage WordPress sites hosted on WPMUDEV, including theme/plugin management and content operations.

| Detail | Value |
|--------|-------|
| Status | Planned |
| Transport | TBD (likely Cloudflare Worker) |
| Auth | WPMUDEV API key |
| Supports | Claude Code, Copilot Agent |

**Planned capabilities**:
- List and manage WordPress sites on the WPMUDEV network
- Install/update themes and plugins
- Query site health and performance metrics
- Content management (posts, pages, media)

**FFC use case**: Managing WordPress-based charity sites without logging into the WPMUDEV dashboard.

---

### Zeffy MCP

**Purpose**: Interact with the Zeffy donation and nonprofit management platform.

| Detail | Value |
|--------|-------|
| Status | Custom build needed |
| Transport | Cloudflare Worker (planned) |
| Auth | Zeffy API credentials |
| Supports | Claude Code, Copilot Agent |

**Planned capabilities**:
- Query donation history and campaigns
- Manage donor records
- Generate financial reports
- Event and ticketing management

**FFC use case**: Querying donation data and generating reports for FFC-managed nonprofits that use Zeffy for fundraising.

---

### WHMCS MCP

**Purpose**: Domain registration tracking and billing management through the WHMCS platform.

| Detail | Value |
|--------|-------|
| Status | Custom build needed |
| Transport | Cloudflare Worker (planned) |
| Auth | WHMCS API credentials |
| Supports | Claude Code, Copilot Agent |

**Planned capabilities**:
- Query domain registration status and expiry dates
- Manage client records and billing
- Check service status across all FFC-managed domains
- Renewal notifications

**FFC use case**: Tracking domain registrations and renewals for all FFC charity domains, ensuring nothing lapses.
