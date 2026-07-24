"""Unit tests for the 731 Actions-run-metrics github-script.

731 aggregates the last 30 days of Actions runs into a per-workflow stats JSON
artifact that feeds the ffcadmin automation-metrics dashboard. It is the last of
the repo's `github-script` monitors that shipped with zero test coverage (the
trail from #806 → #811 → #821 covered 734/732/733 and each flagged 731 as
remaining). A silent regression here would poison the dashboard: an inverted
conclusion bucket would misreport success rates, a broken duration calc would
skew avg durations, a dropped pagination/window bound would under- or
over-count. These lock down its pure aggregation math and its read-only,
gate-free contract:

  - per-workflow grouping keyed on name (falling back to path);
  - success/failure/cancelled bucketing (other conclusions count as runs only);
  - successRate and avgDurationSec rounding; negative durations clamped to 0;
  - runs missing timestamps contribute no duration;
  - output sorted by run count, descending;
  - the 30-day window is passed as the `created:>=` filter from a frozen clock;
  - pagination fetches every page and stops on a short page;
  - the 2000-run safety cap bounds the sweep;
  - the artifact is written as valid JSON with the expected envelope;
  - read-only (contents/actions: read), and never touches a deployment gate.

Refs #752 (process assurance — verify the Agentic OS's own monitors keep working),
AGENTS.md §"Adding or changing a workflow" (embedded logic gets a unit test).
"""

from __future__ import annotations

import datetime
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow, step_github_script  # noqa: E402

WORKFLOW = "731-actions-run-metrics.yml"
JOB = "metrics"
STEP = "Aggregate run stats"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "metrics_fs_shim.mjs"
NODE = shutil.which("node") or "node"
OUT_FILE = "actions-run-metrics.json"

# A fixed "now" so `since` (Date.now()-30d) and `generatedAt` (new Date()) are
# deterministic. JS toISOString() always emits milliseconds, so ms=0 keeps the
# expected literals clean.
NOW = datetime.datetime(2026, 7, 24, 12, 0, 0, tzinfo=datetime.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
SINCE = NOW - datetime.timedelta(days=30)
SINCE_ISO = SINCE.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # matches JS toISOString()
GENERATED_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z")

CTX = {"repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"}}


def _run(runs):
    script = step_github_script(WORKFLOW, JOB, STEP)
    env = {
        "PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin",
        "TEST_NOW_MS": str(NOW_MS),
    }
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


def _metrics(result):
    """Parse the written artifact from a harness result."""
    assert OUT_FILE in result["files"], f"no {OUT_FILE} written: {result}"
    return json.loads(result["files"][OUT_FILE])


def _run_obj(
    name="203. WHMCS - Something",
    conclusion="success",
    started="2026-07-20T00:00:00Z",
    updated="2026-07-20T00:01:00Z",  # +60s
    path=None,
):
    r = {"name": name, "conclusion": conclusion}
    if started is not None:
        r["run_started_at"] = started
    if updated is not None:
        r["updated_at"] = updated
    if path is not None:
        r["path"] = path
    return r


def _by_name(metrics, name):
    for w in metrics["workflows"]:
        if w["name"] == name:
            return w
    raise AssertionError(f"workflow {name!r} not in {[w['name'] for w in metrics['workflows']]}")


def test_groups_runs_and_counts_conclusions():
    runs = [
        _run_obj(name="A", conclusion="success"),
        _run_obj(name="A", conclusion="failure"),
        _run_obj(name="A", conclusion="cancelled"),
        _run_obj(name="A", conclusion="skipped"),  # counts as a run, no bucket
        _run_obj(name="B", conclusion="success"),
    ]
    r = _run(runs)
    assert r["threw"] is None, r
    assert r["failed"] is None, r
    m = _metrics(r)
    assert m["totalRuns"] == 5, m
    assert m["windowDays"] == 30, m
    a = _by_name(m, "A")
    assert a["runs"] == 4, a
    assert a["failures"] == 1, a
    assert a["cancelled"] == 1, a
    # successRate = 1 success / 4 runs = 0.25 (skipped/cancelled/failure are not successes)
    assert a["successRate"] == 0.25, a


def test_success_rate_rounds_to_three_decimals():
    # 2/3 = 0.6666... -> 0.667
    runs = [
        _run_obj(name="R", conclusion="success"),
        _run_obj(name="R", conclusion="success"),
        _run_obj(name="R", conclusion="failure"),
    ]
    m = _metrics(_run(runs))
    assert _by_name(m, "R")["successRate"] == 0.667, m


def test_avg_duration_is_rounded_seconds():
    # durations 60s, 120s -> avg 90s
    runs = [
        _run_obj(name="D", started="2026-07-20T00:00:00Z", updated="2026-07-20T00:01:00Z"),
        _run_obj(name="D", started="2026-07-20T00:00:00Z", updated="2026-07-20T00:02:00Z"),
    ]
    m = _metrics(_run(runs))
    assert _by_name(m, "D")["avgDurationSec"] == 90, m


