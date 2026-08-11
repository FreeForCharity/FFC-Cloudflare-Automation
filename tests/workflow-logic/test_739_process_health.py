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
from wf_extract import load_workflow, step_github_script

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "process-health-metrics-lib.js"
WF_FILE = "739-process-health-metrics.yml"


def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True, encoding="utf-8",
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
    # `ready` is the agent-ready band (#922) — the subset of the agentic-os topic
    # label an agent can actually pick up. Absent input means 0, not null: an
    # empty ready queue is a real, actionable reading ("nothing is pickable"),
    # unlike a mean age over an empty set.
    assert m["agenticOs"] == {"open": 18, "closed": 5, "ready": 0}, m
    assert m["dataPipeline"]["runs"] == 3 and m["dataPipeline"]["success"] == 2, m
    assert m["dataPipeline"]["successRate"] == 0.667, m
    # per-workflow grouping, sorted by name
    bw = m["dataPipeline"]["byWorkflow"]
    assert [w["name"] for w in bw] == ["502. Google", "703. Sites"], bw
    assert bw[0]["successRate"] == 0.5 and bw[1]["successRate"] == 1, bw


def test_the_ready_queue_is_reported_alongside_the_backlog_not_instead_of_it():
    """#922: the band the routine means, without losing the programme's real size.

    `open` counts the whole `agentic-os` topic label — epics, machine-managed
    rolling issues, human-blocked items, durable findings. That number is honest
    and should stay visible; it just is not the thing the 5-15 band was ever
    about. Both are reported so neither reading is lost.
    """
    m = compute({"nowIso": NOW, "agenticOpen": 56, "readyOpen": 6})
    assert m["agenticOs"]["open"] == 56, m
    assert m["agenticOs"]["ready"] == 6, m
    body = render(m, None)
    assert "Ready queue" in body, "the ready queue must render its own row"
    assert "56" in body and "6" in body, "both numbers must survive to the report"


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


