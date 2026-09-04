"""Unit tests for the 740 scheduled-workflow rolling failure-alert github-script.

740 is the hub's single failure alerter for every scheduled workflow (726 drift
audit, 734 janitor, 738 smoke drift, Google 502 + 504, …). It exists
because a gated daily workflow can fail and nothing tells anyone: 726 failed at
2026-07-24 13:33Z and sat unnoticed for ~8.5 hours (#832). Silence read as green.

**It is a POLL, not a `workflow_run` watcher (#843).** The event-driven version
never fired even once — `?event=workflow_run` returned total_count 0 for this
repo all time, while sibling repos showed hundreds — so the alerting layer was
dead for six days and nothing could reveal it, because a `workflow_run` watcher
cannot be run on demand. The poll can, and `workflow_dispatch` is therefore part
of the contract, not a convenience. 732 (the Google-lane twin, equally dead) is
retired into this file.

The state machine is unchanged by that conversion and needs the same guarantees —
never a duplicate issue, never a missed close — plus the ones specific to 740:

  - `cancelled` is REPORTABLE, not ignored. The 13:33Z run was cancelled before
    it reached a runner, executed zero steps, and the run-level conclusion
    (failure) disagreed with the job-level one (cancelled). Filtering to
    `failure` alone would still have missed half the incident.
  - **A declined approval gate is not an outage.** Watched workflow 703 is
    gated on `github-prod` (726 and 735 moved to the ungated `github-prod-read`
    lane in #834, leaving 703 the only gated one), and a declined or expired gate
    surfaces as run-level `failure` with its jobs `cancelled` — identical to a
    real fault in the run object. #834 measured 8 of 21 scheduled runs on that
    lane ending that way, so without the job-level discriminator the alerter's
    dominant output would be "a human said no", and it would be ignored by its
    second week. The negative control (a genuinely failed job, run 29826157099)
    must still alert; an unreadable job list must alert too, never stay quiet.
  - **One rolling issue PER WATCHED WORKFLOW.** The marker is keyed by workflow
    name, so a success only closes the alert for the workflow that recovered.
    With a dozen workflows watched, a shared marker would let 739's weekly green
    run silently close 726's daily outage — the alert would clear itself while
    the thing it is watching is still broken (found by Copilot on PR #833).
  - **The marker keys on the WORKFLOW name, never `run.name`.** The runs API
    returns the rendered `run-name:` when a workflow sets one, which would
    scatter one workflow's alerts across a new marker on every run. Only the
    poll shape can get this wrong, so it is tested explicitly.
  - **The watch list must be resolved past `per_page`.** The repo has >100
    workflows and `per_page` caps at 100; an unpaginated lookup silently drops
    the tail, which already produced one false "740 is not registered" reading
    while diagnosing #843.
  - **A watched name that matches no workflow fails LOUD.** An entry matching
    nothing never fires and never errors — that silence is precisely #832's
    failure mode, and #832's own draft got two of three names wrong.
  - **The watch list is scheduled workflows only.** Polling "the latest run" of
    a dispatch-only workflow reports whatever a human last ran by hand: a stale
    red would alert forever and a stale green would mean nothing. This is why
    501 (dispatch/`workflow_call` only) is excluded even though retired 732
    watched it, while 502 and 504 are absorbed.

Refs #843, #832, #752 (process assurance), AGENTS.md §"Adding or changing a workflow".
"""

from __future__ import annotations

import datetime
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import (  # noqa: E402
    WORKFLOWS,
    child_env,
    find_step,
    load_workflow,
    step_github_script,
)

WORKFLOW = "740-scheduled-workflow-failure-alert.yml"
JOB = "alert"
STEP = "Upsert or close rolling alert issue"
MARKER_PREFIX = "<!-- scheduled-workflow-failure-alert:"
# 732's marker and labels. 732 is deleted, but a stray issue carrying them must
# never be adopted or closed by 740 — and 740 must never reuse the identifiers.
LEGACY_GOOGLE_MARKER = "<!-- google-workflow-failure-alert -->"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "issues_api_shim.mjs"
NODE = shutil.which("node") or "node"

WATCHED_NAME = "726. Repo - Rulesets + Settings Drift Audit [Org]"
OTHER_WATCHED_NAME = "739. Repo - Process Health Metrics Report [GH]"
MARKER = f"{MARKER_PREFIX}{WATCHED_NAME} -->"
OTHER_MARKER = f"{MARKER_PREFIX}{OTHER_WATCHED_NAME} -->"

# A run object's `name` is the rendered `run-name:`, not the workflow's `name:`.
# Every fixture uses a distinct value so any accidental use of it is visible.
RUN_DISPLAY_NAME = "Drift audit: nightly"


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _watched() -> list:
    """The shipped watch list, read out of the step's `env:` block.

    Kept in YAML rather than inside the script so these guards read the list that
    actually ships instead of parsing JavaScript.
    """
    raw = _step()["env"]["WATCHED_WORKFLOWS"]
    return [line.strip() for line in raw.split("\n") if line.strip()]


