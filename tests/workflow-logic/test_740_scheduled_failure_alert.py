"""Unit tests for the 740 scheduled-workflow rolling failure-alert github-script.

740 generalises 732's rolling-issue monitor to the scheduled NON-Google hub
workflows (726 drift audit, 734 janitor, 738 smoke drift, …). It exists because
a gated daily workflow can fail and nothing tells anyone: 726 failed at
2026-07-24 13:33Z and sat unnoticed for ~8.5 hours (#832). Silence read as green.

The state machine is the same as 732's and needs the same guarantees — never a
duplicate issue, never a missed close — plus three that are specific to 740:

  - `cancelled` is REPORTABLE, not ignored. The 13:33Z run was cancelled before
    it reached a runner, executed zero steps, and the run-level conclusion
    (failure) disagreed with the job-level one (cancelled). Filtering to
    `failure` alone would still have missed half the incident.
  - **One rolling issue PER WATCHED WORKFLOW.** The marker is keyed by workflow
    name, so a success only closes the alert for the workflow that recovered.
    With ten workflows watched, a shared marker would let 739's weekly green run
    silently close 726's daily outage — the alert would clear itself while the
    thing it is watching is still broken (found by Copilot on PR #833).
  - **Isolation from 732.** Both alerters watch different workflows and write to
    the same repo; a marker or label collision would let one close the other's
    issue. 740 uses `<!-- scheduled-workflow-failure-alert:NAME -->` +
    `agentic-os,bug` against 732's `<!-- google-workflow-failure-alert -->` +
    `google-api,bug`, and neither may match the other's issue.
  - **The watch list is by exact `name:` string**, which is the part that breaks
    silently: a `workflow_run.workflows` entry that does not match any workflow's
    `name:` never fires and never errors. #832's own draft got two of the three
    names wrong (`734 … [GH]` for `[Repo]`, `738 … [Repo]` for `[Org]`), so the
    list is verified against the shipped workflow files here.

Refs #832, #752 (process assurance), AGENTS.md §"Adding or changing a workflow".
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import (  # noqa: E402
    WORKFLOWS,
    find_step,
    load_workflow,
    step_github_script,
)

WORKFLOW = "740-scheduled-workflow-failure-alert.yml"
JOB = "alert"
STEP = "Upsert or close rolling alert issue"
MARKER_PREFIX = "<!-- scheduled-workflow-failure-alert:"
GOOGLE_MARKER = "<!-- google-workflow-failure-alert -->"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "issues_api_shim.mjs"
NODE = shutil.which("node") or "node"

WATCHED_NAME = "726. Repo - Rulesets + Settings Drift Audit [Org]"
OTHER_WATCHED_NAME = "739. Repo - Process Health Metrics Report [GH]"
MARKER = f"{MARKER_PREFIX}{WATCHED_NAME} -->"
OTHER_MARKER = f"{MARKER_PREFIX}{OTHER_WATCHED_NAME} -->"


def _run(conclusion, *, open_issues=None, name=WATCHED_NAME, head_branch="main"):
    """Drive the step for a workflow_run with the given conclusion."""
    script = step_github_script(WORKFLOW, JOB, STEP)
    context = {
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {
            "repository": {"default_branch": "main"},
            "workflow_run": {
                "name": name,
                "conclusion": conclusion,
                "run_number": 42,
                "head_branch": head_branch,
                "html_url": "https://github.com/x/y/actions/runs/999",
            },
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


def _alert_issue(number=7, *, marker=MARKER):
    body = "ended in a bad conclusion.\n"
    if marker:
        body = f"{marker}\n{body}"
    return {"number": number, "body": body}


# --- failure branch --------------------------------------------------------


def test_failure_with_no_existing_creates_one_issue():
    r = _run("failure", open_issues=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    issue = r["created"][0]
    assert MARKER in issue["body"], issue
    assert WATCHED_NAME in issue["title"], issue  # title names the failing workflow
    assert sorted(issue["labels"]) == ["agentic-os", "bug"], issue
    # the failure line names the workflow, the run and its conclusion
    assert WATCHED_NAME in issue["body"], issue
    assert "run 42" in issue["body"], issue
    assert "https://github.com/x/y/actions/runs/999" in issue["body"], issue
    assert "failure" in issue["body"], issue
    assert r["comments"] == [], r  # nothing to comment on yet
    assert r["updates"] == [], r  # never closes on a failure


def test_failure_with_existing_appends_comment_not_a_second_issue():
    r = _run("failure", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r  # crucial: no duplicate issue
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert WATCHED_NAME in r["comments"][0]["body"], r
    assert r["updates"] == [], r  # a failure never closes the alert


# --- cancelled is reportable (the #832 regression) -------------------------


def test_cancelled_is_reported_not_ignored():
    # A run cancelled before it reached a runner is the exact incident that made
    # this workflow necessary — 732 ignores `cancelled`, 740 must not.
    r = _run("cancelled", open_issues=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert "cancelled" in r["created"][0]["body"], r


def test_cancelled_with_existing_appends_rather_than_duplicating():
    r = _run("cancelled", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert len(r["comments"]) == 1, r
    assert "cancelled" in r["comments"][0]["body"], r


def test_timed_out_and_startup_failure_are_reported():
    for conclusion in ("timed_out", "startup_failure", "action_required"):
        r = _run(conclusion, open_issues=[])
        assert r["threw"] is None, (conclusion, r)
        assert len(r["created"]) == 1, (conclusion, r)
        assert conclusion in r["created"][0]["body"], (conclusion, r)


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


# --- benign conclusions stay quiet ----------------------------------------


def test_skipped_is_ignored():
    r = _run("skipped", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


def test_neutral_is_ignored():
    r = _run("neutral", open_issues=[])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


def test_benign_conclusions_cost_no_api_call():
    # A guaranteed no-op should not spend REST budget: the whole fleet of watched
    # crons shares one 5,000/hr pool with every other agent (AGENTS.md).
    for conclusion in ("skipped", "neutral"):
        r = _run(conclusion, open_issues=[_alert_issue(7)])
        assert r["threw"] is None, (conclusion, r)
        assert r["listForRepoCalls"] == [], (conclusion, r)


def test_success_still_spends_the_lookup_so_it_can_close():
    # `success` is benign in outcome but must NOT early-return: closing a live
    # alert is the whole recovery half of the state machine.
    r = _run("success", open_issues=[_alert_issue(7)])
    assert len(r["listForRepoCalls"]) == 1, r
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r


# --- branch scoping --------------------------------------------------------


def test_failure_on_a_feature_branch_is_ignored():
    # A red run on someone's PR branch is not a fleet alert.
    r = _run("failure", open_issues=[], head_branch="claude/some-branch")
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r
    assert r["listForRepoCalls"] == [], r  # bails before spending an API call


def test_success_on_a_feature_branch_does_not_close_the_alert():
    r = _run("success", open_issues=[_alert_issue(7)], head_branch="claude/some-branch")
    assert r["threw"] is None, r
    assert r["updates"] == [], r
    assert r["comments"] == [], r


# --- one rolling issue per watched workflow --------------------------------


def test_success_of_a_different_watched_workflow_does_not_close_the_alert():
    # The defect this keying exists to prevent: 739's weekly green run must not
    # clear 726's still-broken daily alert.
    r = _run("success", open_issues=[_alert_issue(7)], name=OTHER_WATCHED_NAME)
    assert r["threw"] is None, r
    assert r["updates"] == [], r  # 726's alert stays open
    assert r["comments"] == [], r


def test_success_closes_only_its_own_workflows_alert():
    r = _run(
        "success",
        open_issues=[_alert_issue(7, marker=OTHER_MARKER), _alert_issue(9)],
        name=OTHER_WATCHED_NAME,
    )
    assert r["threw"] is None, r
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r
    assert [c["issue_number"] for c in r["comments"]] == [7], r


def test_a_second_workflow_failing_opens_its_own_issue():
    r = _run("failure", open_issues=[_alert_issue(7)], name=OTHER_WATCHED_NAME)
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r  # not a comment on 726's issue
    assert OTHER_MARKER in r["created"][0]["body"], r
    assert r["comments"] == [], r


# --- isolation from 732 ----------------------------------------------------


def test_a_732_rolling_issue_is_never_matched_or_closed_by_740():
    # 732's issue carries a different marker; 740 must open its own on failure ...
    r = _run("failure", open_issues=[_alert_issue(7, marker=GOOGLE_MARKER)])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert r["comments"] == [], r

    # ... and must not close it on success.
    r2 = _run("success", open_issues=[_alert_issue(7, marker=GOOGLE_MARKER)])
    assert r2["threw"] is None, r2
    assert r2["updates"] == [], r2
    assert r2["comments"] == [], r2


def test_unmarked_open_agentic_os_bug_issue_is_not_treated_as_the_alert():
    # The backlog is full of open agentic-os,bug issues; only the marked one is
    # the rolling alert.
    r = _run("failure", open_issues=[_alert_issue(7, marker=None)])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert r["comments"] == [], r


def test_markers_and_labels_do_not_collide_with_732():
    script = step_github_script(WORKFLOW, JOB, STEP)
    google = step_github_script(
        "732-google-workflow-failure-alert.yml", "alert", "Upsert or close rolling alert issue"
    )
    assert MARKER_PREFIX in script, "marker literal drifted from the test"
    assert GOOGLE_MARKER not in script, "740 must not reuse 732's marker"
    assert MARKER_PREFIX not in google, "732 must not reuse 740's marker"
    assert "'agentic-os', 'bug'" in script, script
    assert "google-api" not in script, "740 must use its own labels"


def test_open_issue_query_is_scoped_to_open_state_and_alert_labels():
    r = _run("failure", open_issues=[])
    assert r["threw"] is None, r
    assert len(r["listForRepoCalls"]) == 1, r
    call = r["listForRepoCalls"][0]
    assert call["state"] == "open", call
    assert call["labels"] == "agentic-os,bug", call
    # `agentic-os,bug` is broad and the backlog is long: page at the max and put
    # the most recently touched issues first, so a live alert cannot fall off
    # page 1 and get duplicated.
    assert call["per_page"] == 100, call
    assert call["sort"] == "updated", call
    assert call["direction"] == "desc", call


# --- YAML-level contract guards (no node needed) ---------------------------


def _watched(workflow_file: str) -> list:
    wf = load_workflow(workflow_file)
    # PyYAML parses the bare `on:` key as the boolean True.
    on = wf.get("on", wf.get(True, {}))
    return on["workflow_run"]["workflows"]


def _all_workflow_names() -> dict:
    names = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        import yaml

        data = yaml.safe_load(path.read_text())
        if isinstance(data, dict) and data.get("name"):
            names[data["name"]] = path.name
    return names


def test_every_watched_name_matches_a_real_workflow_name():
    # The silent-failure mode this whole workflow exists to prevent: a
    # `workflows:` entry that matches no `name:` never fires and never errors.
    known = _all_workflow_names()
    for name in _watched(WORKFLOW):
        assert name in known, f"watched name has no matching workflow name: {name!r}"


def test_watched_workflows_are_actually_scheduled():
    known = _all_workflow_names()
    for name in _watched(WORKFLOW):
        wf = load_workflow(known[name])
        on = wf.get("on", wf.get(True, {}))
        assert "schedule" in on, f"{name} is not scheduled; 740 watches cron-driven workflows"


def test_the_incident_workflows_from_832_are_watched():
    watched = _watched(WORKFLOW)
    for required in (
        "726. Repo - Rulesets + Settings Drift Audit [Org]",
        "734. Repo - Stale Waiting-Run Janitor [Repo]",
        "738. Repo - Fleet Smoke Engine Drift Audit [Org]",
    ):
        assert required in watched, f"{required} must stay watched (#832)"


def test_740_and_732_watch_disjoint_workflow_sets():
    overlap = set(_watched(WORKFLOW)) & set(_watched("732-google-workflow-failure-alert.yml"))
    assert not overlap, f"double-alerting on {overlap}"


def test_740_does_not_watch_itself():
    wf = load_workflow(WORKFLOW)
    assert wf["name"] not in _watched(WORKFLOW), "self-watching would loop"


def test_triggers_only_on_completed_runs():
    wf = load_workflow(WORKFLOW)
    on = wf.get("on", wf.get(True, {}))
    assert list(on) == ["workflow_run"], on
    assert on["workflow_run"]["types"] == ["completed"], on


def test_permissions_are_issue_write_read_only_actions():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms
    assert perms.get("contents") == "read", perms
    assert perms.get("actions") == "read", perms


def test_alerter_is_ungated_and_uses_the_ambient_token_only():
    # It must never be blocked by the gate it is watching, and must not need a PAT.
    wf = load_workflow(WORKFLOW)
    job = wf["jobs"][JOB]
    assert "environment" not in job, job
    raw = (WORKFLOWS / WORKFLOW).read_text()
    assert "CBM_TOKEN" not in raw, "740 must use the ambient GITHUB_TOKEN only"


def test_step_is_the_only_writer_and_never_touches_gates():
    script = step_github_script(WORKFLOW, JOB, STEP)
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"alert must not touch gate approvals: {forbidden}"
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