def _select_required(check_runs: list, names: list) -> list:
    """Run the workflow's OWN latestByName selector against a fixture.

    Extracted from the YAML rather than restated here: a copy in the test would
    keep passing after the workflow reverted to `runs.find(...)`, which is the
    exact mutation this guards.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    start = raw.index("const latestByName")
    end = raw.index("const required =", start)
    selector = "\n".join(line.strip() for line in raw[start:end].splitlines())
    code = (
        "const runs=JSON.parse(process.argv[1]);"
        f"{selector}"
        "process.stdout.write(JSON.stringify("
        "JSON.parse(process.argv[2]).map(latestByName)));"
    )
    proc = subprocess.run(
        ["node", "-e", code, json.dumps(check_runs), json.dumps(names)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_a_rerun_in_flight_does_not_read_as_the_older_success():
    """The re-run case: same name, same SHA, old pass first in API order.

    `runs.find()` returns the stale success and the PR is reported green while
    its current attempt is still running. Selecting the latest attempt must
    surface the in-flight run, whose null conclusion fails the success test.
    """
    runs = [
        {
            "name": "Validate Repository",
            "started_at": "2026-07-20T01:00:00Z",
            "completed_at": "2026-07-20T01:05:00Z",
            "conclusion": "success",
        },
        {
            "name": "Validate Repository",
            "started_at": "2026-07-20T03:00:00Z",
            "completed_at": None,
            "conclusion": None,
        },
    ]
    (picked,) = _select_required(runs, ["Validate Repository"])
    assert picked["started_at"] == "2026-07-20T03:00:00Z", (
        f"must pick the newest attempt, picked {picked['started_at']}"
    )
    assert picked["conclusion"] != "success", "an in-flight re-run must not read as green"


def test_a_rerun_that_failed_beats_the_earlier_pass():
    """Ordering, not conclusion, decides — a later failure must win."""
    runs = [
        {
            "name": "Phantom Revert Guard",
            "started_at": "2026-07-20T01:00:00Z",
            "completed_at": "2026-07-20T01:05:00Z",
            "conclusion": "success",
        },
        {
            "name": "Phantom Revert Guard",
            "started_at": "2026-07-20T04:00:00Z",
            "completed_at": "2026-07-20T04:06:00Z",
            "conclusion": "failure",
        },
    ]
    (picked,) = _select_required(runs, ["Phantom Revert Guard"])
    assert picked["conclusion"] == "failure", "the latest attempt failed; the PR is not green"


def test_a_missing_required_check_still_selects_to_null():
    """Absent stays absent — the caller treats null as not-green."""
    picked = _select_required([{"name": "Some Other Check"}], ["Validate Repository"])
    assert picked == [None], f"a check with no runs must select to null, got {picked}"


def test_the_selector_is_not_a_bare_find():
    """Pin the defect out of the source as well as out of the behaviour."""
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "runs.find((r) => r.name === n)" not in raw, (
        "reverted to first-match; a re-run can mask a failure with an older success"
    )
    assert "started_at" in raw, "latest-attempt ordering must use started_at, set on in-flight runs"


# ---------------------------------------------------------------------------
# Mergeability is computed ASYNCHRONOUSLY, so one GET is a sample, not a read.
#
# A cold `pulls.get` answers `mergeable: null` / `mergeable_state: 'unknown'`
# and only then starts the background computation. The qualifying rule above
# requires `mergeable_state === 'clean'`, so a single sample silently drops the
# PR — and drops it into the flattering direction, because this metric's whole
# job is to notice PRs that are green, clean and unlanded. Measured live on
# 2026-08-11: #1127 and #1039, both green/clean drafts waiting on a human for
# days, answered `unknown` on the first GET and `clean` seconds later.
#
# These exercise the SHIPPED resolver out of the workflow YAML rather than a
# Python re-implementation of it — a ported algorithm cannot catch the workflow
# reverting to a single call.
# ---------------------------------------------------------------------------

RESOLVER_START = "const MERGEABILITY_ATTEMPTS"
RESOLVER_END = "for (const item of agenticPrs) {"


def _resolver_source() -> str:
    """The resolver block, sliced out of the workflow's github-script body.

    Asserts both anchors before slicing: if the block is renamed or moved this
    fails loudly rather than silently testing an empty string.
    """
    body = step_github_script(WF_FILE, "report", "Gather, compute, and post the weekly report")
    assert RESOLVER_START in body, f"resolver block anchor {RESOLVER_START!r} is gone"
    assert RESOLVER_END in body, f"loop anchor {RESOLVER_END!r} is gone"
    start = body.index(RESOLVER_START)
    end = body.index(RESOLVER_END, start)
    src = body[start:end]
    assert "getPrWithMergeability" in src, "the sliced block does not define the resolver"
    return src


def _drive_resolver(responses: list) -> dict:
    """Run the shipped resolver against a scripted sequence of `pulls.get` bodies.

    Returns {calls, warnings, result} — `calls` is what proves a single sample
    was replaced by a resolution, and `warnings` is what proves an unresolvable
    PR is reported rather than dropped in silence.
    """
    harness = (
        "const responses=JSON.parse(process.argv[1]);"
        "const owner='FreeForCharity', repo='FFC-Cloudflare-Automation';"
        "let calls=0; const warnings=[];"
        "const core={warning:(m)=>warnings.push(m)};"
        "const github={rest:{pulls:{get:async()=>{"
        "  const r=responses[Math.min(calls,responses.length-1)]; calls++; return {data:r};"
        "}}}};"
        "(async()=>{"
        + _resolver_source().replace("\n", "\n")
        + "const result=await getPrWithMergeability(1);"
        "process.stdout.write(JSON.stringify({calls,warnings,result}));"
        "})();"
    )
    return _node(harness, json.dumps(responses))


_UNKNOWN = {"number": 1, "draft": True, "mergeable": None, "mergeable_state": "unknown"}
_CLEAN = {"number": 1, "draft": True, "mergeable": True, "mergeable_state": "clean"}


def test_an_unknown_first_read_is_resolved_rather_than_dropped():
    """The exact live shape: cold GET `unknown`, second GET `clean`.

    Before this, the PR was skipped and the count fell to zero — the one number
    that must never read healthy while work sits unlanded.
    """
    out = _drive_resolver([_UNKNOWN, _CLEAN])
    assert out["result"]["mergeable_state"] == "clean", (
        f"a PR that resolves to clean must be returned as clean, got {out['result']}"
    )
    assert out["calls"] >= 2, (
        f"one GET is a sample, not a read — the resolver must retry, made {out['calls']} call(s)"
    )
    assert out["warnings"] == [], "a PR that resolved needs no warning"


def test_a_resolved_first_read_costs_exactly_one_call():
    """Retrying is the fix; retrying unconditionally would be a new defect.

    A weekly job reading every agentic-os PR must not pay the delay per PR when
    the answer arrived immediately.
    """
    out = _drive_resolver([_CLEAN])
    assert out["calls"] == 1, f"already-resolved must not re-poll, made {out['calls']} calls"


def test_a_false_mergeable_is_resolved_too_and_is_not_retried():
    """`mergeable: false` is an ANSWER. Only null means 'not computed yet'.

    Retrying on falsiness rather than on null would poll every conflicted PR to
    the attempt cap for nothing — and would read as working, because the verdict
    is the same either way.
    """
    dirty = {"number": 1, "draft": True, "mergeable": False, "mergeable_state": "dirty"}
    out = _drive_resolver([dirty])
    assert out["calls"] == 1, f"a false mergeable is resolved; made {out['calls']} calls"
    assert out["result"]["mergeable_state"] == "dirty"


def test_a_pr_that_never_resolves_is_warned_about_not_silently_dropped():
    """The residual silence is the thing being closed here.

    Retries shrink the hole; they do not remove it. An unresolvable PR is still
    excluded from the count, so the run has to SAY the count is a floor — the
    denominator rule (L173) applied to the one metric that cannot afford a
    flattering zero.
    """
    out = _drive_resolver([_UNKNOWN])
    assert len(out["warnings"]) == 1, (
        f"an unresolvable PR must be reported, got warnings={out['warnings']}"
    )
    msg = out["warnings"][0]
    assert "#1" in msg, f"the warning must name the PR, got {msg!r}"
    assert "floor" in msg, (
        f"the warning must say the count is understated, not merely that a read failed: {msg!r}"
    )
    assert out["calls"] > 1, "it must exhaust its attempts before giving up"


def test_the_workflow_does_not_sample_mergeability_once():
    """Pin the defect out of the source as well as the behaviour.

    The behavioural tests above run a slice of the YAML; this catches a revert
    that deletes the slice entirely, where the anchor assertions would fail with
    a confusing message instead of this one.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "getPrWithMergeability" in raw, (
        "mergeability must be resolved through the retrying helper, not sampled inline"
    )
    assert "data.mergeable !== null" in raw, (
        "the resolution test must be `mergeable !== null` — `mergeable_state` reads "
        "'unknown' alongside a null mergeable, and truthiness would treat false as unresolved"
    )