def _run(
    conclusion,
    *,
    open_issues=None,
    name=WATCHED_NAME,
    head_branch="main",
    jobs=None,
    jobs_throw=False,
    last_green=None,
    success_runs_throw=None,
    watched=None,
    registered=None,
    filler_workflows=0,
    runs_throw=None,
    extra_runs=None,
):
    """Drive one poll sweep in which `name` has `conclusion` as its latest run.

    Every other watched workflow has no completed run, so the assertions stay
    about the one workflow under test. `jobs` is the fixture returned by
    actions.listJobsForWorkflowRun — the only place the run can be told apart
    from a declined gate. It defaults to a single failed job so the ordinary
    failure cases still alert.

    `filler_workflows` pads the repo inventory ahead of the watched ones, so a
    caller that fails to paginate cannot resolve them. `registered` is the set of
    names the repo actually declares — pass a narrower list than `watched` to
    model a workflow that was renamed out from under the watch list.

    `last_green` is how many days ago this workflow last SUCCEEDED, appended to
    its run list behind the latest run (the API returns newest first). `None`
    means it has never gone green. It is what separates a gate declined once from
    a gate nobody has answered in weeks.
    """
    script = step_github_script(WORKFLOW, JOB, STEP)
    watched = _watched() if watched is None else watched
    registered = watched if registered is None else registered
    context = {
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {"repository": {"default_branch": "main"}},
    }
    # Repo inventory: optional filler, then one entry per watched name.
    inventory = [{"id": 900000 + i, "name": f"{i}. Filler [X]"} for i in range(filler_workflows)]
    ids = {}
    for i, wf_name in enumerate(registered):
        wf_id = 100 + i
        ids[wf_name] = wf_id
        inventory.append({"id": wf_id, "name": wf_name})

    runs = dict(extra_runs or {})
    if name in ids:
        runs[str(ids[name])] = [
            {
                "id": 30116967112,
                "name": RUN_DISPLAY_NAME,
                "conclusion": conclusion,
                "run_number": 42,
                "head_branch": head_branch,
                "html_url": "https://github.com/x/y/actions/runs/999",
            }
        ]
        if last_green is not None:
            green_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=last_green
            )
            runs[str(ids[name])].append(
                {
                    "id": 30116960000,
                    "name": RUN_DISPLAY_NAME,
                    "conclusion": "success",
                    "run_number": 41,
                    "head_branch": head_branch,
                    "updated_at": green_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "html_url": "https://github.com/x/y/actions/runs/998",
                }
            )
    if jobs is None:
        jobs = [{"name": "audit", "conclusion": "failure"}]

    env = child_env(pathlib.Path(NODE).parent, WATCHED_WORKFLOWS="\n".join(watched))
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script, encoding="utf-8")
        (tdp / "context.json").write_text(json.dumps(context), encoding="utf-8")
        (tdp / "open.json").write_text(json.dumps(open_issues or []), encoding="utf-8")
        (tdp / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (tdp / "workflows.json").write_text(json.dumps(inventory), encoding="utf-8")
        (tdp / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_OPEN_ISSUES_FILE"] = str(tdp / "open.json")
        env["TEST_RUN_JOBS_FILE"] = str(tdp / "jobs.json")
        env["TEST_REPO_WORKFLOWS_FILE"] = str(tdp / "workflows.json")
        env["TEST_WORKFLOW_RUNS_FILE"] = str(tdp / "runs.json")
        if jobs_throw:
            env["TEST_JOBS_THROW"] = "1"
        if runs_throw:
            env["TEST_RUNS_THROW"] = json.dumps([str(ids[n]) for n in runs_throw])
        if success_runs_throw:
            env["TEST_SUCCESS_RUNS_THROW"] = json.dumps(
                [str(ids[n]) for n in success_runs_throw]
            )
        proc = subprocess.run(
            [NODE, str(HARNESS)],
            env=env,
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    result["_ids"] = ids
    return result


def _run_obj(conclusion, *, run_number=42, head_branch="main", run_id=30116967113):
    """A run object as `listWorkflowRuns` returns it, for seeding a second workflow.

    `name` is the rendered `run-name:`, deliberately unequal to any workflow name.
    """
    return {
        "id": run_id,
        "name": RUN_DISPLAY_NAME,
        "conclusion": conclusion,
        "run_number": run_number,
        "head_branch": head_branch,
        "html_url": f"https://github.com/x/y/actions/runs/{run_id}",
    }


def _alert_issue(number=7, *, marker=MARKER, last_run=None):
    """An open rolling alert. `last_run` stamps the run it has already logged.

    Defaults to unstamped, which is both the pre-#843 event-era shape and the
    "a newer run has appeared" case, so the ordinary failure tests still expect
    an appended comment.
    """
    body = "ended in a bad conclusion.\n"
    if last_run is not None:
        body = f"<!-- last-recorded-run:{last_run} -->\n{body}"
    if marker:
        body = f"{marker}\n{body}"
    return {"number": number, "body": body}


def _closes(result):
    """Just the close transitions — body re-stamps are also issues.update calls."""
    return [u for u in result["updates"] if u.get("state") == "closed"]


# --- failure branch --------------------------------------------------------


def test_failure_with_no_existing_creates_one_issue():
    r = _run("failure", open_issues=[])
    assert r["threw"] is None, r
    assert r["failed"] is None, r
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
    assert _closes(r) == [], r  # a failure never closes the alert


# --- cancelled is reportable (the #832 regression) -------------------------


def test_cancelled_is_reported_not_ignored():
    # A run cancelled before it reached a runner is the exact incident that made
    # this workflow necessary — retired 732 ignored `cancelled`, 740 must not.
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
    # A guaranteed no-op should not spend REST budget: this now runs 48 times a
    # day and shares one 5,000/hr pool with every other agent (AGENTS.md).
    for conclusion in ("skipped", "neutral"):
        r = _run(conclusion, open_issues=[_alert_issue(7)])
        assert r["threw"] is None, (conclusion, r)
        assert r["listForRepoCalls"] == [], (conclusion, r)


def test_an_all_green_sweep_lists_issues_once_and_writes_nothing():
    # The overwhelmingly common poll outcome: several watched workflows all
    # `success`, nothing open to close.
    #
    # It costs exactly ONE issue listing — not zero, and not one per workflow.
    # Not zero because `success` has to look for an alert to close, which is the
    # recovery half of the state machine; the genuinely-zero case is
    # `skipped`/`neutral`, covered by test_benign_conclusions_cost_no_api_call.
    # Not one per workflow because the listing is cached across the sweep.
    watched = _watched()
    r = _run(
        "success",
        open_issues=[],
        name=watched[0],
        extra_runs={"101": [_run_obj("success", run_number=43)]},
    )
    assert r["threw"] is None, r
    assert len(r["listForRepoCalls"]) == 1, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r


def test_success_still_spends_the_lookup_so_it_can_close():
    # `success` is benign in outcome but must NOT early-return: closing a live
    # alert is the whole recovery half of the state machine.
    r = _run("success", open_issues=[_alert_issue(7)])
    assert len(r["listForRepoCalls"]) == 1, r
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r


def test_one_sweep_lists_open_issues_at_most_once():
    # Budget guard specific to the poll shape: a dozen failing workflows in the
    # same sweep must share one listing, not spend one call each. Safe because
    # each workflow's marker is distinct, so a create for one can never change
    # the lookup for another.
    watched = _watched()
    first, second = watched[0], watched[1]
    r = _run(
        "failure",
        open_issues=[],
        name=first,
        extra_runs={"101": [_run_obj("failure", run_number=43)]},
    )
    assert r["threw"] is None, r
    assert len(r["created"]) == 2, r  # both workflows got their own issue ...
    assert len(r["listForRepoCalls"]) == 1, r  # ... from a single listing
    markers = sorted(i["body"].split("\n")[0] for i in r["created"])
    assert markers == sorted(
        [f"{MARKER_PREFIX}{first} -->", f"{MARKER_PREFIX}{second} -->"]
    ), markers


# --- one comment per failing RUN, not per sweep ----------------------------
#
# The sharpest behavioural difference between the event and the poll. The event
# saw each run once, so appending on every observation was the same as appending
# per run. The poll re-observes the SAME latest run every 30 minutes until a
# newer one completes, so an unconditional append posts ~48 identical comments a
# day per broken workflow — burying the history the issue exists to record.


def test_the_same_failing_run_is_not_re_commented_on_the_next_sweep():
    r = _run("failure", open_issues=[_alert_issue(7, last_run=30116967112)])
    assert r["threw"] is None, r
    assert r["comments"] == [], r  # the whole point
    assert r["created"] == [], r  # and certainly no duplicate issue
    assert _closes(r) == [], r
    assert any("already recorded" in i for i in r["infos"]), r


def test_a_newer_failing_run_appends_and_restamps():
    # A genuinely new failing run is still worth a line — that history is why the
    # rolling issue exists.
    r = _run("failure", open_issues=[_alert_issue(7, last_run=999)])
    assert r["threw"] is None, r
    assert len(r["comments"]) == 1, r
    stamps = [u for u in r["updates"] if u.get("body")]
    assert len(stamps) == 1, r
    assert "<!-- last-recorded-run:30116967112 -->" in stamps[0]["body"], stamps
    assert "<!-- last-recorded-run:999 -->" not in stamps[0]["body"], stamps


def test_an_alert_from_the_event_era_is_adopted_and_stamped():
    # Alerts opened by the pre-#843 workflow carry no run marker. They must be
    # adopted (one entry, then stamped), never duplicated and never ignored.
    r = _run("failure", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert len(r["comments"]) == 1, r
    stamps = [u for u in r["updates"] if u.get("body")]
    assert len(stamps) == 1, r
    assert "<!-- last-recorded-run:30116967112 -->" in stamps[0]["body"], stamps
    # the stamp is added, not substituted for the existing body
    assert MARKER in stamps[0]["body"], stamps


def test_a_new_alert_records_its_run_so_the_next_sweep_stays_quiet():
    r = _run("failure", open_issues=[])
    assert "<!-- last-recorded-run:30116967112 -->" in r["created"][0]["body"], r


def test_recovery_closes_a_stamped_alert():
    # The stamp must not interfere with the recovery half of the state machine.
    r = _run("success", open_issues=[_alert_issue(7, last_run=30116967112)])
    assert r["threw"] is None, r
    assert _closes(r) == [{"issue_number": 7, "state": "closed"}], r
    assert any("Recovered" in c["body"] for c in r["comments"]), r


# --- a declined approval gate is not an outage -----------------------------
#
# Run 43 (#832) established the mechanism against three real runs: a gate Clarke
# declines produces run-level `failure` while its jobs read `cancelled`/`skipped`,
# which is indistinguishable at the run level from a genuine fault. Watched workflow
# 703 is gated on `github-prod` — 726 and 735 moved to the ungated `github-prod-read`
# lane in #834 — and #834 measured 8 of 21 scheduled runs on the gated lane failing
# or cancelling at the gate — so without this
# carve-out the alerter's dominant output would be "a human said no".


def _declined_gate_jobs():
    # Shape of runs 30116967112 (declined) and 29174690119 (abandoned ~25h).
    return [
        {"name": "dns-flip", "conclusion": "cancelled"},
        {"name": "cname-flip", "conclusion": "skipped"},
    ]


def test_declined_gate_does_not_alert():
    # `last_green=3`: the workflow succeeded three days ago, so this really is a
    # one-off decline and the carve-out must hold.
    r = _run("failure", open_issues=[], jobs=_declined_gate_jobs(), last_green=3)
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["comments"] == [], r
    assert r["updates"] == [], r
    # ... but it is logged, not silently dropped
    assert any("Not alerting" in n for n in r["notices"]), r


def test_declined_gate_does_not_alert_even_with_an_alert_already_open():
    # An open alert for this workflow must not collect gate-decline noise either.
    r = _run(
        "failure",
        open_issues=[_alert_issue(7)],
        jobs=_declined_gate_jobs(),
        last_green=3,
    )
    assert r["threw"] is None, r
    assert r["comments"] == [], r
    assert r["created"] == [], r


# --- ...but an ABANDONED gate is an outage (the six-week silence) ------------
#
# The carve-out above was written for a gate a human DECLINES. Nothing declined
# 703: its weekly run sits in `github-prod` waiting for an approval a cron cannot
# give itself, and 734 (Stale Waiting-Run Janitor) reaps it after 7 days exactly
# as designed. What 734 leaves behind — run `cancelled`, jobs `cancelled`, no job
# `failure` — is byte-for-byte the shape above. Every scheduled run from
# 2026-07-27 to 2026-08-31 died that way, the published sites list froze at its
# 2026-07-25 snapshot, and the alerter stayed quiet for six weeks by design.
#
# Recurrence is the discriminator: a decline is followed by a green run next
# cycle, an abandoned gate never is.


def test_abandoned_gate_alerts_once_it_stops_recovering():
    # Same job shape as a decline, but nothing green in ~6 weeks — the real 703.
    r = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=41)
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r


def test_a_never_green_gated_workflow_alerts():
    # No successful run at all is at least as bad as a stale one; it must not read
    # as "recently healthy" just because the lookup came back empty.
    r = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=None)
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert "never had a successful" in r["created"][0]["body"], r


def test_the_abandoned_gate_alert_says_it_needs_an_approval_not_a_debug_session():
    # The remedy differs from a failing job, so the issue must name the shape.
    # Nobody needs to debug 703; somebody needs to approve it or move it off the
    # gate. An alert that reads like a code fault sends the reader to the wrong
    # place, which is most of what this fix is for.
    r = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=41)
    # Assert before indexing: this module's runner catches AssertionError only, so a
    # bare `[0]` on an empty list raises IndexError and takes the whole suite down
    # with it — every test sorting after this one then silently never runs.
    assert len(r["created"]) == 1, r
    body = r["created"][0]["body"]
    assert "approval gate nobody answered" in body, body
    assert "not a debug session" in body, body


def test_the_suppression_boundary_holds_on_both_sides():
    # A threshold that only ever gets tested from one side is a threshold nobody
    # knows the position of.
    quiet = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=20)
    assert quiet["created"] == [], quiet
    loud = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=22)
    assert len(loud["created"]) == 1, loud


