# Human-intervention map for FFC's common work

Written 2026-10-10 from a cloud session that inventoried the live routines, the environment gates in
[`workflow-safety-and-approvals.md`](workflow-safety-and-approvals.md), the rulesets on the hub and
on `FFC-EX-technologymonastery.org`, and the onboarding and migration chains. **This is a
proposal.** Nothing here has been applied. The decisions that are Clarke's are listed at the end.

## The principle

**Hard security lives on `main`. Everything else should run without a human.**

Code reaches `main` only through a PR, Copilot review, the merge queue and required checks. Once
there, it is the reviewed definition of what the automation may do. A human click on each _run_ of
that code adds little that the review of the _code_ did not already decide, and it is the single
largest source of waiting in this org.

So a human stays in the loop in exactly three places:

1. **Changes to the code that defines privilege**: `.github/workflows/`, `.github/actions/`,
   `.claude/hooks/`, and the scripts those call with credentials.
2. **A short, named list of irreversible actions**: actions that spend money, move live traffic off
   a working site, delete or cancel something, or touch an identity tenant (see Tier 2 below).
3. **Anything the charity itself must do**, such as consenting in their own Microsoft or Google
   tenant. That step is inherently human and is not counted below.

Everything else is either removed or done by an agent.

## Where humans are pulled in today

