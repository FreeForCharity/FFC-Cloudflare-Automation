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
import os
import pathlib
import subprocess
import sys

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-open-pr-landing-order.py"
)
_spec = importlib.util.spec_from_file_location("_landing_order", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

landing_plan = _mod.landing_plan
parse_open_prs = _mod.parse_open_prs
pr_ref = _mod.pr_ref
verify_fetched_head = _mod.verify_fetched_head
base_merge_failures = _mod.base_merge_failures
base_fetch_spec = _mod.base_fetch_spec

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


def test_the_merges_read_the_ref_namespace_the_fetch_writes():
    """The defect that made this script exit 2 on every invocation: the merges
    referenced `refs/remotes/pr/<n>`, which nothing created. One function now
    owns the spelling, so the fetch and the merge cannot drift apart again."""
    assert pr_ref(1584) == "refs/ffc/pr1584", f"unexpected ref spelling: {pr_ref(1584)}"
    import inspect

    # Scoped to the function that merges, not the whole module: a module-wide
    # grep also matches any prose explaining the old spelling, which is how this
    # assertion first failed against a correct tree.
    measure_body = [
        ln
        for ln in inspect.getsource(_mod.measure_conflicts).splitlines()
        if '"""' not in ln and not ln.strip().startswith("#")
    ]
    assert not any("refs/" in ln and "pr_ref" not in ln for ln in measure_body), (
        "measure_conflicts hard-codes a ref path instead of calling pr_ref(); the "
        "fetch and the merge can then name different refs, which is the bug that "
        "made this script always exit 2:\n  "
        + "\n  ".join(ln.strip() for ln in measure_body if "refs/" in ln)
    )
    assert measure_body, "could not read measure_conflicts' body"
    fetch_src = inspect.getsource(_mod.fetch_pr_heads)
    assert "fetch" in fetch_src, "fetch_pr_heads must actually fetch"
    measure_src = inspect.getsource(_mod.measure_conflicts)
    assert "fetch_pr_heads" in measure_src, (
        "measure_conflicts must populate the refs it merges; without the fetch the "
        "first `git merge` reports a missing ref as a base conflict"
    )


def test_a_head_that_moved_mid_measurement_RAISES_rather_than_being_reported():
    """The pin. A push to any member during the (minutes-long) pairwise sweep
    changes what was measured, and a plan printed from it would describe commits
    nobody is asking about. Fail closed -- `main()` turns this into exit 2."""
    try:
        verify_fetched_head(1584, "84d4ba8e", "deadbeef")
    except RuntimeError as exc:
        text = str(exc)
        assert "1584" in text, f"the error must name the PR that moved: {text}"
        assert "84d4ba8e" in text and "deadbeef" in text, (
            f"the error must name both SHAs so the drift is visible: {text}"
        )
    else:
        raise AssertionError(
            "a head that moved mid-measurement was accepted; the resulting plan "
            "would be reported as current"
        )


def test_a_head_that_matches_the_recorded_sha_is_accepted():
    """The polarity check: the pin must not refuse the ordinary case, or the
    script trades always-exit-2 for always-exit-2 by another route."""
    verify_fetched_head(1584, "84d4ba8e", "84d4ba8e")


def test_itertools_combinations_never_places_the_LAST_element_first():
    """The defect's mechanism, pinned against the stdlib rather than described.

    This is why a base check carried on the `first` leg of each pair cannot be
    complete: for an N-member SET, the last member is never the first element of
    any pair, so it is never the one merged into `base` alone."""
    import itertools

    prs = [1584, 1586, 1587, 1588, 1589]
    firsts = {a for a, _ in itertools.combinations(prs, 2)}
    assert prs[-1] not in firsts, (
        "itertools.combinations placed the last element first -- the premise of "
        "base_merge_failures no longer holds and the base check could go back on "
        "the pair loop"
    )
    # And every other member IS covered, so the gap is exactly one PR: the newest.
    assert firsts == set(prs[:-1]), f"unexpected first-position coverage: {firsts}"


def test_the_base_check_covers_the_LAST_PR_in_the_SET():
    """The fix. `read_open_prs` sorts oldest-first and does not filter on
    `mergeableState`, so the newest open PR can genuinely conflict with `main`
    and still reach the measurement. Before the up-front sweep this returned
    nothing for that case and the pairwise loop recorded #1589 as conflicting
    with all four others -- one base conflict reported as four content
    conflicts."""
    prs = [1584, 1586, 1587, 1588, 1589]
    failures = base_merge_failures(prs, lambda n: n != 1589)
    assert failures == [1589], (
        "the last PR in the SET was not base-checked; a PR that conflicts with "
        f"base is about to be reported as conflicting with every other member: {failures}"
    )


def test_the_base_check_covers_the_FIRST_PR_too():
    """The old placement did catch this one, so it must keep catching it."""
    prs = [1584, 1586, 1587]
    assert base_merge_failures(prs, lambda n: n != 1584) == [1584]


def test_the_base_check_reports_EVERY_unmergeable_PR_not_just_the_first():
    """The old code raised on the first bad `first` leg, so a second bad PR was
    invisible until someone rebased the first one and re-ran. One sweep names
    them all, which is one rebase round instead of N."""
    prs = [1584, 1586, 1587, 1588]
    failures = base_merge_failures(prs, lambda n: n in (1586, 1588))
    assert failures == [1584, 1587], f"expected both unmergeable PRs: {failures}"


def test_a_SET_that_all_merges_into_base_reports_no_failures():
    assert base_merge_failures([1584, 1586, 1587], lambda n: True) == []


def test_the_base_check_preserves_the_callers_order():
    """Reported in landing order so the message reads as a rebase worklist."""
    prs = [1589, 1584, 1587]
    assert base_merge_failures(prs, lambda n: False) == [1589, 1584, 1587]


def test_measure_conflicts_runs_the_base_sweep_BEFORE_the_pair_loop():
    """Source-level, because `measure_conflicts` itself shells out to git and is
    not reachable on this host. The ordering is the whole correctness claim: a
    base sweep placed after the pair loop would be dead code."""
    import inspect

    src = inspect.getsource(_mod.measure_conflicts)
    assert "base_merge_failures" in src, (
        "measure_conflicts no longer calls base_merge_failures; the base check is "
        "back on the asymmetric pair loop"
    )
    sweep_at = src.index("base_merge_failures")
    loop_at = src.index("itertools.combinations")
    assert sweep_at < loop_at, (
        "the base sweep runs after the pairwise loop, so every pair involving an "
        "unmergeable PR is measured (and misattributed) before the sweep can raise"
    )


def test_a_failed_base_checkout_RAISES_instead_of_measuring_the_wrong_base():
    """Copilot's second finding on this PR. The checkout ran `check=False` with
    its return code never inspected, so a failed `git checkout -f --detach
    <base>` left HEAD wherever the worktree happened to be and every merge after
    it measured against the wrong commit -- producing a confident landing plan
    for a base nobody asked about, which is the exact failure class this script
    exists to prevent. `main()` turns the raise into exit 2."""
    import subprocess

    calls = []

    def fake_run(cmd, cwd=None, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "error: pathspec 'origin/nope'")

    original = _mod._run
    _mod._run = fake_run
    try:
        try:
            _mod.checkout_base("/tmp/wt", "origin/nope")
        except RuntimeError as e:
            msg = str(e)
            assert "origin/nope" in msg, f"the raise must name the base it failed on: {msg}"
            assert "wrong base" in msg, (
                f"the raise must say why it matters, not just that git exited 1: {msg}"
            )
        else:
            raise AssertionError(
                "a failed base checkout returned normally; the conflict graph that "
                "follows would be measured against a stale HEAD"
            )
    finally:
        _mod._run = original
    assert calls, "checkout_base did not invoke git at all"
    assert calls[0][:4] == ["git", "checkout", "-f", "--detach"], (
        f"unexpected checkout invocation: {calls[0]}"
    )


def test_a_successful_base_checkout_returns_normally():
    """The polarity check: failing closed must not refuse the ordinary case, or
    the script trades a wrong plan for no plan at all."""
    import subprocess

    def fake_run(cmd, cwd=None, check=True):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    original = _mod._run
    _mod._run = fake_run
    try:
        _mod.checkout_base("/tmp/wt", "origin/main")
    finally:
        _mod._run = original


def test_BOTH_checkout_sites_go_through_the_failing_closed_helper():
    """There were two, and fixing only the one Copilot pointed at would leave
    the pairwise loop measuring against a stale HEAD. Asserted at source level
    because `measure_conflicts` shells out to git and is not reachable here."""
    import inspect

    src = inspect.getsource(_mod.measure_conflicts)
    assert "checkout_base" in src, "measure_conflicts no longer uses checkout_base"
    bad = [
        ln.strip()
        for ln in src.splitlines()
        if '"checkout"' in ln and "checkout_base" not in ln
    ]
    assert not bad, (
        "a raw `git checkout` with an uninspected return code is back in "
        "measure_conflicts:\n  " + "\n  ".join(bad)
    )
    # Two call sites: the base sweep and the pairwise loop.
    assert src.count("checkout_base(") == 2, (
        f"expected both checkout sites to use the helper, found "
        f"{src.count('checkout_base(')}"
    )


def test_a_remote_tracking_base_is_split_into_remote_and_branch():
    """`origin/main` is the default and the shape that can be refreshed."""
    assert base_fetch_spec("origin/main") == ("origin", "main")
    assert base_fetch_spec("upstream/release") == ("upstream", "release")


def test_a_base_that_cannot_be_fetched_is_reported_as_such_not_guessed_at():
    """A bare SHA, a tag or a local branch is a commit the caller pinned on
    purpose. Returning `None` keeps `resolve_base` from inventing a remote and
    fetching from somewhere the base did not come from -- it pins what it was
    given and `main()` prints which commit that was."""
    assert base_fetch_spec("a35e5a15") is None, "a bare SHA has no remote to refresh"
    assert base_fetch_spec("main") is None, "a local branch has no remote to refresh"
    assert base_fetch_spec("refs/heads/main") is None, "a full refname is not remote/branch"
    assert base_fetch_spec("") is None
    assert base_fetch_spec("origin/") is None, "an empty branch half is not a valid spec"
    assert base_fetch_spec("/main") is None, "an empty remote half is not a valid spec"


def test_the_base_is_FETCHED_before_it_is_measured_against():
    """Copilot's finding on the third review pass of this PR, and the one leg of
    `(base, headA, headB)` that had neither of the protections the heads got.
    The heads are fetched and pinned; the base was whatever `origin/main` the
    caller's worktree last happened to see. `checkout_base` catches a base that
    fails to RESOLVE and is blind to one that resolves and is merely OLD."""
    import subprocess

    calls = []

    def fake_run(cmd, cwd=None, check=True):
        calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "a35e5a15" + "0" * 32 + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    original = _mod._run
    _mod._run = fake_run
    try:
        sha = _mod.resolve_base("/tmp/wt", "origin/main")
    finally:
        _mod._run = original

    fetches = [c for c in calls if c[:2] == ["git", "fetch"]]
    assert fetches == [["git", "fetch", "origin", "main"]], (
        f"the base must be fetched from its remote before being measured "
        f"against; git calls were {calls}"
    )
    assert calls.index(["git", "fetch", "origin", "main"]) < next(
        i for i, c in enumerate(calls) if c[:2] == ["git", "rev-parse"]
    ), "the base was resolved before it was fetched, so the SHA is the stale one"
    assert sha == "a35e5a15" + "0" * 32, f"resolve_base must return the pinned SHA, got {sha!r}"


def test_a_base_fetch_that_FAILS_raises_rather_than_measuring_a_stale_base():
    """Failing closed. Degrading to "measure anyway" would reinstate exactly the
    defect the fetch was added to remove, and the resulting plan would be wrong
    for every pair at once while still reading as coherent."""
    import subprocess

    def fake_run(cmd, cwd=None, check=True):
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
        return subprocess.CompletedProcess(cmd, 0, "deadbeef" + "0" * 32 + "\n", "")

    original = _mod._run
    _mod._run = fake_run
    try:
        try:
            _mod.resolve_base("/tmp/wt", "origin/main")
        except RuntimeError as e:
            msg = str(e)
            assert "stale" in msg, f"the raise must name the defect it prevents: {msg}"
        else:
            raise AssertionError(
                "a failed base fetch returned a SHA; the whole landing plan would "
                "be computed against whatever this worktree last saw"
            )
    finally:
        _mod._run = original


def test_an_unresolvable_base_raises_even_when_the_fetch_succeeded():
    """The second half of `resolve_base`. A fetch can succeed and the name still
    not resolve -- a branch that does not exist on the remote is the common
    case -- and an unpinnable base must never reach the merges."""
    import subprocess

    def fake_run(cmd, cwd=None, check=True):
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: ambiguous argument")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    original = _mod._run
    _mod._run = fake_run
    try:
        try:
            _mod.resolve_base("/tmp/wt", "origin/nope")
        except RuntimeError as e:
            assert "origin/nope" in str(e), f"the raise must name the base: {e}"
        else:
            raise AssertionError("an unresolvable base was accepted")
    finally:
        _mod._run = original


def test_a_non_remote_base_is_pinned_WITHOUT_a_fetch():
    """The polarity check for `base_fetch_spec` returning `None`: a caller who
    passes an explicit SHA gets it measured, not an error and not a fetch of
    some invented remote."""
    import subprocess

    calls = []

    def fake_run(cmd, cwd=None, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "cafe" + "0" * 36 + "\n", "")

    original = _mod._run
    _mod._run = fake_run
    try:
        sha = _mod.resolve_base("/tmp/wt", "a35e5a15")
    finally:
        _mod._run = original
    assert not [c for c in calls if c[:2] == ["git", "fetch"]], (
        f"a bare SHA must not trigger a fetch; git calls were {calls}"
    )
    assert sha == "cafe" + "0" * 36


def test_every_merge_measures_against_the_PINNED_base_not_the_symbolic_name():
    """Source-level, because `measure_conflicts` shells out to git. Pinning once
    is what makes all pairs comparable: handed `origin/main`, the later pairs in
    a multi-minute sweep would silently use a different commit from the earlier
    ones the moment `main` moved, and nothing in the output would say so."""
    import inspect

    src = inspect.getsource(_mod.measure_conflicts)
    assert "resolve_base(" in src, "measure_conflicts no longer resolves the base"
    bad = [
        ln.strip()
        for ln in src.splitlines()
        if "checkout_base(" in ln and "base_sha" not in ln
    ]
    assert not bad, (
        "a checkout_base call is still passing the symbolic base, so that pair is "
        "measured against a name that can move mid-sweep:\n  " + "\n  ".join(bad)
    )
    assert src.count("checkout_base(worktree, base_sha)") == 2, (
        f"expected both checkout sites to use the pinned SHA, found "
        f"{src.count('checkout_base(worktree, base_sha)')}"
    )
    assert "resolve_base(" in src.split("fetch_pr_heads(")[0], (
        "the base must be resolved before the heads are fetched, so a stale base "
        "fails before minutes of merging are spent on it"
    )


def test_the_landing_plan_is_reported_NEXT_TO_the_base_it_was_computed_against():
    """A plan without its base is the citation-without-provenance failure: true
    when written, unfalsifiable later. `main()` prints the resolved SHA."""
    import inspect

    src = inspect.getsource(_mod.main)
    assert "base_sha" in src, "main() no longer receives the base SHA"
    assert "measured against" in src, (
        "main() does not print which commit the plan was computed against"
    )


def _capture_run_env(cmd):
    """Drive `_mod._run(cmd)` with a stubbed `subprocess`, returning the `env`
    it passed. Replaces the module's `subprocess` binding rather than patching
    `subprocess.run` itself, which would reach every other importer of it."""

    class _Stub:
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(argv, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

    captured = {}
    original = _mod.subprocess
    _mod.subprocess = _Stub
    try:
        _mod._run(cmd)
    finally:
        _mod.subprocess = original
    return captured


def test_a_merge_COMMIT_cannot_fail_for_a_missing_git_identity():
    """Copilot's fourth finding on this PR. `measure_conflicts` writes real merge
    commits and configured no identity, so on a host with no `user.name`/
    `user.email` git aborts the merge with exit 128 (`Committer identity
    unknown`) and this script reads that as a merge that did not succeed.

    Measured on a repo with `GIT_CONFIG_GLOBAL=/dev/null` and no local identity:
    a fast-forward leg returns **0** (no commit is written, so no identity is
    needed) and a real merge returns **128**. That asymmetry is what makes it a
    wrong answer rather than an error -- the base sweep fast-forwards and passes,
    then every pair's `second` leg fails and is recorded as a CONFLICT edge, so
    the script prints a coherent landing plan over a fully fabricated conflict
    graph. Supplying the identity through the environment took the same merge
    from 128 to 0."""
    env = _capture_run_env(["git", "merge", "--no-edit", "refs/ffc/pr1584"]).get("env")
    assert env is not None, (
        "_run passes no env, so git inherits whatever identity the host happens "
        "to have -- and aborts the merge with 128 when it has none"
    )
    for var in (
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
    ):
        assert env.get(var), (
            f"{var} is unset in the merge environment; a non-fast-forward "
            f"`git merge` exits 128 without it and is recorded as a conflict"
        )


def test_the_merge_environment_INHERITS_the_ambient_one():
    """The other half, and the one with a price already paid: a *scrubbed* `env`
    dict is what broke every node-spawning module in this suite on Windows
    (#943), where the interpreter cannot start without the inherited
    environment. Fixing the identity by passing only the identity would trade
    this bug for that one, so pin the inheritance, not just the four vars."""
    sentinel = "FFC_LANDING_ORDER_ENV_PROBE"
    os.environ[sentinel] = "1"
    try:
        env = _capture_run_env(["git", "rev-parse", "HEAD"]).get("env") or {}
    finally:
        os.environ.pop(sentinel, None)
    assert env.get(sentinel) == "1", (
        "the merge env does not inherit os.environ, so it is a scrubbed dict -- "
        "the #943 shape, where git/node cannot start for want of PATH, SYSTEMROOT "
        "and friends"
    )


def test_EVERY_git_call_goes_THROUGH_the_identity_carrying_runner():
    """Source-level, because the identity lives in `_run` and the merges are
    only safe for as long as they keep going through it. A later `subprocess.run`
    added beside a `git merge` would reintroduce the 128 silently, and the two
    tests above would still pass."""
    import inspect

    src = inspect.getsource(_mod)
    body = src.split("def _run(", 1)
    assert len(body) == 2, "_run is gone; the merge identity has nowhere to live"
    after = body[1].split("\ndef ", 1)
    # Asserted rather than indexed: an IndexError here would escape the module's
    # AssertionError handler and truncate the roster, which reads as "no
    # failures" rather than as a failure (L194).
    assert len(after) == 2, "could not find the end of _run; the slice below would be wrong"
    outside = body[0] + after[1]
    assert "subprocess.run(" not in outside, (
        "a `subprocess.run(` call exists outside `_run`, so it carries neither "
        "the committer identity nor the inherited environment"
    )
    merge_src = inspect.getsource(_mod.measure_conflicts)
    strays = [ln.strip() for ln in merge_src.splitlines() if "subprocess" in ln]
    assert not strays, f"measure_conflicts shells out directly: {strays}"



# --------------------------------------------------------------------------
# Reading the open-PR SET at all
#
# The deliverable's first step, and it was dead on arrival: `read_open_prs`
# called `gh pr list --json`, which is a GraphQL client call, and GraphQL is
# refused from a Claude Code session with `403 Forbidden`. `gh` exits 1 with
# empty stdout, `_run` raises, and the tool measures nothing. Measured on head
# `49ba3316` two ways -- the raw invocation and `read_open_prs` itself -- and
# with the Conductor retired (411e879) every surviving caller is a session it
# 403s in.
#
# Every test below asserts the COMMAND rather than the response, because the
# command is what a stubbed `_run` still shows. That is the gap that let the
# defect ship green: a stub answers whatever it is asked, so a module that only
# checks the parsed result cannot tell a working call from a refused one.
# --------------------------------------------------------------------------


def _capture_open_pr_read(
    stdout,
    label="agentic-os",
    repo="FreeForCharity/FFC-Cloudflare-Automation",
    allow_raise=False,
):
    """Drive `read_open_prs` with a stubbed `_run`, returning `(cmd, result)`.

    A `RuntimeError` out of `read_open_prs` is converted to an `AssertionError`
    unless the caller is testing for one. That is not cosmetic: the module
    runner below catches `AssertionError` and nothing else, so a reader that
    raises anything else aborts the module mid-roster -- which prints no `FAIL`
    line at all and reads as "no failures" to anything grepping for one (L194).
    Measured: with the GraphQL `gh pr list` restored, `json.loads` on a row of
    text raises `RuntimeError` here and the module reported 27 of its 44 tests.
    """
    import subprocess

    calls = []

    def fake_run(cmd, cwd=None, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    original = _mod._run
    _mod._run = fake_run
    try:
        result = _mod.read_open_prs(repo, label)
    except RuntimeError as e:
        if allow_raise:
            raise
        raise AssertionError(
            f"read_open_prs could not read a well-formed REST row: {e} -- the "
            f"`<created_at> <number> <sha>` rows the REST read emits are not "
            f"what this reader parses (a GraphQL JSON reader sees them as text)"
        ) from None
    finally:
        _mod._run = original
    assert len(calls) == 1, f"expected exactly one call to read the open-PR list, got {calls}"
    return calls[0], result


def test_the_open_PR_list_is_read_over_REST_not_GRAPHQL():
    """The fix. `gh pr list --json` 403s in every session that can still run
    this script, so the tool's first step raises before it measures anything."""
    cmd, _ = _capture_open_pr_read("2026-10-09T16:32:05Z 1584 dd16ff6a\n")
    assert cmd[:2] == ["gh", "api"], (
        f"the open-PR list is not read over REST: {cmd} -- `gh pr list --json` "
        f"is a GraphQL call and is refused with 403 from a Claude Code session"
    )
    joined = " ".join(cmd)
    assert "pr list" not in joined, f"the GraphQL spelling is still in the call: {cmd}"
    assert "/pulls?" in joined, f"the REST read does not name the pulls endpoint: {cmd}"
    assert "state=open" in joined, f"the REST read does not ask for OPEN PRs: {cmd}"


def test_the_open_PR_read_PAGINATES_so_a_truncated_SET_is_not_a_wrong_plan():
    """A short SET is not a smaller answer, it is a wrong landing plan: the PR
    left off page 1 is measured against nothing and reported as landing free.
    The expression stays streaming -- one row per item -- because `--paginate`
    evaluates `--jq` once per page, so an aggregate would be computed per page
    and concatenated."""
    cmd, _ = _capture_open_pr_read("2026-10-09T16:32:05Z 1584 dd16ff6a\n")
    assert "--paginate" in cmd, f"the open-PR read does not paginate: {cmd}"
    jq = cmd[cmd.index("--jq") + 1]
    for aggregate in ("| length", "| last", "| first", "add", "max_by", "min_by"):
        assert aggregate not in jq, (
            f"the jq expression uses the whole-set aggregate {aggregate!r} under "
            f"--paginate, which is evaluated once PER PAGE: {jq}"
        )
    assert jq.lstrip().startswith(".[]"), (
        f"the jq expression is not streaming, so its output will not concatenate "
        f"cleanly across pages: {jq}"
    )


def test_the_LABEL_is_filtered_client_side_because_REST_drops_an_unknown_parameter():
    """`labels=` is an *issues* parameter. `GET /pulls` drops an unrecognised
    query parameter in silence and returns every open PR, so a URL-side filter
    would read as working and quietly admit unrelated PRs into the measured
    SET."""
    cmd, _ = _capture_open_pr_read("2026-10-09T16:32:05Z 1584 dd16ff6a\n", label="agentic-os")
    url = next(a for a in cmd if "/pulls?" in a)
    assert "label" not in url, (
        f"the label is passed as a query parameter: {url} -- `GET /pulls` has no "
        f"label filter and drops it silently, returning every open PR"
    )
    jq = cmd[cmd.index("--jq") + 1]
    assert "agentic-os" in jq and "labels" in jq, (
        f"the label is not filtered client-side either, so the SET is unfiltered: {jq}"
    )


def test_the_open_PR_list_is_ordered_OLDEST_first_whatever_order_REST_returns():
    """`GET /pulls` defaults to newest-first -- the reverse of the landing order
    the caller wants -- and the default is not part of the contract. Sorting
    here is what makes the plan reproducible rather than endpoint-dependent."""
    newest_first = (
        "2026-10-12T10:47:36Z 1647 e048cf74\n"
        "2026-10-11T19:22:18Z 1615 64071b5d\n"
        "2026-10-09T16:32:05Z 1584 dd16ff6a\n"
    )
    _, prs = _capture_open_pr_read(newest_first)
    assert [n for n, _ in prs] == [1584, 1615, 1647], (
        f"the open-PR list is not oldest-first: {prs} -- the landing order would "
        f"be the reverse of the one the caller asked for"
    )
    assert [sha for _, sha in prs] == ["dd16ff6a", "64071b5d", "e048cf74"], (
        f"a SHA was reordered away from its PR number: {prs}"
    )


def test_a_label_that_would_break_OUT_of_the_jq_string_RAISES():
    """`gh api --jq` has no `--arg`, so the label is interpolated into a jq
    string literal. A quote or a backslash would change the expression rather
    than the value compared -- a filter nobody wrote, selecting the wrong SET.
    Fail closed: a raise is recoverable, a silently different SET is not."""
    import subprocess

    def fake_run(cmd, cwd=None, check=True):
        raise AssertionError(f"a dangerous label reached the command line: {cmd}")

    original = _mod._run
    _mod._run = fake_run
    try:
        for bad in ['agentic-os"', "agentic\\os", 'x" or true or "', ""]:
            try:
                _mod.read_open_prs("o/r", bad)
            except RuntimeError as e:
                assert "label" in str(e), f"the raise must name what it refused: {e}"
            else:
                raise AssertionError(f"label {bad!r} was accepted into the jq expression")
    finally:
        _mod._run = original


def test_the_ordinary_LABEL_shapes_are_still_accepted():
    """The polarity check on the guard above. Refusing too much trades a wrong
    SET for no SET at all, and the labels this repo actually uses must pass."""
    for good in ("agentic-os", "testing", "security", "good first issue", "area/dns", "P1:high"):
        cmd, _ = _capture_open_pr_read("", label=good)
        jq = cmd[cmd.index("--jq") + 1]
        assert good in jq, f"label {good!r} was accepted but not filtered on: {jq}"


def test_an_unparseable_REST_row_RAISES_rather_than_being_skipped():
    """Same polarity as `parse_open_prs`: dropping a row it cannot read is the
    quiet direction -- one fewer PR in the SET, no error, and a landing plan
    that omits the PR most likely to collide."""
    try:
        _capture_open_pr_read("2026-10-09T16:32:05Z 1584\n", allow_raise=True)
    except RuntimeError as e:
        assert "1584" in str(e), f"the raise must quote the row it could not read: {e}"
    else:
        raise AssertionError("a two-field REST row was accepted; the SET is short by one PR")


# --------------------------------------------------------------------------
# The six findings of the #1731 substitute review (Copilot was out of credits;
# the review was done by a Claude session at @clarkemoyer's direction, recorded
# on #1737). Numbered as that review numbered them.
# --------------------------------------------------------------------------


class _GitStub:
    """A stubbed `_run` that answers `git` by command shape.

    Every test below drives real module code against it, so the thing under
    test is the module's reaction to git, not a restatement of it. Anything the
    stub is not told about answers exit 0 with empty output, which is the
    permissive direction on purpose: a guard that only fires because the stub
    refused everything would prove nothing.
    """

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, cmd, cwd=None, check=True):
        self.calls.append(list(cmd))
        for shape, (rc, out, err) in self.answers.items():
            if list(cmd)[: len(shape)] == list(shape):
                return subprocess.CompletedProcess(cmd, rc, out, err)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _with_stub(stub, fn):
    original = _mod._run
    _mod._run = stub
    try:
        return fn()
    finally:
        _mod._run = original


_CLEAN_LINKED_WORKTREE = {
    ("git", "rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
    ("git", "rev-parse", "--git-dir"): (0, "/repo/.git/worktrees/wt1731\n", ""),
    ("git", "rev-parse", "--git-common-dir"): (0, "/repo/.git\n", ""),
    ("git", "status", "--porcelain"): (0, "", ""),
}


def _guard(answers):
    """Run `assert_disposable_worktree` against `answers`; return the raise text
    or `None`. A non-`RuntimeError` is converted to an `AssertionError` so the
    module roster cannot be truncated by it (L194)."""
    stub = _GitStub(answers)

    def run():
        try:
            _mod.assert_disposable_worktree("/tmp/wt")
        except RuntimeError as e:
            return str(e)
        except Exception as e:  # pragma: no cover -- turned into a FAIL line
            raise AssertionError(f"the guard raised {type(e).__name__}: {e}") from None
        return None

    return _with_stub(stub, run)


# ---- Finding 1 (BLOCKING): --worktree was unvalidated ---------------------


def test_a_DIRTY_worktree_is_REFUSED_before_git_checkout_minus_f_discards_it():
    """The blocking finding. `checkout_base` runs `git checkout -f --detach`,
    and `--worktree` came straight from the caller, so `--worktree .` against a
    live checkout discarded uncommitted tracked work and left HEAD detached.
    The blast radius is somebody's unpushed work, and the invocation that
    reaches it is the obvious one."""
    answers = dict(_CLEAN_LINKED_WORKTREE)
    answers[("git", "status", "--porcelain")] = (
        0,
        " M scripts/check-open-pr-landing-order.py\n?? notes.md\n",
        "",
    )
    msg = _guard(answers)
    assert msg is not None, (
        "a worktree with uncommitted changes was accepted; the next `git checkout "
        "-f --detach` discards them with no warning and no way back"
    )
    assert "scripts/check-open-pr-landing-order.py" in msg, (
        f"the refusal must name what would have been lost: {msg}"
    )


def test_the_PRIMARY_checkout_is_REFUSED_even_when_it_is_clean():
    """Clean means nothing is destroyed, not that this is an acceptable tree to
    use: the script still detaches HEAD, writes `refs/ffc/*` and leaves
    throwaway merge commits in the checkout somebody is working in. A linked
    worktree has a different `--git-dir` from its `--git-common-dir`; the
    primary one does not."""
    answers = dict(_CLEAN_LINKED_WORKTREE)
    answers[("git", "rev-parse", "--git-dir")] = (0, "/repo/.git\n", "")
    answers[("git", "rev-parse", "--git-common-dir")] = (0, "/repo/.git\n", "")
    msg = _guard(answers)
    assert msg is not None, (
        "the repository's primary checkout was accepted as a disposable worktree"
    )
    assert "worktree add" in msg, (
        f"the refusal must name the one-command remedy, or the next caller "
        f"reaches for --force: {msg}"
    )


def test_a_path_that_is_not_a_git_worktree_AT_ALL_is_refused():
    answers = {("git", "rev-parse", "--is-inside-work-tree"): (128, "", "fatal: not a git repository")}
    assert _guard(answers) is not None, "a non-worktree path was accepted"


def test_a_clean_LINKED_worktree_is_ACCEPTED():
    """The polarity check. A guard that refuses everything trades a destroyed
    working tree for a tool that never runs, and both of this module's earlier
    fail-closed fixes carry the same check for the same reason."""
    assert _guard(dict(_CLEAN_LINKED_WORKTREE)) is None, (
        "a clean linked worktree was refused; the script cannot run at all"
    )


def test_an_UNREADABLE_worktree_status_is_refused_rather_than_assumed_clean():
    """`git status` failing is "we do not know what is in this tree", and the
    reassuring reading of an empty stdout is that it is clean. Same polarity as
    every other read in this module: unknown is not a pass."""
    answers = dict(_CLEAN_LINKED_WORKTREE)
    answers[("git", "status", "--porcelain")] = (128, "", "fatal: bad object HEAD")
    assert _guard(answers) is not None, (
        "a worktree whose status could not be read was treated as clean"
    )


def test_the_WORKTREE_GUARD_runs_BEFORE_anything_destructive():
    """Source-level ordering, because `measure_conflicts` shells out to git. A
    guard placed after the fetch or the checkout is decoration: the thing it
    protects has already happened. It must be the first statement."""
    import inspect

    src = inspect.getsource(_mod.measure_conflicts)
    assert "assert_disposable_worktree(" in src, (
        "measure_conflicts no longer validates --worktree, so `git checkout -f "
        "--detach` runs against whatever path the caller passed"
    )
    guard_at = src.index("assert_disposable_worktree(worktree)")
    for later in ("resolve_base(", "fetch_pr_heads(", "checkout_base("):
        assert guard_at < src.index(later), (
            f"{later} runs before the worktree guard, so the tree is already "
            f"being written to by the time the guard could refuse it"
        )


# ---- Finding 4: a refused merge is not a content conflict -----------------


def test_a_merge_BLOCKED_BY_AN_UNTRACKED_FILE_is_not_recorded_as_a_conflict():
    """Measured: `git merge` refused by an untracked file that would be
    overwritten exits **1 with no MERGE_HEAD**, identical by return code to a
    real conflict. Both legs read any non-zero as a conflict, so it entered the
    graph as an edge and suppressed a PR that would in fact have landed -- a
    broken measurement rendered as a finding, this module's own failure class.
    `MERGE_HEAD` is the discriminator: git writes it once the merge has
    actually begun."""
    stub = _GitStub(
        {
            ("git", "merge"): (1, "", "error: Untracked working tree file 'x' would be overwritten"),
            ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): (1, "", ""),
        }
    )

    def run():
        try:
            _mod.merge_ref("/tmp/wt", "refs/ffc/pr1584")
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            raise AssertionError(f"merge_ref raised {type(e).__name__}: {e}") from None
        return None

    msg = _with_stub(stub, run)
    assert msg is not None, (
        "a merge git REFUSED TO START was reported as a content conflict; the PR "
        "it names is about to be held back for a conflict that does not exist"
    )
    assert "MERGE_HEAD" in msg, f"the raise must name the discriminator: {msg}"


def test_a_REAL_content_conflict_is_still_reported_as_one():
    """The polarity check: the discriminator must not turn every conflict into
    an infrastructure error, or the tool measures nothing and exits 2."""
    stub = _GitStub(
        {
            ("git", "merge"): (1, "CONFLICT (content): Merge conflict in docs/lessons-ledger.md", ""),
            ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): (0, "dead" + "0" * 36 + "\n", ""),
        }
    )
    assert _with_stub(stub, lambda: _mod.merge_ref("/tmp/wt", "refs/ffc/pr1584")) is False, (
        "a genuine conflict was not reported as one"
    )
    aborts = [c for c in stub.calls if c[:3] == ["git", "merge", "--abort"]]
    assert aborts, f"a conflicted merge was left in the tree, un-aborted: {stub.calls}"


def test_a_SUCCESSFUL_merge_returns_True_and_is_not_aborted():
    stub = _GitStub()
    assert _with_stub(stub, lambda: _mod.merge_ref("/tmp/wt", "refs/ffc/pr1584")) is True
    assert not [c for c in stub.calls if c[:3] == ["git", "merge", "--abort"]], (
        f"a successful merge was aborted, so the pairwise leg measured nothing: {stub.calls}"
    )


def test_BOTH_merge_sites_go_through_the_discriminator():
    """There are two, as there were two checkout sites, and fixing only the
    pairwise one would leave the base sweep recording a refused merge as "this
    PR does not merge into main"."""
    import inspect

    src = inspect.getsource(_mod.measure_conflicts)
    bad = [
        ln.strip()
        for ln in src.splitlines()
        if '"merge"' in ln and "merge_ref" not in ln
    ]
    assert not bad, (
        "a raw `git merge` whose return code is read directly is back in "
        "measure_conflicts:\n  " + "\n  ".join(bad)
    )
    assert src.count("merge_ref(") == 3, (
        f"expected three merge sites (base sweep, pair first leg, pair second "
        f"leg) through the helper, found {src.count('merge_ref(')}"
    )


# ---- Finding 3: one unrebasable PR must not cost the others their plan ----


def _run_main(argv, read_open_prs, measure_conflicts):
    """Drive `main()` with its two network-touching steps stubbed, capturing
    stdout. Returns `(rc, output)`."""
    import contextlib
    import io

    originals = (_mod.read_open_prs, _mod.measure_conflicts)
    _mod.read_open_prs, _mod.measure_conflicts = read_open_prs, measure_conflicts
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = _mod.main(argv)
    finally:
        _mod.read_open_prs, _mod.measure_conflicts = originals
    return rc, buf.getvalue()


def test_ONE_unrebasable_PR_does_not_take_the_other_PRs_plan_down_with_it():
    """`measure_conflicts` raised on any PR that would not merge into the base,
    which `main()` turned into **exit 2 with no plan at all** -- although the
    contract assigns "needs a rebase first" to exit 1, and the whole point of
    the tool is to tell a landing session what it CAN land. One stale PR taking
    the other four down defeats it."""
    prs = [(1584, "a" * 40), (1586, "b" * 40), (1587, "c" * 40)]
    rc, out = _run_main(
        ["--worktree", "/tmp/wt"],
        lambda repo, label, base=None: prs,
        lambda wt, base, p: ("f" * 40, [], [1586]),
    )
    assert rc == 1, f"expected exit 1 (a finding), got {rc}; output was:\n{out}"
    assert "#1584" in out and "#1587" in out, (
        f"the PRs that DO land were not reported; one unrebasable sibling cost "
        f"them their plan:\n{out}"
    )
    plan_line = next(ln for ln in out.splitlines() if ln.startswith("lands free"))
    assert "#1586" not in plan_line, (
        f"a PR that does not merge into the base at all was placed in lands_free: {plan_line}"
    )
    assert "#1586" in out, f"the unrebasable PR was not reported at all:\n{out}"


def test_measure_conflicts_RETURNS_the_unrebasable_set_rather_than_raising():
    """The other half of finding 3, and the half `main()`'s stub cannot see.
    Driven end to end against the git stub: #1586 conflicts with the base, the
    other two do not. The old code raised here, so nothing downstream ran."""
    prs = [(1584, "a" * 40), (1586, "b" * 40), (1587, "c" * 40)]
    answers = dict(_CLEAN_LINKED_WORKTREE)
    answers[("git", "rev-parse", "FETCH_HEAD")] = (0, "f" * 40 + "\n", "")
    for number, sha in prs:
        answers[("git", "rev-parse", _mod.pr_ref(number))] = (0, sha + "\n", "")
    # Any merge that fails here has a MERGE_HEAD, i.e. is a real conflict.
    answers[("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")] = (0, "d" * 40 + "\n", "")
    answers[("git", "merge", "--no-edit", _mod.pr_ref(1586))] = (1, "CONFLICT (content)", "")
    stub = _GitStub(answers)

    def run():
        try:
            return _mod.measure_conflicts("/tmp/wt", "origin/main", prs)
        except RuntimeError as e:
            raise AssertionError(
                f"one PR that does not merge into the base aborted the whole "
                f"measurement: {e}"
            ) from None
        except Exception as e:
            raise AssertionError(f"measure_conflicts raised {type(e).__name__}: {e}") from None

    base_sha, edges, unmergeable = _with_stub(stub, run)
    assert base_sha == "f" * 40, f"unexpected base: {base_sha}"
    assert unmergeable == [1586], f"expected #1586 as the only base failure: {unmergeable}"
    assert edges == [], f"a PR excluded from the pairwise sweep still produced edges: {edges}"
    paired = [c for c in stub.calls if c[:3] == ["git", "merge", "--no-edit"] and c[3] == _mod.pr_ref(1586)]
    assert len(paired) == 1, (
        f"#1586 was merged {len(paired)} times: it must be measured once against "
        f"the base and then excluded, not carried into every pair"
    )


def test_a_SET_with_nothing_unrebasable_still_exits_0():
    """Polarity: the clean case must stay exit 0, or every caller sees a
    finding."""
    prs = [(1584, "a" * 40), (1586, "b" * 40)]
    rc, out = _run_main(
        ["--worktree", "/tmp/wt"],
        lambda repo, label, base=None: prs,
        lambda wt, base, p: ("f" * 40, [], []),
    )
    assert rc == 0, f"a conflict-free SET did not exit 0: rc={rc}\n{out}"
    assert "does not merge into the base" not in out, (
        f"the base-failure line printed for an empty failure set:\n{out}"
    )


# ---- Finding 2: an infrastructure fault must not read as a finding --------


def test_an_INFRASTRUCTURE_fault_exits_2_not_1():
    """`main()` caught only `RuntimeError`, so `gh` off PATH -- a
    `FileNotFoundError` -- propagated out of `main()`. Whatever a caller then
    recorded, nothing distinguished "the landing order has a problem" from "the
    tool never ran", which is this repo's L50/L240 shape."""

    def boom(repo, label, base=None):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    rc, out = _run_main(
        ["--worktree", "/tmp/wt"], boom, lambda wt, base, p: ("f" * 40, [], [])
    )
    assert rc == 2, (
        f"an exception that is not a RuntimeError exited {rc}; exit 1 means 'a "
        f"finding' and nothing was measured at all:\n{out}"
    )
    assert "::error::" in out, (
        f"the failure was silent -- no ::error:: line, so a workflow log shows "
        f"nothing:\n{out}"
    )
    assert "FileNotFoundError" in out, (
        f"the ::error:: line does not name the cause, so the operator cannot "
        f"tell which of the two exit-2 reasons they are looking at:\n{out}"
    )


def test_a_RuntimeError_still_exits_2_with_the_MEASUREMENT_wording():
    """The two exit-2 causes stay apart in a log: one measured and could not
    conclude, the other never ran."""
    def boom(repo, label, base=None):
        raise RuntimeError("could not read the open-PR list")

    rc, out = _run_main(
        ["--worktree", "/tmp/wt"], boom, lambda wt, base, p: ("f" * 40, [], [])
    )
    assert rc == 2, f"a RuntimeError no longer exits 2: {rc}"
    assert "could not compute a landing order" in out, (
        f"the measurement failure lost its own wording:\n{out}"
    )
    assert "did not run" not in out, (
        f"a measured failure is being reported as a harness failure:\n{out}"
    )


def test_a_NON_NUMERIC_pr_number_from_REST_raises_RuntimeError_not_ValueError():
    """The other half of finding 2. `parse_open_prs` guards this and has a test
    for it; the REST reader called `int()` bare, so the `ValueError` fell
    through `main()`'s `RuntimeError`-only handler into the same gap."""
    try:
        _capture_open_pr_read("2026-10-09T16:32:05Z not-a-number dd16ff6a\n", allow_raise=True)
    except RuntimeError as e:
        assert "not-a-number" in str(e), f"the raise must quote the row: {e}"
    except Exception as e:
        raise AssertionError(
            f"a non-numeric PR number raised {type(e).__name__}, which main() does "
            f"not catch: {e}"
        ) from None
    else:
        raise AssertionError("a non-numeric PR number was accepted")


# ---- Finding 5: the SET must be filtered on the base branch ---------------


def _capture_base_filtered_read(stdout, base_branch):
    stub = _GitStub({("gh",): (0, stdout, "")})
    result = _with_stub(
        stub,
        lambda: _mod.read_open_prs(
            "FreeForCharity/FFC-Cloudflare-Automation", "agentic-os", base_branch
        ),
    )
    assert len(stub.calls) == 1, f"expected one list call, got {stub.calls}"
    return stub.calls[0], result


def test_the_open_PR_read_FILTERS_ON_THE_BASE_BRANCH():
    """`GET /pulls` returns every open PR whatever its base, so a PR stacked on
    another branch was merged against `origin/main`: either a false "rebase
    first" or false edges against every member of the SET."""
    cmd, _ = _capture_base_filtered_read("2026-10-09T16:32:05Z 1584 dd16ff6a\n", "main")
    jq = cmd[cmd.index("--jq") + 1]
    assert ".base.ref" in jq, (
        f"the open-PR read does not filter on the base branch, so a stacked PR "
        f"is measured against a base it was never opened against: {jq}"
    )
    assert '"main"' in jq, f"the base branch is not the one compared against: {jq}"
    assert "base=" not in " ".join(cmd), (
        f"the base filter moved into the URL, where REST drops an unrecognised "
        f"parameter in silence: {cmd}"
    )


def test_a_BASE_THAT_NAMES_NO_BRANCH_leaves_the_SET_unfiltered():
    """A bare SHA is a commit the caller pinned deliberately and names no
    branch to filter on. Inventing one would be worse than not filtering."""
    cmd, _ = _capture_base_filtered_read("2026-10-09T16:32:05Z 1584 dd16ff6a\n", None)
    jq = cmd[cmd.index("--jq") + 1]
    assert ".base.ref" not in jq, f"a base filter was invented for a SHA base: {jq}"


def test_main_DERIVES_the_base_branch_from_its_own_base_argument():
    """The wiring: a filter nothing passes is the same as no filter, and the
    two tests above would both still pass with `main()` never calling it."""
    seen = {}

    def read(repo, label, base=None):
        seen["base"] = base
        return [(1584, "a" * 40)]

    _run_main(
        ["--worktree", "/tmp/wt", "--base", "upstream/release"],
        read,
        lambda wt, base, p: ("f" * 40, [], []),
    )
    assert seen.get("base") == "release", (
        f"main() passed {seen.get('base')!r} as the base branch; the SET is not "
        f"filtered on the branch the plan is computed against"
    )


# ---- Finding 6: a fetch does not guarantee the tracking ref moved ---------


def test_the_base_is_pinned_to_FETCH_HEAD_not_to_the_TRACKING_REF():
    """`git fetch <remote> <branch>` updates `refs/remotes/<remote>/<branch>`
    only opportunistically: under a restricted `remote.*.fetch` refspec -- a
    `--single-branch` clone is the ordinary case -- the fetch succeeds and
    `rev-parse origin/main` returns the SHA the worktree already had. The
    stale-base defect `resolve_base` exists to prevent is then back, with
    output claiming a refresh, and every pair is wrong in the same direction so
    the plan still reads as coherent. `FETCH_HEAD` names what was just
    fetched."""
    fresh = "f" * 40
    stale = "5" * 40
    stub = _GitStub(
        {
            ("git", "rev-parse", "FETCH_HEAD"): (0, fresh + "\n", ""),
            ("git", "rev-parse", "origin/main"): (0, stale + "\n", ""),
        }
    )
    sha = _with_stub(stub, lambda: _mod.resolve_base("/tmp/wt", "origin/main"))
    assert sha == fresh, (
        f"resolve_base returned {sha[:8]}, the stale tracking ref, although the "
        f"fetch had just brought {fresh[:8]} -- the plan would be computed "
        f"against a base that no longer exists while reporting a refresh"
    )


def test_a_FETCH_HEAD_that_does_not_resolve_raises_rather_than_falling_back():
    """Falling back to the tracking ref here would reinstate the defect under a
    different name, and the fallback would fire precisely in the restricted-
    refspec case the FETCH_HEAD read exists for."""
    stub = _GitStub(
        {
            ("git", "rev-parse", "FETCH_HEAD"): (128, "", "fatal: ambiguous argument"),
            ("git", "rev-parse", "origin/main"): (0, "5" * 40 + "\n", ""),
        }
    )

    def run():
        try:
            return _mod.resolve_base("/tmp/wt", "origin/main")
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            raise AssertionError(f"resolve_base raised {type(e).__name__}: {e}") from None

    out = _with_stub(stub, run)
    assert isinstance(out, str) and "FETCH_HEAD" in out, (
        f"an unresolvable FETCH_HEAD did not raise; resolve_base returned {out!r}"
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
