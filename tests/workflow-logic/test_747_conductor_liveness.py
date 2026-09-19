"""Unit tests for the 747 conductor-liveness decision logic.

The workflow's github-script step `require`s scripts/conductor-liveness-lib.js,
so exercising that module directly tests the shipped logic.

What matters here is not "does it notice a 5-day-old comment" — that arithmetic is
trivial — but the two failure shapes #1339 was filed about.

The first is the fail-open: 17 monitoring workflows were green for five days
because none of them watched the supervisor at all, and any monitor that renders
an *unknown* as an *OK* reproduces that at a smaller scale. So every path that
cannot establish liveness has a test asserting `hasFinding`:

  * the Conductor Log could not be read
  * it returned no comments at all
  * no comment matched the START/END pattern (ledger L215 — the log changed
    format once already and a stale pattern returns a confident wrong answer)
  * the newest comment's timestamp is absent, empty, non-ISO, or in the future
  * the merged-PR query failed, returned nothing, or carried no usable date
  * the open-PR count could not be read

The second is the detection *latency*: a monitor that only fires on day 5
reproduces the bug. `test_the_real_outage_window_reports_on_day_one` replays the
measured window (2026-09-14T10:20Z -> 2026-09-19T09:20Z) and requires a finding
24h in, and `test_the_healthy_window_is_silent` replays the ~3h-cadence window
before it and requires none. Both directions, or it is not a check.

One deliberate asymmetry is pinned here rather than left to reading: the open-PR
COUNT is never a finding on its own. The pile stood at 4 — above the worker cap
of 3 — all through the healthy window, so a level-triggered rule would report
every ordinary day. `pr-growth` carries that signal instead, and
`test_the_cap_alone_is_never_a_finding` is what stops a future edit from
"fixing" the level into an alert.

Shape tests cover the wiring the library cannot see: the schedule, the absence of
an environment gate, and — load-bearing per #1339 — that this monitor holds no
Key Vault credential and no environment, since a liveness check that can be
blocked by a gate only the absent supervisor approves, or that dies with a PAT it
might need to report, goes dark exactly when it is needed.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "conductor-liveness-lib.js"
WF_FILE = "747-conductor-liveness.yml"
WF_RAW = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")

NOW = "2026-09-19T12:00:00Z"

# The measured #1339 window. Run 174's END is the last thing the Conductor said.
LAST_CONDUCTOR = "2026-09-14T10:20:00Z"
LAST_MERGE = "2026-09-14T10:16:14Z"  # PR #1317, inside that same run
DAY_ONE = "2026-09-15T10:20:00Z"  # 24h after the silence began
DAY_FIVE = "2026-09-19T09:20:00Z"  # where it was actually noticed


def _node(expr_body: str, *argv: str):
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def analyze(**kwargs) -> dict:
    kwargs.setdefault("now", NOW)
    return _node(
        "process.stdout.write(JSON.stringify(l.analyze(JSON.parse(process.argv[1]))));",
        json.dumps(kwargs),
    )


def render(analysis: dict, iso: str = NOW) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderBody(JSON.parse(process.argv[1]),process.argv[2])));",
        json.dumps(analysis),
        iso,
    )


def summary(analysis: dict) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.summary(JSON.parse(process.argv[1]))));",
        json.dumps(analysis),
    )


def parse_comments(comments) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.parseConductorComments(JSON.parse(process.argv[1]))));",
        json.dumps(comments),
    )


def parse_history(body) -> list:
    return _node(
        "process.stdout.write(JSON.stringify(l.parseHistory(process.argv[1])));",
        "" if body is None else body,
    )


def render_history(samples) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderHistory(JSON.parse(process.argv[1]))));",
        json.dumps(samples),
    )


def find_rolling(items) -> object:
    return _node(
        "process.stdout.write(JSON.stringify(l.findRollingIssue(JSON.parse(process.argv[1]))));",
        json.dumps(items),
    )


def const(name: str):
    return _node(f"process.stdout.write(JSON.stringify(l.{name}));")


def _comment(at: str, body: str = "## Run 174 — END", **extra) -> dict:
    out = {"body": body, "created_at": at, "html_url": "https://example.invalid/c"}
    out.update(extra)
    return out


def _sig(analysis: dict, name: str) -> dict:
    found = [s for s in analysis["signals"] if s["name"] == name]
    assert found, f"no signal named {name} in {[s['name'] for s in analysis['signals']]}"
    return found[0]


def _healthy(now: str = "2026-09-14T10:20:00Z", **over) -> dict:
    """The window before the outage: ~3h cadence, PRs merging, 4 open and flat."""
    kwargs = dict(
        comments=[
            _comment("2026-09-14T01:15:00Z", "## Run 172 — END"),
            _comment("2026-09-14T04:20:00Z", "## Run 173 — END"),
            _comment("2026-09-14T07:20:00Z", "## Run 174 — START"),
        ],
        mergedPRs=[{"number": 1317, "merged_at": LAST_MERGE}],
        openPRs=4,
        priorBody=f"x {render_history([{'at': '2026-09-14T04:20:00Z', 'openPRs': 4}, {'at': '2026-09-14T07:20:00Z', 'openPRs': 4}])} y",
        now=now,
    )
    kwargs.update(over)
    return analyze(**kwargs)


def _outage(now: str, open_prs: int, history: list) -> dict:
    return analyze(
        comments=[_comment(LAST_CONDUCTOR, "## Run 174 — END")],
        mergedPRs=[{"number": 1317, "merged_at": LAST_MERGE}],
        openPRs=open_prs,
        priorBody=f"prelude {render_history(history)} tail",
        now=now,
    )


# --- the two discrimination fixtures #1339 requires -------------------------


def test_the_real_outage_window_reports_on_day_one():
    """A monitor that only fires at day 5 reproduces the bug it was filed about."""
    a = _outage(DAY_ONE, 6, [{"at": LAST_CONDUCTOR, "openPRs": 4}])
    assert a["hasFinding"], a["signals"]
    silence = _sig(a, "conductor-silence")
    assert silence["verdict"] == "ALERT", silence
    assert silence["measured"] == 24.0, silence
    # The second, independent signal must also have fired by day 1 — #1339's
    # point is that either alone would have caught it. WARN is the expected
    # verdict here, not ALERT: merge-silence is deliberately looser than the log
    # signal (it can go quiet legitimately), and its 24h warn is what makes it
    # corroborate on day 1 rather than race the primary signal.
    merge = _sig(a, "merge-silence")
    assert merge["verdict"] in ("WARN", "ALERT"), merge
    # Load-bearing: this signal must be a FINDING on its own at day 1, so that
    # removing conductor-silence entirely would still report the outage.
    alone = analyze(
        comments=[_comment(DAY_ONE, "## Run 999 — END")],
        mergedPRs=[{"number": 1317, "merged_at": LAST_MERGE}],
        openPRs=6,
        now=DAY_ONE,
    )
    assert _sig(alone, "conductor-silence")["verdict"] == "OK", alone["signals"]
    assert alone["hasFinding"], alone["signals"]


def test_the_healthy_window_is_silent():
    a = _healthy()
    assert not a["hasFinding"], [s for s in a["signals"] if s["verdict"] != "OK"]
    assert [s["verdict"] for s in a["signals"]] == ["OK", "OK", "OK", "OK"], a["signals"]


def test_day_five_is_still_a_finding_and_says_how_long():
    a = _outage(DAY_FIVE, 13, [{"at": LAST_CONDUCTOR, "openPRs": 4}])
    assert a["hasFinding"]
    s = _sig(a, "conductor-silence")
    # Assert the verdict before the magnitude. `measured` is None on every
    # UNKNOWN, and `None > 100` raises TypeError rather than failing an
    # assertion — which the module runner does not catch, so one such comparison
    # aborts the whole roster and every later test silently goes unreported
    # (ledger L194). run_all.py's roster guard catches that in CI; a standalone
    # run of this module would just look like a small failure set.
    assert s["verdict"] == "ALERT", s
    assert s["measured"] > 100, s


def test_the_report_names_every_signal_and_never_averages_them():
    a = _outage(DAY_ONE, 6, [{"at": LAST_CONDUCTOR, "openPRs": 4}])
    body = render(a)
    for name in ("conductor-silence", "merge-silence", "open-pr-cap", "pr-growth"):
        assert f"`{name}`" in body, (name, body)
    # Each carries its own measured value in its own row.
    assert "| 24" in body, body
    assert "conductor-silence=ALERT" in summary(a), summary(a)


# --- fail-closed: unknown is never alive -----------------------------------


def test_an_unreadable_log_is_a_finding():
    a = analyze(commentsError="HTTP 500", mergedPRs=[{"number": 1, "merged_at": NOW}], openPRs=2)
    assert a["hasFinding"]
    s = _sig(a, "conductor-silence")
    assert s["verdict"] == "UNKNOWN" and "HTTP 500" in s["detail"], s


def test_an_empty_comment_page_is_a_finding_and_names_that_cause():
    """The detail text is load-bearing, not decoration. "the log returned no
    comments at all" is a read/API problem; "no START/END matched in N comments"
    is silence or a stale pattern. Both are UNKNOWN and both are findings, so
    asserting only `hasFinding` lets the two diagnoses collapse into one — a
    mutation pass caught exactly that here: deleting the empty-page branch
    changed no verdict, because the matched==0 branch below absorbs it and
    reports the wrong reason. Unknown is never alive, and WHICH unknown is what
    the reader acts on."""
    a = analyze(comments=[], mergedPRs=[{"number": 1, "merged_at": NOW}], openPRs=2)
    assert a["hasFinding"]
    s = _sig(a, "conductor-silence")
    assert s["verdict"] == "UNKNOWN", s
    assert "no comments at all" in s["detail"], s
    # ...and specifically NOT the stale-pattern diagnosis, which would send the
    # reader to fix a regex when the log read came back empty.
    assert "L215" not in s["detail"], s


def test_a_pattern_that_matches_nothing_is_a_finding_and_says_so():
    """Ledger L215: the log changed format once already, and a stale pattern
    returns a confident wrong timestamp rather than an error."""
    a = analyze(
        comments=[_comment(NOW, "just some prose"), _comment(NOW, "another comment")],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=2,
    )
    assert a["hasFinding"]
    s = _sig(a, "conductor-silence")
    assert s["verdict"] == "UNKNOWN", s
    # The detail must distinguish "silent" from "my regex is stale", or the next
    # reader repeats L215.
    assert "L215" in s["detail"] and "2 comments" in s["detail"], s


def test_a_non_iso_timestamp_is_a_finding():
    a = analyze(
        comments=[_comment("2026", "## Run 174 — END")],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=2,
    )
    assert a["hasFinding"]
    assert _sig(a, "conductor-silence")["verdict"] == "UNKNOWN"


def test_an_absent_timestamp_is_a_finding():
    a = analyze(
        comments=[{"body": "## Run 174 — END"}],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=2,
    )
    assert a["hasFinding"]
    assert _sig(a, "conductor-silence")["verdict"] == "UNKNOWN"


def test_a_future_timestamp_is_a_finding_not_very_fresh():
    """Otherwise a bad clock or a hand-edited date reads as alive forever."""
    a = analyze(
        comments=[_comment("2026-09-20T12:00:00Z", "## Run 174 — END")],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=2,
    )
    assert a["hasFinding"]
    s = _sig(a, "conductor-silence")
    assert s["verdict"] == "UNKNOWN" and "future" in s["detail"], s


def test_an_unparseable_now_cannot_read_as_alive():
    """NaN fails both the future test and the threshold test, so without an
    explicit guard a silence of any length falls through to OK."""
    a = analyze(
        comments=[_comment(LAST_CONDUCTOR, "## Run 174 — END")],
        mergedPRs=[{"number": 1, "merged_at": LAST_MERGE}],
        openPRs=2,
        now="not-a-date",
    )
    assert a["hasFinding"]
    assert _sig(a, "conductor-silence")["verdict"] == "UNKNOWN"


def test_a_failed_merge_query_is_a_finding():
    a = analyze(comments=[_comment(NOW)], mergedError="rate limited", openPRs=2)
    assert a["hasFinding"]
    s = _sig(a, "merge-silence")
    assert s["verdict"] == "UNKNOWN" and "rate limited" in s["detail"], s


def test_an_empty_merged_set_is_unknown_not_ok():
    a = analyze(comments=[_comment(NOW)], mergedPRs=[], openPRs=2)
    assert a["hasFinding"]
    assert _sig(a, "merge-silence")["verdict"] == "UNKNOWN"


def test_merged_prs_with_no_usable_date_are_unknown():
    a = analyze(comments=[_comment(NOW)], mergedPRs=[{"number": 7}], openPRs=2)
    assert a["hasFinding"]
    assert _sig(a, "merge-silence")["verdict"] == "UNKNOWN"


def test_a_failed_open_count_is_a_finding():
    a = analyze(
        comments=[_comment(NOW)], mergedPRs=[{"number": 1, "merged_at": NOW}], openError="HTTP 502"
    )
    assert a["hasFinding"]
    assert _sig(a, "open-pr-cap")["verdict"] == "UNKNOWN"


def test_a_non_numeric_open_count_is_a_finding():
    a = analyze(
        comments=[_comment(NOW)], mergedPRs=[{"number": 1, "merged_at": NOW}], openPRs="lots"
    )
    assert a["hasFinding"]
    assert _sig(a, "open-pr-cap")["verdict"] == "UNKNOWN"


def test_a_failed_open_count_never_joins_the_history():
    """Appending an UNKNOWN would let a failed read masquerade as a data point
    and could manufacture a rise."""
    prior = render_history([{"at": "2026-09-18T00:00:00Z", "openPRs": 4}])
    a = analyze(
        comments=[_comment(NOW)],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openError="HTTP 502",
        priorBody=prior,
    )
    assert len(a["history"]) == 1, a["history"]


# --- the START/END pattern, in every spelling the log has carried ----------


def test_the_pattern_matches_the_current_em_dash_heading_form():
    p = parse_comments([_comment(NOW, "## Run 174 — START")])
    assert p["matched"] == 1 and p["newest"]["run"] == 174, p
    assert p["newest"]["phase"] == "START", p


def test_the_pattern_matches_the_pre_87_bare_form():
    """Ledger L215: a filter written for one era silently stops matching."""
    p = parse_comments([_comment(NOW, "RUN 86 START")])
    assert p["matched"] == 1 and p["newest"]["run"] == 86, p


def test_the_pattern_matches_hyphen_and_en_dash_separators():
    for sep in ("-", "–", "—", ""):
        p = parse_comments([_comment(NOW, f"## Run 200 {sep} END")])
        assert p["matched"] == 1, (sep, p)
        assert p["newest"]["phase"] == "END", (sep, p)


def test_the_newest_is_chosen_by_timestamp_not_by_position():
    """The comments endpoint returns oldest-first, but a caller that sliced a page
    can hand over any order, and 'the last element' would inherit that."""
    p = parse_comments(
        [
            _comment("2026-09-19T10:00:00Z", "## Run 180 — END"),
            _comment("2026-09-14T10:00:00Z", "## Run 174 — END"),
        ]
    )
    # `newest` is None whenever nothing matched, and subscripting it then raises
    # TypeError instead of failing an assertion — which aborts the roster and
    # hides every test after this one (ledger L194). Assert it matched first.
    assert p["matched"] == 2 and p["newest"] is not None, p
    assert p["newest"]["run"] == 180, p


def test_matched_is_reported_alongside_total():
    p = parse_comments([_comment(NOW, "## Run 1 — START"), _comment(NOW, "prose")])
    assert p == {
        "total": 2,
        "matched": 1,
        "newest": {
            "run": 1,
            "phase": "START",
            "at": NOW,
            "url": "https://example.invalid/c",
        },
    }, p


# --- the cap is context; growth is the finding -----------------------------


def test_the_cap_alone_is_never_a_finding():
    """4 open PRs was an ordinary healthy day. A level-triggered rule reports
    every one of them and teaches its reader to ignore the monitor."""
    a = _healthy(openPRs=9)
    assert not a["hasFinding"], [s for s in a["signals"] if s["verdict"] != "OK"]
    s = _sig(a, "open-pr-cap")
    assert s["verdict"] == "OK" and s["measured"] == 9, s
    assert "context only" in s["detail"], s


def test_sustained_growth_above_the_cap_alerts():
    a = analyze(
        comments=[_comment(NOW)],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=7,
        priorBody=render_history(
            [
                {"at": "2026-09-19T06:00:00Z", "openPRs": 5},
                {"at": "2026-09-19T09:00:00Z", "openPRs": 6},
            ]
        ),
    )
    s = _sig(a, "pr-growth")
    assert s["verdict"] == "ALERT", s
    assert "5 -> 6 -> 7" in s["detail"], s
    assert a["hasFinding"]


def test_growth_below_the_cap_only_warns():
    a = analyze(
        comments=[_comment(NOW)],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=3,
        priorBody=render_history(
            [
                {"at": "2026-09-19T06:00:00Z", "openPRs": 1},
                {"at": "2026-09-19T09:00:00Z", "openPRs": 2},
            ]
        ),
    )
    assert _sig(a, "pr-growth")["verdict"] == "WARN", _sig(a, "pr-growth")


def test_a_flat_or_falling_pile_is_not_growth():
    for prior in ([9, 9], [9, 8], [2, 12]):
        a = analyze(
            comments=[_comment(NOW)],
            mergedPRs=[{"number": 1, "merged_at": NOW}],
            openPRs=9,
            priorBody=render_history(
                [{"at": "2026-09-19T06:00:00Z", "openPRs": prior[0]}, {"at": "2026-09-19T09:00:00Z", "openPRs": prior[1]}]
            ),
        )
        assert _sig(a, "pr-growth")["verdict"] == "OK", (prior, _sig(a, "pr-growth"))


def test_too_little_history_says_so_rather_than_implying_no_growth():
    a = analyze(comments=[_comment(NOW)], mergedPRs=[{"number": 1, "merged_at": NOW}], openPRs=9)
    s = _sig(a, "pr-growth")
    assert s["verdict"] == "OK", s
    assert "not enough" in s["detail"] and "not the same as no growth" in s["detail"], s


def test_growth_needs_three_samples_so_one_rise_is_not_a_trend():
    assert const("GROWTH_SAMPLES") == 3
    a = analyze(
        comments=[_comment(NOW)],
        mergedPRs=[{"number": 1, "merged_at": NOW}],
        openPRs=9,
        priorBody=render_history([{"at": "2026-09-19T09:00:00Z", "openPRs": 4}]),
    )
    assert _sig(a, "pr-growth")["verdict"] == "OK", _sig(a, "pr-growth")


# --- history round-trip ----------------------------------------------------


def test_history_round_trips_through_the_issue_body():
    samples = [{"at": "2026-09-19T09:00:00Z", "openPRs": 4}]
    assert parse_history(f"prose\n{render_history(samples)}\nmore prose") == samples


def test_a_corrupt_history_block_yields_no_history_and_never_throws():
    for body in (
        "no block at all",
        "<!-- conductor-liveness:history [{ -->",
        "<!-- conductor-liveness:history {} -->",
        '<!-- conductor-liveness:history ["nope"] -->',
        "<!-- conductor-liveness:history [{'at':1}] -->",
        "<!-- conductor-liveness:history [",
    ):
        assert parse_history(body) == [], body


def test_history_is_capped_so_the_issue_body_does_not_become_a_log():
    limit = const("HISTORY_LIMIT")
    big = [{"at": NOW, "openPRs": i} for i in range(limit + 10)]
    assert len(parse_history(render_history(big))) == limit


def test_the_rendered_body_carries_the_history_for_the_next_run():
    a = _healthy()
    body = render(a)
    assert const("HISTORY_PREFIX") in body, body
    assert parse_history(body) == a["history"], (parse_history(body), a["history"])


# --- rolling-issue selection ----------------------------------------------


def test_the_rolling_issue_lookup_skips_pull_requests():
    """`issues.listForRepo` returns PRs too, and the marker is an HTML comment —
    invisible in a rendered body. Without the filter, the clean path would
    comment on and then CLOSE somebody's pull request."""
    marker = const("MARKER")
    items = [
        {"number": 1, "body": f"quoting {marker} in a PR", "pull_request": {"url": "u"}},
        {"number": 2, "body": f"the real rolling issue {marker}"},
    ]
    assert find_rolling(items)["number"] == 2


