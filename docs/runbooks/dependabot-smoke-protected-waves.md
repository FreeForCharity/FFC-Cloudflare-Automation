# Dependabot upgrades (smoke-protected waves)

This runbook formalizes how Free For Charity processes Dependabot PRs safely across supported repos.

The guiding rule:

- Only merge **small waves** of Dependabot PRs that are explicitly **Ready=true**.
- After each wave, run **post-deploy smoke triage** to confirm no new live-site incidents were
  created before starting the next wave.

## Why waves

Dependabot changes are dependency changes. Even when CI is green, they can impact runtime behavior.
Waves reduce blast radius and create a repeatable audit trail.

## Risk controls (non-negotiable)

- **Readiness gating**: only attempt merges where readiness output says `Ready=true`.
  - This includes checks passing and mergeability (no merge conflicts).
- **Post-smoke monitoring** after each wave.
  - Smoke is evaluated only after merge/deploy. If triage finds incidents for **live** sites, stop
    and investigate before running the next wave.
- **Non-live sites** must be explicitly registered so noise is treated correctly.
  - See [data/non-live-sites.json](../../data/non-live-sites.json).
- **Never paste tokens** into scripts or logs.
  - `gh` uses your local auth context.

## Inputs (tracked)

- Supported repos for post-smoke triage:
  [data/dependabot-affected-repos.json](../../data/dependabot-affected-repos.json)
- Non-live registry: [data/non-live-sites.json](../../data/non-live-sites.json)

## Artifacts (local-only)

Wave runs write timestamped artifacts to `reviews/` (gitignored). These provide an audit trail and
can be referenced in issue updates.

## Standard workflow (recommended)

### 0) Prereqs

- `gh` installed and authenticated (`gh auth status`)
- PowerShell 7+ recommended
- Permissions to merge PRs in the FreeForCharity org

### 0.5) Refresh the repo inventory (recommended)

This keeps the triage scope aligned with the current FreeForCharity org repo list.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\Update-DependabotAffectedRepos.ps1
```

Alternatively, the scheduled workflow
[735-repo-dependabot-affected-repos.yml](../../.github/workflows/735-repo-dependabot-affected-repos.yml)
can keep this file refreshed via an automated PR.

### 1) Run a wave (fully automated)

This runs inventory → readiness → merge/queue → post-smoke triage → tracking update.

Important: the smoke check is intentionally post-merge. It is not a pre-merge blocker for the
current wave; it is the decision point for whether the next wave should proceed.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotWave.ps1 `
  -WaveNumber 17 `
  -CommentOnIncidents
```

Dry run (no merges, no comments, no issue updates):

Note: `-WhatIf` still generates local `reviews/` artifacts (including post-smoke triage JSON) for
review, but it will not perform merges, incident comments, or the tracking issue comment.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotWave.ps1 `
  -WaveNumber 17 `
  -WhatIf
```

### 2) What to review after the wave

In `reviews/` you will get (names include timestamp):

- `dependabot-open-prs-search-*.json` — current inventory
- `dependabot-merge-readiness-*.json` — readiness report
- `dependabot-wave<NN>-ready-all-*.json` — ready-only subset
- `dependabot-wave<NN>-automerge-*.json` — merge/queue results
- `post-smoke-triage-*.json` + `post-smoke-triage-latest.json` — post-smoke triage snapshot
- `dependabot-issue42-wave<NN>-update-*.md` — tracking issue update

### 3) If readiness is 0/N

That’s normal sometimes. Common blockers:

- Pending or failed checks
- Merge conflicts (`mergeable != MERGEABLE`)
- Review requirements (where enforced)

In that case, the wave is a no-op merge pass but still produces artifacts and a tracking update.

## Supporting scripts

- [scripts/Test-PrMergeReadiness.ps1](../../scripts/Test-PrMergeReadiness.ps1) — computes readiness
  via GraphQL
- [scripts/Invoke-DependabotAutoMerge.ps1](../../scripts/Invoke-DependabotAutoMerge.ps1) —
  merges/queues Ready=true items
- [scripts/post-smoke-triage.ps1](../../scripts/post-smoke-triage.ps1) — post-smoke triage +
  idempotent incident comments

## Notes on incidents

Post-smoke triage comments are idempotent (a marker is used) to avoid spamming issues.

Operationally, live-site incidents found here should pause the next wave, not retroactively block
the already-merged wave.

For non-live sites, the comment must make it clear the incident is expected pre-launch and include
GitHub Pages publishing target context (custom domain vs default Pages URL).