# ---------------------------------------------------------------------------
# Conductor runs that started and never ended (#970).
#
# Run 63 posted `## Run 63 — START` on #719 at 2026-08-01T01:05:45Z and died:
# no END, no commit, no PR, no claim, no label change. Nothing detected it; run
# 64 found it three hours later by reading the comment tail by hand. A dead run
# approves no gates, merges no PRs and refreshes no feed — while its START
# comment leaves the thread looking like it ran.
# ---------------------------------------------------------------------------

CR_NOW = "2026-08-01T04:00:00Z"


def _comment(created_at: str, body: str) -> dict:
    return {"created_at": created_at, "body": body}


def _start(run: int, created_at: str) -> dict:
    # The header's own timestamp is fuzzed by the Conductor to the minute-tens
    # digit; the fixtures carry that verbatim so nothing can quietly start
    # parsing it. `created_at` is the only exact instant.
    stamp = created_at[:15] + "xZ"
    return _comment(created_at, f"## Run {run} — START ({stamp})\n\nPlan: land the queue.")


def _end(run: int, created_at: str) -> dict:
    stamp = created_at[:15] + "xZ"
    return _comment(created_at, f"## Run {run} — END ({stamp}) · core 4910 / 5000\n\nDone.")


def _dead(comments: list, now: str = CR_NOW, threshold=None) -> dict:
    args = {"nowIso": now, "logComments": comments}
    if threshold is not None:
        args["deadRunThresholdHours"] = threshold
    return compute(args)["conductorRuns"]


def test_a_run_that_started_and_ended_is_not_reported():
    cr = _dead([_start(62, "2026-07-31T22:05:28Z"), _end(62, "2026-07-31T22:27:18Z")])
    assert cr["count"] == 0, cr
    assert cr["observed"] == 1, "the run was still SEEN — 0 of 0 is an unread log, not a clean week"


def test_a_start_with_no_end_past_the_threshold_is_reported():
    """Run 63, as it actually happened — the regression fixture."""
    cr = _dead([_start(63, "2026-08-01T01:05:45Z")])
    assert cr["count"] == 1, cr
    assert cr["dead"][0]["run"] == 63, cr
    assert cr["dead"][0]["startedAt"] == "2026-08-01T01:05:45Z", cr
    assert cr["dead"][0]["ageHours"] == 2.9, cr