def test_an_unreadable_success_history_alerts_rather_than_suppressing():
    # Fail loud, never silent — the same stance the job-list catch already takes.
    # An unreadable history must not be able to buy a workflow permanent quiet.
    #
    # `success_runs_throw` breaks ONLY the success lookup. Making the whole runs
    # API throw would kill the poll first and never reach this code, so the test
    # would pass while exercising nothing.
    r = _run(
        "cancelled",
        open_issues=[],
        jobs=_declined_gate_jobs(),
        last_green=3,  # recent green — it would be SUPPRESSED if the lookup worked
        success_runs_throw=[WATCHED_NAME],
    )
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert "could not be read" in r["created"][0]["body"], r
    assert any("success history" in w for w in r["warnings"]), r


def test_the_success_lookup_is_only_spent_on_the_suppression_path():
    # It is one extra REST call against a 5,000/hr pool shared with every other
    # agent, 48 sweeps a day. It must cost nothing on the ordinary paths.
    green = _run("success", open_issues=[_alert_issue(7)])
    assert [c for c in green["listWorkflowRunsCalls"] if c["status"] == "success"] == [], green
    failed = _run("failure", open_issues=[])  # a real job failure
    assert [c for c in failed["listWorkflowRunsCalls"] if c["status"] == "success"] == [], failed


