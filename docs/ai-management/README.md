# AI-Management migration (from FFC-IN-AI-Management)

Content migrated verbatim from the winding-down FFC-IN-AI-Management repo
(FFC-Cloudflare-Automation#724). The hub is now the canonical home; the
source repo receives no new content.

| Dir          | What                                                            | Status                                                                                          |
| ------------ | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `agents/`    | Canonical fleet agent definitions (dns-audit, site-health, ...) | LIVE — fleet repos' `.claude/agents/` copies derive from these                                   |
| `mcp/`       | MCP server configs + setup guides (Cloudflare, GitHub, Sentry…) | LIVE — Sentry guide complements the 2026-07-19 pilot                                             |
| `managed/`   | Managed-policy CLAUDE.md + settings source                      | LIVE — source of the org-managed policy file                                                     |
| `docs/`      | Architecture / custom-agents / sync guides                      | PARTIALLY historical — sync-guide describes the retired push model (see `archive/sync-ai-configs-2026-04` tag) |
| `scripts/`   | AI-config audit/sync PowerShell                                 | DORMANT — revisit if fleet config sync is rebuilt                                                |
| `inventory/` | 2026-era repo inventory snapshot                                | HISTORICAL                                                                                       |
