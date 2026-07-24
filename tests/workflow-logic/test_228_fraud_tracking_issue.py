"""Unit tests for the 228 rolling Fraud tracking-issue github-script (issue #813 item 2).

The scheduled run of 228 reviews the WHMCS Fraud queue and then upserts ONE rolling
tracking issue listing the current Fraud-status orders + their FraudLabs verdicts,
auto-closing it the first scheduled run the queue comes back clear. It is the 732
"rolling issue" state machine applied to the Fraud queue, so the same failure modes
matter: an inverted empty/non-empty branch would spam a fresh issue every weekday (or
never open one), a lost `<!-- marker -->` would break dedupe so trackers pile up as
duplicates that never close, and a widened label query would collide with unrelated
issues. These lock down each branch:

  - Fraud orders + no existing tracker  -> create exactly one issue (marker, labels, table);
  - Fraud orders + existing tracker      -> append a refresh comment, never a second issue;
  - empty queue + existing tracker       -> post the clear notice AND close it;
  - empty queue + no existing            -> clean no-op;
  - malformed FRAUD_REVIEW_JSON          -> treated as an empty queue, never a crash;
  - the "existing" lookup keys on the marker, not merely an open labelled issue;
  - the open-issue query is scoped to state=open + the agentic-os,whmcs labels.

Runs on plain node (the github-script body + issues-API shim) — no pwsh, so it runs in
any sandbox. Mirrors test_732_google_failure_alert.py. Refs #813, #752, AGENTS.md
§"Adding or changing a workflow" (embedded logic gets a unit test).
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

WORKFLOW = "228-whmcs-fraud-review.yml"
JOB = "tracking_issue"
STEP = "Upsert or close rolling Fraud tracking issue"
MARKER = "<!-- whmcs-fraud-review-tracking -->"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "issues_api_shim.mjs"
NODE = shutil.which("node") or "node"


def _order(ordernum="793", userid="421", name="C***", amount="0.00",
           whmcs_status="Fraud", fraudlabs_status="APPROVE", fraudlabs_score="76",
           recommendation="clear-recommended"):
    return {
        "ordernum": ordernum, "userid": userid, "name": name, "amount": amount,
        "whmcs_status": whmcs_status, "fraudlabs_status": fraudlabs_status,
        "fraudlabs_score": fraudlabs_score, "recommendation": recommendation,
    }


def _run(review_json, *, open_issues=None):
    """Drive the tracking step with a given FRAUD_REVIEW_JSON env value.

    review_json may be a Python object (serialized to JSON) or a raw string (to
    exercise the malformed-JSON path).
    """
    script = step_github_script(WORKFLOW, JOB, STEP)
    context = {"repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"}}
    raw = review_json if isinstance(review_json, str) else json.dumps(review_json)
    env = {
        "PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin",
        "FRAUD_REVIEW_JSON": raw,
    }
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script)
        (tdp / "context.json").write_text(json.dumps(context))
        (tdp / "open.json").write_text(json.dumps(open_issues or []))
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_OPEN_ISSUES_FILE"] = str(tdp / "open.json")
        proc = subprocess.run(
            [NODE, str(HARNESS)], env=env, capture_output=True, text=True, timeout=60
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _tracker(number=7, *, with_marker=True):
    body = "WHMCS orders currently held in Fraud status.\n"
    if with_marker:
        body = f"{MARKER}\n{body}"
    return {"number": number, "body": body}


# --- non-empty queue branch ------------------------------------------------


def test_fraud_orders_with_no_tracker_creates_one_issue():
    r = _run([_order()], open_issues=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    issue = r["created"][0]
    assert MARKER in issue["body"], issue
    assert sorted(issue["labels"]) == ["agentic-os", "whmcs"], issue
    # the table names the order and its verdict
    assert "793" in issue["body"], issue
    assert "APPROVE" in issue["body"], issue
    assert "clear-recommended" in issue["body"], issue
    # summary line counts the clear-recommended orders
    assert "**1** recommended for clearing" in issue["body"], issue
    assert r["comments"] == [], r  # nothing to comment on yet
    assert r["updates"] == [], r  # never closes while orders remain


def test_fraud_orders_with_existing_tracker_appends_comment_not_a_second_issue():
    r = _run([_order(), _order(ordernum="794", recommendation="hold-for-human")],
             open_issues=[_tracker(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r  # crucial: no duplicate tracker
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert "794" in r["comments"][0]["body"], r
    assert r["updates"] == [], r  # a non-empty queue never closes the tracker


def test_masked_name_is_forwarded_verbatim_no_raw_pii():
    # The script forwards the already-masked name; it must not reconstruct or leak more.
    r = _run([_order(name="C***")], open_issues=[])
    body = r["created"][0]["body"]
    assert "C***" in body, body


# --- empty queue branch ----------------------------------------------------


def test_empty_queue_with_existing_tracker_closes_it():
    r = _run([], open_issues=[_tracker(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert "no WHMCS orders remain" in r["comments"][0]["body"], r
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r


def test_empty_queue_with_no_tracker_is_a_clean_noop():
    r = _run([], open_issues=[])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


def test_malformed_review_json_is_treated_as_empty_queue():
    # A broken env payload must degrade to "no orders", never crash the step.
    r = _run("{ not valid json", open_issues=[])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


# --- dedupe keys on the marker, not merely an open labelled issue ----------


def test_labelled_issue_without_marker_is_not_treated_as_the_tracker():
    # A human-filed agentic-os,whmcs issue lacking the marker must NOT be mistaken
    # for the rolling tracker: a non-empty queue still opens the real tracker ...
    r = _run([_order()], open_issues=[_tracker(7, with_marker=False)])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert r["comments"] == [], r  # did not comment on the unrelated issue

    # ... and an empty queue does not accidentally close the unrelated issue.
    r2 = _run([], open_issues=[_tracker(7, with_marker=False)])
    assert r2["threw"] is None, r2
    assert r2["updates"] == [], r2
    assert r2["comments"] == [], r2


def test_open_issue_query_is_scoped_to_open_state_and_tracker_labels():
    r = _run([_order()], open_issues=[])
    assert r["threw"] is None, r
    assert len(r["listForRepoCalls"]) == 1, r
    call = r["listForRepoCalls"][0]
    assert call["state"] == "open", call
    assert call["labels"] == "agentic-os,whmcs", call


# --- YAML-level contract guards (no node needed) ---------------------------


def test_marker_literal_is_present_in_the_engine():
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert MARKER in script, "marker literal drifted from the test"


def test_tracking_job_permissions_are_issue_write_read_only_contents():
    wf = load_workflow(WORKFLOW)
    perms = wf["jobs"][JOB].get("permissions", {})
    assert perms.get("issues") == "write", perms
    assert perms.get("contents") == "read", perms


def test_tracking_job_runs_only_on_schedule_after_the_review():
    wf = load_workflow(WORKFLOW)
    job = wf["jobs"][JOB]
    assert "schedule" in str(job.get("if", "")), job
    needs = job.get("needs")
    assert needs == "fraud_review" or "fraud_review" in (needs or []), job


def test_workflow_has_both_schedule_and_dispatch_triggers():
    wf = load_workflow(WORKFLOW)
    # PyYAML parses the bare `on:` key as the boolean True.
    on = wf.get("on", wf.get(True, {}))
    assert "workflow_dispatch" in on, on
    sched = on["schedule"]
    assert any("cron" in c for c in sched), sched
    # weekday cron (1-5), matching the 209/210 triage window
    assert any("1-5" in c["cron"] for c in sched), sched


def test_tracking_step_never_touches_gate_approvals():
    script = step_github_script(WORKFLOW, JOB, STEP)
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"tracker must not touch gate approvals: {forbidden}"
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