def test_a_start_with_no_end_inside_the_threshold_is_a_run_in_flight():
    """The live conductor run posting this very report must not report itself."""
    cr = _dead([_start(64, "2026-08-01T03:30:00Z")])  # 0.5h old
    assert cr["count"] == 0, f"a run 30 minutes in is still working: {cr}"
    assert cr["observed"] == 1, cr


def test_the_run_63_thread_as_it_stood_reports_63_and_nothing_else():
    """The issue's own verification, run against the real #719 timestamps.

    Runs 60/61/62/64 paired; only 63 is dead. This is the whole metric in one
    assertion, and it is the case a permissive matcher would also pass — which
    is why the discrimination tests below exist too.
    """
    cr = _dead(
        [
            _end(61, "2026-07-31T19:25:04Z"),  # START is off the top of the window
            _start(62, "2026-07-31T22:05:28Z"),
            _end(62, "2026-07-31T22:27:18Z"),
            _start(63, "2026-08-01T01:05:45Z"),
            _start(64, "2026-08-01T04:06:42Z"),
            _end(64, "2026-08-01T04:40:46Z"),
        ],
        now="2026-08-01T05:00:00Z",
    )
    assert [d["run"] for d in cr["dead"]] == [63], cr
    assert cr["observed"] == 3, "61's END with no START in-window must not invent a run"


def test_start_and_end_split_across_a_pagination_boundary_still_pair():
    """The case that will regress.

    listComments returns <=100 oldest-first, so a START and its END routinely
    land on different pages — run 61's did on 2026-07-31. Pairing per page (the
    shape the previous baseline loop used) reports every boundary-split run as
    dead. The workflow accumulates pages before scanning; this pins it.
    """
    page1 = [_start(58, "2026-07-30T06:00:00Z"), _start(59, "2026-07-30T12:00:00Z")]
    page2 = [_end(58, "2026-07-30T06:40:00Z"), _end(59, "2026-07-30T12:30:00Z")]
    cr = _dead(page1 + page2)
    assert cr["count"] == 0, f"pages must be joined before pairing: {cr}"
    # And per-page scanning is exactly what this would have looked like:
    assert _dead(page1)["count"] == 2, "sanity: page 1 alone genuinely looks like two dead runs"


def test_a_quoted_header_cannot_retire_a_dead_run():
    """Only a comment's own header — line-start, first line — may pair a run.

    Documenting the incident must not erase it. Run 64's write-up of run 63
    necessarily contains the string `## Run 63 — END`, and this very PR adds the
    header format to the thread. Two ways prose reaches the matcher, one per
    guard: a header quoted in a BLOCKQUOTE on the first line (defeats a matcher
    that forgot to anchor at line start), and a header further down the body
    (defeats a matcher that scans the whole comment). Both must leave 63 dead.
    """
    blockquoted = _comment(
        "2026-08-01T04:20:00Z",
        "> ## Run 63 — END (2026-08-01T01:5xZ)\n\nquoting the header that never arrived.",
    )
    buried = _comment(
        "2026-08-01T04:25:00Z",
        "Documenting the format the new detector matches:\n\n"
        "```\n## Run 63 — END (2026-08-01T01:5xZ)\n```\n",
    )
    for forged in (blockquoted, buried):
        cr = _dead([_start(63, "2026-08-01T01:05:45Z"), forged])
        assert [d["run"] for d in cr["dead"]] == [63], (
            f"a quoted header must not pair; run 63 stayed dead: {cr}"
        )
        assert cr["observed"] == 1, f"the forged header must not register a run either: {cr}"


def test_a_level_three_heading_is_not_a_run_header():
    """`### Run 63 started and died` is prose, not a START."""
    cr = _dead([_comment("2026-08-01T01:00:00Z", "### Run 63 started and died\n\nnarrative")])
    assert cr["observed"] == 0, f"a ### heading must not register a run: {cr}"
    assert cr["count"] == 0, cr


def test_an_unreadable_log_is_not_assessed_rather_than_zero():
    """The reassuring-direction failure this whole class is about.

    The comment fetch is wrapped in try/catch so a transient API error cannot
    wedge the weekly job. If that swallowed read surfaced as "0 dead runs", the
    report would be at its most confident exactly when it knows least.
    """
    absent = compute({"nowIso": CR_NOW})["conductorRuns"]
    assert absent["assessed"] is False, absent
    assert absent["count"] is None, "null, not 0 — 'unknown' and 'none' are different facts"
    body = render(compute({"nowIso": CR_NOW}), None)
    assert "not assessed" in body, "the report must say so, not print a zero"


