"""Guards for 703's checkout freshness.

703 is a weekly cron whose only job sits on the gated `github-prod`
environment. A cron cannot approve its own gate, so the run waits — and a
run's default checkout is pinned to the SHA it was CREATED at, not the SHA it
eventually runs at. `peter-evans/create-pull-request` branches from whatever is
checked out, so the data PR inherits that staleness.

Measured 2026-09-07: run 11 was created 2026-08-31 and approved seven days
later. It checked out `2ceb54d`, and the PR it opened (#1249) was **133 commits
behind `main`** against Phantom Revert Guard's threshold of 5. The generation
itself was green; the PR simply could not merge without a hand-run
update-branch. That is not a one-off — it recurs every week the gate is
answered late, which is the normal case for a gate a human answers when they
get to it.

The fix is `ref: main` on the checkout, which resolves at checkout time.

These guards are deliberately about the WORKFLOW FILE rather than about a
script's behaviour: the defect lives in the absence of one YAML key, and the
regression is someone dropping it. A test that ran the generator would pass
either way.

Run: python3 tests/workflow-logic/test_703_sites_list_generate.py
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_run

WF_NAME = "703-sites-list-generate.yml"


def _workflow() -> dict:
    # Shared helper rather than a local read (raised in review on #1254): it
    # centralises the utf-8 rationale, which is not cosmetic — workflow files
    # here carry ✓/❌/em-dashes, and a cp1252 decode on Windows crashes the
    # module before any test runs, printing a traceback instead of FAIL lines.
    return load_workflow(WF_NAME)


def _generate_steps() -> list[dict]:
    wf = _workflow()
    assert "generate" in wf["jobs"], f"703 lost its `generate` job: {sorted(wf['jobs'])}"
    return wf["jobs"]["generate"]["steps"]


def _checkout_steps() -> list[dict]:
    return [s for s in _generate_steps() if "actions/checkout" in str(s.get("uses", ""))]


def test_the_generate_job_checks_out_something():
    """Anchor: if the checkout step is gone, every other guard here is vacuous."""
    steps = _checkout_steps()
    assert len(steps) == 1, f"expected exactly one checkout step in `generate`, found {len(steps)}"


def test_checkout_pins_ref_to_main_not_the_runs_creation_sha():
    """The fix itself. Without `ref:`, the checkout is the run's creation SHA."""
    step = _checkout_steps()[0]
    ref = (step.get("with") or {}).get("ref")
    assert ref is not None, (
        "703's checkout has no `ref:`, so it resolves to the SHA the run was CREATED at. "
        "This job waits on the `github-prod` gate, so that SHA ages by however long the "
        "approval takes, and the data PR is opened that far behind `main` (run 11: 133 "
        "commits behind, Phantom Revert Guard threshold 5). Set `ref: main`."
    )
    assert ref == "main", f"expected `ref: main` on 703's checkout, found {ref!r}"


def test_the_gate_that_makes_this_necessary_is_still_there():
    """
    If 703 ever moves off a gated environment the delay disappears and this
    guard's premise weakens. That is a good outcome, not a silent one: the
    reader should be told the reason changed rather than finding a rule whose
    justification quietly stopped applying.
    """
    job = _workflow()["jobs"]["generate"]
    assert job.get("environment") == "github-prod", (
        "703's `generate` job is no longer on `github-prod`. If it is now ungated, the "
        "approval delay this module exists for is gone — re-read the docstring and decide "
        "whether `ref: main` is still wanted (it probably is, for generator freshness) "
        f"rather than leaving a stale rationale. Found: {job.get('environment')!r}"
    )