def test_no_marker_means_no_rolling_issue():
    assert find_rolling([{"number": 1, "body": "unrelated"}]) is None
    assert find_rolling([]) is None
    assert find_rolling([{"number": 1}]) is None


# --- thresholds ------------------------------------------------------------


def test_the_thresholds_are_tighter_than_the_observed_cadence():
    """Healthy cadence is ~3h. A warn threshold at or below it reports every
    ordinary run; an alert above a day reproduces the day-5 detection."""
    warn, alert = const("SILENCE_WARN_HOURS"), const("SILENCE_ALERT_HOURS")
    assert 3 < warn < alert <= 24, (warn, alert)


def test_a_warn_is_already_a_finding():
    """Holding out for ALERT would push first report past #1339's day-1 bar."""
    a = analyze(
        comments=[_comment("2026-09-19T03:00:00Z", "## Run 174 — END")],
        mergedPRs=[{"number": 1, "merged_at": "2026-09-19T03:00:00Z"}],
        openPRs=2,
    )
    assert _sig(a, "conductor-silence")["verdict"] == "WARN", _sig(a, "conductor-silence")
    assert a["hasFinding"]


# --- workflow shape --------------------------------------------------------


def _script() -> str:
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["check"]["steps"]
        if "upsert" in (s.get("name") or "")
    )
    return step["with"]["script"]


