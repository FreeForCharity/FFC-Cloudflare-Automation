# AI-Management migration (from FFC-IN-AI-Management)

Content migrated verbatim from the winding-down FFC-IN-AI-Management repo
(FFC-Cloudflare-Automation#724). The hub is now the canonical home; the source repo receives no new
content.

| Dir          | What                                                            | Status                                                                                                         |
| ------------ | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `agents/`    | Canonical fleet agent definitions (dns-audit, site-health, ...) | LIVE — fleet repos' `.claude/agents/` copies derive from these                                                 |
| `mcp/`       | MCP server configs + setup guides (Cloudflare, GitHub, Sentry…) | LIVE — Sentry guide complements the 2026-07-19 pilot                                                           |
| `managed/`   | Managed-policy CLAUDE.md + settings source                      | LIVE — source of the org-managed policy file                                                                   |
| `docs/`      | Architecture / custom-agents / sync guides                      | PARTIALLY historical — sync-guide describes the retired push model (see `archive/sync-ai-configs-2026-04` tag) |
| `scripts/`   | AI-config audit/sync PowerShell                                 | DORMANT — revisit if fleet config sync is rebuilt                                                              |
| `inventory/` | 2026-era repo inventory snapshot                                | HISTORICAL                                                                                                     |

> **Verbatim archive caveat:** files are migrated byte-for-byte from the source repo and may still
> reference FFC-IN-AI-Management as the system of record, GitHub-Secrets-era guidance, retired sync
> flows, or hard-coded paths. The table above is the authority on what is LIVE; content corrections
> to LIVE files (and any change to `managed/` — the deployed org-policy source, Clarke-gated) are
> tracked as follow-ups on #724, not rewritten during migration.

## Follow-up tranches (#724 step 3)

Remaining work from the migration, each a candidate follow-up issue. Verified against the migrated
tree on 2026-07-21; the counts below are lingering `FFC-IN-AI-Management` references that a scoped
grep still finds in each directory.

- [x] **LIVE agent correction** — `agents/cross-repo-sync.md` named FFC-IN-AI-Management as the
      repository it audits; retargeted to the hub (`FFC-Cloudflare-Automation`) as the canonical
      config source, with the wound-down repo kept as a clearly-historical provenance reference.
      `mcp/` and `managed/` were already reference-clean, so this was the only LIVE-tier correction
      outstanding.
- [x] **`docs/` historical annotation** — `architecture.md`, `custom-agents-guide.md`, and
      `sync-guide.md` each carry a "historical — superseded, see the
      `archive/sync-ai-configs-2026-04` tag" banner directly under their title, pointing readers to
      the hub as the canonical home; the archived prose is otherwise left byte-for-byte intact.
- [ ] **`scripts/` revive-or-retire decision** — the DORMANT AI-config PowerShell
      (`Sync-AIConfigs.ps1`, `Audit-AIConfigs.ps1`, `Install-ManagedSettings.ps1`,
      `Get-RepoType.ps1`) still targets the source repo; decide whether to rebuild fleet config sync
      on the hub or formally retire these.
- [ ] **`managed/` org-policy review (Clarke-gated)** — confirm the deployed org-policy source
      (`CLAUDE.md` + managed settings) is current for the hub. Reference-clean today; any change to
      `managed/` is gated.
- [ ] **`inventory/` supersession** — the HISTORICAL 2026-era repo snapshot is dated; either point
      readers to a live inventory source or keep it as a clearly labeled archive.
