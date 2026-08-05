"""Unit tests for scripts/audit-open-pr-readiness.py.

The audit exists because two different PR states look identical to CI and to a
human skimming a check list, and both mean "not mergeable":

  * a green PR with unresolved review threads (found 2026-08-04 on #1064/#825);
  * a green PR whose `Phantom Revert Guard` pass was measured before `main`
    moved (found 2026-08-05 on #1018/#1039/#1062, all 9 behind against a
    threshold of 5, all reported CLEAN).

So the tests here are almost entirely about the ways this script could produce a
**falsely clean** report, because that is the only failure that costs anything:
a false finding wastes a minute, a false clean re-buries the bug it was written
for.

Locked down here:
  * the threshold is read from `727-phantom-revert-guard.yml` and NOT hardcoded
    -- asserted by rewriting the fixture YAML's number and requiring the parse to
    follow it, which a hardcoded constant cannot pass;
  * a workflow stating no threshold, or two different ones, is INCOMPLETE and
    exits non-zero -- never "clean";
  * `behind == threshold` passes and `threshold + 1` fails, matching `-gt`
    exactly, so an off-by-one in either direction is caught;
  * a PENDING rollup is never called a stale green -- CI mid-run is an
    unfinished measurement, not a verdict, and flagging it would make the audit
    noisy in the way #992 warns about;
  * a PR that is BOTH stale and thread-blocked appears in both sets, because
    suppressing either hides half the work;
  * an unreadable branch comparison is a finding, not a pass;
  * the GraphQL read follows `$endCursor`, and aborts rather than truncating
    when a page claims a next page with no cursor (run 61's failure);
  * `has_findings` is asserted in-process, because a subprocess against a clean
    fixture only ever exercises the zero path and would pass against a `main`
    that returned 0 unconditionally (#912/#927).

No network: `_graphql` and `_request` are injected.

Run: python3 tests/workflow-logic/test_open_pr_readiness.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "audit-open-pr-readiness.py"
GUARD = ROOT / ".github" / "workflows" / "727-phantom-revert-guard.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("audit_open_pr_readiness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()

# Failures recorded by `check()` for the test function currently executing.
# `main()` turns each into exactly one `  PASS <name>` / `  FAIL <name>: ...`
# line, which is the roster convention `run_all.py` enforces -- a module that
# prints its own tally instead is indistinguishable from one that died halfway
# through with the PASSes already on stdout (L82).
_CURRENT = []


def check(name, cond, detail=""):
    if not cond:
        _CURRENT.append("%s%s" % (name, " -- " + detail if detail else ""))


def pr(number=1, rollup="SUCCESS", unresolved=0, behind=0, draft=True, title="t"):
    return {
        "number": number,
        "title": title,
        "is_draft": draft,
        "head": "b%d" % number,
        "labels": ["agentic-os"],
        "rollup": rollup,
        "unresolved": unresolved,
        "behind": behind,
    }


# ---------------------------------------------------------------- threshold


def test_threshold_is_parsed_not_hardcoded():
    """Rewrite the number in the YAML; a hardcoded constant cannot follow it."""
    yaml = 'if [ "$BEHIND_COUNT" -gt 5 ]; then\n'
    check("threshold parses 5", M.phantom_revert_threshold(yaml) == 5)
    moved = yaml.replace("-gt 5", "-gt 11")
    check(
        "threshold follows the workflow to 11",
        M.phantom_revert_threshold(moved) == 11,
        "a hardcoded 5 would fail here",
    )


def test_repeated_agreeing_thresholds_are_one_answer():
    yaml = (
        'if [ "$BEHIND_COUNT" -gt 5 ]; then\n'
        'if [ -n "$X" ] || [ "$BEHIND_COUNT" -gt 5 ]; then\n'
    )
    check("agreeing duplicates collapse", M.phantom_revert_threshold(yaml) == 5)


def test_disagreeing_thresholds_are_incomplete():
    yaml = (
        'if [ "$BEHIND_COUNT" -gt 5 ]; then\n'
        'if [ "$BEHIND_COUNT" -gt 9 ]; then\n'
    )
    try:
        M.phantom_revert_threshold(yaml)
        check("disagreeing thresholds are Incomplete", False, "no exception raised")
    except M.Incomplete:
        check("disagreeing thresholds are Incomplete", True)


def test_absent_threshold_is_incomplete_not_a_default():
    try:
        M.phantom_revert_threshold("jobs:\n  guard:\n    steps: []\n")
        check("absent threshold is Incomplete", False, "returned a value")
    except M.Incomplete:
        check("absent threshold is Incomplete", True)


def test_the_real_workflow_still_states_a_threshold():
    """If 727 is rewritten so this no longer parses, that is the finding."""
    text = GUARD.read_text(encoding="utf-8")
    try:
        t = M.phantom_revert_threshold(text)
        check("live 727 states a single threshold (got %r)" % t, isinstance(t, int))
    except M.Incomplete as exc:
        check("live 727 states a single threshold", False, str(exc))


# ------------------------------------------------------------- classification


def test_behind_equal_to_threshold_is_not_a_finding():
    r = M.classify([pr(behind=5)], 5)
    check("behind == threshold passes", not r["stale_green"] and r["ok"])


def test_behind_one_over_threshold_is_a_finding():
    r = M.classify([pr(behind=6)], 5)
    check("behind == threshold+1 is flagged", len(r["stale_green"]) == 1)


def test_the_run_97_case_is_flagged():
    """#1018 as it actually stood: green, CLEAN, no threads, 9 behind."""
    r = M.classify([pr(number=1018, behind=9, unresolved=0)], 5)
    check(
        "the run-97 case (green, 9 behind) is flagged",
        len(r["stale_green"]) == 1 and not r["unresolved_threads"],
    )