def test_the_threshold_is_configurable_and_actually_applied():
    comments = [_start(63, "2026-08-01T01:05:45Z")]  # 2.9h before CR_NOW
    assert _dead(comments, threshold=2)["count"] == 1
    assert _dead(comments, threshold=6)["count"] == 0, "a wider threshold must spare it"
    assert _dead(comments)["thresholdHours"] == 2, "default stays 2h"


def test_an_unusable_threshold_override_falls_back_instead_of_alarming():
    """Both coercion failures fail in the ALARMING direction, and neither is loud.

    `Number('')` is 0, so every in-flight run reads dead. `Number('2h')` is NaN,
    and `ageHours < NaN` is false, so the in-flight skip never fires — again
    every unmatched START reads dead. A false alarm about the supervisor is
    worse than the silence this metric replaces, and workflow_dispatch inputs
    arrive as strings, so the bad value is a plausible input rather than a
    hypothetical one. The effective threshold is returned and rendered, so the
    fallback announces itself rather than hiding.
    """
    fresh = [_start(64, "2026-08-01T03:30:00Z")]  # 0.5h old: alive
    # `Number()` maps every one of these to 0 or NaN. Note "" / "   " / False /
    # [] coerce to 0 — finite AND non-negative — so a value check alone passes
    # them; the type has to be narrowed first. (NaN itself is unreachable
    # through this harness: JSON has no NaN literal, so "NaN" covers the branch.)
    for bad in ("", "   ", "2h", "abc", "NaN", -1, False, True, [], [5], {}):
        cr = _dead(fresh, threshold=bad)
        assert cr["thresholdHours"] == 2, f"threshold={bad!r} must fall back to the default: {cr}"
        assert cr["count"] == 0, f"threshold={bad!r} must not report a live run dead: {cr}"
    # A usable override still wins — the fallback must not swallow real values.
    assert _dead(fresh, threshold=0.25)["count"] == 1, "0.5h exceeds a 0.25h threshold"
    assert _dead(fresh, threshold=0)["thresholdHours"] == 0, "zero is a real value, not 'unset'"


def test_a_direct_call_with_a_broken_clock_fails_fast():
    """`findDeadConductorRuns` is exported, so it cannot lean on computeMetrics.

    An invalid nowIso makes every age NaN; `NaN < threshold` is false, so the
    in-flight skip never fires and every unmatched START is reported dead with
    ageHours serialising to null.
    """
    for bad in ("", "not-a-date", None):
        try:
            _node(
                "const a=JSON.parse(process.argv[1]);"
                "process.stdout.write(JSON.stringify(l.findDeadConductorRuns(a.c,a.now)));",
                json.dumps({"c": [_start(63, "2026-08-01T01:05:45Z")], "now": bad}),
            )
        except AssertionError as e:
            assert "nowIso" in str(e), e
            continue
        raise AssertionError(f"expected findDeadConductorRuns to throw for nowIso={bad!r}")


def test_a_direct_call_with_an_unreadable_log_is_not_a_reassuring_zero():
    """Same reasoning as the broken clock, applied to the comments argument.

    `buildConductorRuns` guards `Array.isArray(logComments)` so the *workflow*
    path is correct today, but that guard is internal and unexported, while
    `findDeadConductorRuns` is the only exported entry point to the scan. A
    direct caller handed a failed read got `assessed: true, count: 0` — the
    reassuring zero the design exists to prevent, and the one direction the
    other two validations were fixed for.
    """
    for bad in (None, "", 0, False, {}, {"body": "## Run 63 - START"}):
        cr = _node(
            "const a=JSON.parse(process.argv[1]);"
            "process.stdout.write(JSON.stringify(l.findDeadConductorRuns(a.c,a.now)));",
            json.dumps({"c": bad, "now": NOW}),
        )
        assert cr["assessed"] is False, f"non-array {bad!r} must not read as assessed: {cr}"
        assert cr["count"] is None, f"non-array {bad!r} must not report a count: {cr}"
        assert cr["dead"] == [], cr

    # An array that is merely empty is a different thing and stays assessed:
    # the log was read and held no runs, which `observed: 0` discloses.
    cr = _dead([])
    assert cr["assessed"] is True and cr["count"] == 0 and cr["observed"] == 0, cr


