"""A fleet-wide `update-branch` sweep destroys the Copilot reviews it depends on.

Measured on 2026-10-12, Conductor run 257. `audit-open-pr-readiness.py` named 19
stale-green PRs and printed `Fix: PUT /pulls/<n>/update-branch` beside each, with
nothing saying how many may be done at once. A Conductor ran all 19 two seconds
apart; 14 heads moved; **17 Copilot error bodies** landed between 07:27:12Z and
07:43:43Z, reading:

    Copilot encountered an error and was unable to review this pull request.

The control group arrived for free. #1584 and #1588 were the only two PRs still
holding a valid Copilot review (both from 2026-10-10), and they are exactly the
two whose `update-branch` returned a CONFLICT -- so their heads never moved.
Heads that moved: error. Heads that did not: intact.

Why it is expensive rather than cosmetic: AGENTS.md's merge condition requires a
Copilot review on the **current head** whose body opens with
`<!-- ccr-overview-v2 -->`, and states that Copilot's error bodies are not
reviews. The sweep therefore took 14 PRs that were one `update-branch` from
landing and made all 14 unenqueueable.

And it is unrepairable in place -- both routes were tried and both are dead:

- `POST pulls/<n>/requested_reviewers` with `copilot-pull-request-reviewer`
  -> **422**, "Reviews may only be requested from collaborators"; read-back
  confirms `requested_reviewers: []`.
- `POST check-runs/<id>/rerequest` on the `copilot-pull-request-reviewer`
  check-run (which exists, `app=github-actions`, `completed/failure`) -> **404**.

Only another push re-triggers a review, and that re-runs the branch's whole CI
(Validate Repository ~17 min). One cheap mechanical sweep becomes N expensive
ones.

So the remedy is a cap the tool applies, not a sentence an operator may skip.
The asymmetry settles the size: too small costs one extra run, too large costs a
full CI cycle on every PR touched.

Pure Python against the script's own helper -- no `git`, no `gh`, no network, so
it is reachable on the Conductor's Windows host where the bash-invoking modules
in this suite are red for platform reasons (#1119).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "audit-open-pr-readiness.py"
)
_spec = importlib.util.spec_from_file_location("_pr_readiness", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

update_branch_batch = _mod.update_branch_batch
CAP = _mod.UPDATE_BRANCH_BATCH_CAP

# The run-257 sweep, in the order the audit printed them.
RUN_257_STALE = [
    1584, 1586, 1587, 1588, 1589, 1590, 1592, 1596, 1599, 1600,
    1604, 1607, 1610, 1611, 1612, 1613, 1615, 1619, 1634,
]


def test_the_run_257_sweep_would_now_be_capped_instead_of_swept():
    batch, deferred = update_branch_batch(RUN_257_STALE)
    assert len(batch) == CAP, (
        f"the 19-PR sweep that cost 14 Copilot reviews must be capped at {CAP}, "
        f"got {len(batch)}"
    )
    assert len(batch) + len(deferred) == len(RUN_257_STALE), (
        "every stale PR must be either in the batch or explicitly deferred -- "
        "silently dropping one makes it look reviewed"
    )


def test_the_cap_is_well_below_the_smallest_burst_OBSERVED_to_fail():
    """14 simultaneous head moves produced 17 error bodies. A cap at or above
    that is not a cap. The margin is deliberate: the true threshold is unknown,
    only that 14 fails, so the value has to sit far below it rather than just
    under it."""
    assert CAP < 14, f"cap {CAP} is not below the measured failing burst of 14"
    assert CAP <= 5, (
        f"cap {CAP} is too close to the failing burst to be safe; the threshold "
        f"is unmeasured and only its failure at 14 is known"
    )
    assert CAP >= 1, "a cap of zero would stall every stale PR forever"


def test_the_batch_preserves_the_callers_priority_order():
    """The audit orders by PR number, and a Conductor may order by what unblocks
    the most work. Re-ordering here would silently override that -- run 257's
    keystone was #1589, whose Prettier exemption removes the cause of four other
    PRs' ledger conflicts."""
    batch, deferred = update_branch_batch([1589, 1596, 1615, 1584, 1588])
    assert batch == [1589, 1596, 1615], f"order not preserved: {batch}"
    assert deferred == [1584, 1588], f"deferred order not preserved: {deferred}"


