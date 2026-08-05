# AI-Management migration (from FFC-IN-AI-Management)

Content migrated verbatim from the winding-down FFC-IN-AI-Management repo
(FFC-Cloudflare-Automation#724). The hub is now the canonical home; the source repo receives no new
content.

| Dir               | What                                                            | Status                                                                                                         |
| ----------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `agents/`         | Canonical fleet agent definitions (dns-audit, site-health, ...) | LIVE — fleet repos' `.claude/agents/` copies derive from these                                                 |
| `mcp/`            | MCP server configs + setup guides (Cloudflare, GitHub, Sentry…) | LIVE — Sentry guide complements the 2026-07-19 pilot                                                           |
| `managed/`        | Managed-policy CLAUDE.md + settings source                      | LIVE — source of the org-managed policy file                                                                   |
| `docs/`           | Architecture / custom-agents / sync guides                      | PARTIALLY historical — sync-guide describes the retired push model (see `archive/sync-ai-configs-2026-04` tag) |
| `scripts/`        | AI-config audit/sync PowerShell                                 | DORMANT — revisit if fleet config sync is rebuilt                                                              |
| `inventory/`      | 2026-era repo inventory snapshot                                | HISTORICAL                                                                                                     |
| `templates/`      | Base + `powershell-infra` overlay configs pushed to fleet repos | **RETIRED — deliberately not migrated**, see below                                                             |
| `.claude/agents/` | The source repo applying its own `agents/` to itself            | **DUPLICATE — not migrated**; byte-identical to `agents/`, verified file-by-file                               |

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
- [x] **`scripts/` revive-or-retire decision — RETIRE.** Recommendation with evidence below; the
      files stay in place as a labelled archive rather than being deleted. `templates/` shares this
      decision, because it is this mechanism's payload rather than an independent asset.
- [ ] **`managed/` org-policy review (Clarke-gated)** — confirm the deployed org-policy source
      (`CLAUDE.md` + managed settings) is current for the hub. Reference-clean today; any change to
      `managed/` is gated.
- [x] **`inventory/` supersession** — the HISTORICAL 2026-02-16 repo snapshot (`audit-report.md`,
      `repos.json`) is dated and has no live successor for AI-config presence, so it is kept as a
      clearly-labeled archive: `audit-report.md` carries a "Historical — superseded" banner under
      its title and a new `inventory/README.md` labels the whole directory (covering the
      `repos.json` data file, left byte-for-byte). Both point back to this migration status table.

## `templates/` was omitted from this migration, and that is now a decision rather than a gap

#724's source map lists `templates/ — base/, overlays/`, and its acceptance criteria require that
**every source path carries an explicit verdict — no silent omissions**. The table above had no row
for `templates/` (14 files) or for the source repo's own `.claude/agents/` (6 files). Both are
recorded above now.

`.claude/agents/` is easy: it is the source repo applying its own `agents/` definitions to itself,
and all six files are **byte-identical** to the `agents/` copies that were migrated. Nothing is
lost.

`templates/` needs an argument, because it is the one directory deliberately **not** copied while
everything else came across byte-for-byte.

### Why it is retired rather than migrated

**It is the payload of a mechanism that is already retired.** `scripts/Sync-AIConfigs.ps1` exists to
push `templates/base` + `templates/overlays/<type>` into target repos over the GitHub API. That push
model is already recorded above as superseded (`archive/sync-ai-configs-2026-04`). Migrating the
payload of a retired pusher, without the pusher, produces files nothing reads.

**Copying them into the hub would import a regression.** The templates are earlier, smaller
ancestors of files the hub now owns and has grown well past:

| Template                                                                      | Lines | Hub equivalent                            | Lines |
| ----------------------------------------------------------------------------- | ----- | ----------------------------------------- | ----- |
| `templates/base/CLAUDE.md`                                                    | 87    | `CLAUDE.md`                               | 941   |
| `templates/base/AGENTS.md`                                                    | 171   | `AGENTS.md`                               | 306   |
| `templates/overlays/powershell-infra/.github/agents/AI_AGENT_INSTRUCTIONS.md` | 332   | `.github/agents/AI_AGENT_INSTRUCTIONS.md` | 436   |

The overlay is named `powershell-infra` and this repo _is_ the PowerShell-infra repo, so those three
are the hub's own files at an earlier stage. The hub's `AI_AGENT_INSTRUCTIONS.md` is the policy the
`.claude/hooks/` guards enforce; a 332-line copy of an older draft sitting in `docs/` is a second,
staler answer to "what is the policy", which is worse than no copy.

**The fleet is no longer uniformly synced from them anyway.** Sampled three `FFC-EX-*` clones:
`thecoreyvmoorejrinitiative.org` has `.claude/agents/` with **4** files; `amargraves.org` and
`technologymonastery.org` have none. Whatever the sync once guaranteed, it does not hold now — which
is evidence for _dormant_, and equally evidence that **`agents/` must stay LIVE**: it is still the
canonical definition those 4 files derive from.

### What this does not decide

- **Nothing is deleted.** `templates/` and `scripts/` remain in `FFC-IN-AI-Management`, which is
  wound down but not archived. Recover with
  `git -C FFC-IN-AI-Management show main:templates/base/CLAUDE.md`.
- **If fleet config sync is ever rebuilt**, rebuild it against the hub's _current_ `AGENTS.md`,
  `CLAUDE.md` and `.github/agents/AI_AGENT_INSTRUCTIONS.md` — not against these templates. That is
  the whole reason to retire them explicitly instead of leaving them as a tempting starting point.
- **`managed/` is untouched and still open** — the deployed org-policy source is Clarke-gated, and
  nothing here changes it.
