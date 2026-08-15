# FFC repo map — what lives where, and when to go read it

This repository is the **infrastructure automation hub**, not the whole of Free For Charity. Several
things an agent working here will need — how FFC develops sites, what the house standards are, how
an intake becomes a provisioned charity — are **authoritative somewhere else**, and until this file
existed nothing here said so.

**Why this page exists.** On 2026-08-15, a session onboarding a new charity wrote a from-scratch
"how to build your site" guide out of the FFC-EX conventions described in this repo's own docs. That
guide was reasonable and partly wrong: it named the wrong package manager, told the builder to merge
her own PRs (FFC's canonical workflow says maintainers merge), and duplicated onboarding material
that the site template already ships. The information needed to get it right was one clone away and
nothing pointed at it. That is the failure mode this page is meant to prevent — not "we lack docs",
but "the docs exist in a repo this one never names."

## The constellation

| Repo                                 | What it is                                                     | Authoritative for                                                                                                           |
| ------------------------------------ | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **FFC-Cloudflare-Automation** (here) | ~105 Actions workflows driving CF, WHMCS, M365, Google, GitHub | Infrastructure automation, workflow catalog + numbering, the gate/approval model, the lessons ledger                        |
| **FFC-IN-ffcadmin.org**              | The admin portal (Next.js → Pages, live at `ffcadmin.org`)     | **Org development process and standards**, the agent issue→PR workflow, the four-gate intake journey, agentic-OS governance |
| **FFC_Single_Page_Template**         | The charity site template                                      | What a charity site **is** — structure, `site.config.ts`, CI guards, and its own onboarding docs                            |
| **FFC-EX-`<domain>`**                | One per charity, created from the template by workflow 701     | That charity's content and config                                                                                           |
| **FFC-IN-`<name>`**                  | FFC's own internal sites                                       | Their own content                                                                                                           |

Note the naming convention it encodes: **`-IN-` is internal to FFC, `-EX-` is an external charity.**

## Go read FFCadmin when the task is…

Clone it read-only — it is public, so no attachment is needed:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
  https://github.com/FreeForCharity/ffc-in-ffcadmin.org /workspace/freeforcharity/ffc-in-ffcadmin.org
```

| If you are about to…                                  | Read first                                                                         |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Write anything about how FFC develops                 | `docs/agent-issue-pr-workflow.md`, `CONTRIBUTING.md`, `CODE_QUALITY.md`            |
| Tell a charity how to work on their site              | The **template's own** docs — see the next section                                 |
| Reason about intake → provisioning end to end         | `docs/intake-automation-architecture.md`, `docs/gated-journey-operator-runbook.md` |
| Touch agentic-OS governance, autonomy, or the roadmap | `docs/agentic-os/` (`03-target-architecture`, `06-governance`, `07-autonomy`)      |
| Change a footer, or check site parity                 | `docs/footer-standard-adoption-checklist.md`, `docs/standards/`                    |
| Do a DNS cutover for a live site                      | `docs/dns-cutover-runbook.md` — alongside this repo's own runbooks                 |

**The one rule worth internalizing:** this repo is authoritative for _how the automation runs_.
FFCadmin is authoritative for _how FFC works_. When they appear to disagree about process, FFCadmin
wins and this repo should be corrected.

## Charity-facing guidance lives in the charity's own repo

Every `FFC-EX-<domain>` repo is created from `FFC_Single_Page_Template`, which already ships the
onboarding material — so **do not hand-write a getting-started guide for a charity.** Point at what
provisioning already gave them:

- `TEMPLATE_CUSTOMIZATION.md` — the map of what to change and where
- `TEMPLATE_SETUP_CHECKLIST.md` — the ordered checklist for a fresh site
- `CONTENT_REPLACEMENT_GUIDE.md` — the content-gathering worksheet for the charity
- `QUICK_START.md`, `CONTRIBUTING.md`, `CODE_QUALITY.md`, `DEPLOYMENT.md`
- `.claude/agents/onboarding.md` — a **Claude agent that runs the whole customization interview**,
  and `.claude/skills/rebrand/`, `.claude/rules/`, `.claude/hooks/`, plus `.copilot/mcp-config.json`

That last line is the one most likely to be missed: **AI-assisted development is already configured
in every provisioned charity repo.** A charity does not need to be told how to set up tooling; they
need to be told the tooling is there.

Two specifics that a guide written from this repo alone will get wrong:

- **The template uses `npm`** (`package-lock.json`, `npm audit`). FFCadmin uses **`pnpm`**. The
  commands are not interchangeable, and FFCadmin's pre-commit chain does not transfer verbatim.
- **Validation is template-specific**: `npm run check:drift`, `check:site-config`, `check:rebrand`,
  `verify:build`, `smoke` — these exist in the charity repo, not here.

## Where the two repos actually touch

These are live couplings, verified in the workflow sources. Changing either side can break the
other.

| Direction           | Mechanism                                                                                                                                                                                                                                                                        |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FFCadmin → **here** | FFCadmin's `trigger-provisioning.yml` sends a `repository_dispatch` of type **`ffcadmin-website-provision`**, which is a documented trigger on **701**. Its `client_payload` carries `charity_title`, `ffcadmin_issue`, `issue_url`, `sponsor`, `domain`.                        |
| **here** → FFCadmin | **502** generates the GA4 report and delivers it to FFCadmin **as a reviewable PR, never a direct push**; it also seeds time-series history by fetching `public/data/google-analytics/<site>.timeseries.json` from FFCadmin `main` over raw.githubusercontent (public, no auth). |
| **here** → FFCadmin | The **agentic-OS status feed** is generated here (`scripts/generate-agentic-os-status.py`) and delivered into FFCadmin's `public/data/`, rendered at `ffcadmin.org/agentic-os/`. **744** checks its freshness.                                                                   |
| **here** → FFCadmin | **703** generates the sites list here; FFCadmin **pulls** the published files. Workflow docs render publicly at `ffcadmin.org/automation/`.                                                                                                                                      |
| Fleet-wide          | **731**, **737**, **739**, **741**, **742** treat FFCadmin as one repo in the managed fleet.                                                                                                                                                                                     |

A trap already recorded in `CLAUDE.md` and worth repeating here because it is a cross-repo one:
FFCadmin runs `lint-staged` → `prettier --write` in a **pre-commit hook** over `*.{json,md,css}`, so
a feed this repo hand-delivers can be rewritten after the staged-blob check. Verify delivered bytes
against `HEAD:<path>` **after** committing, never before.

## Reading order for a new agent

1. `AGENTS.md` here — this repo's own onboarding.
2. This page — so you know what this repo is _not_ authoritative for.
3. FFCadmin's `docs/agent-issue-pr-workflow.md` — the org's process, if you are writing or
   describing process.
4. The relevant skill in `.claude/skills/` — `charity-onboarding` for the full chain.