def test_the_pr_step_still_branches_from_the_checkout():
    """
    `ref: main` only fixes the PR's base if the PR is still cut from the
    checked-out commit. An explicit `base:` on create-pull-request would make
    the checkout ref irrelevant to the PR and silently re-open the hazard from
    the other side.
    """
    pr_steps = [
        s for s in _generate_steps() if "create-pull-request" in str(s.get("uses", ""))
    ]
    assert len(pr_steps) == 1, f"expected one create-pull-request step, found {len(pr_steps)}"
    with_ = pr_steps[0].get("with") or {}
    base = with_.get("base")
    assert base in (None, "main"), (
        "create-pull-request carries an explicit `base` that is not `main`, so the "
        f"checkout ref no longer determines what the data PR branches from: {base!r}"
    )


# --------------------------------------------------------------------------
# The 601 decoupling.
#
# 703 used to dispatch 601 from inside its own gated job and block on
# `gh run watch`. 601 sits on `wpmudev-prod`, which has a required reviewer, so
# the second approval prompt only APPEARED ~10 minutes after this job's own was
# granted -- and if nobody answered it, the step waited until it timed out,
# wasting the approval already spent on this run. Measured 2026-09-07: the
# operator approved 703, and 601 surfaced as a separate prompt ten minutes later.
#
# 703 now reuses 601's newest successful export and dispatches the next one
# from its LAST step, fire-and-forget. That asks for 601's approval at the one
# moment an operator is provably present -- they answered this job's own gate
# minutes earlier -- while nothing in the job depends on the answer.
#
# The reviewer on `wpmudev-prod` is deliberately KEPT: this is about when the
# approval is asked for, not about removing it.
#
# A cron on 601 would put both prompts up simultaneously and is what the first
# draft of this change did. `test_gated_env_hygiene.py` rejects it -- a
# Reads-level job on a schedule AND a reviewer gate parks at `waiting` with
# nobody watching -- and that guard deliberately has no allowlist for the
# scheduled case, so the dispatch-last arrangement is what shipped.
# --------------------------------------------------------------------------

EXPORT_STEP = "Collect export artifacts"
DISPATCH_STEP = "Dispatch the gated WPMUDEV export"
WPMUDEV_WF = "601-wpmudev-export-sites.yml"


def _export_step_body() -> str:
    return step_run(WF_NAME, "generate", EXPORT_STEP)


def _step_index(name_substring: str) -> int:
    for i, s in enumerate(_generate_steps()):
        if name_substring in str(s.get("name", "")):
            return i
    raise AssertionError(f"703 has no step whose name contains {name_substring!r}")


def test_703_does_not_dispatch_the_gated_export_from_the_collection_step():
    """
    Re-adding 601 to the dispatch loop recreates the second, late approval
    prompt and the timeout that wasted the first one.

    Note the scope: this is about the ARTIFACT-COLLECTION step, which waits on
    what it dispatches. The job dispatches 601 from its last step by design --
    see test_703_primes_the_next_export_from_its_last_step.
    """
    body = _export_step_body()
    dispatch_section = body.split("download_latest")[0]
    assert WPMUDEV_WF not in dispatch_section, (
        f"703 dispatches {WPMUDEV_WF} from its artifact-collection step again. That "
        "workflow is gated on `wpmudev-prod`, so dispatching it from inside this "
        "already-gated job asks for a second approval ten minutes after the first, and "
        "then blocks on it. Reuse its newest successful run here and prime the next one "
        "from the job's LAST step, where nothing waits on the answer. Do NOT reach for a "
        "cron on 601 instead -- `test_gated_env_hygiene.py` refuses a scheduled, "
        "reviewer-gated Reads job outright."
    )


def test_703_still_consumes_the_gated_export():
    """
    Dropping the dispatch without the reuse would silently stop feeding WPMUDEV
    membership into the sites list -- green, and quietly wrong.
    """
    body = _export_step_body()
    # Anchored on the call itself, not on the two strings appearing anywhere in
    # the step: an `or` between a precise check and a loose one is only ever as
    # strong as the loose one.
    assert re.search(rf"download_latest_success\s+{re.escape(WPMUDEV_WF)}\s", body), (
        f"703 no longer reuses {WPMUDEV_WF}'s artifact. Dropping the dispatch without the "
        "reuse silently stops feeding WPMUDEV membership into the sites list."
    )