def test_a_short_list_is_returned_whole_with_nothing_deferred():
    """The polarity check: capping must not refuse the ordinary small case."""
    batch, deferred = update_branch_batch([1589, 1596])
    assert batch == [1589, 1596]
    assert deferred == []


def test_an_empty_list_yields_an_empty_batch_rather_than_raising():
    batch, deferred = update_branch_batch([])
    assert batch == [] and deferred == []


def test_a_non_positive_cap_RAISES_rather_than_updating_nothing():
    """An empty batch from a zero cap would print "0 now, 19 deferred" and read
    as "nothing needs updating" -- the false-clean this script exists to prevent.
    Fail closed instead."""
    for bad in (0, -1):
        try:
            update_branch_batch(RUN_257_STALE, cap=bad)
        except ValueError as e:
            assert "positive" in str(e), f"the raise must name the constraint: {e}"
        else:
            raise AssertionError(
                f"cap={bad} returned a batch instead of raising; an empty batch "
                f"reads as 'nothing is stale'"
            )


def test_an_iterator_is_accepted_without_being_consumed_into_nothing():
    """`result["stale_green"]` is a list today, but a generator comprehension is
    a natural refactor and would make `ordered[:cap]` fail or silently empty."""
    batch, deferred = update_branch_batch(iter([1589, 1596, 1615, 1584]))
    assert batch == [1589, 1596, 1615]
    assert deferred == [1584]


def test_the_report_hands_over_a_capped_PLAN_not_19_bare_remedies():
    """The defect was not a missing fact, it was a report that invited the sweep:
    19 independent `Fix: PUT ...` lines and no statement of how many at once. The
    rendered output must carry the plan, the cap, and the one-at-a-time
    instruction."""
    import inspect

    src = inspect.getsource(_mod.render)
    assert "update_branch_batch(" in src, (
        "render() no longer computes a capped batch, so the report is back to "
        "listing every stale PR as independently actionable"
    )
    assert "ONE AT A TIME" in src, (
        "the report does not tell the operator to stagger the updates"
    )
    # "one at a time" alone does not say what to wait FOR. Without the
    # confirmation clause an operator staggers by a second, which is exactly
    # what run 257 did -- the sweep's calls were two seconds apart.
    assert "before moving the next" in src, (
        "the report says to go one at a time but not what to wait for; a "
        "two-second gap is 'one at a time' and is what caused the burst"
    )
    for token in ("422", "404"):
        assert token in src, (
            f"the report omits {token}; without both dead re-trigger routes a "
            f"reader will try to repair an error body in place"
        )


def test_the_cap_is_named_ONCE_so_the_report_and_the_helper_cannot_disagree():
    """A second literal is how a cap and the sentence describing it drift apart."""
    import inspect

    src = inspect.getsource(_mod)
    assert src.count("UPDATE_BRANCH_BATCH_CAP = ") == 1, (
        "the cap is defined more than once"
    )
    # Asserted at SOURCE level, not by comparing the resolved default. An
    # earlier version of this test used
    #     inspect.signature(...).parameters["cap"].default is CAP
    # which passes for a hardcoded `cap=3` because CPython interns small ints,
    # so `3 is 3` is True -- the test could not fail for the defect it names,
    # and the mutation run proved it by staying green.
    src_sig = [
        ln for ln in inspect.getsource(_mod.update_branch_batch).splitlines()
        if ln.startswith("def update_branch_batch(")
    ]
    assert len(src_sig) == 1, f"could not read the signature: {src_sig}"
    assert "cap=UPDATE_BRANCH_BATCH_CAP" in src_sig[0], (
        f"the default cap is a separate literal from UPDATE_BRANCH_BATCH_CAP, so "
        f"the two can drift apart: {src_sig[0]}"
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
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
