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


def test_the_pattern_matches_every_format_the_log_has_carried():
    """The live log caught this module shipping the very defect it cites.

    #719 changed format a second time at run 167, from a heading to a BOLD line:

        ## Run 166 — END                    <- the first revision matched this
        **Conductor run 167 — START** (…)   <- and silently did not match this

    Measured over all 871 comments: the first pattern found 260 matches ending at
    run 166 (2026-09-13T10:24:41Z), the current one finds 296 ending at run 174
    (2026-09-14T10:20:04Z) — 36 missed, and the newest now agrees with the figure
    #1339 quotes independently.

    It was invisible because the Conductor was genuinely down, so BOTH patterns
    said ALERT and the monitor looked right. The stale one was right for the
    wrong reason; its real failure is on recovery, where it would never see the
    new comments and would alert forever.
    """
    forms = {
        "**Conductor run 167 — START**": (167, "START"),  # current, run 167+
        "**Conductor run 174 — END** (2026-09-14 10:20Z)": (174, "END"),
        "## Run 166 — END": (166, "END"),  # heading era, ~87-166
        "RUN 86 START": (86, "START"),  # pre-87 bare form
        "_Conductor run 200 - END_": (200, "END"),  # underscore emphasis
        "> **Run 201 – START**": (201, "START"),  # quoted, en dash
    }
    for body, (num, phase) in forms.items():
        p = parse_comments([_comment(NOW, body)])
        assert p["matched"] == 1, (body, p)
        assert p["newest"]["run"] == num, (body, p)
        assert p["newest"]["phase"] == phase, (body, p)


def test_the_pattern_does_not_match_mere_prose_about_a_run():
    """The leading class is permissive by design; the rest must not be, or a
    worker comment discussing the Conductor would read as a heartbeat."""
    for body in (
        "the cloud worker saw run 174 START in the log",  # not at line start
        "## Cloud worker — landing sweep, 2026-09-20",
        "Run 174 was the last one before the outage",  # no phase word
        "## Run START",  # no number
    ):
        p = parse_comments([_comment(NOW, body)])
        assert p["matched"] == 0, (body, p)


def test_a_markdown_bullet_is_not_a_heartbeat():
    """`* run 174 START` is a list item, and it used to read as a heartbeat.

    The prefix class was `[>#*_ \\t]*`, which does not distinguish an emphasis
    marker from a list bullet. #719 is written by cloud workers and humans as
    well as the Conductor, and a bullet mentioning a run number and phase is
    something any of them might write. The monitor takes the NEWEST match, so
    one such line hands over that comment's `created_at` as the heartbeat and
    reports the Conductor ALIVE while it is down.

    That is the worst direction this module can fail in. Every other guard here
    exists to stop an unknown reading as an OK; this one would have made a real
    outage read as health — and the surrounding tests would not have noticed,
    because they only ever fed it well-formed Conductor lines.

    The discriminator is that emphasis binds to the text while a bullet is
    followed by whitespace, so the prefix admits `*` only via `\\*(?!\\s)`.
    Measured against the live log (all 117 #719 comments from 2026-09-13 to
    2026-09-24, spanning the whole current silence) both the old and the new
    pattern return 13 matches with the same newest timestamp, so the tightening
    costs no real detection.
    """
    for body in (
        "* run 174 START",
        "*   run 174 START",
        "  * run 174 END",
        "> * run 174 START",
        "- run 174 START",
    ):
        p = parse_comments([_comment(NOW, body)])
        assert p["matched"] == 0, (body, p)

    # Control, in the same test: every emphasis spelling still matches, so this
    # cannot pass by having broken the pattern outright.
    for body, num in (
        ("**Conductor run 174 — START**", 174),
        ("*Conductor run 174 — START*", 174),
        ("_Conductor run 174 — START_", 174),
    ):
        p = parse_comments([_comment(NOW, body)])
        assert p["matched"] == 1 and p["newest"]["run"] == num, (body, p)


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