def test_the_workflow_requires_the_shipped_library():
    assert "./scripts/conductor-liveness-lib.js" in _script()


def test_the_workflow_reads_the_log_reference_from_the_library():
    """Hardcoding 719 in the YAML would let the tested constant and the fetched
    issue drift apart silently."""
    script = _script()
    assert "lib.LOG_ISSUE" in script, script
    assert "issue_number: 719" not in script, script


def test_the_workflow_uses_the_library_lookup_and_does_not_hand_roll_it():
    script = _script()
    assert "lib.findRollingIssue(" in script, script
    # A second, unguarded marker match would reintroduce the defect while the
    # library call above still made it look fixed.
    assert "includes(lib.MARKER)" not in script, script


def test_the_comment_read_is_paginated():
    """The comments endpoint returns at most 100 per page OLDEST-first, so an
    unpaginated read of a 450+-comment log yields the EARLIEST comments and
    reports a years-old timestamp as the newest one."""
    script = _script()
    assert "github.paginate(github.rest.issues.listComments" in script, script


def test_the_open_pr_count_is_paginated():
    """The count is an absence-shaped claim ('this many and no more')."""
    script = _script()
    assert "github.paginate(github.rest.issues.listForRepo" in script, script


def test_the_monitor_holds_no_key_vault_credential():
    """#1339's load-bearing constraint, carried up from #977: a liveness check
    must not depend on a credential whose death it might need to report."""
    assert "secrets-from-kv" not in WF_RAW
    assert "azure/login" not in WF_RAW
    assert "id-token" not in WF_RAW
    assert "${{ secrets." not in WF_RAW
    # A `read-all-*` NAME may appear in the header comment explaining why none is
    # used; it must not appear in anything that executes.
    code = [ln for ln in WF_RAW.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "read-all-" in ln], code