def test_the_success_lookup_is_scoped_like_the_poll():
    r = _run("cancelled", open_issues=[], jobs=_declined_gate_jobs(), last_green=41)
    calls = [c for c in r["listWorkflowRunsCalls"] if c["status"] == "success"]
    assert len(calls) == 1, r
    assert calls[0]["branch"] == "main", calls
    assert calls[0]["per_page"] == 1, calls


def test_a_real_job_failure_still_alerts_the_negative_control():
    # Run 29826157099: post-cutover-smoke genuinely failed. This MUST still alert —
    # a discriminator that suppresses this one has broken the whole workflow.
    r = _run(
        "failure",
        open_issues=[],
        jobs=[
            {"name": "post-cutover-smoke", "conclusion": "failure"},
            {"name": "notify", "conclusion": "cancelled"},
        ],
    )
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r


def test_unreadable_job_list_alerts_anyway():
    # Fail loud, never silent: if the jobs API is unavailable we must not swallow
    # a possible outage — that is the exact failure mode #832 exists to end.
    r = _run("failure", open_issues=[], jobs_throw=True)
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert any("Could not read jobs" in w for w in r["warnings"]), r


def test_empty_job_list_alerts_anyway():
    # Zero jobs is the 726-incident shape (cancelled before reaching a runner):
    # there is no evidence of a decline, so it stays reportable.
    r = _run("cancelled", open_issues=[], jobs=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r


def test_gate_check_is_skipped_on_the_success_path():
    # A green run needs no classification — don't spend the call.
    r = _run("success", open_issues=[_alert_issue(7)])
    assert r["threw"] is None, r
    assert r["listJobsCalls"] == [], r
    assert r["updates"] == [{"issue_number": 7, "state": "closed"}], r


def test_gate_check_reads_only_the_latest_attempt_of_this_run():
    r = _run("failure", open_issues=[])
    assert len(r["listJobsCalls"]) == 1, r
    call = r["listJobsCalls"][0]
    assert call["run_id"] == 30116967112, call
    # `filter: latest` — a re-run's earlier failed attempt must not keep the alert
    # alive after a successful retry.
    assert call["filter"] == "latest", call


def test_benign_conclusions_do_not_even_check_jobs():
    for conclusion in ("skipped", "neutral"):
        r = _run(conclusion, open_issues=[])
        assert r["listJobsCalls"] == [], (conclusion, r)


def test_alert_body_states_the_observation_without_diagnosing_a_cause():
    # "all jobs cancelled" has at least two causes (declined gate, cancel-in-progress
    # supersession); the issue must report what was seen, not guess why.
    r = _run("cancelled", open_issues=[], jobs=[])
    body = r["created"][0]["body"]
    assert "what was observed" in body, body
    assert "usually means" not in body, body


# --- branch scoping --------------------------------------------------------


def test_the_run_query_is_scoped_to_completed_default_branch_runs():
    # The poll asks the API for exactly the run the state machine models: the
    # latest COMPLETED run on the default branch. Anything looser would feed it
    # in-progress runs (conclusion null) or another branch's red.
    r = _run("failure", open_issues=[])
    calls = {c["workflow_id"]: c for c in r["listWorkflowRunsCalls"]}
    assert calls, r
    for call in calls.values():
        assert call["branch"] == "main", call
        assert call["status"] == "completed", call
        assert call["per_page"] == 1, call


def test_failure_on_a_feature_branch_is_ignored():
    # A red run on someone's PR branch is not a fleet alert. The API filter should
    # already exclude it; this guards the in-script check that backs it up.
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


def test_a_workflow_with_no_completed_run_is_a_quiet_noop():
    # A brand-new workflow that has never run on the default branch is not broken.
    r = _run("failure", open_issues=[], name="__no_such_watched_workflow__")
    assert r["threw"] is None, r
    assert r["failed"] is None, r
    assert r["created"] == [], r
    assert r["listForRepoCalls"] == [], r


def test_an_unreadable_run_list_warns_rather_than_reading_as_green():
    # Nothing can be upserted without a run, but a workflow whose runs cannot be
    # read must not pass as healthy in silence.
    r = _run("failure", open_issues=[], runs_throw=[WATCHED_NAME])
    assert r["threw"] is None, r
    assert r["created"] == [], r
    assert r["updates"] == [], r
    assert any("Could not read runs" in w for w in r["warnings"]), r


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


def test_the_marker_keys_on_the_workflow_name_not_the_run_display_name():
    # Poll-specific hazard: `workflow_runs[].name` is the rendered `run-name:`,
    # which changes per run for several watched workflows. Keying on it would
    # open a brand-new issue every failure and never close any of them.
    r = _run("failure", open_issues=[])
    body = r["created"][0]["body"]
    assert MARKER in body, body
    assert RUN_DISPLAY_NAME not in body, body
    assert RUN_DISPLAY_NAME not in r["created"][0]["title"], r["created"][0]


# --- watch-list resolution -------------------------------------------------


def test_watch_list_is_resolved_past_the_first_page_of_workflows():
    # THE #843 TRUNCATION. The repo has >100 workflows and `per_page` caps at 100;
    # a single unpaginated page silently drops the tail, and a watched workflow in
    # that tail becomes permanently invisible with no error.
    r = _run("failure", open_issues=[], filler_workflows=104)
    assert r["threw"] is None, r
    assert r["failed"] is None, f"watched names must resolve past page 1: {r['failed']}"
    assert len(r["created"]) == 1, r
    assert len(r["listRepoWorkflowsCalls"]) >= 2, r  # it actually paged
    assert all(c["per_page"] == 100 for c in r["listRepoWorkflowsCalls"]), r


def test_a_watched_name_matching_no_workflow_fails_loudly():
    # A renamed workflow must not become invisible — this alerter's own failure
    # mode. Silence is the bug; a red run is the fix.
    watched = _watched() + ["999. Repo - Renamed Away [GH]"]
    r = _run("failure", open_issues=[], watched=watched, registered=_watched())
    assert r["threw"] is None, r
    assert r["failed"] is not None, "an unresolvable watched name must fail the run"
    assert "999. Repo - Renamed Away [GH]" in r["failed"], r["failed"]


def test_an_unresolvable_name_does_not_suppress_the_other_alerts():
    # One bad entry must not disable alerting for every good one — the failure is
    # reported after the sweep, not instead of it.
    watched = _watched() + ["999. Repo - Renamed Away [GH]"]
    r = _run("failure", open_issues=[], watched=watched, registered=_watched())
    assert len(r["created"]) == 1, r
    assert MARKER in r["created"][0]["body"], r


# --- isolation from the retired 732 ----------------------------------------


def test_a_legacy_732_rolling_issue_is_never_matched_or_closed():
    # 732 is deleted and never produced a run, so no such issue exists today —
    # but 740 must still refuse to adopt or close one if it ever appears.
    r = _run("failure", open_issues=[_alert_issue(7, marker=LEGACY_GOOGLE_MARKER)])
    assert r["threw"] is None, r
    assert len(r["created"]) == 1, r
    assert r["comments"] == [], r

    r2 = _run("success", open_issues=[_alert_issue(7, marker=LEGACY_GOOGLE_MARKER)])
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


def test_marker_and_labels_do_not_reuse_the_retired_732_identifiers():
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert MARKER_PREFIX in script, "marker literal drifted from the test"
    assert LEGACY_GOOGLE_MARKER not in script, "740 must not reuse 732's marker"
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


def test_the_alert_lookup_pages_past_the_first_hundred_open_issues():
    # `agentic-os,bug` is broad and the backlog is long. Sorting by `updated` used to
    # be the whole defence, on the theory that a live alert is touched constantly —
    # but now that a re-observed run is NOT re-commented, a weekly workflow's alert is
    # touched only when a new failing run appears and can sink past page 1. Reading
    # one page would then open a duplicate, or miss the close on recovery.
    filler = [{"number": 1000 + i, "body": "unrelated agentic-os bug\n"} for i in range(120)]
    target = _alert_issue(7)
    r = _run("failure", open_issues=filler + [target])
    assert r["threw"] is None, r
    assert r["created"] == [], f"paged past page 1 and still opened a duplicate: {r}"
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 7, r
    assert len(r["listForRepoCalls"]) == 2, r  # 100 + 21, stops on the short page
    assert [c["page"] for c in r["listForRepoCalls"]] == [1, 2], r


def test_a_recovery_also_finds_an_alert_past_the_first_page():
    # The close half has the same exposure: a missed lookup leaves a stale alert open
    # forever, claiming a workflow is broken after it recovered.
    filler = [{"number": 1000 + i, "body": "unrelated agentic-os bug\n"} for i in range(120)]
    r = _run("success", open_issues=filler + [_alert_issue(7, last_run=30116967112)])
    assert r["threw"] is None, r
    assert _closes(r) == [{"issue_number": 7, "state": "closed"}], r


# --- YAML-level contract guards (no node needed) ---------------------------


def _all_workflow_names() -> dict:
    names = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("name"):
            names[data["name"]] = path.name
    return names


def test_every_watched_name_matches_a_real_workflow_name():
    # The silent-failure mode this whole workflow exists to prevent: a watch-list
    # entry that matches no `name:` resolves to nothing. The script fails loudly at
    # run time; this catches it at PR time instead.
    known = _all_workflow_names()
    for name in _watched():
        assert name in known, f"watched name has no matching workflow name: {name!r}"


def test_watched_workflows_are_actually_scheduled():
    # Load-bearing for the POLL shape (#843): "the latest run" of a dispatch-only
    # workflow is whatever a human last ran by hand — a stale red would alert
    # forever, a stale green would mean nothing.
    known = _all_workflow_names()
    for name in _watched():
        wf = load_workflow(known[name])
        on = wf.get("on", wf.get(True, {}))
        assert "schedule" in on, f"{name} is not scheduled; 740 polls cron-driven workflows"


# Scheduled workflows this alerter deliberately does NOT watch, with the reason.
# Anything scheduled and absent from both this map and the watch list fails the
# test below — a new cron must not join the fleet unnoticed, which is #832 all
# over again. 741 proved the risk: it landed on `main` while PR #833 was open and
# would have shipped as a day-one blind spot if the merge hadn't surfaced it.
#
# EMPTY ON PURPOSE (#932), and the emptiness is not the point — the forcing function is.
# It held five names on a "not hub plumbing (#832 scope)" rationale, which described the
# ticket 740 was built for rather than anything about those workflows. 228 then failed four
# consecutive scheduled runs unalerted (#912), inside that exclusion. The reason a map entry
# is allowed to exist is "this cron cannot be polled" (see 501's shape), never "this cron is
# someone else's lane"; keep the map so the next new cron still has to choose, and read a
# rationale that names a ticket's scope as a smell.
DELIBERATELY_UNWATCHED: dict[str, str] = {}


def test_every_scheduled_workflow_is_watched_or_explicitly_excluded():
    # Turns silent coverage drift into a build failure with a forced decision:
    # add the new cron to the watch list, or record why it is out of scope.
    watched = set(_watched())
    self_name = load_workflow(WORKFLOW)["name"]
    for name, path in _all_workflow_names().items():
        wf = load_workflow(path)
        on = wf.get("on", wf.get(True, {}))
        if not isinstance(on, dict) or "schedule" not in on:
            continue
        if name == self_name:
            continue  # a terminal alerter cannot watch itself; see #752
        assert name in watched or name in DELIBERATELY_UNWATCHED, (
            f"{name} is scheduled but unwatched: add it to 740's WATCHED_WORKFLOWS, "
            f"or to DELIBERATELY_UNWATCHED with a reason"
        )


def test_the_incident_workflows_from_832_are_watched():
    watched = _watched()
    for required in (
        "726. Repo - Rulesets + Settings Drift Audit [Org]",
        "734. Repo - Stale Waiting-Run Janitor [Repo]",
        "738. Repo - Fleet Smoke Engine Drift Audit [Org]",
    ):
        assert required in watched, f"{required} must stay watched (#832)"


def test_the_credentialed_lanes_from_932_are_watched():
    # These five were excluded on a scope rationale, and the exclusion is what let 228 fail
    # every scheduled run from 07-27 to 07-30 with nobody told (#912). A future scope trim has
    # to argue with this test rather than quietly re-add a name to DELIBERATELY_UNWATCHED.
    watched = _watched()
    for required in (
        "209. WHMCS - Tickets Triage (Open/Customer-Reply) [WHMCS]",
        "210. WHMCS - Orders Triage (Pending/Fraud/Active) [WHMCS]",
        "225. WHMCS - Domain Order URL Verify [WHMCS]",
        "228. WHMCS - Fraud Review (FraudLabs Pro) [FRAUDLABS+WHMCS]",
        "320. Azure - Key Vault Secret Inventory (audit) [MS]",
    ):
        assert required in watched, f"{required} must stay watched (#932)"


def test_the_only_unwatched_scheduled_workflow_is_the_alerter_itself():
    # The complement of the two tests above, stated once as a whole-fleet property so it
    # survives someone rewriting either list: after #932, every `schedule:` in the tree is
    # watched except 740, and 740 is excluded for a structural reason (self-watch loops),
    # not a scope one. This is the assertion that goes red if a new cron lands unwatched
    # AND its author records a rationale in DELIBERATELY_UNWATCHED to silence the forcing
    # function above — the escape hatch that made #912's four silent days possible.
    watched = set(_watched())
    self_name = load_workflow(WORKFLOW)["name"]
    unwatched = []
    for name, path in _all_workflow_names().items():
        wf = load_workflow(path)
        on = wf.get("on", wf.get(True, {}))
        if not isinstance(on, dict) or "schedule" not in on:
            continue
        if name not in watched:
            unwatched.append(name)
    unwatched.sort()
    assert unwatched == [self_name], (
        f"expected 740 alone to be unwatched, got {unwatched}. A scheduled workflow may only "
        f"be left unwatched when polling it is unsound, and that case needs its own reasoning "
        f"here — not a DELIBERATELY_UNWATCHED entry."
    )


def test_the_google_lane_absorbed_from_732_is_watched():
    # 732 is retired into 740; its scheduled workflows must not be dropped on the
    # way across. 501 is deliberately not here — dispatch/`workflow_call` only.
    watched = _watched()
    assert "502. Google - Analytics Report (GA4 -> JSON) [GOOGLE]" in watched, watched
    assert "504. Google - GTM Container Backups (weekly export) [GOOGLE]" in watched, watched
    assert not any(n.startswith("501.") for n in watched), (
        "501 is dispatch/workflow_call only — polling its latest run reports whatever "
        "a human last triggered, so it must not be watched"
    )


def test_the_watch_list_has_no_duplicates():
    watched = _watched()
    assert len(watched) == len(set(watched)), watched


def test_740_does_not_watch_itself():
    wf = load_workflow(WORKFLOW)
    assert wf["name"] not in _watched(), "self-watching would loop"


def test_triggers_are_schedule_plus_dispatch_only():
    # The whole point of #843: `workflow_run` never fired in this repo, and an
    # alerter that cannot be run on demand cannot be proven to work.
    wf = load_workflow(WORKFLOW)
    on = wf.get("on", wf.get(True, {}))
    assert sorted(on) == ["schedule", "workflow_dispatch"], on
    crons = [e["cron"] for e in on["schedule"]]
    assert crons, on


def _cron_minutes(expr: str) -> set:
    """Every minute of the hour a cron expression's minute field fires on.

    Covers the syntaxes cron accepts: `*`, `a`, `a,b`, `a-b`, `*/n`, `a-b/n`, and
    Vixie's `a/n` (= `a-59/n`). The first cut of this parser matched literal digits
    only, which read `*/3` — a schedule that fires on :09, colliding head-on with
    the poll — as "no minutes at all" (caught in review on PR #846). Anything it
    cannot parse raises rather than returning an empty set, because silently
    understanding nothing is exactly how a collision guard stops guarding.
    """
    field = expr.split()[0]
    out = set()
    for part in field.split(","):
        step = 1
        has_step = "/" in part
        if has_step:
            part, _, raw_step = part.partition("/")
            if not raw_step.isdigit() or int(raw_step) == 0:
                raise ValueError(f"unparsable cron step in {expr!r}")
            step = int(raw_step)
        if part == "*":
            lo, hi = 0, 59
        elif "-" in part:
            lo_s, _, hi_s = part.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"unparsable cron range in {expr!r}")
            lo, hi = int(lo_s), int(hi_s)
        elif part.isdigit():
            # A bare literal FOLLOWED BY A STEP is Vixie's `a/n` shorthand for
            # `a-59/n`. The trigger is the presence of the `/`, not the step's
            # value: `9/1` means every minute from :09 to :59, so keying off
            # `step > 1` would collapse it to just {9} (caught in review on #846).
            lo = int(part)
            hi = 59 if has_step else lo
        else:
            raise ValueError(f"unparsable cron minute field in {expr!r}")
        if not (0 <= lo <= hi <= 59):
            raise ValueError(f"cron minute out of range in {expr!r}")
        out |= set(range(lo, hi + 1, step))
    return out


