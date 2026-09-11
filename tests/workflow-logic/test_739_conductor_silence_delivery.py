"""Unit tests for 739's Conductor-silence DELIVERY path (#1269).

The silence *detection* (#1215) is tested in test_739_process_health.py and was
never the defect. It fired on schedule four times across a 30-day outage, named
the last run and the hour count, and stated that only a human could fix it — and
it was delivered as a comment on #719, the Conductor log, so the one component
guaranteed to be down was the only subscriber to its own outage alarm. Four
correct alarms, zero addressees.

What is tested here is therefore the branch that turns that verdict into
something a person receives: an assigned rolling issue, upserted while the
silence holds and closed when it clears.

The load-bearing case is `assessed: false`. The workflow already degrades a
truncated or failed #719 read to "not assessed", correctly, because a
`since`-bounded read returns oldest-first and a capped one is missing exactly
the newest comments that prove liveness. If that reading reached the close path
it would post "Conductor is running again" and close a live alarm — a lie that
would make this delivery path worse than the silence it replaces. So every
not-assessed shape below asserts `none`, in BOTH directions: nothing opened, and
an existing alarm left open and untouched.

Every decision case is asserted against its own input rather than in a batch, so
one shape cannot pass on another's behalf, and the rendering tests assert on the
strings a reader actually sees (title, marker, hour count, the "needs a human"
sentence) rather than on the presence of a function.

Run: python3 tests/workflow-logic/test_739_conductor_silence_delivery.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow, step_github_script

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "process-health-metrics-lib.js"
WF_FILE = "739-process-health-metrics.yml"
STEP = "silence rolling issue"

# The real outage, as 739's own 09-07 report rendered it.
OUTAGE = {
    "thresholdHours": 48,
    "assessed": True,
    "lastRun": 152,
    "lastRunIso": "2026-08-12T04:58:13Z",
    "hours": 625.5,
    "silent": True,
}
HEALTHY = {
    "thresholdHours": 48,
    "assessed": True,
    "lastRun": 153,
    "lastRunIso": "2026-09-11T11:33:00Z",
    "hours": 3.2,
    "silent": False,
}
# The capped/failed read: computeMetrics emits exactly this shape.
NOT_ASSESSED = {
    "thresholdHours": 48,
    "assessed": False,
    "lastRun": None,
    "lastRunIso": None,
    "hours": None,
    "silent": None,
}
# Read in full, no run header anywhere in the window — silent, but with no
# number to quote. `hours` stays null on purpose upstream.
NO_HEADER = {
    "thresholdHours": 48,
    "assessed": True,
    "lastRun": None,
    "lastRunIso": None,
    "hours": None,
    "silent": True,
}

ISSUE = {"number": 1300, "body": "<!-- conductor-silence -->\nold body"}
NOW = "2026-09-11T16:00:00Z"
RUN_URL = "https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/42"


def _node(expr_body: str, *argv: str):
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def decide(silence, existing=None) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.decideSilenceDelivery("
        "JSON.parse(process.argv[1]),JSON.parse(process.argv[2]))));",
        json.dumps(silence),
        json.dumps(existing),
    )


def title(silence) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.silenceIssueTitle(JSON.parse(process.argv[1]))));",
        json.dumps(silence),
    )


def body(silence, now: str = NOW, run_url: str = RUN_URL) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderSilenceIssueBody("
        "JSON.parse(process.argv[1]),{nowIso:process.argv[2],runUrl:process.argv[3]})));",
        json.dumps(silence),
        now,
        run_url,
    )


def recovery(silence, run_url: str = RUN_URL) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderSilenceRecoveryComment("
        "JSON.parse(process.argv[1]),{runUrl:process.argv[2]})));",
        json.dumps(silence),
        run_url,
    )


def find(items) -> object:
    return _node(
        "process.stdout.write(JSON.stringify(l.findSilenceIssue(JSON.parse(process.argv[1]))));",
        json.dumps(items),
    )


def const(name: str):
    return _node(f"process.stdout.write(JSON.stringify(l.{name}));")


# --- the three-way branch ---------------------------------------------------


def test_an_active_silence_with_no_open_issue_opens_one():
    assert decide(OUTAGE, None)["action"] == "open", decide(OUTAGE, None)


def test_an_active_silence_with_an_open_issue_updates_it_rather_than_duplicating():
    d = decide(OUTAGE, ISSUE)
    assert d["action"] == "update", d
    # The reason names the issue, so the run log says WHICH one was refreshed.
    assert "1300" in d["reason"], d


def test_the_upsert_is_idempotent_across_repeated_silent_runs():
    # Same input twice must not drift from update to open — the marker match is
    # what makes this true, and this pins it as a property of the decision.
    assert [decide(OUTAGE, ISSUE)["action"] for _ in range(2)] == ["update", "update"]


def test_a_recovered_conductor_closes_the_open_issue():
    d = decide(HEALTHY, ISSUE)
    assert d["action"] == "close", d


def test_a_healthy_conductor_with_no_open_issue_does_nothing():
    assert decide(HEALTHY, None)["action"] == "none", decide(HEALTHY, None)


# --- not assessed: the case that can silently cancel a true alarm -----------


def test_a_not_assessed_read_leaves_an_open_alarm_open():
    d = decide(NOT_ASSESSED, ISSUE)
    assert d["action"] == "none", d
    assert "not assessed" in d["reason"], d


def test_the_unknown_verdict_reason_names_both_causes():
    """Copilot's finding on #1275: one branch, two very different places to look.

    `core.notice` prints this string and a responder acts on it. `assessed: false`
    means the #719 read failed or hit its cap — look at the log. A non-boolean
    `silent` means the metrics object is malformed — look at this workflow. The
    first draft named only the read, which would send someone to the wrong one.
    """
    for silence in (NOT_ASSESSED, {"assessed": True, "silent": "true"}):
        reason = decide(silence, ISSUE)["reason"]
        assert "not assessed" in reason, reason
        assert "malformed" in reason, reason
        # The observed values, so the notice says WHICH of the two it was.
        assert f"assessed={silence.get('assessed')}".lower() in reason.lower(), reason
        assert "silent=" in reason, reason


def test_a_not_assessed_read_raises_no_new_alarm():
    assert decide(NOT_ASSESSED, None)["action"] == "none", decide(NOT_ASSESSED, None)


def test_an_absent_silence_block_is_not_a_recovery():
    # A report predating #1215 carries no silence block at all. Reading a missing
    # field as `silent: false` would close a live alarm on the first run after a
    # rollback.
    for existing in (None, ISSUE):
        assert decide(None, existing)["action"] == "none", existing
        assert decide({}, existing)["action"] == "none", existing


def test_a_malformed_silence_block_is_not_a_recovery():
    # Only an explicit boolean acts. A truthy string, a number, or an `assessed`
    # that is not exactly `true` are all "we do not know".
    for bad in (
        {"assessed": True, "silent": "false"},
        {"assessed": True, "silent": "true"},
        {"assessed": True, "silent": 0},
        {"assessed": "true", "silent": False},
        {"assessed": 1, "silent": True},
        {"silent": True},
    ):
        for existing in (None, ISSUE):
            assert decide(bad, existing)["action"] == "none", (bad, existing)


def test_a_silence_block_that_is_not_an_object_is_not_a_recovery():
    for bad in ("silent", 3, True, []):
        assert decide(bad, ISSUE)["action"] == "none", bad


# --- selecting the rolling issue -------------------------------------------


def test_the_marker_selects_the_rolling_issue():
    found = find([{"number": 1, "body": "unrelated"}, ISSUE])
    assert found and found["number"] == 1300, found


def test_a_pull_request_quoting_the_marker_is_never_selected():
    # The issues listing returns PRs too, and the close path closes whatever this
    # selects — so a PR that documents the marker (this one's own PR does) must
    # not be selectable.
    items = [{"number": 9, "body": "<!-- conductor-silence -->", "pull_request": {"url": "…"}}]
    assert find(items) is None, find(items)


def test_an_issue_with_no_body_is_not_selected():
    assert find([{"number": 2}, {"number": 3, "body": None}]) is None


def test_an_empty_listing_selects_nothing():
    assert find([]) is None
    assert find(None) is None


def test_the_marker_is_distinct_from_the_weekly_report_marker():
    # Sharing 739's report marker would make the silence upsert match its own
    # weekly comments (and vice versa).
    assert const("SILENCE_MARKER") != const("MARKER")
    assert const("SILENCE_MARKER") == "<!-- conductor-silence -->"


# --- title -----------------------------------------------------------------


def test_the_title_carries_the_last_run_number():
    assert "run 152" in title(OUTAGE), title(OUTAGE)


def test_the_title_does_not_carry_the_varying_hour_count():
    # A title that changes every run is unreadable in a notification list and in
    # board #9; the hour count belongs in the body.
    t = title(OUTAGE)
    assert "625" not in t, t


def test_the_title_is_stable_as_the_outage_lengthens():
    later = dict(OUTAGE, hours=793.5)
    assert title(later) == title(OUTAGE)


def test_the_title_never_renders_a_null_run_number():
    t = title(NO_HEADER)
    assert "null" not in t.lower(), t
    assert "no run header" in t, t


# --- body ------------------------------------------------------------------


def test_the_body_carries_the_marker_for_the_upsert():
    assert const("SILENCE_MARKER") in body(OUTAGE)


def test_the_body_names_the_run_the_duration_and_the_threshold():
    b = body(OUTAGE)
    assert "run 152" in b, b
    assert "2026-08-12T04:58:13Z" in b, b
    assert "625.5h" in b, b
    assert "48h" in b, b


def test_the_body_records_when_it_was_measured_and_by_which_run():
    b = body(OUTAGE)
    assert NOW in b, b
    assert RUN_URL in b, b


def test_the_body_fabricates_no_hour_count_when_the_gap_outran_the_window():
    b = body(NO_HEADER)
    assert "0h" not in b, b
    assert "nullh" not in b, b
    assert "at least as long as the comment window" in b, b


def test_the_zero_header_body_names_both_causes_of_that_reading():
    # A reader who checks #719, sees the Conductor posting, and is told nothing
    # about the header format learns only that the alarm is wrong.
    b = body(NO_HEADER)
    assert "format changed" in b, b
    assert "## Run N" in b, b


def test_the_run_numbered_body_does_not_hedge_about_the_header_format():
    # That hedge is correct only for the zero-header reading; printing it when a
    # header WAS seen invites the reader to dismiss a measured outage.
    assert "format changed" not in body(OUTAGE)


def test_the_body_says_no_workflow_can_fix_it():
    b = body(OUTAGE)
    assert "not a GitHub Actions workflow" in b, b
    assert "restart" in b.lower(), b


def test_the_body_explains_why_the_rest_of_the_report_looks_clean():
    # `0 of 0` dead runs is the reading that made this invisible for 30 days.
    assert "0 of 0" in body(OUTAGE)


def test_the_body_tells_the_reader_not_to_close_it_by_hand():
    assert "Do not close it by hand" in body(OUTAGE)


def test_the_body_states_the_auto_close_contract_including_the_not_assessed_case():
    b = body(OUTAGE)
    assert "auto-closed" in b.lower(), b
    assert "open and untouched" in b, b


def test_the_body_says_it_is_not_agent_workable():
    assert "not** labelled `agent-ready`" in body(OUTAGE)


def test_a_body_with_no_run_url_still_renders():
    # `runUrl` is optional; a missing one must not print `undefined` at a reader.
    b = _node(
        "process.stdout.write(JSON.stringify(l.renderSilenceIssueBody("
        "JSON.parse(process.argv[1]),{nowIso:process.argv[2]})));",
        json.dumps(OUTAGE),
        NOW,
    )
    assert "undefined" not in b, b
    assert const("SILENCE_MARKER") in b


# --- recovery comment ------------------------------------------------------


def test_the_recovery_comment_names_the_run_that_broke_the_silence():
    c = recovery(HEALTHY)
    assert "run 153" in c, c
    assert "2026-09-11T11:33:00Z" in c, c
    assert RUN_URL in c, c


def test_the_recovery_comment_renders_without_a_run_number():
    c = recovery({"thresholdHours": 48, "assessed": True, "silent": False})
    assert "null" not in c.lower(), c
    assert "running again" in c, c


# --- labels and assignment (the substance of #1269) -------------------------


def test_the_issue_is_labelled_agentic_os():
    assert "agentic-os" in const("SILENCE_ISSUE_LABELS")


def test_the_issue_is_not_labelled_agent_ready():
    # The remedy is an action on a workstation. An `agent-ready` label here
    # spends one sandboxed worker run a week rediscovering that.
    assert "agent-ready" not in const("SILENCE_ISSUE_LABELS")


def test_the_issue_is_assigned_to_the_conductors_operator():
    # Assignment is the delivery mechanism: it produces a notification and a
    # board row, which is exactly what a #719 comment does not.
    assert const("SILENCE_ASSIGNEES") == ["clarkemoyer"]


# --- workflow wiring -------------------------------------------------------


def test_the_workflow_has_a_delivery_step_using_the_library():
    js = step_github_script(WF_FILE, "report", STEP)
    assert "process-health-metrics-lib.js" in js, js[:200]
    for fn in (
        "findSilenceIssue",
        "decideSilenceDelivery",
        "renderSilenceIssueBody",
        "renderSilenceRecoveryComment",
    ):
        assert fn in js, fn


def test_the_delivery_step_never_hand_rolls_the_decision():
    # A local `silent === true` in the YAML would be a second, untested copy of
    # the three-way branch — the one place a not-assessed read could leak into
    # the close path.
    js = step_github_script(WF_FILE, "report", STEP)
    assert "silence.silent" not in js, js
    assert "=== true" not in js, js


def test_the_delivery_step_runs_even_when_an_earlier_step_failed():
    step = _delivery_step()
    assert str(step.get("if")) == "always()", step.get("if")


def test_the_delivery_step_is_ordered_after_the_artifact_upload():
    steps = load_workflow(WF_FILE)["jobs"]["report"]["steps"]
    names = [str(s.get("name", s.get("uses", ""))) for s in steps]
    upload = max(i for i, n in enumerate(names) if "upload-artifact" in n)
    delivery = max(i for i, n in enumerate(names) if STEP in n)
    assert delivery > upload, names


def test_a_missing_metrics_file_opens_and_closes_nothing():
    # The file is the step's only input, and `if: always()` means it may not be
    # there. Absence must reach neither branch.
    js = step_github_script(WF_FILE, "report", STEP)
    assert "existsSync" in js, js[:200]
    head = js.split("const { owner, repo }")[0]
    assert head.count("return;") >= 2, head


def test_the_metrics_read_is_wrapped_so_a_corrupt_file_is_not_a_recovery():
    js = step_github_script(WF_FILE, "report", STEP)
    assert "try {" in js and "JSON.parse" in js, js[:200]


def test_the_workflow_still_only_needs_issues_write():
    perms = load_workflow(WF_FILE)["permissions"]
    assert perms.get("issues") == "write", perms
    assert "contents" in perms and perms["contents"] == "read", perms


def test_the_monitor_is_never_gated():
    # A gated monitor of the Conductor cannot report while the Conductor — the
    # thing that approves gates — is the component that is down.
    job = load_workflow(WF_FILE)["jobs"]["report"]
    assert "environment" not in job, job.get("environment")


def test_the_monitor_holds_no_key_vault_credential():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    code = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "read-all-" in ln or "wr-all-" in ln], code


def test_the_header_never_wraps_mid_hyphenated_word():
    # The catalog generator joins header comment lines with a space, so a word
    # split across the wrap ships to the public catalog as `read- only` (#840).
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    offenders = [ln for ln in raw.splitlines() if ln.startswith("#") and ln.rstrip().endswith("-")]
    assert not offenders, offenders


def test_the_header_says_the_silence_finding_leaves_the_log_thread():
    # The header is what the public catalog renders, and "writes ONE comment on
    # #719" stopped being true with this change.
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    header = "\n".join(ln for ln in raw.splitlines() if ln.startswith("#"))
    assert "rolling issue" in header, header
    assert "1269" in header, header


def _delivery_step() -> dict:
    for step in load_workflow(WF_FILE)["jobs"]["report"]["steps"]:
        if STEP in str(step.get("name", "")):
            return step
    raise AssertionError("no silence-delivery step in 739")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
