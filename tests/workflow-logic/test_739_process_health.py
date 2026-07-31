"""Unit tests for the 739 process-health metrics decision logic.

The workflow's github-script step `require`s scripts/process-health-metrics-lib.js,
so exercising that module directly tests the shipped math + Markdown: how gathered
REST data becomes the weekly metrics object, how the previous week's baseline is
recovered from the hidden data block (for trends), and how "no data" stays "—"
instead of a misleading 0. A shape test guards the workflow wiring (read-only,
requires the lib, schedule + dispatch).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "process-health-metrics-lib.js"
WF_FILE = "739-process-health-metrics.yml"


def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def compute(input_obj: dict) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.computeMetrics(JSON.parse(process.argv[1]))));",
        json.dumps(input_obj),
    )


def render(metrics: dict, prev, opts=None) -> str:
    return _node(
        "const a=JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify("
        "l.renderReport(a.metrics, a.prev, a.opts||{})));",
        json.dumps({"metrics": metrics, "prev": prev, "opts": opts or {}}),
    )


def extract_prev(comments: list) -> object:
    return _node(
        "const r=l.extractPreviousMetrics(JSON.parse(process.argv[1]));"
        "process.stdout.write(JSON.stringify(r===null?null:r));",
        json.dumps(comments),
    )


NOW = "2026-07-20T00:00:00Z"


# --- aggregation -----------------------------------------------------------

def test_counts_means_and_success_rate():
    m = compute({
        "nowIso": NOW,
        # ages: 5d and 10d -> mean 7.5
        "smokeOpen": [
            {"created_at": "2026-07-15T00:00:00Z"},
            {"created_at": "2026-07-10T00:00:00Z"},
        ],
        # time-to-close: 2d
        "smokeClosedRecent": [
            {"created_at": "2026-07-01T00:00:00Z", "closed_at": "2026-07-03T00:00:00Z"},
        ],
        "claimedOpen": [{"created_at": "2026-07-18T00:00:00Z"}],  # 2d
        "agenticOpen": 18,
        "agenticClosedRecent": 5,
        "pipelineRuns": [
            {"name": "502. Google", "conclusion": "success"},
            {"name": "502. Google", "conclusion": "failure"},
            {"name": "703. Sites", "conclusion": "success"},
        ],
    })
    assert m["smokeFailures"]["open"] == 2, m
    assert m["smokeFailures"]["meanAgeDays"] == 7.5, m
    assert m["smokeFailures"]["closed"] == 1, m
    assert m["smokeFailures"]["meanTimeToCloseDays"] == 2, m
    assert m["claims"]["open"] == 1 and m["claims"]["meanAgeDays"] == 2, m
    assert m["agenticOs"] == {"open": 18, "closed": 5}, m
    assert m["dataPipeline"]["runs"] == 3 and m["dataPipeline"]["success"] == 2, m
    assert m["dataPipeline"]["successRate"] == 0.667, m
    # per-workflow grouping, sorted by name
    bw = m["dataPipeline"]["byWorkflow"]
    assert [w["name"] for w in bw] == ["502. Google", "703. Sites"], bw
    assert bw[0]["successRate"] == 0.5 and bw[1]["successRate"] == 1, bw


def test_empty_inputs_use_null_not_zero_for_means():
    m = compute({"nowIso": NOW})
    assert m["smokeFailures"]["open"] == 0, m
    assert m["smokeFailures"]["meanAgeDays"] is None, m
    assert m["smokeFailures"]["meanTimeToCloseDays"] is None, m
    assert m["claims"]["meanAgeDays"] is None, m
    assert m["dataPipeline"]["successRate"] is None, m
    assert m["dataPipeline"]["byWorkflow"] == [], m


def test_missing_or_invalid_nowiso_throws():
    # Fail fast instead of emitting NaN ages / "Generated: undefined".
    for bad in ({}, {"nowIso": ""}, {"nowIso": "not-a-date"}):
        try:
            compute(bad)
        except AssertionError as e:
            assert "nowIso" in str(e), e
            continue
        raise AssertionError(f"expected computeMetrics to throw for input {bad}")


def test_future_created_at_clamps_to_zero_age():
    # A clock-skewed created_at must not produce a negative age.
    m = compute({
        "nowIso": NOW,
        "smokeOpen": [{"created_at": "2026-07-25T00:00:00Z"}],
    })
    assert m["smokeFailures"]["meanAgeDays"] == 0, m


# --- trend baseline round-trip ---------------------------------------------

def test_render_embeds_data_block_and_extract_recovers_it():
    m = compute({"nowIso": NOW, "agenticOpen": 7})
    body = render(m, None)
    assert "<!-- process-health-metrics-report -->" in body, body
    assert "First report" in body, body  # no baseline
    # The embedded block round-trips through the extractor the next run uses.
    recovered = extract_prev([{"body": "unrelated"}, {"body": body}])
    assert recovered["agenticOs"]["open"] == 7, recovered
    assert recovered["generatedAt"] == NOW, recovered


def test_extract_prev_picks_latest_and_skips_malformed():
    good1 = render(compute({"nowIso": NOW, "agenticOpen": 1}), None)
    good2 = render(compute({"nowIso": "2026-07-27T00:00:00Z", "agenticOpen": 2}), None)
    comments = [
        {"body": good1},
        {"body": "<!-- phm-data:{not json} -->"},  # malformed: skipped, not thrown
        {"body": good2},
    ]
    recovered = extract_prev(comments)
    assert recovered["agenticOs"]["open"] == 2, recovered


def test_extract_prev_none_when_absent():
    assert extract_prev([{"body": "no data here"}]) is None
    assert extract_prev([]) is None


def test_render_trend_arrows_against_previous():
    prev = compute({"nowIso": NOW, "agenticOpen": 20})
    cur = compute({"nowIso": NOW, "agenticOpen": 18})
    body = render(cur, prev)
    assert "Trend column compares against the previous weekly report." in body, body
    assert "▼ -2" in body, body  # backlog dropped by 2


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_is_read_only():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "scripts/process-health-metrics-lib.js" in raw, "workflow must require the shipped lib"
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    assert perms.get("actions") == "read", perms
    # Read-only: no environment gate on any job.
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


# ---------------------------------------------------------------------------
# "Green, clean, and stuck in draft" (#900).
#
# Every other signal in this report reads GREEN while finished agent work sits
# unlanded — backlog and claim counts actually look *healthier* as PRs pile up
# in draft. Four consecutive landing runs rediscovered that and wrote it up as
# prose. These pin the metric that replaces the prose.
# ---------------------------------------------------------------------------

RW_NOW = "2026-07-31T12:00:00Z"


def _rw(candidates) -> dict:
    return compute({"nowIso": RW_NOW, "readyWaiting": candidates})["readyWaiting"]


def test_no_candidates_yields_zero_and_null_ages():
    """Absent input must not throw, and must not render a misleading 0 age."""
    for empty in ([], None):
        r = compute({"nowIso": RW_NOW} if empty is None else {"nowIso": RW_NOW, "readyWaiting": empty})
        rw = r["readyWaiting"]
        assert rw["count"] == 0, rw
        assert rw["meanAgeDays"] is None, "empty must be null, not 0 — see how claims does it"
        assert rw["maxAgeDays"] is None, rw
        assert rw["oldest"] is None, rw


def test_one_candidate_is_counted_and_aged():
    rw = _rw([{"number": 897, "readySinceIso": "2026-07-30T00:00:00Z"}])
    assert rw["count"] == 1
    assert rw["maxAgeDays"] == 1.5, rw
    assert rw["oldest"] == {"number": 897, "ageDays": 1.5}, rw


def test_a_parked_candidate_is_excluded_from_the_count_but_still_listed():
    """The load-bearing exclusion.

    A PR parked for a stated reason and a PR simply forgotten look identical.
    Counting the first alerts forever and trains everyone to ignore the metric.
    """
    rw = _rw([
        {"number": 897, "readySinceIso": "2026-07-30T00:00:00Z"},
        {"number": 837, "readySinceIso": "2026-07-25T00:00:00Z", "parked": True},
    ])
    assert rw["count"] == 1, f"parked must not be counted: {rw}"
    assert rw["numbers"] == [897], rw
    assert rw["parked"] == [837], "a parked PR must still be listed, not hidden"
    assert rw["oldest"]["number"] == 897, "the parked PR must not become 'oldest'"


def test_the_oldest_is_the_oldest_not_the_first():
    rw = _rw([
        {"number": 898, "readySinceIso": "2026-07-31T06:00:00Z"},
        {"number": 897, "readySinceIso": "2026-07-29T12:00:00Z"},
    ])
    assert rw["oldest"]["number"] == 897, rw


def test_the_report_names_the_prs_not_just_a_count():
    """A count nobody can act on is what the four prior landing runs already had."""
    m = compute({"nowIso": RW_NOW, "readyWaiting": [
        {"number": 897, "readySinceIso": "2026-07-30T00:00:00Z"},
        {"number": 837, "readySinceIso": "2026-07-25T00:00:00Z", "parked": True},
    ]})
    out = render(m, None)
    assert "#897" in out, "the report must name the waiting PR"
    assert "PRs ready but unlanded" in out, "the table row must render"
    assert "#837" in out and "parked" in out.lower(), "parked PRs must be shown as excluded"


def test_the_threshold_callout_fires_only_past_a_day():
    hot = compute({"nowIso": RW_NOW, "readyWaiting": [
        {"number": 897, "readySinceIso": "2026-07-30T00:00:00Z"},  # 1.5d
    ]})
    fresh = compute({"nowIso": RW_NOW, "readyWaiting": [
        {"number": 898, "readySinceIso": "2026-07-31T06:00:00Z"},  # 0.25d
    ]})
    assert "need promoting out of draft" in render(hot, None), "1.5d must trigger the callout"
    assert "need promoting out of draft" not in render(fresh, None), (
        "a PR ready for six hours is not yet a problem worth naming"
    )


def test_a_report_predating_the_field_gives_no_arrow_and_does_not_crash():
    """`prev` from before #900 has no readyWaiting key — must render, not throw."""
    m = compute({"nowIso": RW_NOW, "readyWaiting": [
        {"number": 897, "readySinceIso": "2026-07-30T00:00:00Z"},
    ]})
    out = render(m, {"agenticOs": {"open": 5, "closed": 1}, "claims": {"open": 2}})
    assert "PRs ready but unlanded" in out
    assert "—" in out, "a missing previous value must render an em dash, not an arrow"


def test_the_workflow_only_counts_drafts_that_are_clean_and_green():
    """The lib is pure, so the qualifying rule lives in the workflow — pin it.

    Mutation-relevant: dropping any one of these three conditions turns the
    metric into "count of open agentic-os PRs", which is not the thing.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "pr.draft" in raw, "must require the PR to be a draft"
    assert "mergeable_state !== 'clean'" in raw, "must require a clean mergeable_state"
    assert "Validate Repository" in raw and "Phantom Revert Guard" in raw, (
        "must name the repo's actual required checks"
    )
    # Naming them is not enforcing them. The first version of this test asserted
    # only the names, and a mutation deleting the conclusion check sailed past —
    # the REQUIRED_CHECKS array survived, so the names were still "present".
    # Presence mistaken for validity, in the guard for a metric about exactly
    # that. Assert the comparison itself.
    assert "conclusion === 'success'" in raw, (
        "must assert the required checks CONCLUDED success — listing their names proves nothing"
    )
    assert "checks\n                .listForRef" in raw or "checks.listForRef" in raw, (
        "must read check-runs; the legacy status API reads pending forever here (L26)"
    )
    assert "parked" in raw, "must honour the parked marker/label"


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
