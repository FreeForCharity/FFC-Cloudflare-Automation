"""Guard: a scheduled gated workflow must fire faster than janitor 734 reaps.

`test_gated_env_hygiene.py` already forbids the two shapes #834 found, and its
docstring blesses a third as the *correct* fix for a Writes-level gated cron:

> (2) catches a *Writes* level scheduled workflow that legitimately needs its
> gate but should not silently cancel itself — for that shape the correct fix
> is `cancel-in-progress: false` plus letting janitor 734 reap it, not ungating.

That is sound only while **reaping is rarer than answering.** It stops being
sound when the workflow's own cron period is as long as 734's `max_age_days`,
because then every gate that is not answered inside a single period is
destroyed, and the next period opens an identical gate into the same race. The
reap is no longer a cleanup of an abandoned run; it is the workflow's default
outcome.

**703 is the hub's only occupant of the blessed shape, and it is measured to
lose.** `703-sites-list-generate.yml` fires `0 8 * * 1` — weekly, a 7-day
period — and 734's `max_age_days` default is **7**. Measured 2026-10-09 over
703's last 11 scheduled runs:

| outcome                 | count |
| ----------------------- | ----- |
| `cancelled` (reaped)    | 7     |
| `success` (answered)    | 3     |
| `waiting` (in the race) | 1     |

All seven cancellations are 734's, not a successor's — 703 sets
`cancel-in-progress: false`, so no successor can cancel it, and every one of
the seven was `updated_at` at **created + 8 days, ~06:5xZ**, which is the first
janitor tick after it crossed the 7-day threshold:

    34822353952  created 2026-09-14T08:22:03Z  cancelled 2026-09-22T06:53:13Z
    34100182363  created 2026-09-07T08:21:42Z  cancelled 2026-09-15T06:52:44Z
    32705656342  created 2026-08-24T08:18:44Z  cancelled 2026-09-01T06:52:04Z
    32009612154  created 2026-08-17T08:16:53Z  cancelled 2026-08-25T06:48:56Z
    31370775983  created 2026-08-10T08:35:55Z  cancelled 2026-08-18T06:48:20Z
    30800404614  created 2026-08-03T09:12:25Z  cancelled 2026-08-11T06:50:38Z
    30252940885  created 2026-07-27T09:12:27Z  cancelled 2026-08-04T07:25:50Z

Seven for seven on one mechanism is not latency, it is the design.

**Why this is not already caught.** `test_703_stays_gated` asserts 703 keeps
its gate, which is correct and is about a different question — *should this
gate exist* — and reads as though 703 has been considered. Nothing anywhere
compares a gated cron's **period** against the janitor's **threshold**, so the
one number that decides whether the shape can ever finish is unasserted. The
failure is silent by construction: a reaped run is `cancelled`, which no
failure alerter treats as a failure, and 734's own warning goes to a run
summary and to #719 — neither of which blocks anything.

This module asserts the comparison and enumerates the occupants as a **closed
set**, so a new scheduled gated workflow fails here rather than quietly
joining the race, and 703's exemption fails too once 703 is fixed. Deciding
*how* to fix 703 is an operator call (shorten its cron, raise 734's
`max_age_days`, or make it dispatch-only), which is why the row below is an
acknowledged exemption carrying its measurement rather than a silent allow.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
JANITOR = WORKFLOWS / "734-stale-waiting-run-janitor.yml"

# Same gated set as test_gated_env_hygiene.py — environments carrying
# `required_reviewers`, i.e. a run parks at `status: waiting` until a human acts.
GATED_ENVS = {
    "cloudflare-prod",
    "cloudflare-prod-write",
    "github-prod",
    "google-prod-write",
    "m365-prod",
    "whmcs-prod",
    "wpmudev-prod",
}

# Scheduled + reviewer-gated workflows whose cron period is NOT shorter than
# the janitor's reap threshold, so an unanswered gate is destroyed rather than
# accumulating. Each entry is an acknowledged operator decision, not a pass.
#
# Keep this a CLOSED set: `test_the_reap_dominated_set_is_exactly_the_occupants`
# fails both when a new workflow acquires the shape and when a listed one is
# fixed, so neither direction can drift silently.
REAP_DOMINATED = {
    # 703 fires weekly (7d) against max_age_days=7. 7 of its last 11 scheduled
    # runs were reaped by 734 at created+8d; see this module's docstring.
    # Tracked for an operator decision — do not remove without changing 703's
    # cron, 734's threshold, or 703's triggers.
    "703-sites-list-generate.yml",
}


def _janitor_max_age_days() -> int:
    """734's `max_age_days` default, read from the workflow's own input block.

    Parsed rather than hard-coded: the whole point of this module is that the
    two numbers must be compared, so a stale copy of one of them would make the
    guard assert against itself.
    """
    text = JANITOR.read_text(encoding="utf-8-sig")
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))
    inputs = ((on or {}).get("workflow_dispatch") or {}).get("inputs") or {}
    raw = (inputs.get("max_age_days") or {}).get("default")
    assert raw is not None, (
        f"{JANITOR.name}: could not read `max_age_days.default` from the "
        "workflow_dispatch inputs — the input block was renamed or removed, so "
        "this module cannot compare a cron period against the reap threshold."
    )
    return int(str(raw))


def _field_matches(spec: str, value: int, lo: int, hi: int) -> bool:
    """Match one cron field against one value. Supports `*`, `*/n`, `a-b`, lists."""
    for part in spec.split(","):
        part = part.strip()
        if part in ("*", "?"):
            return True
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            step = int(step_s)
            if part in ("*", ""):
                part = f"{lo}-{hi}"
        if "-" in part.lstrip("-"):
            a_s, _, b_s = part.partition("-")
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(part)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def _fires_at(cron: str, when: dt.datetime) -> bool:
    """Standard 5-field cron semantics, including the dom/dow OR rule."""
    minute, hour, dom, month, dow = cron.split()
    if not _field_matches(minute, when.minute, 0, 59):
        return False
    if not _field_matches(hour, when.hour, 0, 23):
        return False
    if not _field_matches(month, when.month, 1, 12):
        return False
    # cron dow: 0 and 7 are both Sunday; python weekday() is Mon=0..Sun=6.
    cron_dow = (when.weekday() + 1) % 7
    dom_restricted = dom.strip() not in ("*", "?")
    dow_restricted = dow.strip() not in ("*", "?")
    dom_ok = _field_matches(dom, when.day, 1, 31)
    dow_ok = _field_matches(dow, cron_dow, 0, 7) or (
        cron_dow == 0 and _field_matches(dow, 7, 0, 7)
    )
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True


def _min_period_days(crons: list[str], window_days: int = 70) -> float | None:
    """Smallest gap, in days, between consecutive firings of any of `crons`.

    Simulated minute-by-minute over a fixed window starting on a Monday, so the
    answer does not depend on when the suite runs. Returns None when fewer than
    two firings occur in the window — a cadence rarer than ~10 weeks, which this
    guard reports rather than silently treating as fast.
    """
    start = dt.datetime(2026, 1, 5, 0, 0)  # a Monday
    hits: list[dt.datetime] = []
    cursor = start
    end = start + dt.timedelta(days=window_days)
    while cursor < end:
        if any(_fires_at(c, cursor) for c in crons):
            hits.append(cursor)
        cursor += dt.timedelta(minutes=1)
    if len(hits) < 2:
        return None
    gaps = [
        (b - a).total_seconds() / 86400.0 for a, b in zip(hits, hits[1:], strict=False)
    ]
    return min(gaps)


def _scheduled_gated_workflows() -> dict[str, dict]:
    """filename -> {crons, gated_envs, jobs} for every scheduled + gated workflow."""
    out: dict[str, dict] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if not isinstance(wf, dict):
            continue
        # PyYAML resolves a bare `on:` key to boolean True (YAML 1.1).
        on = wf.get("on", wf.get(True))
        if not isinstance(on, dict) or "schedule" not in on:
            continue
        crons = [
            s["cron"]
            for s in (on.get("schedule") or [])
            if isinstance(s, dict) and s.get("cron")
        ]
        if not crons:
            continue
        gated: set[str] = set()
        jobs: list[str] = []
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            env = job.get("environment")
            names: set[str] = set()
            if isinstance(env, dict):
                if isinstance(env.get("name"), str):
                    names = {env["name"]}
            elif env is not None:
                names = {str(env)}
            hit = names & GATED_ENVS
            if hit:
                gated |= hit
                jobs.append(job_id)
        if gated:
            out[path.name] = {
                "crons": crons,
                "gated": sorted(gated),
                "jobs": sorted(jobs),
            }
    return out


def test_the_janitor_threshold_is_discoverable():
    """The reap threshold must parse to a positive int.

    Without this, a renamed input would make `_janitor_max_age_days()` raise or
    return something falsy and every comparison below would pass vacuously —
    the module would be measuring its own parse failure and reporting green.
    """
    max_age = _janitor_max_age_days()
    assert isinstance(max_age, int) and max_age > 0, (
        f"{JANITOR.name}: max_age_days parsed as {max_age!r}; a non-positive or "
        "non-integer threshold makes every period comparison in this module "
        "meaningless."
    )


def test_the_cron_parser_discriminates():
    """The period calculator must distinguish the cadences this guard turns on.

    A parser that returned a constant would make the real assertion pass or fail
    uniformly, so pin it against known answers before trusting it on 703.
    """
    cases = [
        ("17 7 * * *", 1.0, "daily"),
        ("0 8 * * 1", 7.0, "weekly Monday"),
        ("31 6 * * *", 1.0, "daily 06:31"),
        ("7 13 * * 1-5", 1.0, "weekdays — min gap is Mon→Tue, not Fri→Mon"),
        ("9,39 * * * *", 0.5 / 24, "twice hourly"),
    ]
    for cron, expected, label in cases:
        got = _min_period_days([cron])
        assert got is not None, f"{cron} ({label}): no firings found in the window"
        assert abs(got - expected) < 1e-6, (
            f"{cron} ({label}): min period computed as {got}, expected {expected}"
        )


def test_the_reap_dominated_set_is_exactly_the_occupants():
    """REAP_DOMINATED must equal the set that actually fails the comparison.

    Closed-set equality rather than an allowlist, so this fails in BOTH
    directions: a new scheduled gated workflow with a too-slow cron fails here
    instead of silently joining the race, and a listed one that gets fixed fails
    here instead of leaving a stale exemption behind. An opt-in allowlist would
    measure enrolment rather than correctness.
    """
    max_age = _janitor_max_age_days()
    actual = set()
    for name, info in _scheduled_gated_workflows().items():
        period = _min_period_days(info["crons"])
        if period is None or period >= max_age:
            actual.add(name)
    assert actual == REAP_DOMINATED, (
        "the set of scheduled, reviewer-gated workflows whose cron period is not "
        f"shorter than janitor 734's max_age_days={max_age} has changed.\n"
        f"  newly reap-dominated (add, with evidence, or fix): {sorted(actual - REAP_DOMINATED)}\n"
        f"  no longer reap-dominated (remove the stale entry): {sorted(REAP_DOMINATED - actual)}\n"
        "A gate on such a workflow is destroyed rather than answered late: see "
        "this module's docstring for 703's 7-of-11 measurement."
    )


def test_every_reap_dominated_entry_still_has_the_shape():
    """A listed file must still be scheduled AND gated.

    Separate from the equality check above because the failure it catches is
    different: an entry naming a workflow that no longer has a cron, no longer
    has a gate, or no longer exists is dead weight that reads as coverage.
    """
    occupants = _scheduled_gated_workflows()
    for name in sorted(REAP_DOMINATED):
        assert (WORKFLOWS / name).exists(), (
            f"REAP_DOMINATED names {name}, which does not exist in "
            f"{WORKFLOWS.relative_to(REPO_ROOT)} — remove the entry."
        )
        assert name in occupants, (
            f"REAP_DOMINATED names {name}, but it is no longer both scheduled "
            "and gated on a reviewer environment. Remove the entry; the shape "
            "this module guards no longer applies to it."
        )


def test_703_is_the_measured_instance():
    """Pin the specific numbers the docstring's measurement rests on.

    If 703's cron or 734's threshold moves, the evidence in the docstring stops
    describing the tree and this fails — the citation-drift problem, applied to
    two numbers in two different files rather than to a line number.
    """
    name = "703-sites-list-generate.yml"
    occupants = _scheduled_gated_workflows()
    assert name in occupants, (
        f"{name} is no longer scheduled + reviewer-gated; this module's "
        "docstring measurement no longer describes the tree. Re-measure before "
        "editing, and update REAP_DOMINATED."
    )
    info = occupants[name]
    assert info["crons"] == ["0 8 * * 1"], (
        f"{name}: cron changed to {info['crons']} (was ['0 8 * * 1'], a 7-day "
        "period). Re-derive the period-vs-threshold comparison and the "
        "docstring's run table."
    )
    assert "github-prod" in info["gated"], (
        f"{name}: expected the github-prod gate, got {info['gated']}."
    )
    period = _min_period_days(info["crons"])
    max_age = _janitor_max_age_days()
    assert period is not None and period >= max_age, (
        f"{name}: period {period}d is now SHORTER than max_age_days={max_age}, "
        "so it is no longer reap-dominated. Remove it from REAP_DOMINATED — the "
        "operator decision this module was filed for has been taken."
    )


def test_the_janitor_reaps_rather_than_reporting_on_the_scheduled_path():
    """The reap must actually happen on cron, or none of the above matters.

    734 takes a `dry_run` input defaulting to true for a manual dispatch, and
    resolves it to the literal `'false'` on any other event. If that ever
    flipped, an unanswered gate would survive and this whole module would be
    guarding a cost nobody pays — so assert the scheduled path is the
    destructive one.
    """
    text = JANITOR.read_text(encoding="utf-8-sig")
    normalized = re.sub(r"\s+", " ", text)
    assert "github.event_name == 'workflow_dispatch'" in normalized, (
        f"{JANITOR.name}: the dry_run resolution no longer keys on "
        "`github.event_name == 'workflow_dispatch'`; re-read how the scheduled "
        "path resolves dry_run before trusting this module's premise."
    )
    assert re.search(
        r"github\.event_name == 'workflow_dispatch' && "
        r"github\.event\.inputs\.dry_run \|\| 'false'",
        normalized,
    ), (
        f"{JANITOR.name}: expected the scheduled path to resolve dry_run to the "
        "literal 'false' (so cron really cancels). If that changed, an "
        "unanswered gate is no longer destroyed and REAP_DOMINATED's premise is "
        "void."
    )


# The roster is POSITION-SENSITIVE: it is a comprehension over `globals()`, so
# any test defined below this line is never collected. Keep it last.
TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
