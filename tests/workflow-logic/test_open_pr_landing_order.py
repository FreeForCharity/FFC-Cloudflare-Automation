"""`mergeable_state: clean` on every open PR is not evidence the queue can drain.

The defect, measured on the live queue in Conductor run 243 (2026-10-10), base
`main` = `a35e5a15`. Seven open `agentic-os` PRs, every one `clean` against
`main`, every required check green, every review thread resolved. Three cloud
worker runs that day read exactly that and reported "nothing to fix". Merging
them in landing order conflicted twice, both times on `docs/lessons-ledger.md`.

Pinned heads, because a landability claim is a claim about `(base, headA, headB)`
and a push to any of the three voids it -- CLAUDE.md's "a pairwise landability
matrix is invalidated by a push to EITHER side":

    base a35e5a15 | 1584 84d4ba8e | 1586 bbc3fad5 | 1587 f79772d7
                  | 1588 f8404f42 | 1589 67b697fb

    1584 x 1586 CONFLICT     1586 x 1587 clean
    1584 x 1587 clean        1586 x 1588 CONFLICT
    1584 x 1588 CONFLICT     1587 x 1588 clean

{1584, 1586, 1588} is a conflict TRIANGLE, so at most one of the three lands
un-rebased -- a fact no single pair exhibits, which is why pairwise-clean is not
enough. Cause, from `git diff --numstat` on the ledger against each PR's own
merge base: #1584 **+149/-145** and #1588 **+149/-144** each rewrite ~145 rows
they did not author (Prettier's table re-pad), while #1586 appends +6/-0 into the
region they re-pad. #1589 removes the cause by exempting the ledger from the
re-pad, and collides with nothing.

Everything here is pure Python against fixtures. No `git`, no `gh`, no network --
deliberately, so the decision logic is reachable on the Conductor's Windows host
where every bash-invoking module in this suite is red for platform reasons
(#1119) and a `TemporaryDirectory` teardown can turn an all-pass module into a
non-zero exit.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-open-pr-landing-order.py"
)
_spec = importlib.util.spec_from_file_location("_landing_order", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

landing_plan = _mod.landing_plan
parse_open_prs = _mod.parse_open_prs

# The run-243 queue, oldest first -- the order a Conductor would land them in.
RUN_243_PRS = [1584, 1586, 1587, 1588, 1589]
RUN_243_EDGES = [(1584, 1586), (1584, 1588), (1586, 1588)]
TRIANGLE = {1584, 1586, 1588}


def test_an_empty_conflict_graph_lands_every_PR_and_rebases_none():
    """The common case, and the one that must stay cheap: nothing collides."""
    free, rebase = landing_plan(RUN_243_PRS, [])
    assert free == RUN_243_PRS, f"expected every PR to land free, got {free}"
    assert rebase == [], f"expected nothing to need a rebase, got {rebase}"


def test_the_run_243_triangle_lands_exactly_one_of_its_three_members():
    """The measured case. Two PRs collide with nothing; the triangle gets one slot.

    This is the assertion that pairwise-clean cannot produce: no pair in the
    matrix shows that {1584, 1586, 1588} is mutually exclusive, only the triple
    does.
    """
    free, rebase = landing_plan(RUN_243_PRS, RUN_243_EDGES)
    assert 1587 in free, f"#1587 collides with nothing and must land free: {free}"
    assert 1589 in free, f"#1589 collides with nothing and must land free: {free}"
    from_triangle = TRIANGLE.intersection(free)
    assert len(from_triangle) == 1, (
        f"a 3-clique admits exactly one member, got {sorted(from_triangle)}"
    )
    assert len(free) == 3, f"expected 3 free landers, got {free}"
    assert sorted(rebase) == sorted(set(RUN_243_PRS) - set(free)), (
        f"needs_rebase must be the complement of lands_free: {rebase} vs {free}"
    )


def test_the_returned_free_set_never_contains_a_conflicting_pair():
    """Soundness, over every subset of the measured graph.

    Maximality is a nice-to-have; soundness is the whole contract. An unsound set
    sends a conflicting pair into the merge queue, which is the ~17-minute cycle
    (CLAUDE.md's measured budget) this exists to avoid.
    """
    import itertools

    for size in range(len(RUN_243_EDGES) + 1):
        for edges in itertools.combinations(RUN_243_EDGES, size):
            free, _ = landing_plan(RUN_243_PRS, list(edges))
            for a, b in itertools.combinations(free, 2):
                assert (a, b) not in edges and (b, a) not in edges, (
                    f"unsound: #{a} and #{b} conflict under {edges} but both landed free"
                )


def test_a_hub_PR_is_preferred_over_a_higher_degree_one_rather_than_first_come():
    """Ordering by conflict degree, not by the caller's order, is what buys the extra lander.

    Edges A-B and A-C. Taking the caller's order literally spends the only slot on
    A and strands both B and C -- one lander. Fewest-collisions-first takes B and
    C and strands A -- two. This case exists because the triangle above cannot
    distinguish the two policies: every ordering of a 3-clique yields one member.
    """
    free, rebase = landing_plan([10, 11, 12], [(10, 11), (10, 12)])
    assert free == [11, 12], f"expected the two zero-degree PRs to land, got {free}"
    assert rebase == [10], f"expected only the hub PR to need a rebase, got {rebase}"


def test_lands_free_is_reported_in_the_callers_landing_order():
    """A plan a human acts on must be reproducible, so it cannot depend on set order."""
    free, _ = landing_plan([1589, 1587, 1584], [(1584, 1587)])
    assert free == [1589, 1587], f"caller order 1589,1587 not preserved: {free}"
    free_reversed, _ = landing_plan([1587, 1589, 1584], [(1584, 1587)])
    assert free_reversed == [1587, 1589], f"caller order 1587,1589 not preserved: {free_reversed}"


def test_a_conflict_edge_is_symmetric():
    """A merge conflict has no direction; treating it as directed would let `(b, a)`
    present as clean when `(a, b)` was the pair actually measured."""
    forward, _ = landing_plan([1, 2], [(1, 2)])
    backward, _ = landing_plan([1, 2], [(2, 1)])
    assert forward == backward == [1], (
        f"edge direction changed the plan: {forward} vs {backward}"
    )


def test_an_edge_naming_a_PR_outside_the_SET_raises_rather_than_being_ignored():
    """Fail closed. Dropping the edge is the quiet direction: the plan would then
    claim a PR lands free on the strength of a collision nobody recorded."""
    try:
        landing_plan([1584, 1586], [(1584, 9999)])
    except RuntimeError as exc:
        assert "9999" in str(exc), f"error must name the unknown PR: {exc}"
    else:
        raise AssertionError(
            "an edge naming a PR outside the set was ignored; it must raise so main() "
            "can fail closed"
        )


def test_a_self_edge_raises():
    """A PR cannot conflict with itself, so this is a corrupt graph, not a no-op."""
    try:
        landing_plan([1584, 1586], [(1584, 1584)])
    except RuntimeError as exc:
        assert "twice" in str(exc), f"unexpected error text: {exc}"
    else:
        raise AssertionError("a self-edge was accepted; it must raise")


def test_a_repeated_PR_in_the_landing_order_raises():
    """A duplicated PR would be planned twice and silently inflate the free count."""
    try:
        landing_plan([1584, 1584], [])
    except RuntimeError as exc:
        assert "twice" in str(exc), f"unexpected error text: {exc}"
    else:
        raise AssertionError("a duplicated PR in the order was accepted; it must raise")


def test_open_PR_rows_parse_to_number_and_sha_in_order():
    """The happy path, and it must preserve order -- the caller sorts by createdAt."""
    rows = ["1584 84d4ba8e", "  1586 bbc3fad5  ", "", "1587 f79772d7"]
    assert parse_open_prs(rows) == [
        (1584, "84d4ba8e"),
        (1586, "bbc3fad5"),
        (1587, "f79772d7"),
    ], "rows did not parse to ordered (number, sha) pairs"


def test_an_unparseable_open_PR_row_RAISES_rather_than_being_skipped():
    """Skipping is the quiet direction again: one fewer sibling in the set, no
    error, and a landing plan that omits the PR most likely to collide."""
    try:
        parse_open_prs(["1584 84d4ba8e", "this is not a row", "1586 bbc3fad5"])
    except RuntimeError as exc:
        assert "unparseable" in str(exc), f"wrong error text: {exc}"
    else:
        raise AssertionError(
            "an unparseable open-PR row was skipped silently; it must raise so main() "
            "can fail closed"
        )


def test_a_non_numeric_PR_NUMBER_raises():
    """Distinct from the arity check above: the row has two fields and is still bad,
    so a field-count test alone would pass it straight through to `int()`."""
    try:
        parse_open_prs(["not-a-number 84d4ba8e"])
    except RuntimeError as exc:
        assert "unparseable" in str(exc), f"wrong error text: {exc}"
    else:
        raise AssertionError("a non-numeric PR number was accepted; it must raise")


def test_a_duplicated_PR_in_the_open_PR_LIST_raises():
    """Two rows for one PR means the upstream read is wrong; planning against it
    would double-count a branch."""
    try:
        parse_open_prs(["1584 84d4ba8e", "1584 deadbeef"])
    except RuntimeError as exc:
        assert "duplicate" in str(exc), f"wrong error text: {exc}"
    else:
        raise AssertionError("a duplicate PR number was accepted; it must raise")


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
