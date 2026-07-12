---
name: run-checks
description:
  Smoke-validate the FFC-Cloudflare-Automation repo without touching live Cloudflare — run the
  workflow reference guard, doc-consistency check, and catalog freshness check. Use when asked to
  run, verify, smoke-test, or sanity-check this automation repo, or before dispatching a workflow.
  To actually run a charity-onboarding workflow, see the charity-onboarding skill.
---

This repo automates Cloudflare / Microsoft 365 / GitHub operations through **81 GitHub Actions
workflows** plus PowerShell and Python scripts. Actually _running_ a workflow dispatches it against
live infrastructure (needs Cloudflare API tokens and approvals) — that flow is documented in the
**`charity-onboarding` skill**.

This skill is the **local smoke test**: the repo's own validators that confirm the workflow catalog,
cross-references, and safety docs are wired correctly. They need **no secrets and touch no
network**, so they run anywhere.

All paths below are relative to the repo root.

## Prerequisites

Python 3.11+ (the validators are stdlib-only). No `pip install` needed.

```bash
python3 --version
```

## Run (agent path)

Run the three guards. Each exits non-zero on a real problem, so they compose in a script or CI gate:

```bash
python3 scripts/check-workflow-references.py       # cross-refs + dispatch targets resolve
python3 scripts/check-workflow-doc-consistency.py  # every workflow covered in the safety-doc table
python3 scripts/generate-workflow-catalog.py --check   # docs/workflow-catalog.json is up to date
```

Expected output (verified in-container):

```
Workflow reference guard OK: 81 workflows, all cross-references resolve and dispatch targets are dispatchable.
Workflow / safety-doc consistency OK: 81 workflows, 72 table rows covered.
```

If `--check` reports the catalog is dirty, regenerate it and commit the result:

```bash
python3 scripts/generate-workflow-catalog.py        # rewrites docs/workflow-catalog.json
```

## Actually dispatching a workflow

That is a different job — it hits live Cloudflare/M365. Use the **`charity-onboarding` skill**
(`.claude/skills/charity-onboarding/SKILL.md`), which maps which workflow to run, in what order, the
approval gates, and the gotchas. From the web sandbox, MCP GitHub tools can dispatch
`workflow_dispatch` workflows once they're merged to `main`.

## Gotchas

- **These guards are the CI gate too.** The repo runs the same three scripts in CI, so a green local
  run predicts a green check. Run them before opening a PR that touches `.github/workflows/` or the
  safety docs.
- **A new workflow file fails the reference guard until it's cross-referenced.** Adding
  `.github/workflows/NN-foo.yml` also means updating the safety-doc table and any dispatch
  references — the guards enforce that pairing.
- **`workflow_dispatch` by filename only works after merge to `main`.** A brand new workflow on a
  branch can't be dispatched by name yet; merge first.