def test_the_reused_export_reports_its_age():
    """
    A reused artifact is by definition old. Using one silently is how the sites
    list would carry stale membership flags with nothing saying so.
    """
    body = _export_step_body()
    # Assert the COMPARISON, not that the words appear. The first version of this
    # guard checked only that `STALE_EXPORT_DAYS` and `::warning::` were present
    # somewhere in the step, and a mutation replacing the condition with
    # `if false; then` passed it — the names survive while the check does not.
    cond = re.search(
        r'if\s+\[\s+"\$age_days"\s+-ge\s+"\$STALE_EXPORT_DAYS"\s+\]', body
    )
    assert cond, (
        "the reuse path no longer compares the artifact's age against "
        "STALE_EXPORT_DAYS, so a months-old export would be used silently"
    )
    # ...and that the branch it guards is the one that warns.
    after = body[cond.end() :].split("fi", 1)[0]
    assert "::warning::" in after, (
        "the staleness branch no longer warns; a stale reused export would pass quietly"
    )
    step = find_step(_workflow(), "generate", EXPORT_STEP)
    threshold = (step.get("env") or {}).get("STALE_EXPORT_DAYS")
    assert threshold is not None and int(threshold) > 0, (
        "STALE_EXPORT_DAYS is not set to a positive value in the step's env, so the "
        f"comparison above reads an empty string and never fires. Found: {threshold!r}"
    )


def test_the_ungated_read_lanes_are_still_dispatched():
    """
    201 and 108 are ungated (`whmcs-prod-read` / `cloudflare-prod-read`), so
    dispatch-and-wait costs nothing. Moving them to reuse would make the sites
    list a week stale for no benefit.
    """
    dispatch_section = _export_step_body().split("download_latest")[0]
    assert "gh workflow run" in dispatch_section, "703 dispatches nothing at all any more"
    for wf in ("201-whmcs-export-domains.yml", "108-export-summary.yml"):
        assert wf in dispatch_section, (
            f"703 no longer dispatches {wf}. It is an ungated read lane, so reusing an old "
            "run would make the sites list a week stale for no benefit."
        )


def test_every_run_lookup_is_confined_to_the_default_branch():
    """
    Raised in review on #1255, twice -- first against the reuse lane, then
    against the dispatch lane, which had the same defect and was pre-existing.
    A data-integrity finding rather than a nit: `gh run list` spans every
    branch, so a `workflow_dispatch` of an export workflow from anyone's
    feature branch can become the run 703 consumes, and its data is published
    in the sites list through a run that looks entirely normal.

    Asserted over ALL lookups rather than each by name, because the first fix
    corrected one of the two and left its sibling six lines away untouched. A
    guard written per-call-site would have passed on exactly that state.
    """
    body = _export_step_body()
    # Whitespace-tolerant terminator (raised in review on #1258): pinning the
    # exact `)\n` made a trailing space enough to drop a lookup from the list
    # and fail the count assertion below. The guard is about `--branch main`,
    # not about spacing.
    lookups = re.findall(r"run_id=\$\(gh run list.*?\)[^\S\n]*\n", body, re.S)
    assert len(lookups) == 2, (
        f"expected 703 to look runs up in two places (dispatch lane, reuse lane); "
        f"found {len(lookups)}. If a lane was added or removed, extend this guard "
        "rather than deleting it."
    )
    unconfined = [lk for lk in lookups if "--branch main" not in lk]
    assert not unconfined, (
        "a `gh run list` lookup in 703 is not confined to the default branch, so a "
        "dispatch of that export on ANY branch can become the run the published sites "
        f"list is built from. Unconfined: {unconfined}"
    )


