"""Guard: scheduled workflows must not be structurally unable to finish (#834).

Two independent failure modes, both proven by the 2026-07 run history that
motivated #834 (7 of the 18 scheduled runs of the Reads-level GitHub workflows
502, 726 and 735 failed or were cancelled; #834's headline 8-of-21 also counts
703, which is Writes-level and stays gated):

1. **A Reads-level workflow pinned to a reviewer-gated environment.** A
   report-only audit that cannot finish without a human is an audit that mostly
   does not finish. Before #834 there was no ungated `github-prod-read`, so
   every Reads-level GitHub workflow was forced through gated `github-prod` —
   726 sat at the gate for up to three days at a stretch. Without this
   assertion the next Reads workflow gets wired to `github-prod` again and
   nothing notices.

2. **A scheduled workflow that is self-cancelling by construction** — it
   combines a `schedule:` trigger, a job on a `required_reviewers` environment,
   and `concurrency.cancel-in-progress: true`. The run parks at the gate and
   the next scheduled occurrence cancels its waiting predecessor; 726 had
   exactly this shape and produced the 07-21 and 07-22 `cancelled` outcomes
   (07-21's run was updated at the exact second the 07-22 run was created).

The two are NOT redundant. (1) stops the general case. (2) catches a *Writes*
level scheduled workflow that legitimately needs its gate but should not
silently cancel itself — for that shape the correct fix is
`cancel-in-progress: false` plus letting janitor 734 reap it, not ungating. So
the fix for #834 does not have to also be the fix for every future gated cron.

Source of truth for which environments gate: the "Environment approval gates"
layer in docs/workflow-safety-and-approvals.md, audited by workflow 730 — the
same list `scripts/check-workflow-doc-consistency.py` carries. Update both
after any Settings -> Environments change.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SAFETY_DOC = REPO_ROOT / "docs" / "workflow-safety-and-approvals.md"

# Environments configured with required reviewers. Mirrors GATED_ENVS in
# scripts/check-workflow-doc-consistency.py; test_gated_envs_match_doc_checker
# below fails if the two lists drift apart.
GATED_ENVS = {
    "cloudflare-prod",
    "cloudflare-prod-write",
    "github-prod",
    "m365-prod",
    "whmcs-prod",
    "wpmudev-prod",
}

# Safety-doc levels that mean "this run changes nothing operators must approve".
# Reporting plumbing (opening an issue or a data PR with the findings) is not a
# Write in this taxonomy — see AGENTS.md on what the [TAG] counts.
#
# Matched by PREFIX, not equality. The table qualifies the level in parentheses
# — `Reads (+ PR delivery)` (401), `Reads (builds a file)` (213) — and an exact
# `== "Reads"` check silently skipped both, which is how 401 sat on gated
# `github-prod` unnoticed by this guard. A qualifier describes how the run
# reports its findings; it never makes the run a Write. `Writes (…)` levels can
# never collide with this prefix, and test_every_safety_level_is_classifiable
# fails if a future level matches neither prefix.
READS_PREFIX = "reads"
WRITES_PREFIX = "writes"


def _is_reads_level(level: str) -> bool:
    return level.strip().lower().startswith(READS_PREFIX)

# Known Reads-level jobs still pinned to a gated environment. Every one is
# `workflow_dispatch`-only, which is why they are tolerable rather than broken:
# the operator who dispatched the run is present to approve it seconds later.
# That is the whole difference from #834 — a *scheduled* gated read fires at
# 06:15 UTC with nobody watching, waits, and gets cancelled by its own
# successor. The scheduled case therefore has NO allowlist (see
# test_scheduled_reads_workflows_are_never_gated); this one exists so the
# remaining debt stays enumerated instead of merely counted.
#
# The list is exact, not a floor: adding a new gated Reads job fails the test,
# and so does ungating one without deleting its line here. Fix by giving the
# category an ungated read lane (`whmcs-prod-read`, `github-prod-read`) — see
# docs/workflow-safety-and-approvals.md, which tracks 301-303 / 601 as known.
DISPATCH_ONLY_GATED_READS = {
    ("101-domain-status.yml", "m365"),
    # Surfaced only once the level match became prefix-based: 401 is
    # `Reads (+ PR delivery)`, and its deliver job opens the campaigns PR with a
    # PAT fetched from Key Vault on the writer identity. A candidate to follow
    # 502 onto github-prod-read later — it needs the same reporting scope — but
    # that is a separate change, and it is dispatch-only, so it is not #834's
    # failure class.
    ("401-zeffy-campaigns-export.yml", "deliver"),
    ("104-domain-export-inventory.yml", "export_m365"),
    ("104-domain-export-inventory.yml", "export_wpmudev"),
    ("114-cloudflare-registrar-access-check.yml", "validate"),
    ("221-whmcs-application-search.yml", "application_search"),
    ("301-m365-domain-preflight.yml", "graph"),
    ("302-m365-list-domains.yml", "list-domains"),
    ("303-m365-domain-and-dkim.yml", "m365"),
    ("306-discover-uncaptured-comms.yml", "discover"),
    ("601-wpmudev-export-sites.yml", "export"),
}


def _workflow_number(text: str) -> int | None:
    m = re.search(r"^name:\s*['\"]?(\d{3})\.", text, re.M)
    return int(m.group(1)) if m else None


def _job_environment_names(job: dict) -> set[str]:
    env = job.get("environment")
    if env is None:
        return set()
    if isinstance(env, dict):
        name = env.get("name")
        return {name} if isinstance(name, str) else set()
    return {str(env)}


def _safety_levels() -> dict[int, str]:
    """number -> safety level string, parsed from the operator safety table."""
    rows: dict[int, str] = {}
    if not SAFETY_DOC.exists():
        return rows
    for ln in SAFETY_DOC.read_text(encoding="utf-8").split("\n"):
        m = re.match(
            r"^\|\s*(\d{3})(?:\s*[–-]\s*(\d{3}))?\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]*)\|?\s*$",
            ln,
        )
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        for n in range(lo, hi + 1):
            rows[n] = m.group(4).strip()
    return rows


def _workflows():
    """(path, text, parsed-yaml) for every workflow file, parsed once."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8-sig")
        out.append((path, text, yaml.safe_load(text)))
    return out


