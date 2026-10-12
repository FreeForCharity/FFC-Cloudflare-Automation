#!/usr/bin/env python3
"""`mergeable_state: clean` on every open PR is not evidence the queue can drain.

The defect this measures, observed on the live queue in Conductor run 243
(2026-10-10). Seven open `agentic-os` PRs. Every one of them read
`mergeable_state: clean` from `pulls/<n>`, every required check was green, and
every review thread was resolved. Three cloud-worker runs that day (06:44Z,
09:47Z, 12:21Z) each read exactly that and reported "nothing to fix".

Every fact in those reports was true. The conclusion was wrong. Merging them in
landing order conflicts twice:

    main + #1584            -> merged
            + #1586         -> CONFLICT  docs/lessons-ledger.md
            + #1587         -> merged
            + #1588         -> CONFLICT  docs/lessons-ledger.md
            + #1589         -> merged

`mergeable_state` answers "does this branch merge into `main` *as main is now*".
Each PR did -- I checked all five against bare `main` and all five were clean. It
cannot answer "can this SET land", because the second lander meets a `main` that
the first one moved. Nothing in GitHub's per-PR view, and nothing in CI, computes
that: each PR's checks run on its own branch, where nothing is wrong. That gap is
#1552.

Pairwise is not enough either, and this run is the counter-example to reaching
for it. The four ledger-touching PRs gave:

    1584 x 1586  CONFLICT      1586 x 1587  clean
    1584 x 1587  clean         1586 x 1588  CONFLICT
    1584 x 1588  CONFLICT      1587 x 1588  clean

{1584, 1586, 1588} is a conflict *triangle*, so at most ONE of the three can land
un-rebased -- a fact no single pair exhibits. The cause was measurable once
looked for: `git diff --numstat` on the ledger gave #1584 **+149/-145** and #1588
**+149/-144**, i.e. both rewrite ~145 rows they did not author (Prettier's table
re-pad), while #1586 appends +6/-0 into the region they re-pad.

WHAT THIS PRINTS. A landing plan: the PRs that land with no rebase, in order,
and the ones that need `update-branch` first. The `lands_free` set is SOUND --
`landing_plan` verifies it contains no conflict edge before returning it, and
raises if it ever does. It is a greedy lower bound on size, not a proven maximum:
maximum-independent-set is NP-hard and a bigger set is a nice-to-have, whereas an
*unsound* set would send a conflicting pair into the merge queue, which is the
17-minute cycle this exists to avoid.

FAIL-CLOSED, per #993's rule that "we could not read it" must never render as
"nothing to report". An unreadable PR list, an unparseable row, or an edge naming
a PR outside the set all raise, and `main()` turns that into exit 2. A guard that
goes quiet when its input is missing is the failure mode it exists to prevent,
and this one shells out to `git` and `gh`, so the quiet path is reachable in
ordinary operation.

Exit 0 when every open PR lands free, 1 when at least one needs a rebase first
(a finding, not a breakage), 2 when the comparison could not be made.

The decision logic is pure and lives in `landing_plan` / `parse_open_prs`;
`tests/workflow-logic/test_open_pr_landing_order.py` drives both with fixtures,
so none of it needs the network or a worktree to be tested. That split is
deliberate: on the Conductor's Windows host every bash-invoking module in this
suite is red for platform reasons (#1119), so logic that matters must be
reachable without one.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys

# A row of the open-PR list: `<number> <head sha>`, whitespace-separated. Kept
# this narrow on purpose -- the caller formats it, so anything else is a bug in
# the pipeline rather than input to be tolerated.
_ROW_FIELDS = 2


def parse_open_prs(rows):
    """Rows of `<number> <sha>` -> ordered list of (number, sha).

    Raises on a row this cannot read. Skipping it would be the quiet direction:
    one fewer PR in the set, no error, and a landing plan that silently omits the
    PR most likely to collide.
    """
    out = []
    seen = set()
    for row in rows:
        row = row.strip()
        if not row:
            continue
        parts = row.split()
        if len(parts) != _ROW_FIELDS:
            raise RuntimeError(f"unparseable open-PR row: {row!r}")
        num_text, sha = parts
        try:
            num = int(num_text)
        except ValueError:
            raise RuntimeError(f"unparseable open-PR row: {row!r}") from None
        if num in seen:
            raise RuntimeError(f"duplicate PR number in the open-PR list: {num}")
        seen.add(num)
        out.append((num, sha))
    return out


def landing_plan(prs, conflict_edges):
    """Split `prs` into (lands_free, needs_rebase) given the conflict graph.

    `prs` is an ordered sequence of PR numbers -- order is the caller's landing
    preference (oldest first, or root-fix first) and is preserved, so the plan is
    reproducible rather than dependent on set iteration order.

    `conflict_edges` is an iterable of 2-tuples. Direction is ignored: a merge
    conflict is symmetric, and treating it otherwise would let `(a, b)` present
    as clean when `(b, a)` was measured.

    Soundness is checked, not assumed -- see the module docstring.
    """
    order = list(prs)
    known = set(order)
    if len(known) != len(order):
        raise RuntimeError("the same PR appears twice in the landing order")

    adjacency = {n: set() for n in order}
    for edge in conflict_edges:
        a, b = edge
        for side in (a, b):
            if side not in known:
                raise RuntimeError(
                    f"conflict edge {edge!r} names PR {side}, which is not in the "
                    f"PR set {sorted(known)} -- refusing to plan against a graph "
                    f"that does not match the queue"
                )
        if a == b:
            raise RuntimeError(f"conflict edge {edge!r} names one PR twice")
        adjacency[a].add(b)
        adjacency[b].add(a)

    # Greedy by ascending conflict degree, ties broken by the caller's order.
    # Fewest-collisions-first is what makes the triangle fall out correctly: the
    # two PRs that collide with nothing are taken before any triangle member, so
    # the single slot the triangle gets is spent last rather than wasted first.
    ranked = sorted(order, key=lambda n: (len(adjacency[n]), order.index(n)))

    lands_free = []
    for n in ranked:
        if adjacency[n].isdisjoint(lands_free):
            lands_free.append(n)

    lands_free.sort(key=order.index)
    needs_rebase = [n for n in order if n not in lands_free]

    for a, b in itertools.combinations(lands_free, 2):
        if b in adjacency[a]:
            raise RuntimeError(
                f"landing_plan returned an unsound set: #{a} and #{b} conflict but "
                f"both were placed in lands_free"
            )

    return lands_free, needs_rebase


def _run(cmd, cwd=None, check=True):
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc


def read_open_prs(repo, label):
    """Ask `gh` for the open PRs carrying `label`, oldest first."""
    proc = _run(
        [
            "gh", "pr", "list", "--repo", repo, "--label", label, "--state", "open",
            "--limit", "100", "--json", "number,headRefOid,createdAt",
        ]
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse the open-PR list as JSON: {exc}") from None
    data.sort(key=lambda p: p["createdAt"])
    return parse_open_prs(f"{p['number']} {p['headRefOid']}" for p in data)


def pr_ref(number):
    """The local ref `fetch_pr_heads` writes a PR head to, and the merges read.

    Its own namespace rather than a remote-tracking one: nothing else in the
    repo writes `refs/ffc/*`, so a stale ref here is this script's to overwrite
    and cannot be confused with a branch some other tool maintains.
    """
    return f"refs/ffc/pr{number}"


def verify_fetched_head(number, recorded, observed):
    """Raise unless the ref we just fetched is the SHA the PR list reported.

    This is the pin, and it is the whole reason the fetch records a SHA at all.
    `measure_conflicts` takes minutes on a set this size; a push to any member
    mid-run moves that PR's head, and without this check the plan would still be
    printed as current -- a measurement of commits that are no longer the ones
    anyone is asking about. Fail closed: the caller turns this into exit 2
    ("could not measure"), never into a clean plan.
    """
    if observed != recorded:
        raise RuntimeError(
            f"#{number} moved while being measured: the open-PR list reported "
            f"{recorded}, the fetched ref is {observed} -- re-run to measure the "
            f"current heads"
        )


def fetch_pr_heads(worktree, prs):
    """Populate `pr_ref(n)` for every PR in the set, pinned to its recorded SHA.

    Without this the merges below reference refs that never existed, `git merge`
    answers "not something we can merge", and the `first.returncode != 0` branch
    reports that as "#<a> does not merge into main" -- a missing ref rendered as
    a base conflict, for every PR, so the script always exits 2. That is how it
    came to ship having never once produced a landing plan (#1596 review).
    """
    for number, sha in prs:
        ref = pr_ref(number)
        _run(["git", "fetch", "origin", f"pull/{number}/head:{ref}", "-f"], cwd=worktree)
        observed = _run(["git", "rev-parse", ref], cwd=worktree).stdout.strip()
        verify_fetched_head(number, sha, observed)


def base_fetch_spec(base):
    """Split a remote-tracking base like `origin/main` into `("origin", "main")`.

    Returns `None` for any other shape -- a bare SHA, a tag, a local branch --
    which the caller must then treat as a commit it was handed deliberately
    rather than one this script is able to refresh.
    """
    if "/" not in base:
        return None
    remote, _, branch = base.partition("/")
    if not remote or not branch or "/" in branch:
        return None
    return remote, branch


def resolve_base(worktree, base):
    """Refresh `base` from its remote, then pin it to a concrete SHA.

    The heads of every PR in the set are fetched and then pinned by
    `verify_fetched_head`, for a reason the module docstring spells out: the
    measurement is only about the commits it says it is about. The base leg had
    neither half of that protection. `checkout_base` guards the case where
    `--base` fails to *resolve*; it cannot see the case where it resolves fine
    and is simply **old**, because the worktree's `origin/main` is only as
    current as the last `git fetch` somebody happened to run in it.

    That is the same `(base, headA, headB)` invalidation this script was written
    to expose, with two legs pinned and the third left to chance -- and it is
    the worse leg to lose, because the base is common to every pair. A base an
    hour behind real `main` yields a landing plan for a repository state that no
    longer exists, and every pair in it is wrong in the same direction, so the
    output stays internally consistent and reads as current. Found by Copilot on
    the third review pass of #1596.

    Two things happen here, and both matter:

    1. **Fetch**, so the base is current rather than whatever the caller's
       worktree last saw. Failing closed, because an unrefreshed base is exactly
       the defect being fixed -- it must not degrade to "measure anyway".
    2. **Pin to a SHA**, which is what gets returned and what every
       `checkout_base` is then given. Resolving once means all pairs are
       measured against one immutable commit even if `main` moves mid-run; with
       the symbolic name the later pairs would silently use a different base
       from the earlier ones.

    A base this script cannot refresh (a SHA or a local ref) is accepted and
    pinned, never silently fetched from somewhere it did not come from. The
    caller said which commit it wanted; `main` reports which one was used.
    """
    spec = base_fetch_spec(base)
    if spec is not None:
        remote, branch = spec
        r = _run(["git", "fetch", remote, branch], cwd=worktree, check=False)
        if r.returncode != 0:
            raise RuntimeError(
                f"could not fetch {branch} from {remote} in {worktree} (git exited "
                f"{r.returncode}): {(r.stderr or '').strip()[:300]} -- the base "
                f"would have been measured at whatever this worktree last saw, "
                f"which is the stale-base defect this fetch exists to prevent"
            )
    r = _run(["git", "rev-parse", base], cwd=worktree, check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"could not resolve {base} to a commit in {worktree} (git exited "
            f"{r.returncode}): {(r.stderr or '').strip()[:300]}"
        )
    return r.stdout.strip()


def checkout_base(worktree, base):
    """`git checkout -f --detach <base>` in `worktree`, failing closed.

    This ran with `check=False` and its return code was never inspected, unlike
    every `git merge` in `measure_conflicts`. A failed checkout -- `--base`
    misspelled, the worktree pointed somewhere unexpected, `origin/main` not
    fetched -- leaves HEAD on whatever the worktree was last left at, and the
    merge that follows then measures against the **wrong base**. Both answers
    the measurement can give are bogus in that case: `merges_into_base` returns
    a True/False about a base nobody asked about, and the pairwise loop builds
    its conflict graph on a stale HEAD. The script then prints a confident
    landing plan computed against the wrong commit, which is the one failure
    mode it exists to prevent.

    So this raises instead, and the raise is what `main()` turns into exit 2.
    """
    r = _run(
        ["git", "checkout", "-f", "--detach", base, "-q"], cwd=worktree, check=False
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"could not check out {base} in {worktree} (git exited "
            f"{r.returncode}): {(r.stderr or '').strip()[:300]} -- every merge "
            f"after this would measure against the wrong base"
        )


def base_merge_failures(numbers, merges_into_base):
    """Which PRs in the SET do not merge into `base` on their own.

    Split out of `measure_conflicts` and driven by `numbers` rather than by
    `itertools.combinations`, because combinations is **asymmetric** over its
    input: it never places the last element in the first position. The base
    check used to live on the `first` leg of each pair, so for
    `[a, b, c]` it ran on `a` twice and `b` once and on `c` **never**.

    The cost of that gap is not a missing warning, it is a *wrong answer*. A
    last-element PR that conflicts with `base` fails on the `second` leg of
    every pair it appears in, so it is recorded as a conflict edge against
    every other member of the SET -- a base conflict reported as N-1 content
    conflicts, which is exactly the misattribution this script exists to
    prevent. `read_open_prs` does not filter on `mergeableState`, so such a PR
    really does reach here.

    Checking every member once up front is also cheaper than the old placement:
    N base merges instead of one per pair.

    `merges_into_base(number) -> bool` is the seam, so the ordering logic is
    testable without git.
    """
    failures = [n for n in numbers if not merges_into_base(n)]
    return failures


def measure_conflicts(worktree, base, prs):
    """Real pairwise merges in `worktree`. Returns the conflict edge set.

    A real `git merge` in a worktree, never `git merge-tree` on a tree OID --
    that takes commits, and chaining its output reports CONFLICT for every pair
    convincingly enough to believe.

    Fetches the heads first: the merges read local refs, and nothing else in the
    process creates them.

    Resolves the base first too, and measures every pair against the returned
    SHA rather than against `base` as written. `resolve_base` says why: the base
    is the one leg common to every pair, so a stale one is wrong everywhere at
    once and still reads as a coherent plan.

    Returns `(base_sha, edges)` -- the plan is only meaningful next to the
    commit it was computed against, so the caller is given both and prints both.
    """
    base_sha = resolve_base(worktree, base)
    fetch_pr_heads(worktree, prs)
    numbers = [n for n, _ in prs]

    def merges_into_base(number):
        checkout_base(worktree, base_sha)
        r = _run(["git", "merge", "--no-edit", pr_ref(number)], cwd=worktree, check=False)
        if r.returncode != 0:
            _run(["git", "merge", "--abort"], cwd=worktree, check=False)
            return False
        return True

    unmergeable = base_merge_failures(numbers, merges_into_base)
    if unmergeable:
        listed = ", ".join(f"#{n}" for n in unmergeable)
        raise RuntimeError(
            f"{listed} do not merge into {base} ({base_sha[:8]}) on their own, so no "
            f"pair involving them can be measured -- rebase first"
        )

    edges = []
    for a, b in itertools.combinations(numbers, 2):
        checkout_base(worktree, base_sha)
        first = _run(["git", "merge", "--no-edit", pr_ref(a)], cwd=worktree, check=False)
        if first.returncode != 0:
            # Unreachable after the up-front sweep unless a head moved under us,
            # which verify_fetched_head is there to catch. Fail closed anyway
            # rather than recording a base conflict as a content edge.
            _run(["git", "merge", "--abort"], cwd=worktree, check=False)
            raise RuntimeError(
                f"#{a} merged into {base} ({base_sha[:8]}) during the base sweep but "
                f"not during the pairwise sweep -- a head moved mid-measurement"
            )
        second = _run(["git", "merge", "--no-edit", pr_ref(b)], cwd=worktree, check=False)
        if second.returncode != 0:
            edges.append((a, b))
            _run(["git", "merge", "--abort"], cwd=worktree, check=False)
    return base_sha, edges


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="FreeForCharity/FFC-Cloudflare-Automation")
    ap.add_argument("--label", default="agentic-os")
    ap.add_argument("--worktree", required=True, help="an existing worktree this may reset")
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args(argv)

    try:
        prs = read_open_prs(args.repo, args.label)
        if not prs:
            print(f"no open PRs labelled {args.label} -- nothing to plan")
            return 0
        base_sha, edges = measure_conflicts(args.worktree, args.base, prs)
        lands_free, needs_rebase = landing_plan([n for n, _ in prs], edges)
    except RuntimeError as exc:
        print(f"::error::could not compute a landing order: {exc}")
        return 2

    print(f"open PRs labelled {args.label}: {len(prs)}")
    print(f"measured against {args.base} = {base_sha}")
    print(f"conflict edges: {len(edges)}")
    for a, b in edges:
        print(f"  #{a} x #{b}: CONFLICT")
    print("lands free, in this order: " + ", ".join(f"#{n}" for n in lands_free))
    if needs_rebase:
        print("needs update-branch first: " + ", ".join(f"#{n}" for n in needs_rebase))
        print(
            "::warning::"
            f"{len(needs_rebase)} open PR(s) read clean against main but cannot land "
            f"without a rebase once their siblings land"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