def test_the_monitor_is_never_gated():
    """A gate only the absent supervisor can approve would make this workflow
    unable to report the very outage it exists to report."""
    job = load_workflow(WF_FILE)["jobs"]["check"]
    assert "environment" not in job, job.get("environment")


def test_the_permissions_are_read_plus_issues_only():
    perms = load_workflow(WF_FILE)["permissions"]
    assert perms == {"contents": "read", "issues": "write"}, perms


def test_the_workflow_is_scheduled_and_dispatchable():
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    assert "schedule" in on, on
    # A monitor with no manual trigger cannot be verified on demand (#843).
    assert "workflow_dispatch" in on, on


def test_the_schedule_polls_faster_than_the_alert_threshold():
    """A 12h threshold polled daily is a 12-36h detector."""
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    crons = [s["cron"] for s in on["schedule"]]
    assert any("/" in c.split()[1] for c in crons), crons


def test_the_cron_collides_with_no_other_hub_schedule():
    mine = {
        s["cron"]
        for s in load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))["schedule"]
    }
    others = set()
    for f in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if f.name == WF_FILE:
            continue
        raw = f.read_text(encoding="utf-8-sig")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("- cron:"):
                others.add(line.split("cron:", 1)[1].strip().strip("'\"").split("#")[0].strip())
    minutes_mine = {c.split()[0] for c in mine}
    minutes_others = {c.split()[0] for c in others}
    assert minutes_mine.isdisjoint(minutes_others), (minutes_mine & minutes_others, mine)


def test_the_sweep_is_not_cancel_in_progress():
    assert load_workflow(WF_FILE)["concurrency"]["cancel-in-progress"] is False


def test_the_header_never_wraps_mid_hyphenated_word():
    # The catalog generator joins header comment lines with a space, so a word
    # split across the wrap ships to the public catalog as `read- only` (#840).
    offenders = [
        ln for ln in WF_RAW.splitlines() if ln.startswith("#") and ln.rstrip().endswith("-")
    ]
    assert not offenders, offenders


def test_the_workflow_has_a_safety_table_row():
    doc = (REPO_ROOT / "docs" / "workflow-safety-and-approvals.md").read_text(encoding="utf-8")
    assert "| 747 " in doc, "add a row to docs/workflow-safety-and-approvals.md"


def test_agents_md_gives_the_worker_the_escalation_trigger():
    """#1339 scope B. Without this the worker files a 39th 'normal terminal
    state' and the monitor's finding reaches nobody who acts on it."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "747" in agents, "AGENTS.md must name the liveness monitor"
    section = agents[agents.index("The landing sweep, and when it is finished") :]
    section = section[: section.index("\n## ")] if "\n## " in section else section
    assert "747" in section, "the escalation trigger belongs in the landing-sweep section"


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