def test_pending_rollup_is_never_a_stale_green():
    r = M.classify([pr(rollup="PENDING", behind=99)], 5)
    check(
        "PENDING is not a stale green",
        not r["stale_green"],
        "CI mid-run is an unfinished measurement, not a verdict",
    )


def test_failing_rollup_is_not_reported_as_stale_green():
    r = M.classify([pr(rollup="FAILURE", behind=99)], 5)
    check("FAILURE is not a stale green", not r["stale_green"])


def test_unresolved_threads_on_a_green_pr_are_flagged():
    r = M.classify([pr(unresolved=2)], 5)
    check("green + unresolved threads is flagged", len(r["unresolved_threads"]) == 1)


def test_unresolved_threads_on_a_red_pr_are_not_the_finding():
    r = M.classify([pr(rollup="FAILURE", unresolved=2)], 5)
    check(
        "red + unresolved threads is not this audit's finding",
        not r["unresolved_threads"],
        "CI is the blocker there and it is visible",
    )


def test_a_pr_can_be_in_both_sets():
    r = M.classify([pr(behind=9, unresolved=1)], 5)
    check(
        "both blockers are reported, not just the first",
        len(r["stale_green"]) == 1 and len(r["unresolved_threads"]) == 1,
    )


def test_unreadable_comparison_is_a_finding_not_a_pass():
    r = M.classify([pr(behind=None)], 5)
    check(
        "behind=None is incomplete, not ok",
        len(r["incomplete"]) == 1 and not r["ok"],
    )


def test_a_clean_pr_set_has_no_findings():
    r = M.classify([pr(number=1, behind=0), pr(number=2, behind=5)], 5)
    check("a clean set is clean", not M.has_findings(r) and len(r["ok"]) == 2)


def test_has_findings_is_true_for_each_category():
    for key, sample in (
        ("stale_green", pr(behind=9)),
        ("unresolved_threads", pr(unresolved=1)),
        ("incomplete", pr(behind=None)),
    ):
        r = M.classify([sample], 5)
        check("has_findings fires on %s" % key, M.has_findings(r))


# ---------------------------------------------------------------- pagination


def graphql_pages(pages):
    calls = {"n": 0}

    def _call(query, variables, token):
        page = pages[calls["n"]]
        calls["n"] += 1
        return page

    return _call, calls