def test_an_empty_ish_open_count_is_unknown_not_a_confident_zero():
    """`Number.isFinite(Number(v))` reads as a numeric guard and is not one.

    `Number(null)`, `Number('')`, `Number(' ')`, `Number([])` and `Number(false)`
    are every one of them `0`, so each passed the guard and was reported as a
    measured count of zero — "0 open, at or below the cap of 3". That is the
    exact shape this whole module exists to refuse: a value that could not be
    read, rendered as a healthy measurement rather than as UNKNOWN.

    `undefined` and `{}` coerce to NaN and were always caught, which is what made
    the hole easy to miss — the obvious probe passes.
    """
    for empty in (None, "", " ", [], False):
        s = _sig(_healthy(openPRs=empty), "open-pr-cap")
        assert s["verdict"] == "UNKNOWN", (empty, s)
        assert s["measured"] is None, (empty, s)
    # Control: real counts, including a numeric string, still measure.
    for good, want in ((0, 0), (7, 7), ("7", 7)):
        s = _sig(_healthy(openPRs=good), "open-pr-cap")
        assert s["verdict"] == "OK" and s["measured"] == want, (good, s)


def test_a_null_history_sample_is_dropped_not_read_as_zero():
    """parseHistory's input is the rolling ISSUE BODY, not our own wiring.

    A `{"openPRs": null}` sample became `0`, so a history of `null,2,3` read back
    as `0,2,3` — a STEEPER rise than the truth, which can manufacture a growth
    finding as readily as flatten a real one. The PR promised a corrupt block
    "suppresses only that signal, and says so"; a null laundered into a zero is
    neither suppressed nor said.
    """
    poisoned = parse_history(
        render_history([{"at": "a", "openPRs": None}, {"at": "b", "openPRs": 2}])
    )
    assert [h["openPRs"] for h in poisoned] == [2], poisoned
    # An all-null block yields no samples at all, so growth reports that it
    # cannot judge a trend rather than reporting no growth.
    assert parse_history(render_history([{"at": "a", "openPRs": None}] * 3)) == []


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


def test_the_merged_pr_read_pages_forward_until_it_finds_one():
    """A single page of closed items cannot be assumed to carry a merged PR.

    This read looks for a MAXIMUM (`merged_at`), so it does NOT need the full
    `github.paginate` walk the open count needs — the first page carrying any
    merged PR is enough. But it does need more than one page, and the shipped
    comment argued the opposite: the closed agentic-os set is already 113 items,
    and a closed item is re-sorted to the top by any comment or label change, not
    only by a merge. Exhaust 100 such items and the newest merge is on page 2,
    `mergedPRs` is empty, and merge-silence reports UNKNOWN while merges are
    happening — fail-closed, so noise rather than blindness, but noise is what
    teaches a reader to ignore a monitor.

    Assert the loop and its bound, not just the word 'page': an unbounded walk
    over every closed PR in the repo is the other way to get this wrong.

    And assert the STOP CONDITION, not merely that one exists. Breaking on the
    first page carrying any merge is what the first version of this fix did, and
    it is wrong for a reason the page count cannot see: the listing is ordered by
    `updated_at`, not `merged_at`, so an old merge commented on yesterday sorts
    above an untouched merge from this morning. The sound bound rests on
    `merged_at <= updated_at` — once this page's oldest `updated_at` is at or
    below the newest `merged_at` seen, no later page can carry a newer merge.
    Without the `oldestUpdatedT <= newestMergeT` assertion, a regression back to
    the too-early break still satisfies every other line here.
    """
    script = _script()
    assert "MAX_MERGE_PAGES" in script, "the merged-PR read must page forward"
    assert "page," in script, "the listForRepo call must pass a page argument"
    # Bounded: a cap the loop actually tests against, not a walk of everything.
    assert "page <= MAX_MERGE_PAGES" in script, script
    # The stop condition is the updated_at/merged_at bound, not "found one".
    assert "newestMergeT" in script, "the loop must track the newest merged_at"
    assert "oldestUpdatedT <= newestMergeT" in script, (
        "stopping on the first page with any merge measures from the wrong "
        "merge under sort=updated"
    )


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
    """A 12h threshold polled daily is a 12-36h detector.

    This asserted only that the hour field contains a `/`, which is a statement
    about the field's *spelling* and not about the contract in the test's own
    name. `19 */12 * * *` contains a slash and polls exactly as slowly as the
    alert threshold, so the monitor would report a stall up to 24h after it
    began — the latency failure #1339 is about, passing a test named for
    preventing it. Copilot's finding; third instance on this PR of a test
    asserting that a mechanism *exists* rather than that it is *right*.

    Measure the real quantity — the longest wait between consecutive fires — and
    compare it against the threshold read from the library, so the guard tracks
    `SILENCE_ALERT_HOURS` instead of pinning a literal that can drift away from
    it.
    """
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    crons = [s["cron"] for s in on["schedule"]]
    alert = const("SILENCE_ALERT_HOURS")
    worst = min(_max_gap_hours(c.split()[1]) for c in crons)
    assert worst < alert, (
        f"longest gap between polls is {worst}h against a {alert}h alert "
        f"threshold, so a stall is reported up to {worst + alert}h late",
        crons,
    )