def test_a_reposted_start_does_not_reset_the_clock():
    cr = _dead(
        [_start(63, "2026-08-01T01:05:45Z"), _start(63, "2026-08-01T03:50:00Z")],
    )
    assert cr["count"] == 1, cr
    assert cr["dead"][0]["startedAt"] == "2026-08-01T01:05:45Z", (
        f"the run began when it first said it began: {cr}"
    )


def test_the_report_names_the_dead_runs_and_renders_a_trend():
    m = compute({"nowIso": CR_NOW, "logComments": [_start(63, "2026-08-01T01:05:45Z")]})
    body = render(m, None)
    assert "Conductor runs that died" in body, "the table row must render"
    assert "run 63" in body and "2026-08-01T01:05:45Z" in body, (
        "a count nobody can investigate a week later is not the metric"
    )
    # Trend against a week with no dead runs.
    prev = compute({"nowIso": "2026-07-25T04:00:00Z", "logComments": []})
    assert "▲ +1" in render(m, prev), "a run lost since last week must show as a rise"


def test_a_report_predating_the_dead_run_field_renders_without_an_arrow():
    m = compute({"nowIso": CR_NOW, "logComments": [_start(63, "2026-08-01T01:05:45Z")]})
    out = render(m, {"agenticOs": {"open": 5, "closed": 1}, "claims": {"open": 2}})
    assert "Conductor runs that died" in out
    assert "—" in out, "a missing previous value must render an em dash, not an arrow"


def _run_comment_pager(pages: list) -> dict:
    """Run the workflow's OWN comment-paging loop against fixtured pages.

    Extracted from the YAML rather than restated here: a copy in the test would
    keep passing after the workflow went back to scanning one page at a time,
    which is the exact mutation this guards. `github.rest.issues.listComments`
    is stubbed to serve `pages` and record which pages were requested.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    start = raw.index("const acc = [];")
    end = raw.index("logComments = acc;", start) + len("logComments = acc;")
    loop = "\n".join(line.strip() for line in raw[start:end].splitlines())
    code = (
        "const pages=JSON.parse(process.argv[1]);"
        "const MAX_BASELINE_PAGES=10, owner='o', repo='r', logIssue=719, since='s';"
        "const asked=[];"
        "const github={rest:{issues:{listComments:async(p)=>{"
        "asked.push(p.page);return {data:pages[p.page-1]||[]};}}}};"
        "let logComments=null;"
        f"(async()=>{{{loop}\n"
        "process.stdout.write(JSON.stringify({comments:logComments,asked}));})();"
    )
    proc = subprocess.run(
        ["node", "-e", code, json.dumps(pages)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_the_workflow_joins_every_page_before_the_scan_sees_them():
    """A START on page 1 and its END on page 2 must reach the lib together.

    Run 61's did on 2026-07-31. If the workflow hands the lib one page at a
    time, every boundary-split run is reported dead — a false alarm on the
    supervisor itself, which is worse than the silence it replaces.
    """
    filler = [_comment("2026-07-30T05:00:00Z", "routine note") for _ in range(98)]
    page1 = [_start(58, "2026-07-30T06:00:00Z"), _start(59, "2026-07-30T12:00:00Z")] + filler
    page2 = [_end(58, "2026-07-30T06:40:00Z"), _end(59, "2026-07-30T12:30:00Z")]
    assert len(page1) == 100, "page 1 must be full or the loop stops before page 2"

    out = _run_comment_pager([page1, page2])
    assert out["asked"] == [1, 2], f"a full page must be followed by the next: {out['asked']}"
    assert len(out["comments"]) == 102, (
        f"every page must survive into one list, got {len(out['comments'])}"
    )
    # And the joined list is what makes the pairing correct.
    cr = _dead(out["comments"], now="2026-07-31T00:00:00Z")
    assert cr["count"] == 0, f"58 and 59 both ended; neither is dead: {cr}"


def test_the_workflow_wiring_feeds_one_read_to_both_consumers():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "logComments," in raw, "the workflow must pass the fetched comments to computeMetrics"
    assert "extractPreviousMetrics(logComments)" in raw, (
        "baseline and dead-run scan must share ONE read of #719 — not two"
    )
    assert "per_page: 100" in raw and "page: bp" in raw, "the comment read must be paginated"


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