def test_the_cron_minute_parser_understands_steps_and_ranges():
    # The collision guard below is only as good as this parser.
    assert _cron_minutes("9,39 * * * *") == {9, 39}
    assert _cron_minutes("*/30 * * * *") == {0, 30}
    assert 9 in _cron_minutes("*/3 * * * *")  # the case the literal-only parser missed
    assert 9 in _cron_minutes("5-15 * * * *")
    assert _cron_minutes("10-20/5 * * * *") == {10, 15, 20}
    assert _cron_minutes("* * * * *") == set(range(60))
    assert _cron_minutes("9/20 * * * *") == {9, 29, 49}
    # `a/1` is `a-59/1`, NOT the single minute `a` — the `/` is what makes it a
    # range, so a step of 1 must still expand to the rest of the hour.
    assert _cron_minutes("9/1 * * * *") == set(range(9, 60))
    assert _cron_minutes("9 * * * *") == {9}  # ... while a bare literal stays one minute
    for bad in ("x * * * *", "1-y * * * *", "*/0 * * * *", "70 * * * *"):
        try:
            _cron_minutes(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} must raise, not parse to a silent empty set")


def test_the_poll_declares_literal_minutes_not_a_wildcard():
    # Keep 740's OWN cron strict: a stepped or wildcard expression here would
    # spread the sweep across slots the staggering is meant to keep clear, and
    # would make the collision guard below far blunter than it needs to be.
    wf = load_workflow(WORKFLOW)
    for e in wf.get("on", wf.get(True, {}))["schedule"]:
        field = e["cron"].split()[0]
        assert all(p.isdigit() for p in field.split(",")), (
            f"740's own cron must name concrete minutes, not a wildcard/stepped "
            f"expression: {e['cron']!r}"
        )