def _triggers(wf: dict) -> set[str]:
    # PyYAML resolves a bare `on:` key to the boolean True (YAML 1.1).
    on = wf.get("on", wf.get(True))
    if isinstance(on, dict):
        return set(on)
    if isinstance(on, list):
        return set(on)
    return {str(on)} if on else set()


def _gated_reads_jobs():
    """(path, job_id, gated-envs, is_scheduled) for every Reads job on a gate."""
    levels = _safety_levels()
    assert levels, f"parsed no rows from {SAFETY_DOC} — the table format changed"

    found = []
    for path, text, wf in _workflows():
        number = _workflow_number(text)
        if number is None:
            continue  # repo-internal / CI workflow, no operator safety row
        if not _is_reads_level(levels.get(number, "")):
            continue
        scheduled = "schedule" in _triggers(wf)
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            gated = _job_environment_names(job) & GATED_ENVS
            if gated:
                found.append((path.name, job_id, sorted(gated), scheduled))
    return found


def test_scheduled_reads_workflows_are_never_gated():
    """The #834 recurrence guard. No allowlist: this shape cannot finish.

    A scheduled Reads-level run on a reviewer-gated environment fires with
    nobody watching, parks at `status: waiting`, and is eventually cancelled or
    reaped. 7 of the 18 such runs failed or were cancelled in 2026-07.
    """
    violations = [
        f"{name}: job '{job_id}' is Reads-level, runs on a SCHEDULE, and is "
        f"gated on {gated} — it will park at `status: waiting` with no operator "
        "present and be cancelled by its own successor or reaped by janitor 734. "
        "Move it to an ungated read lane (e.g. github-prod-read), or reclassify "
        "the row in docs/workflow-safety-and-approvals.md if it really writes."
        for name, job_id, gated, scheduled in _gated_reads_jobs()
        if scheduled
    ]
    assert not violations, "\n".join(violations)


def test_dispatch_only_gated_reads_stay_enumerated():
    """Dispatch-only gated reads are tolerable debt, but must stay listed.

    Tolerable because the operator who dispatched the run is present to approve
    it. Enumerated because "a count is not evidence; the list is" — an
    unlisted one is new debt nobody decided to take on.
    """
    actual = {(name, job_id) for name, job_id, _, scheduled in _gated_reads_jobs() if not scheduled}

    added = sorted(actual - DISPATCH_ONLY_GATED_READS)
    removed = sorted(DISPATCH_ONLY_GATED_READS - actual)

    problems = []
    if added:
        problems.append(
            "NEW Reads-level jobs on a gated environment (add an ungated read "
            f"lane for their category, or record the decision here): {added}"
        )
    if removed:
        problems.append(
            "These are no longer gated Reads jobs — delete them from "
            f"DISPATCH_ONLY_GATED_READS so the list keeps meaning something: {removed}"
        )
    assert not problems, "\n".join(problems)


