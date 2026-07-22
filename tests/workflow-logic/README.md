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

| Module                             | Workflow under test                                       | What it locks down                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_701_parse.py`                | 701 website-provision `resolve` script                    | all three event paths (manual, `repository_dispatch` client_payload, issues), domain normalization, loud failure on missing domain, no comment-posting for non-issue runs (refs #419)                                                                                                                                                                                                                                                                                                                                                                       |
| `test_726_drift_audit.py`          | 726 drift audit bash                                      | **PUBLIC/private visibility-enum regression** (the bug that silenced `no-merge-queue` in #667), paginated teams fetch, distinct `teams-check-failed` vs false `missing-teams`, Option C private-repo checks                                                                                                                                                                                                                                                                                                                                                 |
| `test_720_owner_parse.py`          | 720 create-repo tier-team grants (pwsh)                   | owner-qualified `RepoName` parsing, foreign-owner skip makes zero API calls, all five tier teams granted                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `test_736_preflight.py`            | 736 archive `preflight` job                               | live archive requires a matching successful dry-run within 48h; fails fast on missing/already-archived; warns (non-blocking) on recent push/Pages/open-issue references; dry runs bypass all checks                                                                                                                                                                                                                                                                                                                                                         |
| `test_502_deliver.py`              | 502 GA-report `deliver` bash                              | **shallow-clone sync-branch regression (#733):** bases the `chore/ga-data-sync` checkout on `FETCH_HEAD` (a `--depth 1` clone has no `origin/<branch>` ref); fresh-branch + `pr create` when absent; early-exit with no push/PR churn when data is unchanged. Git runs against a local bare repo, `gh` is faked — no network.                                                                                                                                                                                                                               |
| `test_734_stale_run_janitor.py`    | 734 stale-waiting-run janitor `github-script`             | the only monitor that **cancels** runs: staleness cutoff honours `maxAgeDays` (a huge threshold cancels nothing); only runs older than the cutoff are cancelled; dry-run reports without cancelling; a scheduled run defaults to a real cancel; pagination fetches every page; a per-run cancel 409 is swallowed so the sweep stays green; cancel-only — never approves a gate. Uses `harness/actions_run_shim.mjs` (Actions runs API + `core.summary` mocks).                                                                                              |
| `test_732_google_failure_alert.py` | 732 Google-workflow rolling failure alert `github-script` | the **rolling issue upsert/close** state machine: failure + no alert opens exactly one marked issue (labels `google-api,bug`); failure + existing appends a comment (never a duplicate issue); success + existing posts "Recovered" **and** closes it; success + no alert is a clean no-op; cancelled/skipped are ignored; the existing-alert lookup keys on the `<!-- marker -->`, not merely an open labelled issue; the query is scoped to `state=open` + the alert labels. Uses `harness/issues_api_shim.mjs` (issue list/create/comment/update mocks). |

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