def test_the_poll_cron_collides_with_no_other_hub_schedule():
    # Two crons in the same minute slot contend for the same runner burst; the
    # hub deliberately staggers them.
    minutes = _cron_minutes
    wf = load_workflow(WORKFLOW)
    mine = set()
    for e in wf.get("on", wf.get(True, {}))["schedule"]:
        mine |= minutes(e["cron"])
    assert mine, "the poll must declare a concrete minute, not '*'"
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == WORKFLOW:
            continue
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(wf, dict):
            continue
        on = wf.get("on", wf.get(True, {}))
        if not isinstance(on, dict) or "schedule" not in on:
            continue
        for e in on["schedule"]:
            clash = mine & minutes(e["cron"])
            assert not clash, f"{path.name} already uses minute slot(s) {clash}"


def test_no_workflow_run_trigger_remains_anywhere_in_the_repo():
    # Acceptance criterion of #843: the event is unusable in this repo, so no
    # workflow may depend on it until the platform-side cause is resolved.
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(wf, dict):
            continue
        on = wf.get("on", wf.get(True, {}))
        if isinstance(on, dict) and "workflow_run" in on:
            offenders.append(path.name)
        elif isinstance(on, list) and "workflow_run" in on:
            offenders.append(path.name)
    assert not offenders, (
        f"`workflow_run` has never fired in this repo (#843); these workflows would "
        f"be silently dead: {offenders}"
    )


