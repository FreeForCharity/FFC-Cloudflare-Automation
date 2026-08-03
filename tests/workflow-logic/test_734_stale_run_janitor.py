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

Since #960 it also warns before it reaps, and those cases are locked down here
too. The warning's whole value is the *date* it publishes, so the arithmetic
behind that date gets more coverage than the rest of the step combined:

  - a run inside the warning window is reported; one outside it is not;
  - the published instant is the janitor's next daily 06:31Z tick after the run
    crosses the threshold — NOT `created_at + max_age_days`. Run 59 shipped the
    latter and sent two deadlines wrong by a day to a human's phone;
  - two runs of identical age but different times of day therefore resolve to
    different cancellation dates (the exact shape of that off-by-a-day);
  - dry-run still warns; both buckets empty posts nothing at all;
  - a failed comment cannot turn a completed sweep red.

Refs #752 (process assurance — verify the Agentic OS's own monitors keep working),
#960, AGENTS.md §"Adding or changing a workflow" (embedded logic gets a unit test).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow, step_github_script  # noqa: E402

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

# The daily cron the janitor actually runs on. Asserted against the workflow's
# own `schedule:` below, so this constant cannot quietly drift away from it.
TICK_HOUR = 6
TICK_MINUTE = 31

# The Conductor Log, where the warning/cancellation report is posted.
LOG_ISSUE = 719

CTX = {
    "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
    "eventName": "schedule",
    "payload": {},
}


