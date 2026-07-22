"""Unit tests for the 734 stale-waiting-run janitor github-script.

The janitor is the only monitor in this repo that *cancels* workflow runs, so
its decision logic — the staleness cutoff, the dry-run gate, and pagination —
must not drift silently: a wrong cutoff or an inverted dry-run flag would cancel
runs legitimately paused at an environment approval gate. These lock down:

  - only runs older than `maxAgeDays` are cancelled (fresh ones untouched);
  - `maxAgeDays` is honored (a huge threshold cancels nothing);
  - dry-run reports without cancelling; a real run cancels exactly the stale set;
  - a scheduled run defaults to a *real* cancel (the surprising, risky default);
  - pagination fetches every page of waiting runs;
  - a per-run cancel error is swallowed (the sweep continues, step stays green);
  - the janitor only ever lists/cancels — it never approves a gate.

Refs #752 (process assurance — verify the Agentic OS's own monitors keep working),
AGENTS.md §"Adding or changing a workflow" (embedded logic gets a unit test).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_github_script  # noqa: E402

WORKFLOW = "734-stale-waiting-run-janitor.yml"
JOB = "janitor"
STEP = "Cancel stale waiting runs"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "actions_run_shim.mjs"
NODE = shutil.which("node") or "node"

# Unambiguously stale / fresh timestamps: far enough from any real "now" that the
# test never depends on when it runs. 2000 is always > default cutoff old; 2999 is
# never older than any sane threshold.
STALE_TS = "2000-01-01T00:00:00Z"
FRESH_TS = "2999-01-01T00:00:00Z"

CTX = {
    "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
    "eventName": "schedule",
    "payload": {},
}


def _run(runs, *, dry_run=None, max_age_days=None, cancel_fail_ids=None):
    script = step_github_script(WORKFLOW, JOB, STEP)
    env = {
        "PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin",
    }
    if dry_run is not None:
        env["DRY_RUN"] = dry_run
    if max_age_days is not None:
        env["MAX_AGE_DAYS"] = max_age_days
    if cancel_fail_ids:
        env["TEST_CANCEL_FAIL_IDS"] = ",".join(str(i) for i in cancel_fail_ids)
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script)
        (tdp / "context.json").write_text(json.dumps(CTX))
        (tdp / "runs.json").write_text(json.dumps(runs))
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_RUNS_FILE"] = str(tdp / "runs.json")
        proc = subprocess.run(
            [NODE, str(HARNESS)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _run_obj(run_id, created_at, name="203. WHMCS - Something"):
    return {
        "id": run_id,
        "name": name,
        "created_at": created_at,
        "html_url": f"https://github.com/x/y/actions/runs/{run_id}",
    }


def test_only_stale_runs_are_cancelled():
    r = _run([_run_obj(1, STALE_TS), _run_obj(2, FRESH_TS)], dry_run="false")
    assert r["threw"] is None, r
    assert r["failed"] is None, r
    assert r["cancelledIds"] == [1], r  # fresh run 2 is left alone


def test_large_threshold_cancels_nothing():
    # 36500 days ~= 100y: even a 2000 run is not older than the cutoff.
    r = _run([_run_obj(1, STALE_TS)], dry_run="false", max_age_days="36500")
    assert r["threw"] is None, r
    assert r["cancelAttempts"] == [], r


def test_dry_run_reports_without_cancelling():
    r = _run([_run_obj(1, STALE_TS), _run_obj(2, STALE_TS)], dry_run="true")
    assert r["threw"] is None, r
    assert r["cancelAttempts"] == [], r  # nothing cancelled in dry-run
    # It still recognises the stale runs (summary heading counts them).
    assert any("2 run(s)" in s for s in r["summaryText"]), r


def test_real_run_cancels_all_stale():
    runs = [_run_obj(i, STALE_TS) for i in (10, 11, 12)]
    r = _run(runs, dry_run="false")
    assert r["threw"] is None, r
    assert sorted(r["cancelledIds"]) == [10, 11, 12], r


def test_unset_dry_run_defaults_to_real_cancel():
    # A scheduled run passes DRY_RUN='false' via the step expression, but even an
    # absent value must not accidentally enable dry-run (String(undefined) != 'true').
    r = _run([_run_obj(1, STALE_TS)])  # DRY_RUN not set at all
    assert r["threw"] is None, r
    assert r["cancelledIds"] == [1], r


def test_pagination_fetches_every_page():
    runs = [_run_obj(i, STALE_TS) for i in range(101)]  # 100 -> page2 (1 more)
    r = _run(runs, dry_run="false")
    assert r["threw"] is None, r
    pages = [c["page"] for c in r["listCalls"]]
    assert pages == [1, 2], r  # stopped after the short second page
    assert len(r["cancelledIds"]) == 101, r
    assert all(c["status"] == "waiting" for c in r["listCalls"]), r


def test_cancel_error_is_swallowed_and_sweep_continues():
    runs = [_run_obj(i, STALE_TS) for i in (1, 2, 3)]
    r = _run(runs, dry_run="false", cancel_fail_ids=[2])
    assert r["threw"] is None, r  # one 409 must not abort the whole step
    assert r["failed"] is None, r
    assert r["cancelAttempts"] == [1, 2, 3], r  # every stale run was attempted
    assert r["cancelledIds"] == [1, 3], r  # 2 threw; 1 and 3 still cancelled


def test_empty_queue_is_a_clean_noop():
    r = _run([], dry_run="false")
    assert r["threw"] is None, r
    assert r["cancelAttempts"] == [], r
    assert any("clean" in s.lower() for s in r["summaryText"]), r


# --- YAML-level contract guards (no node needed) ---------------------------


def test_scheduled_run_defaults_to_real_cancel_expression():
    step = find_step(load_workflow(WORKFLOW), JOB, STEP)
    dry_expr = str(step["env"]["DRY_RUN"])
    # dry-run only when a workflow_dispatch explicitly asks; schedule -> 'false'.
    assert "workflow_dispatch" in dry_expr, dry_expr
    assert "'false'" in dry_expr, dry_expr


def test_workflow_is_cancel_only_never_approves_a_gate():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("actions") == "write", perms
    assert perms.get("contents") == "read", perms
    script = step_github_script(WORKFLOW, JOB, STEP)
    # It lists waiting runs and cancels them — and does nothing that could
    # approve/advance an environment deployment gate.
    assert "cancelWorkflowRun" in script, script
    assert "waiting" in script, script
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"janitor must not touch gate approvals: {forbidden}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    sys.exit(1 if failures else 0)