def test_732_is_retired():
    assert not (WORKFLOWS / "732-google-workflow-failure-alert.yml").exists()
    here = pathlib.Path(__file__).resolve().parent
    assert not (here / "test_732_google_failure_alert.py").exists()


def test_permissions_are_issue_write_read_only_actions():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms
    assert perms.get("contents") == "read", perms
    # `actions: read` covers both halves of the poll: enumerating workflows/runs
    # and reading a run's job list for the gate discriminator.
    assert perms.get("actions") == "read", perms


def test_alerter_is_ungated_and_uses_the_ambient_token_only():
    # It must never be blocked by the gate it is watching, and must not need a PAT.
    wf = load_workflow(WORKFLOW)
    job = wf["jobs"][JOB]
    assert "environment" not in job, job
    raw = (WORKFLOWS / WORKFLOW).read_text(encoding="utf-8")
    assert "CBM_TOKEN" not in raw, "740 must use the ambient GITHUB_TOKEN only"


def test_sweeps_never_overlap_and_are_never_cancelled_mid_flight():
    # A half-finished sweep leaves the workflows it never reached unalerted, so
    # the newer run must queue behind the older one rather than kill it.
    wf = load_workflow(WORKFLOW)
    conc = wf.get("concurrency", {})
    assert conc.get("group"), conc
    assert conc.get("cancel-in-progress") is False, conc