def _ago(days, *, at=None):
    """A UTC timestamp `days` before now, optionally pinned to a time of day.

    The warning window is relative to *now*, so unlike the staleness cases these
    fixtures cannot use fixed years. `at=(h, m)` moves the time of day without
    changing the calendar date, which is how the two "same age, different clock
    time" runs are built.
    """
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    if at is not None:
        t = t.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _expected_cancel_at(created_iso, max_age_days=7):
    """The first cron tick strictly after the run crosses `max_age_days`.

    Deliberately a second implementation rather than a constant lifted from the
    step: if the step ever reverts to publishing `created_at + max_age_days`,
    every case whose threshold lands after 06:31Z catches it.
    """
    created = dt.datetime.strptime(created_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    threshold = created + dt.timedelta(days=max_age_days)
    tick = threshold.replace(hour=TICK_HOUR, minute=TICK_MINUTE, second=0, microsecond=0)
    if tick <= threshold:
        tick += dt.timedelta(days=1)
    return tick.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _run(
    runs,
    *,
    dry_run=None,
    max_age_days=None,
    warn_days=None,
    cancel_fail_ids=None,
    comment_fails=False,
):
    script = step_github_script(WORKFLOW, JOB, STEP)
    env = child_env(pathlib.Path(NODE).parent)
    if dry_run is not None:
        env["DRY_RUN"] = dry_run
    if max_age_days is not None:
        env["MAX_AGE_DAYS"] = max_age_days
    if warn_days is not None:
        env["WARN_DAYS"] = warn_days
    if cancel_fail_ids:
        env["TEST_CANCEL_FAIL_IDS"] = ",".join(str(i) for i in cancel_fail_ids)
    if comment_fails:
        env["TEST_COMMENT_FAILS"] = "1"
    env["LOG_ISSUE"] = str(LOG_ISSUE)
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script, encoding="utf-8")
        (tdp / "context.json").write_text(json.dumps(CTX), encoding="utf-8")
        (tdp / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_RUNS_FILE"] = str(tdp / "runs.json")
        proc = subprocess.run(
            [NODE, str(HARNESS)],
            env=env,
            capture_output=True,
            text=True, encoding="utf-8",
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


def test_a_negative_max_age_does_not_cancel_the_entire_queue():
    # `Number(x) || 7` accepted -1: finite and truthy, so it survived the
    # fallback and put `cutoff` in the FUTURE, making every waiting run "stale".
    # One mistyped dispatch would have reaped the whole queue — the worst
    # possible failure for the one workflow in this repo that cancels runs.
    # Raised by review on #967.
    #
    # The fixture is a run from SIX HOURS AGO, not FRESH_TS. A negative age moves
    # the cutoff into the near future — `-1` puts it at now+1d — so a year-2999
    # timestamp sails past it untouched and the case passes whether or not the
    # bug is present. That is what the first draft of this test did, and only
    # reintroducing the defect exposed it. The victims of this bug are ordinary
    # recent runs, so the fixture has to be one.
    recent = _run_obj(1, _ago(0.25))
    for bad in ("-1", "-0.5", "nonsense", ""):
        r = _run([recent], dry_run="false", max_age_days=bad)
        assert r["threw"] is None, (bad, r)
        assert r["cancelAttempts"] == [], (bad, r)  # fell back to the 7d default

    # ...and the fallback is the default, not "cancel nothing ever": a genuinely
    # stale run is still reaped when the input is garbage.
    r = _run([_run_obj(2, STALE_TS)], dry_run="false", max_age_days="-1")
    assert r["cancelledIds"] == [2], r


def test_zero_warn_days_means_cancel_without_warning():
    # 0 is a legitimate warn_days (opt out of warnings), not a bad input, so it
    # must not fall back to 2 and start reporting runs nobody asked about.
    r = _run([_run_obj(1, _ago(6))], dry_run="false", max_age_days="7", warn_days="0")
    assert r["threw"] is None, r
    assert r["comments"] == [], r
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


# --- the warning pass (#960) ------------------------------------------------


def test_run_outside_the_warning_window_produces_no_comment():
    # 4.5d old against a 7d threshold with a 2d warning window: the window opens
    # at 5d, so this run is not reported. (4.5 rather than exactly 5.0 on
    # purpose — at the boundary the verdict turns on the milliseconds between
    # building the fixture and the step reading Date.now(), which is a coin flip,
    # not a test.) The same claim at an unambiguous distance: 5d old with a 1d
    # window, where the window does not open until 6d.
    r = _run([_run_obj(1, _ago(4.5))], dry_run="false", max_age_days="7", warn_days="2")
    assert r["threw"] is None, r
    assert r["comments"] == [], r
    assert r["cancelAttempts"] == [], r

    r = _run([_run_obj(1, _ago(5))], dry_run="false", max_age_days="7", warn_days="1")
    assert r["comments"] == [], r


def test_expiring_run_is_reported_with_the_next_cron_tick():
    created = _ago(5.5)
    r = _run([_run_obj(1, created)], dry_run="false", max_age_days="7", warn_days="2")
    assert r["threw"] is None, r
    assert r["cancelAttempts"] == [], r  # warned about, not reaped
    assert len(r["comments"]) == 1, r
    body = r["comments"][0]["body"]
    assert _expected_cancel_at(created) in body, body
    assert "Expiring" in body, body


def test_cancellation_instant_is_the_tick_not_the_threshold():
    # The exact off-by-a-day run 59 shipped. Two runs of the same age on the same
    # calendar date, one created before the 06:31Z tick and one after: their
    # thresholds land on the same day, but the first is reaped that morning and
    # the second not until the next one. Reporting `created_at + 7d` would give
    # them the same date.
    early = _run_obj(1, _ago(6, at=(4, 0)))  # threshold 04:00Z -> tick same day
    late = _run_obj(2, _ago(6, at=(14, 34)))  # threshold 14:34Z -> tick next day
    r = _run([early, late], dry_run="false", max_age_days="7", warn_days="2")
    assert r["threw"] is None, r
    assert len(r["comments"]) == 1, r
    body = r["comments"][0]["body"]

    early_at = _expected_cancel_at(early["created_at"])
    late_at = _expected_cancel_at(late["created_at"])
    assert early_at[:10] != late_at[:10], (early_at, late_at)  # different DATES
    assert early_at in body, (early_at, body)
    assert late_at in body, (late_at, body)
    # And neither raw threshold leaked out in place of a tick.
    for run in (early, late):
        threshold = (
            dt.datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            + dt.timedelta(days=7)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        assert threshold not in body, (threshold, body)


def test_dry_run_still_warns_and_still_cancels_nothing():
    created = _ago(5.5)
    runs = [_run_obj(1, STALE_TS), _run_obj(2, created)]
    r = _run(runs, dry_run="true", max_age_days="7", warn_days="2")
    assert r["threw"] is None, r
    assert r["cancelAttempts"] == [], r
    assert len(r["comments"]) == 1, r
    body = r["comments"][0]["body"]
    assert "dry run" in body.lower(), body
    assert _expected_cancel_at(created) in body, body


def test_both_buckets_empty_posts_nothing():
    # A queue of nothing, and a queue of runs too young to be either — neither is
    # worth a daily comment. This is what keeps the Conductor Log readable.
    assert _run([], dry_run="false")["comments"] == []
    assert _run([_run_obj(1, FRESH_TS)], dry_run="false")["comments"] == []


def test_cancelled_run_is_reported_as_needing_re_dispatch():
    r = _run([_run_obj(1, STALE_TS)], dry_run="false")
    assert r["cancelledIds"] == [1], r
    assert len(r["comments"]) == 1, r
    body = r["comments"][0]["body"]
    assert "re-dispatch" in body.lower(), body
    assert "cancelled" in body.lower(), body


def test_report_goes_to_the_conductor_log():
    r = _run([_run_obj(1, STALE_TS)], dry_run="false")
    assert [c["issue_number"] for c in r["comments"]] == [LOG_ISSUE], r


def test_comment_failure_does_not_fail_the_sweep():
    # Reporting is the point of #960, but it is still the second job: a 503 from
    # the comment API must not turn a completed cancellation sweep red, or the
    # janitor starts filing "🚨 Scheduled workflow failing" issues about itself.
    r = _run([_run_obj(1, STALE_TS)], dry_run="false", comment_fails=True)
    assert r["threw"] is None, r
    assert r["failed"] is None, r
    assert r["cancelledIds"] == [1], r
    assert r["comments"] == [], r


def test_warn_days_wider_than_max_age_does_not_swallow_future_runs():
    # warn_days >= max_age_days makes the window cover a run's whole life; the
    # boundary must clamp at `now` rather than running past it and reporting runs
    # created in the future (which the fixture set deliberately contains).
    r = _run([_run_obj(1, FRESH_TS)], dry_run="false", max_age_days="7", warn_days="30")
    assert r["threw"] is None, r
    assert r["comments"] == [], r


# --- YAML-level contract guards (no node needed) ---------------------------


def _triggers(wf):
    # PyYAML reads YAML 1.1, where a bare `on:` key is the boolean True.
    return wf.get("on") or wf.get(True) or {}


def test_publishing_tick_matches_the_cron_the_janitor_runs_on():
    # The cancellation instants the janitor publishes are only correct while the
    # tick constants in its script agree with its own `schedule:`. Retiming the
    # cron without retiming the constants would keep every test green and make
    # every published deadline wrong — the #960 defect wearing a new hat.
    wf = load_workflow(WORKFLOW)
    crons = [s["cron"] for s in _triggers(wf)["schedule"]]
    assert len(crons) == 1, crons
    minute, hour = crons[0].split()[:2]

    script = step_github_script(WORKFLOW, JOB, STEP)
    script_hour = re.search(r"TICK_HOUR\s*=\s*(\d+)", script)
    script_minute = re.search(r"TICK_MINUTE\s*=\s*(\d+)", script)
    assert script_hour and script_minute, script
    assert int(script_hour.group(1)) == int(hour) == TICK_HOUR, (script_hour, hour)
    assert int(script_minute.group(1)) == int(minute) == TICK_MINUTE, (script_minute, minute)


def test_warn_days_is_a_dispatch_input_wired_into_the_step():
    wf = load_workflow(WORKFLOW)
    inputs = _triggers(wf)["workflow_dispatch"]["inputs"]
    assert str(inputs["warn_days"]["default"]) == "2", inputs["warn_days"]
    step = find_step(wf, JOB, STEP)
    assert "warn_days" in str(step["env"]["WARN_DAYS"]), step["env"]
    # And the report has an addressee: the Conductor Log, not this job's summary.
    assert str(step["env"]["LOG_ISSUE"]) == str(LOG_ISSUE), step["env"]


def test_workflow_may_comment_but_still_only_reads_contents():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms  # needed to post the warning
    assert perms.get("contents") == "read", perms


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