def test_the_ungated_lanes_are_dispatched_onto_the_default_branch():
    """
    The other half of the lookup guard. `gh workflow run` without `--ref`
    happens to default to the default branch, so the pair worked by luck;
    stating it makes the lookup's `--branch main` verifiable rather than
    coincidental.

    Raised in review on #1258: the bare matcher also hit COMMENT lines, so
    prose mentioning `gh workflow run` would fail this guard spuriously. The
    remedy is to drop comments, NOT to anchor the match at start-of-line as
    suggested -- that would stop matching a dispatch written mid-line
    (`... && gh workflow run "$wf"`), which is a silent PASS on an unpinned
    dispatch. A false fail is cheap; a guard that stops looking is not.
    """
    dispatch_section = _export_step_body().split("download_latest")[0]
    code = "\n".join(
        ln for ln in dispatch_section.split("\n") if not ln.lstrip().startswith("#")
    )
    run_cmds = re.findall(r"gh workflow run.*", code)
    assert run_cmds, "703 dispatches nothing at all any more"
    missing = [c for c in run_cmds if "--ref main" not in c]
    assert not missing, (
        "a dispatch in 703's collection step does not pin `--ref main`, so the run it "
        "creates may not be the one the `--branch main` lookup below can see. "
        f"Found: {missing}"
    )


def test_703_primes_the_next_export_from_its_last_step():
    """
    Reuse without a dispatch is a slow leak: the artifact ages a week per run
    until STALE_EXPORT_DAYS fires, and nothing ever asks for the approval that
    would refresh it.
    """
    steps = _generate_steps()
    idx = _step_index(DISPATCH_STEP)
    assert idx == len(steps) - 1, (
        "the WPMUDEV dispatch is no longer 703's last step. Its whole point is that "
        "nothing in this job depends on the second approval; a step after it can only "
        f"reintroduce that dependency. It is step {idx + 1} of {len(steps)}."
    )
    body = str(steps[idx].get("run", ""))
    assert re.search(rf"gh workflow run\s+{re.escape(WPMUDEV_WF)}\b", body), (
        f"703's last step no longer dispatches {WPMUDEV_WF}, so nothing ever asks for the "
        "approval that refreshes the export the step above reuses."
    )


def test_the_priming_dispatch_does_not_wait_for_the_gate():
    """
    The defect being fixed was `gh run watch` on a gated run, not the dispatch
    itself. Re-adding a wait here recreates it exactly, one step further down.
    """
    body = str(_generate_steps()[_step_index(DISPATCH_STEP)].get("run", ""))
    assert "gh run watch" not in body, (
        "the priming dispatch waits on the run it just created. That is the original "
        "defect: 601 is gated on `wpmudev-prod`, so the wait blocks on a human who has "
        "already walked away until the step times out."
    )
    assert "--ref main" in body, (
        "the priming dispatch does not target the default branch, so the run it creates "
        "will not be found by the `--branch main` reuse lookup above."
    )


def test_601_is_not_itself_put_on_a_schedule():
    """
    The premise of the whole arrangement, and the reason it is shaped this way
    rather than as two crons. `test_gated_env_hygiene.py` owns this rule; the
    assertion is duplicated here so that a reader who reaches for the obvious
    fix ("just give 601 a cron") is told why it was not taken, at the file
    where they are making the change.
    """
    wf = load_workflow(WPMUDEV_WF)
    on = wf.get("on") or wf.get(True) or {}
    assert "schedule" not in on, (
        f"{WPMUDEV_WF} has gained a `schedule:` trigger. It is a Reads-level job on the "
        "reviewer-gated `wpmudev-prod` environment, so a cron parks it at `waiting` with "
        "nobody watching, to be cancelled by its successor or reaped by janitor 734 -- "
        "which is what `test_gated_env_hygiene.py` refuses, with no allowlist for the "
        "scheduled case. 703 dispatches it from its last step instead."
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
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