def test_scheduled_gated_workflows_do_not_cancel_themselves():
    """schedule + gated env + cancel-in-progress:true is self-cancelling."""
    violations = []
    for path, text, wf in _workflows():
        if "schedule" not in _triggers(wf):
            continue
        concurrency = wf.get("concurrency")
        if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not True:
            continue
        gated = set()
        for job in (wf.get("jobs") or {}).values():
            if isinstance(job, dict):
                gated |= _job_environment_names(job) & GATED_ENVS
        if gated:
            violations.append(
                f"{path.name}: scheduled workflow on gated environment "
                f"{sorted(gated)} with concurrency.cancel-in-progress: true — "
                "each scheduled run cancels its waiting predecessor, so the run "
                "can never be approved in time. Set cancel-in-progress: false "
                "and let janitor 734 reap stale waiting runs."
            )
    assert not violations, "\n".join(violations)


def test_every_safety_level_is_classifiable():
    """No safety level may fit neither the Reads nor the Writes prefix.

    The guard above decides what to police by prefix-matching the level cell.
    A level that matches neither prefix — `Read-only`, `Audit`, `Mixed` — would
    be silently skipped by every assertion in this module, which is exactly the
    failure that let `Reads (+ PR delivery)` (401) escape the original exact
    match. Fail loudly at review time instead: either name the new level to fit
    the taxonomy, or teach this module about it deliberately.
    """
    unclassifiable = sorted(
        {
            level
            for level in _safety_levels().values()
            if not level.strip().lower().startswith((READS_PREFIX, WRITES_PREFIX))
        }
    )
    assert not unclassifiable, (
        "safety-doc levels matching neither 'Reads…' nor 'Writes…': "
        f"{unclassifiable} — these are invisible to every check in this module. "
        "Rename them to fit the taxonomy, or extend the prefixes on purpose."
    )


def test_reads_prefix_covers_the_qualified_variants():
    """Pin the specific regression: qualified Reads levels must be policed."""
    for level in ("Reads", "Reads (+ PR delivery)", "Reads (builds a file)"):
        assert _is_reads_level(level), f"{level!r} must count as Reads-level"
    for level in ("Writes (gated)", "Writes (data PR only)", "Writes (dry-run default)"):
        assert not _is_reads_level(level), f"{level!r} must not count as Reads-level"

    # And confirm the qualified variants are actually present in the doc — if
    # they ever disappear this test is guarding nothing and should be revisited.
    present = set(_safety_levels().values())
    assert {"Reads (+ PR delivery)", "Reads (builds a file)"} <= present, (
        "the qualified Reads levels this test pins are gone from the safety "
        f"doc; present levels: {sorted(present)}"
    )


def test_gated_envs_match_doc_checker():
    """This module and the doc-consistency script must agree on what gates."""
    checker = (REPO_ROOT / "scripts" / "check-workflow-doc-consistency.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r"GATED_ENVS\s*=\s*\{(.*?)\}", checker, re.S)
    assert m, "GATED_ENVS not found in scripts/check-workflow-doc-consistency.py"
    theirs = set(re.findall(r"['\"]([a-z0-9-]+)['\"]", m.group(1)))
    assert theirs == GATED_ENVS, (
        "gated-environment lists drifted:\n"
        f"  check-workflow-doc-consistency.py: {sorted(theirs)}\n"
        f"  {pathlib.Path(__file__).name}: {sorted(GATED_ENVS)}"
    )


def test_github_prod_read_is_not_treated_as_gated():
    """The whole point of the #834 read lane: it must stay ungated."""
    assert "github-prod-read" not in GATED_ENVS
    # Substring traps: 'github-prod-read' must never be matched by a naive
    # `in`-check against the gated 'github-prod'.
    for env in GATED_ENVS:
        assert env != "github-prod-read"


def test_reads_lane_workflows_actually_moved():
    """The three workflows #834 names are on the ungated read lane."""
    expected = {
        "502-google-analytics-report.yml": "deliver",
        "726-repo-rulesets-drift-audit.yml": "audit",
        "735-repo-dependabot-affected-repos.yml": "update",
    }
    for filename, job_id in expected.items():
        wf = yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8-sig"))
        job = (wf.get("jobs") or {}).get(job_id)
        assert isinstance(job, dict), f"{filename}: job '{job_id}' missing"
        assert _job_environment_names(job) == {"github-prod-read"}, (
            f"{filename}: job '{job_id}' should run on github-prod-read, "
            f"got {sorted(_job_environment_names(job))}"
        )


def test_703_stays_gated():
    """703 is Writes (data PR only) — explicitly out of #834's scope."""
    wf = yaml.safe_load(
        (WORKFLOWS / "703-sites-list-generate.yml").read_text(encoding="utf-8-sig")
    )
    envs = set()
    for job in (wf.get("jobs") or {}).values():
        if isinstance(job, dict):
            envs |= _job_environment_names(job)
    assert "github-prod" in envs, (
        "703 is classified Writes (data PR only) and must keep its gate — it "
        "appeared in #834's evidence table to show gate latency, not because "
        "it should be ungated."
    )


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