def test_step_is_the_only_writer_and_never_touches_gates():
    script = step_github_script(WORKFLOW, JOB, STEP)
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"alert must not touch gate approvals: {forbidden}"
    step = _step()
    assert "github-script" in step.get("uses", ""), step


# --- the alert lookup must never select a pull request (#980) -------------
#
# `issues.listForRepo` returns pull requests as well as issues, and the success
# branch CLOSES whatever the lookup selects — so a PR carrying a marker would be
# closed by a scheduled sweep, with a comment claiming a recovery it had nothing
# to do with. The `agentic-os,bug` label narrowing does not help: PRs carry
# labels too. And the marker is an HTML comment, invisible in a rendered PR
# body, so a PR can carry it without its author ever seeing it — a lessons PR
# quoting this file's marker format is the ordinary path.


def _alert_pr(number=5, **kw):
    """A PULL REQUEST carrying the alert marker, as listForRepo returns it."""
    return {**_alert_issue(number, **kw), "pull_request": {"url": "https://api…/pulls/5"}}


def test_a_recovery_never_closes_a_pull_request_carrying_the_marker():
    r = _run("success", open_issues=[_alert_pr(5)])
    assert r["threw"] is None, r
    assert _closes(r) == [], r  # the PR is NOT closed
    assert r["comments"] == [], r  # and gets no "Recovered" comment


def test_a_failure_never_appends_to_a_pull_request_carrying_the_marker():
    r = _run("failure", open_issues=[_alert_pr(5)])
    assert r["threw"] is None, r
    assert [c["issue_number"] for c in r["comments"]] == [], r
    # the PR is not the alert, so the real rolling alert is still opened
    assert len(r["created"]) == 1, r


def test_the_alert_is_found_when_a_marked_pull_request_is_listed_first():
    r = _run("success", open_issues=[_alert_pr(5), _alert_issue(9)])
    assert r["threw"] is None, r
    assert _closes(r) == [{"issue_number": 9, "state": "closed"}], r
    assert [c["issue_number"] for c in r["comments"]] == [9], r

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
