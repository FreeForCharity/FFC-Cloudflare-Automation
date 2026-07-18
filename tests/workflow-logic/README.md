# Workflow-logic tests

Unit tests for the **logic embedded inside GitHub Actions workflows** — the `github-script` blocks,
bash steps, and pwsh steps where this repo's real automation decisions live. The repo's other guards
(reference guard, safety-doc consistency, catalog freshness, actionlint) validate workflow
_structure_; these validate workflow _behavior_, so regressions fail a PR instead of surfacing as
the next quiet production failure.

## How it works

- `wf_extract.py` pulls the **actual script text out of the workflow YAML** at test time. Tests can
  never drift from what ships: change the embedded logic and the tests run against your change
  immediately.
- `harness/github_script_shim.mjs` executes a `github-script` body the same way
  `actions/github-script` does (async wrapper with `context`/`core`/ `github`), capturing
  `setOutput`/`setFailed`/`notice` for assertions.
- `harness/gh` is a fake `gh` CLI put on `PATH`. Scenario fixtures are fed through `TEST_*` env vars
  and every invocation is logged so tests can assert which API calls were — or were not — made.

## Covered today

| Module                    | Workflow under test                     | What it locks down                                                                                                                                                                                          |
| ------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_701_parse.py`       | 701 website-provision `resolve` script  | all three event paths (manual, `repository_dispatch` client_payload, issues), domain normalization, loud failure on missing domain, no comment-posting for non-issue runs (refs #419)                       |
| `test_726_drift_audit.py` | 726 drift audit bash                    | **PUBLIC/private visibility-enum regression** (the bug that silenced `no-merge-queue` in #667), paginated teams fetch, distinct `teams-check-failed` vs false `missing-teams`, Option C private-repo checks |
| `test_720_owner_parse.py` | 720 create-repo tier-team grants (pwsh) | owner-qualified `RepoName` parsing, foreign-owner skip makes zero API calls, all five tier teams granted                                                                                                    |

## Running

```bash
python3 tests/workflow-logic/run_all.py     # everything (what CI runs)
python3 tests/workflow-logic/test_701_parse.py   # one module
```

Needs `python3` + PyYAML and `node`; the 720 module needs `pwsh` and self-skips where PowerShell
isn't installed (it always runs in CI).

## Adding coverage

When you add or change embedded workflow logic, add a scenario here:

1. Extract the step with `wf_extract.step_run(...)` / `step_github_script(...)`.
2. Drive it with a simulated context (JS) or `TEST_*` fixtures + the fake `gh` (shell), asserting on
   outputs, flags, or the gh call log.
3. `run_all.py` auto-discovers any `test_*.py` module — no registration.
