"""Unit tests for the 732 Google-workflow rolling failure-alert github-script.

732 is the canonical "rolling issue" monitor: on a Google reporting workflow
failure it upserts ONE alert issue, and on the next success it auto-closes that
same issue. The whole value of the pattern is that its upsert/close state
machine never drifts — an inverted success/failure branch would spam a new issue
every night (or never open one), and a lost `<!-- marker -->` would break the
dedupe so failures pile up as duplicate issues that never close. These lock down
each branch of that machine:

  - failure + no open alert  -> create exactly one issue (marker, labels, line);
  - failure + existing alert -> append a comment, never a second issue;
  - success + existing alert -> post "Recovered" AND close it;
  - success + no alert       -> clean no-op (no create/comment/close);
  - cancelled / skipped       -> ignored entirely (no writes);
  - the "existing" lookup keys on the marker, not merely an open labelled issue;
  - the open-issue query is scoped to state=open + the google-api,bug labels.

Refs #752 (process assurance — verify the Agentic OS's own monitors keep
working), AGENTS.md §"Adding or changing a workflow" (embedded logic gets a unit
test). Mirrors the 734 janitor coverage (PR #806), which flagged 731/732/733 as
the remaining untested github-script monitors.
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

WORKFLOW = "732-google-workflow-failure-alert.yml"
JOB = "alert"
STEP = "Upsert or close rolling alert issue"
MARKER = "<!-- google-workflow-failure-alert -->"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "issues_api_shim.mjs"
NODE = shutil.which("node") or "node"


def _run(conclusion, *, open_issues=None, name="502. Google - Analytics Report"):
    """Drive the step for a workflow_run with the given conclusion."""
    script = step_github_script(WORKFLOW, JOB, STEP)
    context = {
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {
            "workflow_run": {
                "name": name,
                "conclusion": conclusion,
                "run_number": 42,
                "html_url": "https://github.com/x/y/actions/runs/999",
            }
        },
    }
    env = {"PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin"}
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script)
        (tdp / "context.json").write_text(json.dumps(context))
        (tdp / "open.json").write_text(json.dumps(open_issues or []))
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_OPEN_ISSUES_FILE"] = str(tdp / "open.json")
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


def _alert_issue(number=7, *, with_marker=True):
    body = "A Google reporting workflow is failing.\n"
    if with_marker:
        body = f"{MARKER}\n{body}"
    return {"number": number, "body": body}


# --- failure branch --------------------------------------------------------


def test_failure_with_no_existing_creates_one_issue():
    r = _run("failure", open_issues=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    issue = r["created"][0]
    assert MARKER in issue["body"], issue
    assert sorted(issue["labels"]) == ["bug", "google-api"], issue
    # the failure line names the run and its conclusion
    assert "502. Google - Analytics Report" in issue["body"], issue
    assert "run 42" in issue["body"], issue
    assert r["comments"] == [], r  # nothing to comment on yet
    assert r["updates"] == [], r  # never closes on a failure


def test_failure_with_existing_appends_comment_not_a_second_issue():
    r = _run("failure", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r  # crucial: no duplicate issue
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert "502. Google - Analytics Report" in r["comments"][0]["body"], r
    assert r["updates"] == [], r  # a failure never closes the alert


# --- success branch --------------------------------------------------------


def test_success_with_existing_recovers_and_closes():
    r = _run("success", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    # posts a recovery comment ...
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert "Recovered" in r["comments"][0]["body"], r
    # ... and closes the same issue
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r


def test_success_with_no_existing_is_a_clean_noop():
    r = _run("success", open_issues=[])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


# --- ignored conclusions ---------------------------------------------------


def test_cancelled_is_ignored():
    r = _run("cancelled", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    # not success (no close) and not failure (no comment/create): pure no-op
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


def test_skipped_is_ignored():
    r = _run("skipped", open_issues=[])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


# --- dedupe keys on the marker, not just an open labelled issue ------------


def test_open_labelled_issue_without_marker_is_not_treated_as_the_alert():
    # A human-filed google-api,bug issue that lacks the marker must NOT be
    # mistaken for the rolling alert: a failure still opens the real alert ...
    r = _run("failure", open_issues=[_alert_issue(7, with_marker=False)])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r  # opened its own marked issue
    assert r["comments"] == [], r  # did not comment on the unrelated issue

    # ... and a success does not accidentally close the unrelated issue.
    r2 = _run("success", open_issues=[_alert_issue(7, with_marker=False)])
    assert r2["threw"] is None, r2
    assert r2["updates"] == [], r2
    assert r2["comments"] == [], r2


def test_open_issue_query_is_scoped_to_open_state_and_alert_labels():
    r = _run("failure", open_issues=[])
    assert r["threw"] is None, r
    assert len(r["listForRepoCalls"]) == 1, r
    call = r["listForRepoCalls"][0]
    assert call["state"] == "open", call
    assert call["labels"] == "google-api,bug", call


# --- YAML-level contract guards (no node needed) ---------------------------


def test_marker_literal_is_present_in_the_engine():
    # If the marker string changes, dedupe silently breaks; keep the test's copy
    # honest against the shipped script.
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert MARKER in script, "marker literal drifted from the test"


def test_permissions_are_issue_write_read_only_actions():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms
    assert perms.get("contents") == "read", perms
    assert perms.get("actions") == "read", perms


def test_triggers_on_completed_runs_of_the_google_reporting_workflows():
    wf = load_workflow(WORKFLOW)
    # PyYAML parses the bare `on:` key as the boolean True.
    on = wf.get("on", wf.get(True, {}))
    wr = on["workflow_run"]
    assert wr["types"] == ["completed"], wr
    watched = " ".join(wr["workflows"])
    assert "501. Google - API Smoke" in watched, wr
    assert "502. Google - Analytics Report" in watched, wr


def test_step_is_the_only_writer_and_never_touches_gates():
    # Sanity: the alert step only ever lists/creates/comments/closes issues; it
    # must not reach for any deployment-gate approval surface.
    script = step_github_script(WORKFLOW, JOB, STEP)
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"alert must not touch gate approvals: {forbidden}"
    # confirm the step we extracted is a real github-script step
    step = find_step(load_workflow(WORKFLOW), JOB, STEP)
    assert "github-script" in step.get("uses", ""), step


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