def test_negative_duration_is_clamped_to_zero():
    # updated before started (clock skew / retries) must not push avg negative.
    runs = [
        _run_obj(name="N", started="2026-07-20T00:05:00Z", updated="2026-07-20T00:00:00Z"),
    ]
    m = _metrics(_run(runs))
    assert _by_name(m, "N")["avgDurationSec"] == 0, m


def test_runs_missing_timestamps_contribute_no_duration():
    # One timed run (60s) + one with no timestamps -> total 60s over 2 runs = 30s.
    runs = [
        _run_obj(name="M", started="2026-07-20T00:00:00Z", updated="2026-07-20T00:01:00Z"),
        _run_obj(name="M", started=None, updated=None),
    ]
    m = _metrics(_run(runs))
    w = _by_name(m, "M")
    assert w["runs"] == 2, w
    assert w["avgDurationSec"] == 30, w


def test_name_falls_back_to_path_when_unnamed():
    runs = [_run_obj(name=None, path=".github/workflows/999-x.yml", conclusion="success")]
    m = _metrics(_run(runs))
    assert _by_name(m, ".github/workflows/999-x.yml")["runs"] == 1, m


def test_output_sorted_by_run_count_desc():
    runs = (
        [_run_obj(name="few")]
        + [_run_obj(name="many") for _ in range(3)]
        + [_run_obj(name="some") for _ in range(2)]
    )
    m = _metrics(_run(runs))
    order = [w["name"] for w in m["workflows"]]
    assert order == ["many", "some", "few"], order


def test_window_is_thirty_days_from_frozen_now():
    r = _run([_run_obj(name="A")])
    assert r["threw"] is None, r
    assert r["listCalls"], r
    assert r["listCalls"][0]["created"] == f">={SINCE_ISO}", r["listCalls"][0]
    m = _metrics(r)
    assert m["generatedAt"] == GENERATED_ISO, m


def test_pagination_fetches_every_page():
    # 150 runs -> page 1 (100) then page 2 (50, short) -> stop.
    runs = [_run_obj(name="A") for _ in range(150)]
    r = _run(runs)
    assert r["threw"] is None, r
    pages = [c["page"] for c in r["listCalls"]]
    assert pages == [1, 2], r["listCalls"]
    m = _metrics(r)
    assert m["totalRuns"] == 150, m
    assert _by_name(m, "A")["runs"] == 150, m


def test_exactly_100_runs_makes_a_second_probe_page():
    # A full page of exactly 100 is NOT short, so the script requests page 2,
    # which comes back empty (length 0 < 100) and stops.
    runs = [_run_obj(name="A") for _ in range(100)]
    r = _run(runs)
    pages = [c["page"] for c in r["listCalls"]]
    assert pages == [1, 2], r["listCalls"]
    m = _metrics(r)
    assert m["totalRuns"] == 100, m


def test_two_thousand_run_cap_bounds_the_sweep():
    # 2100 available: the fetched>=2000 guard stops after 20 pages (2000 runs);
    # page 21 is never requested.
    runs = [_run_obj(name="A") for _ in range(2100)]
    r = _run(runs)
    assert r["threw"] is None, r
    pages = [c["page"] for c in r["listCalls"]]
    assert pages == list(range(1, 21)), pages  # 1..20, no page 21
    m = _metrics(r)
    assert m["totalRuns"] == 2000, m


def test_empty_repo_writes_valid_empty_metrics():
    r = _run([])
    assert r["threw"] is None, r
    m = _metrics(r)
    assert m["totalRuns"] == 0, m
    assert m["workflows"] == [], m
    # The listing was still probed once.
    assert len(r["listCalls"]) == 1, r


def test_summary_reports_totals():
    runs = [_run_obj(name="A"), _run_obj(name="B")]
    r = _run(runs)
    joined = " ".join(r["summaryText"])
    assert "Actions run metrics" in joined, r["summaryText"]
    assert "Total runs: 2" in joined, r["summaryText"]
    assert "workflows: 2" in joined, r["summaryText"]


# --- YAML-level contract guards (no node needed) ---------------------------


def test_workflow_is_read_only_and_never_approves_a_gate():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("contents") == "read", perms
    assert perms.get("actions") == "read", perms
    # No environment gate on the metrics job.
    assert "environment" not in wf["jobs"][JOB], wf["jobs"][JOB]
    script = step_github_script(WORKFLOW, JOB, STEP)
    # Read-only aggregation: it lists runs and writes a file — nothing that could
    # mutate runs or approve/advance a deployment gate.
    for forbidden in (
        "cancelWorkflowRun",
        "pending_deployments",
        "reviewCustomProtectionRule",
        "createDeployment",
        "approve",
    ):
        assert forbidden not in script, f"metrics job must stay read-only: {forbidden}"


def test_artifact_upload_fails_loud_on_no_file():
    # The metrics artifact must be uploaded with if-no-files-found: error so a
    # silent write regression can't ship a green run with no data. The upload
    # step carries no `name:`, so match on its `uses:` instead.
    steps = load_workflow(WORKFLOW)["jobs"][JOB]["steps"]
    upload = next(s for s in steps if "upload-artifact" in str(s.get("uses", "")))
    assert upload["with"]["if-no-files-found"] == "error", upload
    assert upload["with"]["path"] == OUT_FILE, upload


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
