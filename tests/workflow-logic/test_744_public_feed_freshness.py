"""Unit tests for the 744 public-feed-freshness decision logic.

The workflow's github-script step `require`s scripts/public-feed-freshness-lib.js,
so exercising that module directly tests the shipped logic.

What matters here is not "does it notice a 3-day-old timestamp" — that arithmetic
is trivial — but the fail-open shapes #977 was filed about. Three process checks
were green while the published page was three days stale, and each of them was
green because an *unknown* had been rendered as an *OK*. So every path that
cannot establish freshness has a test asserting `hasFinding`:

  * the file is absent (404)
  * the fetch failed for any other reason
  * the body will not parse
  * `generated_at` is absent, empty, non-string, or not ISO-8601
  * `generated_at` is in the future (fresh forever, otherwise)
  * the workflow returned no observation at all for a registered path

Shape tests cover the wiring the library cannot see: the schedule, the absence
of an environment gate, and — load-bearing per #977 — that this monitor holds no
Key Vault credential, since a freshness check that depends on the credential
whose death it reveals goes dark exactly when it is needed.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "public-feed-freshness-lib.js"
WF_FILE = "744-repo-public-feed-freshness.yml"
WF_RAW = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")

NOW = "2026-08-01T12:00:00Z"
PUBLIC = "public/data/agentic-os-status.json"
SRC = "src/data/agentic-os-status.json"


def _node(expr_body: str, *argv: str):
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def classify(obs, now: str = NOW) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.classifyFeed(JSON.parse(process.argv[1]),process.argv[2])));",
        json.dumps(obs),
        now,
    )


def analyze(**kwargs) -> dict:
    kwargs.setdefault("now", NOW)
    return _node(
        "process.stdout.write(JSON.stringify(l.analyze(JSON.parse(process.argv[1]))));",
        json.dumps(kwargs),
    )


def render(analysis: dict) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderBody(JSON.parse(process.argv[1]),process.argv[2])));",
        json.dumps(analysis),
        NOW,
    )


def const(name: str):
    return _node(f"process.stdout.write(JSON.stringify(l.{name}));")


def _feed(generated_at, path: str = PUBLIC, **extra) -> dict:
    payload = {"generated_at": generated_at, "org": "FreeForCharity"}
    payload.update(extra)
    return {"path": path, "status": 200, "text": json.dumps(payload), "error": None}


def _both(generated_at) -> list:
    return [_feed(generated_at, PUBLIC), _feed(generated_at, SRC)]


# --- freshness classification ---------------------------------------------


def test_a_recent_timestamp_is_fresh():
    r = classify(_feed("2026-08-01T10:24:42Z"))
    assert r["verdict"] == "FRESH", r
    assert r["ageHours"] == 1.6, r


def test_the_977_observation_a_three_day_old_feed_is_stale():
    # The live state #977 was filed about: 502's deliver job had been failing
    # since 2026-07-29 and the published feed never moved.
    r = classify(_feed("2026-07-29T10:00:00Z"))
    assert r["verdict"] == "STALE", r
    assert r["ageHours"] > 36, r


def test_the_threshold_is_36_hours():
    assert const("STALE_AFTER_HOURS") == 36


def test_one_missed_daily_tick_does_not_report():
    # 502 publishes daily at 07:17Z. A single skipped day leaves the feed ~29h
    # old at this workflow's 10:13Z slot — inside the window, on purpose.
    r = classify(_feed("2026-07-31T07:17:00Z"))
    assert r["verdict"] == "FRESH", r


def test_two_missed_ticks_do_report():
    r = classify(_feed("2026-07-30T07:17:00Z"))
    assert r["verdict"] == "STALE", r


def test_exactly_at_the_threshold_is_not_yet_stale():
    # Guards the comparison's direction and its boundary: `> threshold`, so the
    # 36h mark itself is still fresh. Flipping the operator flips this test AND
    # test_the_977_observation, which is the mutation check #977 asks for.
    r = classify(_feed("2026-07-31T00:00:00Z"))
    assert r["ageHours"] == 36.0, r
    assert r["verdict"] == "FRESH", r


def test_just_past_the_threshold_is_stale():
    r = classify(_feed("2026-07-30T23:00:00Z"))
    assert r["ageHours"] == 37.0, r
    assert r["verdict"] == "STALE", r


# --- the fail-open shapes #977 exists to close -----------------------------


def test_a_missing_file_is_a_finding_not_fresh():
    absent = {"path": PUBLIC, "status": 404, "text": None, "error": None}
    assert classify(absent)["verdict"] == "MISSING"
    a = analyze(observations=[absent, _feed("2026-08-01T10:00:00Z", SRC)])
    assert a["hasFinding"], a
    assert [r["path"] for r in a["missing"]] == [PUBLIC], a


def test_an_empty_generated_at_is_a_finding_not_fresh():
    r = classify(_feed(""))
    assert r["verdict"] == "INVALID", r
    assert "empty" in r["detail"], r


def test_an_absent_generated_at_is_a_finding_not_fresh():
    obs = {"path": PUBLIC, "status": 200, "text": json.dumps({"org": "FreeForCharity"}), "error": None}
    r = classify(obs)
    assert r["verdict"] == "INVALID", r
    assert "absent" in r["detail"], r


def test_an_unparseable_timestamp_is_a_finding_not_fresh():
    r = classify(_feed("last Tuesday"))
    assert r["verdict"] == "INVALID", r


def test_a_date_without_a_time_cannot_express_freshness_and_is_invalid():
    # Date.parse('2026-08-01') succeeds and would read as midnight — accepting it
    # would mean a feed could be up to 24h wrong about its own age.
    r = classify(_feed("2026-08-01"))
    assert r["verdict"] == "INVALID", r


def test_a_bare_year_is_invalid_even_though_date_parse_accepts_it():
    assert classify(_feed("2026"))["verdict"] == "INVALID"


def test_a_non_string_generated_at_is_a_finding():
    assert classify(_feed(1754049882))["verdict"] == "INVALID"


def test_a_future_timestamp_is_a_finding_not_permanently_fresh():
    # Without this, one bad clock or hand-edited date makes the feed read fresh
    # forever — the same fail-open, wearing a green hat.
    r = classify(_feed("2026-09-01T00:00:00Z"))
    assert r["verdict"] == "INVALID", r
    assert "future" in r["detail"], r


def test_ordinary_clock_skew_is_tolerated():
    r = classify(_feed("2026-08-01T12:30:00Z"))
    assert r["verdict"] == "FRESH", r


def test_a_body_that_is_not_json_is_a_finding():
    obs = {"path": PUBLIC, "status": 200, "text": "<html>404</html>", "error": None}
    assert classify(obs)["verdict"] == "INVALID"


def test_a_json_scalar_is_not_a_feed():
    obs = {"path": PUBLIC, "status": 200, "text": "null", "error": None}
    assert classify(obs)["verdict"] == "INVALID"


def test_a_transport_error_is_unreadable_never_fresh():
    obs = {"path": PUBLIC, "status": None, "text": None, "error": "getaddrinfo ENOTFOUND"}
    r = classify(obs)
    assert r["verdict"] == "UNREADABLE", r


def test_a_5xx_is_unreadable_not_a_missing_file():
    obs = {"path": PUBLIC, "status": 500, "text": None, "error": "Server Error"}
    assert classify(obs)["verdict"] == "UNREADABLE"


def test_an_empty_body_is_unreadable():
    obs = {"path": PUBLIC, "status": 200, "text": "   ", "error": None}
    assert classify(obs)["verdict"] == "UNREADABLE"


def test_each_unbelievable_timestamp_raises_a_finding_not_only_a_verdict():
    """The acceptance criterion is about `hasFinding`, not about the label.

    Asserting only `verdict == "INVALID"` leaves the INVALID bucket free to be
    dropped from `hasFinding` with no test failing — a mutation run proved
    exactly that, so this is the assertion that pins the workflow's behaviour
    rather than its vocabulary.
    """
    bad = {
        "absent": {"path": PUBLIC, "status": 200, "text": json.dumps({"org": "FFC"}), "error": None},
        "empty": _feed(""),
        "unparseable": _feed("last Tuesday"),
        "date-only": _feed("2026-08-01"),
        "future": _feed("2026-09-01T00:00:00Z"),
        "not-json": {"path": PUBLIC, "status": 200, "text": "<html>", "error": None},
    }
    for label, obs in bad.items():
        a = analyze(observations=[obs, _feed("2026-08-01T10:00:00Z", SRC)])
        assert a["hasFinding"], (label, a)
        assert [r["path"] for r in a["invalid"]] == [PUBLIC], (label, a)


def test_each_unreachable_path_raises_a_finding_not_only_a_verdict():
    for label, obs in {
        "transport": {"path": PUBLIC, "status": None, "text": None, "error": "ENOTFOUND"},
        "5xx": {"path": PUBLIC, "status": 500, "text": None, "error": "Server Error"},
        "empty-body": {"path": PUBLIC, "status": 200, "text": "   ", "error": None},
    }.items():
        a = analyze(observations=[obs, _feed("2026-08-01T10:00:00Z", SRC)])
        assert a["hasFinding"], (label, a)
        assert [r["path"] for r in a["unreadable"]] == [PUBLIC], (label, a)


def test_a_stale_path_raises_a_finding_not_only_a_verdict():
    a = analyze(observations=_both("2026-07-28T10:00:00Z"))
    assert a["hasFinding"], a
    assert len(a["stale"]) == 2, a


def test_a_registered_path_with_no_observation_is_a_finding():
    """The one-level-up version of the same defect: a step that silently skips a
    path must not produce a green run."""
    a = analyze(observations=[_feed("2026-08-01T10:00:00Z", PUBLIC)])
    assert a["hasFinding"], a
    assert [r["path"] for r in a["unreadable"]] == [SRC], a
    assert a["checked"] == 2, a


def test_no_observations_at_all_is_a_finding():
    a = analyze(observations=[])
    assert a["hasFinding"], a
    assert len(a["unreadable"]) == 2, a
    assert not a["fresh"], a


# --- both copies, reported by path ----------------------------------------


def test_both_registered_copies_are_checked():
    assert const("FEED_PATHS") == [PUBLIC, SRC]


def test_both_fresh_is_the_only_clean_state():
    a = analyze(observations=_both("2026-08-01T10:24:42Z"))
    assert not a["hasFinding"], a
    assert len(a["fresh"]) == 2, a


def test_one_stale_copy_is_reported_by_path_not_averaged_away():
    a = analyze(
        observations=[_feed("2026-08-01T10:00:00Z", PUBLIC), _feed("2026-07-28T10:00:00Z", SRC)]
    )
    assert a["hasFinding"], a
    assert [r["path"] for r in a["stale"]] == [SRC], a
    assert [r["path"] for r in a["fresh"]] == [PUBLIC], a


def test_an_unregistered_extra_path_is_still_classified():
    a = analyze(observations=_both("2026-08-01T10:00:00Z") + [_feed("2026-07-01T00:00:00Z", "x.json")])
    assert a["checked"] == 3, a
    assert [r["path"] for r in a["stale"]] == ["x.json"], a


def test_the_feed_repo_is_the_published_site():
    assert const("FEED_OWNER") == "FreeForCharity"
    assert const("FEED_REPO") == "FFC-IN-ffcadmin.org"


# --- report ----------------------------------------------------------------


def test_the_body_carries_the_marker_for_the_rolling_upsert():
    body = render(analyze(observations=_both("2026-07-20T00:00:00Z")))
    assert body.startswith(const("MARKER")), body[:80]


def test_a_stale_report_names_the_path_and_the_age():
    body = render(analyze(observations=_both("2026-07-28T10:00:00Z")))
    assert PUBLIC in body and SRC in body, body
    assert "Stale" in body, body


def test_every_finding_bucket_renders_a_section():
    a = analyze(
        observations=[
            _feed("2026-07-20T00:00:00Z", PUBLIC),
            {"path": SRC, "status": 500, "text": None, "error": "Server Error"},
            _feed("", "a.json"),
            {"path": "b.json", "status": 404, "text": None, "error": None},
        ]
    )
    body = render(a)
    for heading in ("Stale", "Could not be read", "Unbelievable timestamp", "Missing"):
        assert heading in body, (heading, body)


def test_the_summary_reports_checked_against_registered():
    a = analyze(observations=[_feed("2026-08-01T10:00:00Z", PUBLIC)])
    line = _node(
        "process.stdout.write(JSON.stringify(l.summary(JSON.parse(process.argv[1]))));",
        json.dumps(a),
    )
    # A short denominator must be visible rather than invisible (#966's lesson).
    assert "checked=2 of 2" in line, line
    assert "unreadable=1" in line, line


# --- rolling-issue lookup (the close path decides what this returns) -------


def _find(items):
    return _node(
        "process.stdout.write(JSON.stringify(l.findRollingIssue(JSON.parse(process.argv[1]))));",
        json.dumps(items),
    )


MARKED = "<!-- public-feed-freshness -->\n\nbody"


def test_a_pull_request_carrying_the_marker_is_never_selected():
    """`issues.listForRepo` returns PRs too, and the clean-run path CLOSES what
    this selects — so a PR quoting the marker would be closed by the monitor.

    The marker is an HTML comment, i.e. invisible in a rendered PR body, so a PR
    can carry it without its author ever seeing it.
    """
    assert _find([{"number": 5, "body": MARKED, "pull_request": {"url": "…"}}]) is None


def test_the_issue_is_selected_when_a_marked_pr_is_listed_first():
    got = _find(
        [
            {"number": 5, "body": MARKED, "pull_request": {"url": "…"}},
            {"number": 9, "body": MARKED},
        ]
    )
    assert got and got["number"] == 9, got


def test_a_plain_marked_issue_is_selected():
    got = _find([{"number": 9, "body": MARKED}])
    assert got and got["number"] == 9, got


def test_no_marker_selects_nothing():
    assert _find([{"number": 9, "body": "unrelated issue"}]) is None


def test_a_null_or_missing_body_does_not_throw():
    assert _find([{"number": 1, "body": None}, {"number": 2}, None]) is None


def test_an_empty_listing_selects_nothing():
    assert _find([]) is None


def test_the_workflow_uses_the_library_lookup_and_does_not_hand_roll_it():
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["check"]["steps"]
        if "upsert" in (s.get("name") or "")
    )
    script = step["with"]["script"]
    assert "lib.findRollingIssue(" in script, script
    # A second, unguarded marker match would reintroduce the defect while the
    # library call above still made it look fixed.
    assert "includes(lib.MARKER)" not in script, script


# --- workflow shape --------------------------------------------------------


def test_the_workflow_requires_the_shipped_library():
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["check"]["steps"]
        if "upsert" in (s.get("name") or "")
    )
    assert "./scripts/public-feed-freshness-lib.js" in step["with"]["script"]


def test_the_workflow_reads_every_registered_path_from_the_library():
    """Hardcoding the paths in the YAML would let the tested registry and the
    fetched set drift apart silently."""
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["check"]["steps"]
        if "upsert" in (s.get("name") or "")
    )
    assert "lib.FEED_PATHS" in step["with"]["script"]


def test_the_monitor_holds_no_key_vault_credential():
    """#977's load-bearing constraint: a freshness monitor that depends on the
    credential whose death it reveals is circular. No KV action, no OIDC, no
    azure/login, and no id-token permission."""
    assert "secrets-from-kv" not in WF_RAW
    assert "azure/login" not in WF_RAW
    assert "id-token" not in WF_RAW
    # `${{ secrets.* }}` at all — the #912 class, and there is nothing this
    # workflow could legitimately want from one.
    assert "${{ secrets." not in WF_RAW
    # A `read-all-*` NAME may appear in the header comment explaining why none
    # is used; it must not appear in anything that executes.
    code = [ln for ln in WF_RAW.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "read-all-" in ln], code


def test_the_monitor_is_never_gated():
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
    assert "| 744 " in doc, "add a row to docs/workflow-safety-and-approvals.md"


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