| #   | Intervention                                                     | Where it bites                                           | Verdict                                                       |
| --- | ---------------------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------- |
| H1  | Confirming `add_repo` for a repo the session itself just created | Every new charity, every migration, every fleet sweep    | **Remove** (see H1 below)                                     |
| H2  | Environment approval on `github-prod`                            | 701 repo job, 702, 703 sites list, 706 deliver, 120      | **Remove** for additive writes; keep for destructive ones     |
| H3  | Environment approval on `cloudflare-prod-write`                  | 701 DNS job, 102, 103, 105, 106, 113, 119, 120, 122      | **Split**: additive DNS ungated, live cutover gated           |
| H4  | Environment approval on `google-prod-write`                      | 505 GA4, 503 GTM                                         | **Remove** (already planned as `google-prod-provision`, #636) |
| H5  | Environment approval on `whmcs-prod`                             | 204 onboard, 211 order update, 212 product add           | **Split**: idempotent creates ungated, cancel/accept gated    |
| H6  | Environment approval on `m365-prod`                              | 101's M365 job, 103's EXO jobs, 301–306                  | **Keep** gated (FFC tenant identity); move reads off it       |
| H7  | A gate that appears at the end of a 15-minute run                | 706 `deliver`                                            | Moot once H2 is removed; otherwise split (gate review B)      |
| H8  | An unattended routine asking a question and waiting              | Consent sweep, wedged on `AskUserQuestion` on 2026-10-10 | **Remove**: routines must decide or log, never ask            |
| H9  | Harness classifier blocking Azure AD / Entra writes              | Federated-credential repairs                             | **Keep**: identity changes are Tier 2                         |
| H10 | Merges to `main`                                                 | Hub and every `FFC-EX-*` repo                            | **Already right**: no human approval is required              |

H10 is worth stating plainly because it is already the model this document asks for. The org ruleset
_FFC Branch Protection - Default Branch_ requires a PR with **0** approvals plus Copilot review. The
repo rulesets add the merge queue (and, on the hub, the `Validate Repository` and
`Phantom Revert Guard` checks). _FFC Repo Protection_ blocks repo deletion, transfer and rename
org-wide. The only bypass is `OrganizationAdmin`. So an agent can already land a green PR on any
charity site without a person, and cannot delete or move a repo at all.

## Gate tiers (replaces "one reviewer on every write environment")

GitHub environments offer two independent protections. The proposal is to use the second one
everywhere and the first only where it earns its click:

- **Required reviewers**: a person approves each run.
- **Deployment branch policy, "Protected branches only"**: the job runs only from `main`.

The branch policy is what makes removing reviewers safe. Today a `workflow_dispatch` can name any
ref, and the checked-out scripts come from that ref. That is a branch dispatch running **unreviewed
code with production credentials**, flagged in
[`gate-review-consolidation.md`](gate-review-consolidation.md). With "protected branches only" on
every write environment, the only code that can hold a credential is code that passed review on
`main`. That is the exact meaning of "hard security on commits to `main`".

| Tier                | Protection                           | What belongs here                                                                                                                                                                                                                                                                                                  |
| ------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **0: read**         | none                                 | Every `*-read` environment, as today                                                                                                                                                                                                                                                                               |
| **1: provision**    | main-only branch policy, no reviewer | Additive, idempotent or PR-shaped writes: create `FFC-EX-<domain>` from a template, enable Pages, add the Technical POC, open a PR in an `FFC-EX-*` repo (706 deliver, 703 data PR), GA4/GTM provisioning, DNS records on a zone **not** serving a live site, WHMCS 204 idempotent creates, status-marker products |
| **2: irreversible** | main-only **and** a reviewer         | 113 `execute-register` (spends money); 120 / 106 / 103 `dry_run=false` against a zone that **is** serving a live site; record deletes; 211 accept/cancel; any write to the FFC M365 tenant or Entra; anything that changes repo visibility or org settings                                                         |

### How to implement it

- **Create the Tier 1 environments** next to the gated ones, mirroring the existing
  `whmcs-prod-read` and `github-prod-read` pattern: `github-prod-provision`,
  `cloudflare-prod-provision`, `google-prod-provision` (#636) and `whmcs-prod-provision`. Each gets
  its own federated credential and KV scope where the provider allows narrower credentials.
- **Route by input inside the workflow.** An `environment:` name may be an expression, so a single
  job can pick its tier from the dispatch:

  ```yaml
  environment:
    ${{ (inputs.dry_run == 'true' || needs.classify.outputs.live_site == 'false') &&
    'cloudflare-prod-provision' || 'cloudflare-prod-write' }}
  ```

  A `classify` job decides from facts it measures, not from a field the caller types. For DNS, the
  fact is: does the apex currently resolve to a non-GitHub origin that answers HTTP 200? Dry runs
  never wait for anyone.

- **Move the safety into the workflow, because it now carries it.** An ungated job runs whatever
  inputs any dispatcher passes, so Tier 1 workflows must refuse out-of-scope inputs:
  - repository names must match `FFC-EX-*`;
  - zones must already be in the FFC Cloudflare account;
  - records must not overwrite an existing record of a different type or target without Tier 2;
  - WHMCS creates must be idempotent (204 already is).

  `dry_run` defaults, typed confirmations and concurrency groups stay exactly as they are.

- **Extend `scripts/check-environment-protection.py`** (CI, #993) to assert the second protection
  too: every write environment, Tier 1 or Tier 2, must carry a deployment branch policy limited to
  protected branches. Today it checks only `required_reviewers`, so a Tier 1 environment created
  without the branch policy would be a fully open lane with the suite green.
- **Re-run `730. Repo - Audit Environment Approval Gates`** after the settings change, and update §2
  of `workflow-safety-and-approvals.md` from its output.

### What was considered and rejected

- **A "gate broker" that auto-approves pending deployments with Clarke's token.** This is a gate
  with the reviewer removed and the record obscured. If a run is safe enough to approve by script,
  it belongs in Tier 1, where the absence of a reviewer is visible in settings.
- **Removing the reviewer from `github-prod` wholesale.** Declined in the gate review, and still
  right. That environment holds repo-admin power across the org. Tier 1 is a **new, narrower**
  environment. The old one keeps its reviewer and serves only Tier 2.

## H1: `add_repo` for a repo the session just created

A session's GitHub scope is fixed at start, and each repo added later goes through a confirmation.
For FFC this lands on the commonest path: 701 creates `FFC-EX-<domain>`, and the very session that
asked for it cannot edit it without a person approving the attachment. Three fixes, in order of
preference. Try the first and keep whichever works:

1. **Allow it in the session's own settings.** Multi-repo cloud sessions ignore each clone's
   `.claude/settings.json`. The session root's `/home/user/.claude/settings.json`, rendered at step
   0 by `scripts/verify-conductor-hooks.py --render`, **is** read, and mid-session
   ([`runbooks/conductor-hook-wiring.md`](runbooks/conductor-hook-wiring.md), measured 2026-09-08
   for hooks). Add a `permissions.allow` block to `.claude/conductor/settings.template.json` for
   `mcp__claude-code-remote__add_repo` and the GitHub MCP write tools. This also loads the hub's
   hooks, which no multi-repo session loads today. **Unverified for permissions:** test it once by
   rendering, then calling `add_repo` on a repo not yet in scope. If the confirmation is enforced
   server-side rather than by the session's permission check, this step will not remove it, and the
   next two fixes apply.
2. **Hand the new repo to a new session instead of growing this one.** After 701 succeeds, the
   session calls `create_session` with `source_url` set to the new repo and a self-contained prompt.
   The child session starts with the repo attached, so nothing is added. The parent stays in the hub
   and watches.
3. **Make the hub the only writer.** 701 already seeds the repo from a template. Extend it, or a
   follow-up workflow, to commit the Phase 4 rebrand (`site.config.ts` from the Phase 0 application)
   and the Phase 5 analytics ids **as a PR** using the workflow's own token. The session then never
   needs the charity repo in scope at all. It reads the result through the public clone and merges
   through the merge queue.

Also confirm that the Claude GitHub App is installed on **All repositories** in the org rather than
on a selected list. With a selected list, every new repo is invisible to every session until an
owner adds it, which is a human step that no session-side fix can remove.

## The common workflows, mapped

Notation: ⛔ = a human is required today; ✅ = no human. After the change, only items marked **Tier
2** keep a person.

### A. New charity onboarding

| Step                                   | Today                                      | After                                                      |
| -------------------------------------- | ------------------------------------------ | ---------------------------------------------------------- |
| 0. Find application (221 → 219)        | ✅ (`whmcs-prod-read`)                     | ✅                                                         |
| 3. File + assign provision issue (701) | ✅ agent via MCP                           | ✅                                                         |
| 3a. 701 `dns` job                      | ⛔ `cloudflare-prod-write`                 | ✅ Tier 1 when the zone has no live site; Tier 2 otherwise |
| 3b. 701 `repo` job (chained behind 3a) | ⛔ `github-prod`                           | ✅ Tier 1                                                  |
| 3c. Attach new repo to the session     | ⛔ `add_repo` confirmation                 | ✅ H1                                                      |
| 4. Rebrand PR in `FFC-EX-<domain>`     | ✅ merge queue, 0 approvals                | ✅                                                         |
| 5. 505 GA4 → 503 GTM                   | ⛔⛔ `google-prod-write`                   | ✅ Tier 1 (#636)                                           |
| 5a. Wire ids PR                        | ✅                                         | ✅                                                         |
| 1. Buy domain (113, done **last**)     | ⛔ gate + typed confirm                    | ⛔ **Tier 2**: spends money                                |
| 2. 103 `dry_run=false` on the new zone | ⛔ `cloudflare-prod-write` (+ `m365-prod`) | ✅ Tier 1 with `skip_m365=true` (no live site yet)         |
| 6. 204 WHMCS onboard                   | ⛔ `whmcs-prod`                            | ✅ Tier 1 (idempotent)                                     |
| 6a. 211 accept / cancel order          | ⛔                                         | ⛔ **Tier 2**                                              |

**Human clicks per charity: 8–10 today, 1–2 after** (the domain purchase, plus an order decision
when there is one).

### B. WordPress → GitHub Pages migration (epic #702)

| Step                                       | Today                          | After                                                       |
| ------------------------------------------ | ------------------------------ | ----------------------------------------------------------- |
| 705 inspect / capture                      | ✅ ungated                     | ✅                                                          |
| 701 provision `FFC-EX-<domain>` if missing | ⛔⛔ (see A.3)                 | ✅                                                          |
| Attach the repo                            | ⛔ `add_repo`                  | ✅ H1                                                       |
| 706 `convert`                              | ✅ ungated                     | ✅                                                          |
| 706 `deliver` (opens a PR)                 | ⛔ `github-prod`, at minute 15 | ✅ Tier 1: a PR is reversible and still goes through review |
| 121 DNS-ready preflight                    | ✅ ungated                     | ✅                                                          |
| Merge the site PR                          | ✅ merge queue                 | ✅                                                          |
| 120 / 106 DNS cutover of the live site     | ⛔                             | ⛔ **Tier 2**: moves real visitors off a working site       |

**Per site: 4+ clicks today (more after any post-gate failure), 1 after**, and that one is the
decision that should be human.

### C. Fleet-wide site changes (consent mode, footer standard, CSP)

| Step                                  | Today                                   | After                                                                                                                                      |
| ------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Attach the target repos               | ⛔ per repo not chosen at session start | ✅ the routine attaches them at creation, or H1                                                                                            |
| Push branches, open draft PRs, fix CI | ✅                                      | ✅                                                                                                                                         |
| Promote draft and merge               | ⛔ by standing instruction, per PR      | ✅ for code changes after a **canary** PR merges green; ⛔ only where the change is a **policy** decision (privacy wording, a legal model) |
| Session asks a clarifying question    | ⛔ and the routine wedges               | ✅ the routine logs the question to the tracking issue and continues with the rest                                                         |

The human decision belongs **once per change**, at the canary, not once per repo. "Merge the canary
first, then fan out" is already the consent sweep's own recommendation. Make it the rule.

### D. Sites list refresh (703 → ffcadmin sync)

| Step                    | Today            | After     |
| ----------------------- | ---------------- | --------- |
| 703 data PR             | ⛔ `github-prod` | ✅ Tier 1 |
| Merge data PR, run sync | ✅               | ✅        |

A scheduled data refresh should never need a person. It is the same failure that #834 fixed for the
scheduled reads.

### E. Charity support (206 → 209/210 → 207)

| Step                         | Today                | After                                                        |
| ---------------------------- | -------------------- | ------------------------------------------------------------ |
| 206 issue → ticket           | ⛔ `whmcs-prod`      | ✅ Tier 1: creates a ticket, idempotent by issue number      |
| 209/210 triage               | ✅ `whmcs-prod-read` | ✅                                                           |
| 207 respond, `dry_run=false` | ⛔                   | ⛔ **Tier 2** until replies have a track record; then Tier 1 |

## Routine conventions that follow from this

These turn the tiers into behaviour for the routines that do the work:

- **Each routine run decides or logs. It never asks.** A question goes on the tracking issue as a
  `DECISION NEEDED:` line, and the run continues with everything that does not depend on it. A
  routine that calls `AskUserQuestion` blocks every later firing into the same session.
- **New session per run, state in a tracking issue.** Persistent sessions grow without limit: the
  consent sweep's session had used about 436k tokens of context after six weeks. Its state already
  lives in prose. Move that prose to an issue, and keep the prompt short and stable.
- **Step 0 renders the session settings** (H1, fix 1), so hooks and the permission allowlist load in
  every multi-repo run.
- **Attach the hub plus only the repos the current unit of work needs.** Grow the set with
  `add_repo` once H1 is fixed, rather than cloning a hundred repos every night.
- **Grant only the connectors the routine uses.** Routines get every connector by default, with
  write access and no prompt.
- **A Tier 2 action is reported, not waited on.** Post the run link and what it will do on the
  tracking issue, then end the run. The approval is Clarke's whenever he next looks, and the next
  run picks up from the result.

## Decisions for Clarke

1. Approve the tier split, and the Tier 2 list above, adding or removing items as you see fit.
2. Create the Tier 1 environments and set **"Protected branches only"** on every write environment
   (GitHub settings; a session cannot reach the environments API through the proxy).
3. Confirm the Claude GitHub App covers **All repositories** in the org.
4. Decide whether fleet merges after a green canary may proceed without a per-repo instruction
   (workflow C).

Once 1–3 are decided, each workflow change is an ordinary PR through the merge queue, and the
routine changes are edits to the routines themselves.