def test_the_poll_gap_measure_is_not_satisfied_by_a_slash():
    """The guard above is only as strong as `_max_gap_hours`.

    Pin the spellings that decide it, including the one that used to pass: a
    slash is not evidence of anything, a single literal hour is a 24h gap rather
    than a 0h one, and the shipped schedule must come out well under the
    threshold rather than merely under it.
    """
    assert _max_gap_hours("*/2") == 2
    assert _max_gap_hours("*/12") == 12  # contains a slash, and is NOT fast enough
    assert _max_gap_hours("*") == 1
    assert _max_gap_hours("3") == 24  # once a day, not a zero-length gap
    assert _max_gap_hours("0,12") == 12
    assert _max_gap_hours("0,6,12,18") == 6
    # The shipped schedule, against the shipped threshold.
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    shipped = [s["cron"] for s in on["schedule"]]
    assert min(_max_gap_hours(c.split()[1]) for c in shipped) == 2, shipped
    assert const("SILENCE_ALERT_HOURS") == 12


def _cron_field(field, ceiling):
    """Expand one cron field to the concrete set of values it fires on.

    Comparing the raw field STRING is what the collision guard did first, and it
    is weaker than it reads: `19` and `19,49` are different strings and the same
    collision. 740 already schedules `9,39` in this repo, so the list form is not
    hypothetical — it simply happens not to overlap :19 today, which is exactly
    the kind of accident that stops being true on someone else's edit.

    Handles the four field shapes cron allows: a literal, a `a,b` list, an `a-b`
    range, and a `*`/`a-b` with a `/n` step. `ceiling` is the field's inclusive
    maximum — 59 for minutes, 23 for hours — so the same parser serves both
    rather than each guard growing its own.
    """
    out = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, _, raw_step = part.partition("/")
            step = int(raw_step)
        if part == "*":
            lo, hi = 0, ceiling
        elif "-" in part:
            lo_text, _, hi_text = part.partition("-")
            lo, hi = int(lo_text), int(hi_text)
        else:
            lo = hi = int(part)
        out.update(range(lo, hi + 1, step))
    return out


def _cron_minutes(field):
    """The minute field, as the collision guard reads it."""
    return _cron_field(field, 59)


def _max_gap_hours(hour_field):
    """The longest wait between two consecutive fires, in hours.

    This is the quantity the schedule's contract is actually about, and reading
    the field's *spelling* does not produce it. `*/12` and `*/2` both "contain a
    slash"; one leaves a 12-hour blind window and the other a 2-hour one. The gap
    wraps midnight, so a single literal hour is a 24-hour gap rather than a
    zero-hour one, which is the case a naive max-minus-min would score as best.
    """
    hours = sorted(_cron_field(hour_field, 23))
    if not hours:
        return 24
    if len(hours) == 1:
        return 24
    gaps = [b - a for a, b in zip(hours, hours[1:])]
    gaps.append(24 - hours[-1] + hours[0])  # the wrap across midnight
    return max(gaps)