def node(number, has_thread_unresolved=0, rollup="SUCCESS"):
    return {
        "number": number,
        "title": "t%d" % number,
        "isDraft": True,
        "headRefName": "b%d" % number,
        "labels": {"nodes": [{"name": "agentic-os"}]},
        "reviewThreads": {
            "nodes": [{"isResolved": False}] * has_thread_unresolved
            + [{"isResolved": True}]
        },
        "commits": {
            "nodes": [
                {"commit": {"oid": "a" * 40, "statusCheckRollup": {"state": rollup}}}
            ]
        },
    }


def page(nodes, has_next=False, cursor=None):
    return {
        "repository": {
            "pullRequests": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": nodes,
            }
        }
    }


def test_pr_read_follows_the_cursor():
    call, calls = graphql_pages(
        [page([node(1)], has_next=True, cursor="c1"), page([node(2)])]
    )
    prs = M.fetch_open_prs("tok", graphql=call)
    check(
        "second page is followed",
        [p["number"] for p in prs] == [1, 2] and calls["n"] == 2,
    )


def test_pr_read_aborts_when_hasnextpage_has_no_cursor():
    call, _ = graphql_pages([page([node(1)], has_next=True, cursor=None)])
    try:
        M.fetch_open_prs("tok", graphql=call)
        check("truncated page aborts", False, "returned a partial list as complete")
    except M.Incomplete:
        check("truncated page aborts", True)


def test_unresolved_threads_are_counted_not_just_detected():
    call, _ = graphql_pages([page([node(1, has_thread_unresolved=3)])])
    prs = M.fetch_open_prs("tok", graphql=call)
    check("unresolved threads counted", prs[0]["unresolved"] == 3)


def test_a_missing_rollup_reads_as_none_not_as_a_crash():
    n = node(1)
    n["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
    call, _ = graphql_pages([page([n])])
    prs = M.fetch_open_prs("tok", graphql=call)
    check("absent rollup is None", prs[0]["rollup"] is None)


def test_fetch_behind_returns_none_on_a_bad_payload():
    def bad(url, token, accept=None):
        return {"unexpected": True}

    check(
        "a payload without behind_by yields None",
        M.fetch_behind({"head": "b"}, "tok", request=bad) is None,
    )


def test_fetch_behind_reads_the_number():
    def good(url, token, accept=None):
        return {"behind_by": 9, "ahead_by": 2}

    check("behind_by is read", M.fetch_behind({"head": "b"}, "tok", request=good) == 9)


def test_the_script_performs_no_writes():
    """A read-only audit must issue no mutating call.

    The first version of this test grepped the whole source for the word
    `mutation` and failed on its own docstring, which says the script performs
    none -- the fourth instance in this repo of a text-scanning check tripping
    over prose that describes it (see #1018's `_blank_quoted`). So assert on the
    two surfaces that can actually write instead of on the vocabulary:

      * the GraphQL document is a `query` operation and contains no `mutation`;
      * the only REST helper never passes a `data=` body, which is what would
        turn `urllib`'s implicit GET into a POST.
    """
    check(
        "the GraphQL document is a query, not a mutation",
        M.PR_QUERY.strip().startswith("query") and "mutation" not in M.PR_QUERY,
    )
    src = SCRIPT.read_text(encoding="utf-8")
    rest = src[src.index("def _request("):src.index("def _graphql(")]
    check(
        "the REST helper sends no body (so it cannot be a write)",
        "data=" not in rest and "add_data" not in rest,
    )


def main():
    print("open PR readiness audit (scripts/audit-open-pr-readiness.py):")
    failed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        del _CURRENT[:]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            _CURRENT.append("%s: %s" % (type(exc).__name__, exc))
        if _CURRENT:
            failed += 1
            print("  FAIL %s: %s" % (name, "; ".join(_CURRENT)))
        else:
            print("  PASS %s" % name)
    print("\n%d failed" % failed if failed else "\nall passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