def test_the_minute_expander_sees_through_every_cron_spelling():
    """The collision guard below is only as strong as this expansion.

    An expander that silently returned the empty set would make every collision
    disjoint — the same fail-open shape the rest of this module exists to refuse,
    one layer down in the test's own machinery. Pin each spelling, including the
    list form that produced the finding.
    """
    assert _cron_minutes("19") == {19}
    assert _cron_minutes("19,49") == {19, 49}
    assert _cron_minutes("9,39") == {9, 39}
    assert _cron_minutes("0-4") == {0, 1, 2, 3, 4}
    assert _cron_minutes("*/15") == {0, 15, 30, 45}
    assert _cron_minutes("10-40/10") == {10, 20, 30, 40}
    assert len(_cron_minutes("*")) == 60
    # The finding itself, stated as an assertion: different STRINGS, overlapping
    # minute SETS. The raw-string comparison this replaced passes on this pair.
    assert "19" != "19,49"
    assert _cron_minutes("19") & _cron_minutes("19,49") == {19}


def test_the_cron_collides_with_no_other_hub_schedule():
    """The header comment claims minute :19 is used by no other hub workflow.

    Assert that by the minutes the crons FIRE on rather than by how they are
    spelled — a `19,49` elsewhere is a real collision that a raw-string
    comparison scores as disjoint. Copilot's finding on #1341, and correct.
    """
    on = load_workflow(WF_FILE)
    mine = {s["cron"] for s in on.get(True, on.get("on"))["schedule"]}
    minutes_mine = set()
    for c in mine:
        minutes_mine |= _cron_minutes(c.split()[0])

    others = {}
    for f in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if f.name == WF_FILE:
            continue
        raw = f.read_text(encoding="utf-8-sig")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("- cron:"):
                cron = line.split("cron:", 1)[1].strip().strip("'\"").split("#")[0].strip()
                others.setdefault(f.name, set()).update(_cron_minutes(cron.split()[0]))

    clashes = {
        name: sorted(minutes_mine & mins) for name, mins in others.items() if minutes_mine & mins
    }
    assert not clashes, (clashes, sorted(minutes_mine))


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


def test_the_safety_row_carries_no_pipe_and_so_actually_parses():
    """The row's PRESENCE is not its correctness, and this is the gap that shipped.

    `generate-workflow-catalog.py:parse_safety_table` matches each cell with
    `([^|]+)` — cells may contain no `|` at all, and escaping it as `\\|` does not
    help because the class excludes the character, not the sequence. A row with a
    stray pipe therefore matches nothing, and the generator still exits 0 while
    emitting `safetyLevel: ''`, `approvalEnv: ''`, `guard: ''` for this workflow;
    the README then prints the `(repo plumbing)` fallback. Nothing failed: the
    doc-consistency check passed, the generator passed, and the presence test
    above passed, because a malformed row is still a row beginning `| 747 `.

    This shipped here as a literal `START|END` inside a code span, and prettier
    compounded it by widening the table's separator to the 6 columns the split
    cell implied. Assert the parse, not the presence.
    """
    row = next(
        ln
        for ln in (REPO_ROOT / "docs" / "workflow-safety-and-approvals.md")
        .read_text(encoding="utf-8")
        .split("\n")
        if ln.startswith("| 747 ")
    )
    cells = row.split("|")[1:-1]
    assert len(cells) == 5, (len(cells), "a 747 row cell contains a stray `|`")

    cat = json.loads((REPO_ROOT / "docs" / "workflow-catalog.json").read_text(encoding="utf-8"))
    entries = cat["workflows"] if isinstance(cat, dict) and "workflows" in cat else cat
    mine = next(w for w in entries if str(w.get("number")) == "747")
    # The three fields that silently came back empty.
    assert mine["safetyLevel"] == "Reads", mine["safetyLevel"]
    assert mine["guard"], "guard is empty -- the safety row did not parse"


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
