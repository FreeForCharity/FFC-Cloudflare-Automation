"""Unit tests for scripts/review_threads_rest.py.

The module decides AGENTS.md merge condition 3 ("resolved review threads") over
REST, because GraphQL is unavailable to the scheduled cloud worker -- the class
of session that runs every landing sweep (#1686 is the same gap for the
update-branch cap).

Almost every test here is about **one** failure: a falsely clean report. The two
protocols spell the field differently --

    GraphQL   reviewThreads.nodes[].isResolved
    REST/CCR  pulls/{n}/ccr/review_threads -> [{"resolved": ...}]

-- and the obvious port of the GraphQL check to the REST route carries the
GraphQL spelling with it. Filtering a REST payload on `isResolved`/`is_resolved`
matches nothing (`None is False` is false; jq's `null == false` is false), so it
reports every PR as thread-clean. Nothing raises and nothing looks empty in a
suspicious way.

Measured on the 2026-11-10 cohort of 12 open `agentic-os` PRs: the wrong
spelling reported 0 unresolved threads on all 12, including #1647 -- which has a
genuinely open thread at `.claude/hooks/guard_bash.py:2336` and is sitting at
`mergeable_state: blocked` because of it. A false finding costs a minute; a
false clean puts a PR that `main` refuses to merge onto a "ready to enqueue"
list.

So the contract under test is not "the filter works" -- a filter that returns []
for everything also passes that. It is that the module **refuses** a payload
whose resolution state it did not read:

  * a non-empty thread list with no `resolved` key raises `ThreadShapeError`,
    and `condition3` never returns True for it;
  * a payload carrying the GraphQL spelling is refused with a message that names
    that spelling, so the operator is told which mistake was made;
  * the refusal is distinguished from "satisfied" at the process boundary too --
    `main()` exits 2, not 0;
  * an EMPTY list is still satisfied, because a PR with no threads trivially
    passes and refusing it would make the guard useless;
  * `resolved: null` present-but-unread counts as unresolved, so an unknown is
    never promoted to clean in either direction.

Also locked down, both measured against the live route on 2026-11-10 and both
load-bearing for callers:

  * the route is unpaginated and `per_page` is silently ignored, so
    `evaluate_pr` must not send it -- asserted by inspecting the requested path,
    which a module that "helpfully" added a cap could not pass;
  * `outdated` never excuses an unresolved thread (the GraphQL helper
    `Test-PrMergeReadiness.ps1:320` skips outdated ones; the stricter reading is
    deliberate and is the safe direction), while the outdated subset stays
    separately reportable.

Fixtures are the real shapes returned by the live route, trimmed.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "review_threads_rest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("review_threads_rest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_module()

_CURRENT = []


def check(name, cond, detail=""):
    if not cond:
        _CURRENT.append("%s%s" % (name, " -- " + detail if detail else ""))


# --- fixtures: real payload shapes from pulls/{n}/ccr/review_threads ---------

#: #1647 on 2026-11-10: two resolved+outdated threads and one genuinely open.
PR1647 = [
    {
        "resolved": True,
        "outdated": True,
        "path": ".claude/hooks/guard_bash.py",
        "line": None,
        "comment_ids": [4247251711, 4248475147, 4309521522],
    },
    {
        "resolved": True,
        "outdated": True,
        "path": ".claude/hooks/guard_bash.py",
        "line": None,
        "comment_ids": [4344264694, 4344323810, 4348657315],
    },
    {
        "resolved": False,
        "outdated": False,
        "path": ".claude/hooks/guard_bash.py",
        "line": 2336,
        "comment_ids": [4512791137, 4513034955],
    },
]

#: #1586 on 2026-11-10: all threads resolved. The live PR carried EIGHT; two of
#: them are kept here verbatim (one outdated with a null line, one current with a
#: real line), because the assertions below are about the resolved/unresolved
#: split and the remaining six were identical in shape. The live count of 8 is
#: what the `per_page` test cites, and it is a fact about the route, not about
#: this fixture -- so the two numbers are deliberately different.
PR1586 = [
    {
        "resolved": True,
        "outdated": True,
        "path": ".github/workflows/112-dns-bulk-replace-a-ip.yml",
        "line": None,
        "comment_ids": [4233616803, 4233674617],
    },
    {
        "resolved": True,
        "outdated": False,
        "path": "docs/lessons-ledger.md",
        "line": 263,
        "comment_ids": [4236380416, 4236389717],
    },
]


def _graphql_shaped(threads):
    """The same threads as the GraphQL route would spell them."""
    out = []
    for t in threads:
        copy = {k: v for k, v in t.items() if k != "resolved"}
        copy["isResolved"] = t["resolved"]
        out.append(copy)
    return out


def _raises_shape_error(fn):
    try:
        fn()
    except M.ThreadShapeError:
        return True
    except Exception:  # noqa: BLE001 -- any other error is also a failure here
        return False
    return False


# --- the core contract: a payload it did not understand is REFUSED -----------


def test_the_graphql_spelling_is_refused_not_filtered_to_a_silent_zero():
    """The run-that-wrote-this-module mistake. Must raise, never return []."""
    payload = _graphql_shaped(PR1647)
    check(
        "unresolved_threads must refuse an isResolved payload",
        _raises_shape_error(lambda: M.unresolved_threads(payload)),
        "it returned a value instead of raising, which is the false clean",
    )
    check(
        "condition3 must refuse it too",
        _raises_shape_error(lambda: M.condition3(payload)),
    )


def test_the_refusal_message_names_the_foreign_spelling_it_found():
    """An operator must be told WHICH mistake was made, not 'unexpected shape'."""
    try:
        M.unresolved_threads(_graphql_shaped(PR1647))
    except M.ThreadShapeError as exc:
        text = str(exc)
        check("message names isResolved", "isResolved" in text, text)
        check("message names the right key", "'resolved'" in text, text)
        check("message names the route", "ccr/review_threads" in text, text)
    else:
        check("expected a ThreadShapeError", False)


def test_the_snake_case_spelling_is_refused_as_well():
    """`is_resolved` is the jq-filter form of the same mistake."""
    payload = [dict(t) for t in PR1647]
    for t in payload:
        t["is_resolved"] = t.pop("resolved")
    check(
        "is_resolved payload is refused",
        _raises_shape_error(lambda: M.condition3(payload)),
    )
    try:
        M.condition3(payload)
    except M.ThreadShapeError as exc:
        check("message names is_resolved", "is_resolved" in str(exc), str(exc))


def test_a_thread_list_with_no_resolution_key_at_all_is_refused():
    """Not every wrong shape carries a known foreign key; still never clean."""
    payload = [{"path": "a.py", "line": 1, "comment_ids": [1]}]
    check(
        "an unrecognised thread shape is refused",
        _raises_shape_error(lambda: M.condition3(payload)),
    )


def test_condition3_NEVER_returns_satisfied_for_an_unreadable_payload():
    """The property that matters, asserted directly over several bad shapes."""
    bad = [
        _graphql_shaped(PR1647),
        [{"path": "a.py"}],
        {"unexpected": "object"},
        "a string",
        42,
        [None],
        [["not", "a", "dict"]],
    ]
    for payload in bad:
        try:
            satisfied, _ = M.condition3(payload)
        except M.ThreadShapeError:
            continue
        except Exception as exc:  # noqa: BLE001
            check("bad payload raised the wrong error", False, repr(exc))
            continue
        check(
            "an unreadable payload must never be SATISFIED",
            not satisfied,
            "condition3(%r) returned satisfied=True" % (payload,),
        )


# --- and the guard must not be so strict that it is useless ------------------


def test_an_empty_thread_list_is_satisfied_not_refused():
    """A PR with no threads trivially passes condition 3."""
    satisfied, reason = M.condition3([])
    check("empty list is satisfied", satisfied, reason)
    check("reason says there are none", "no review threads" in reason, reason)


def test_an_all_resolved_payload_is_satisfied_and_counts_the_threads():
    satisfied, reason = M.condition3(PR1586)
    check("all-resolved is satisfied", satisfied, reason)
    check("reason counts them", "2 review thread(s) resolved" in reason, reason)


def test_a_real_open_thread_is_found_and_located():
    """#1647's live state: 1 of 3 open, and the report names where."""
    satisfied, reason = M.condition3(PR1647)
    check("an open thread blocks condition 3", not satisfied, reason)
    check("it counts 1 of 3", "1 of 3 review thread(s) unresolved" in reason, reason)
    check("it names the file and line", "guard_bash.py:2336" in reason, reason)
    open_threads = M.unresolved_threads(PR1647)
    check("exactly one unresolved", len(open_threads) == 1, str(len(open_threads)))


# --- the discrimination tests: these are what prove the filter is real -------


def test_flipping_the_one_open_thread_resolved_flips_the_verdict():
    """A mutation whose only change is the field this module claims to read.

    A module that ignored `resolved` -- or read the wrong key -- would give the
    same answer for both trees, which is the whole failure being guarded.
    """
    patched = [dict(t) for t in PR1647]
    patched[-1]["resolved"] = True
    satisfied, reason = M.condition3(patched)
    check("resolving the last thread satisfies condition 3", satisfied, reason)
    before, _ = M.condition3(PR1647)
    check("and the unpatched tree still does not", not before)


def test_flipping_a_resolved_thread_open_also_flips_the_verdict():
    """The other direction, so the test cannot pass by always reporting clean."""
    patched = [dict(t) for t in PR1586]
    patched[0]["resolved"] = False
    satisfied, reason = M.condition3(patched)
    check("one open thread is enough to block", not satisfied, reason)
    check("it counts 1 of 2", "1 of 2" in reason, reason)


def test_a_present_but_null_resolution_counts_as_unresolved():
    """`resolved: null` is an unread value, never a resolved one."""
    payload = [{"resolved": None, "outdated": False, "path": "a.py", "line": 2}]
    satisfied, reason = M.condition3(payload)
    check("null resolution is not clean", not satisfied, reason)


def test_a_truthy_non_true_resolution_is_not_accepted_as_resolved():
    """Only the boolean True resolves a thread; "true"/1 are not the API's shape."""
    for value in ("true", 1, "resolved", []):
        payload = [{"resolved": value, "outdated": False, "path": "a.py", "line": 2}]
        satisfied, _ = M.condition3(payload)
        check(
            "resolved=%r must not count as resolved" % (value,),
            not satisfied,
        )


# --- outdated is reported, never an excuse -----------------------------------


def test_an_outdated_unresolved_thread_still_blocks():
    """`Test-PrMergeReadiness.ps1:320` skips outdated; the strict reading is deliberate."""
    payload = [
        {"resolved": False, "outdated": True, "path": "a.py", "line": 1, "comment_ids": [1]}
    ]
    satisfied, reason = M.condition3(payload)
    check("outdated does not excuse an open thread", not satisfied, reason)
    check("the report marks it outdated", "(outdated)" in reason, reason)


def test_the_outdated_subset_stays_separately_reportable():
    """So a caller can apply the looser reading deliberately, not by accident."""
    payload = [
        {"resolved": False, "outdated": True, "path": "a.py", "line": 1},
        {"resolved": False, "outdated": False, "path": "b.py", "line": 2},
    ]
    check("2 unresolved", len(M.unresolved_threads(payload)) == 2)
    check("1 of them outdated", len(M.outdated_unresolved(payload)) == 1)


# --- the route's measured contract --------------------------------------------


def test_evaluate_pr_does_not_send_per_page_because_the_route_ignores_it():
    """Measured 2026-11-10: ?per_page=2 on an 8-thread PR returns all 8.

    Sending it would imply a bound that does not exist, and a short array must
    never be read as a first page.
    """
    seen = []

    def fake_get(path, token):
        seen.append(path)
        return PR1586

    M.evaluate_pr(1586, "t", get=fake_get)
    check("exactly one request", len(seen) == 1, str(seen))
    check("no per_page", "per_page" not in seen[0], seen[0])
    check("no page walk", "page=" not in seen[0], seen[0])
    check(
        "it hits the ccr route",
        seen[0] == "/repos/FreeForCharity/FFC-Cloudflare-Automation/pulls/1586/ccr/review_threads",
        seen[0],
    )


def test_evaluate_pr_reports_the_open_threads_it_found():
    result = M.evaluate_pr(1647, "t", get=lambda path, token: PR1647)
    check("condition3 false", result["condition3"] is False, str(result))
    check("3 threads", result["threads"] == 3, str(result))
    check("1 unresolved listed", len(result["unresolved"]) == 1, str(result))
    check(
        "the listing locates it",
        "guard_bash.py:2336" in result["unresolved"][0],
        str(result),
    )


def test_evaluate_pr_honours_owner_and_repo_overrides():
    seen = []
    M.evaluate_pr(
        7, "t", owner="o", repo="r", get=lambda path, token: (seen.append(path) or [])
    )
    check("override used", seen[0] == "/repos/o/r/pulls/7/ccr/review_threads", str(seen))


def test_an_object_wrapper_around_the_list_is_accepted():
    """A proxy or future revision could wrap it; guessing wrong is refused anyway."""
    satisfied, reason = M.condition3({"review_threads": PR1586})
    check("wrapped all-resolved is satisfied", satisfied, reason)
    check(
        "a wrapper with no list is refused",
        _raises_shape_error(lambda: M.condition3({"total_count": 3})),
    )


# --- the process boundary: refusal is exit 2, never exit 0 -------------------


def _restoring_token(value="t"):
    """Set GH_TOKEN and hand back a restorer for the PREVIOUS value.

    The module runs its own in-process runner, so a test that leaves GH_TOKEN
    mutated makes later tests order-dependent -- and in this file the very next
    test by sort order is the one that asserts behaviour when the token is
    ABSENT. Restoring the prior value (including its absence) keeps each test's
    environment its own.
    """
    had = "GH_TOKEN" in M.os.environ
    previous = M.os.environ.get("GH_TOKEN")
    M.os.environ["GH_TOKEN"] = value

    def restore():
        if had:
            M.os.environ["GH_TOKEN"] = previous
        else:
            M.os.environ.pop("GH_TOKEN", None)

    return restore


def test_main_exits_2_on_an_unreadable_payload_and_0_only_when_satisfied():
    """"Could not tell" and "satisfied" must not share an exit code."""
    restore_token = _restoring_token()
    original = M._get
    try:
        M._get = lambda path, token: _graphql_shaped(PR1647)
        check("bad payload exits 2", M.main(["--pr", "1647"]) == 2)
        M._get = lambda path, token: PR1647
        check("open thread exits 1", M.main(["--pr", "1647"]) == 1)
        M._get = lambda path, token: PR1586
        check("all resolved exits 0", M.main(["--pr", "1586"]) == 0)
        M._get = lambda path, token: []
        check("no threads exits 0", M.main(["--pr", "1"]) == 0)
    finally:
        M._get = original
        restore_token()


def test_main_exits_2_on_a_TRANSPORT_failure_not_1():
    """A read that never happened must not render as "threads unresolved".

    `HTTPError` subclasses `URLError`, so the HTTP clause alone left a timeout,
    DNS failure or connection reset uncaught -- and Python exits 1 for an
    uncaught exception, which this module defines as "condition 3 not
    satisfied". Reproduced on this PR before the fix: `main()` raised
    `URLError` instead of returning anything.
    """
    restore_token = _restoring_token()
    original = M._get
    try:
        for exc in (
            M.urllib.error.URLError("connection reset"),
            TimeoutError("read timed out"),
            OSError("network unreachable"),
        ):

            def boom(path, token, _exc=exc):
                raise _exc

            M._get = boom
            try:
                rc = M.main(["--pr", "1647"])
            except Exception as leaked:  # noqa: BLE001
                check(
                    "%s must not escape main()" % type(exc).__name__,
                    False,
                    "uncaught %s: %s" % (type(leaked).__name__, leaked),
                )
                continue
            check(
                "%s must exit 2, not 1" % type(exc).__name__,
                rc == 2,
                "got %r -- 1 would mean 'threads unresolved'" % (rc,),
            )
    finally:
        M._get = original
        restore_token()


def test_main_exits_2_on_a_MALFORMED_body_not_1():
    """A body that is not valid UTF-8, or truncated JSON, is "could not tell".

    `_get` ends in `json.loads(fh.read().decode("utf-8"))`. Both failures
    subclass `ValueError` and neither is an `OSError`, so the transport clause
    does not reach them. Reproduced before the fix: `main()` raised and Python
    exited 1, which this module defines as "threads unresolved".
    """
    restore_token = _restoring_token()
    original = M._get
    try:
        for exc in (
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            M.json.JSONDecodeError("Expecting value", '[{"resolved"', 12),
        ):

            def boom(path, token, _exc=exc):
                raise _exc

            M._get = boom
            try:
                rc = M.main(["--pr", "1727"])
            except Exception as leaked:  # noqa: BLE001
                check(
                    "%s must not escape main()" % type(exc).__name__,
                    False,
                    "uncaught %s" % type(leaked).__name__,
                )
                continue
            check(
                "%s must exit 2, not 1" % type(exc).__name__,
                rc == 2,
                "got %r" % (rc,),
            )
    finally:
        M._get = original
        restore_token()


def test_exit_1_is_reachable_ONLY_from_a_completed_evaluation():
    """The structural property, asserted over an exception type nobody listed.

    Two review findings on this PR were each "another exception type exits 1":
    transport, then decode. Enumerating types cannot close that class, because
    exit 1 is this module's word for the VERDICT "some threads are unresolved".
    So any failure to complete must be 2, and this test uses a bespoke exception
    class so it cannot be satisfied by adding one more `except` clause.
    """

    class SomethingNobodyAnticipated(Exception):
        pass

    restore_token = _restoring_token()
    original = M._get
    try:
        for exc in (
            SomethingNobodyAnticipated("surprise"),
            KeyError("parents"),
            AttributeError("code"),
            RecursionError("deep"),
        ):

            def boom(path, token, _exc=exc):
                raise _exc

            M._get = boom
            try:
                rc = M.main(["--pr", "1727"])
            except Exception as leaked:  # noqa: BLE001
                check(
                    "%s must not escape main()" % type(exc).__name__,
                    False,
                    "uncaught %s" % type(leaked).__name__,
                )
                continue
            check(
                "%s must exit 2, never 1" % type(exc).__name__,
                rc == 2,
                "got %r -- 1 would assert a verdict from a read that failed" % (rc,),
            )
        # and the converse: 1 must still be reachable, or the above is vacuous
        M._get = lambda path, token: PR1647
        check("a real unresolved thread still exits 1", M.main(["--pr", "1647"]) == 1)
    finally:
        M._get = original
        restore_token()


def test_get_sends_the_repo_standard_headers_including_a_User_Agent():
    """GitHub throttles unidentified clients harder, and a throttled read here
    is a read that failed -- which this module must never let look like a
    verdict. Four other REST callers in `scripts/` already send a `User-Agent`;
    this one did not until review on #1727 caught it.

    Also pins that the agent is a generic TOOL name: it identifies the script,
    never the operator, so the header cannot leak who ran the sweep.
    """
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        # urllib normalises header names to Capitalised-Form in this dict.
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return FakeResponse()

    original = M.urllib.request.urlopen
    try:
        M.urllib.request.urlopen = fake_urlopen
        out = M._get("/repos/o/r/pulls/1/ccr/review_threads", "tok")
    finally:
        M.urllib.request.urlopen = original

    check("the body is parsed", out == [], repr(out))
    h = seen.get("headers", {})
    check("User-Agent is sent", "user-agent" in h, str(sorted(h)))
    check(
        "User-Agent is the module's constant",
        h.get("user-agent") == M.USER_AGENT,
        repr(h.get("user-agent")),
    )
    check(
        "User-Agent names the tool, not a person",
        M.USER_AGENT.startswith("ffc-") and "@" not in M.USER_AGENT,
        M.USER_AGENT,
    )
    check(
        "Authorization is a bearer token, not a literal",
        h.get("authorization") == "Bearer tok",
        repr(h.get("authorization")),
    )
    check("Accept pins the v3 JSON media type",
          h.get("accept") == "application/vnd.github+json", repr(h.get("accept")))
    check("the API version is pinned",
          h.get("x-github-api-version") == "2022-11-28",
          repr(h.get("x-github-api-version")))
    check("a timeout is set", seen.get("timeout") == 60, repr(seen.get("timeout")))
    check(
        "the request goes to api.github.com",
        seen.get("url", "").startswith("https://api.github.com/"),
        repr(seen.get("url")),
    )


def test_each_failure_class_is_NAMED_distinctly_on_stderr():
    """The specific `except` clauses exist for their messages, so pin them.

    With the catch-all backstop in place, deleting any one specific clause
    leaves the exit code at 2 -- so an exit-code test cannot tell the clauses
    apart, and removing one is an equivalent mutant. Measured: deleting the
    `ValueError` clause left the whole suite green. An operator debugging a
    broken sweep needs "unreadable response body" distinguished from "transport
    failure" and from "payload NOT UNDERSTOOD", so that distinction is the
    contract these clauses actually carry, and this is the test that holds it.
    """
    import contextlib
    import io as _io

    cases = [
        (M.urllib.error.URLError("reset"), "transport failure"),
        (
            M.json.JSONDecodeError("Expecting value", '[{"resolved"', 12),
            "unreadable response body",
        ),
        (KeyError("surprise"), "could not evaluate"),
    ]
    restore_token = _restoring_token()
    original = M._get
    try:
        for exc, expected in cases:

            def boom(path, token, _exc=exc):
                raise _exc

            M._get = boom
            err = _io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = M.main(["--pr", "1727"])
            text = err.getvalue()
            check("%s exits 2" % type(exc).__name__, rc == 2, "got %r" % (rc,))
            check(
                "%s is reported as %r" % (type(exc).__name__, expected),
                expected in text,
                "stderr was %r" % (text.strip(),),
            )
        # the shape-refusal message must stay distinct from all three above
        M._get = lambda path, token: _graphql_shaped(PR1647)
        err = _io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = M.main(["--pr", "1727"])
        check("a shape refusal exits 2", rc == 2, "got %r" % (rc,))
        check(
            "a shape refusal says NOT UNDERSTOOD",
            "NOT UNDERSTOOD" in err.getvalue(),
            "stderr was %r" % (err.getvalue().strip(),),
        )
    finally:
        M._get = original
        restore_token()


def test_an_HTTP_error_is_still_reported_as_HTTP_not_as_transport():
    """The except-clause ORDER is load-bearing: HTTPError subclasses URLError.

    Catching `URLError` first would report every server response as a transport
    failure, losing the status code from the message.
    """
    restore_token = _restoring_token()
    original = M._get
    try:

        def not_found(path, token):
            raise M.urllib.error.HTTPError(
                "https://api.github.com" + path, 404, "Not Found", {}, None
            )

        M._get = not_found
        check("HTTP error exits 2", M.main(["--pr", "1647"]) == 2)
    finally:
        M._get = original
        restore_token()


def test_no_test_in_this_module_leaves_GH_TOKEN_mutated():
    """The leak the review found, asserted as a property of the whole module.

    Written as its own test because the fix is a convention every future test
    has to follow, and a convention with nothing checking it is prose.

    This runs each sibling test and compares the GH_TOKEN state after it against
    the state before -- the behaviour itself, not the source text. The first
    draft of this guard DID scan the source, and it failed on its own string
    literals, which is a fair warning about what source-scraping measures.
    """
    me = "test_no_test_in_this_module_leaves_GH_TOKEN_mutated"
    had = "GH_TOKEN" in M.os.environ
    previous = M.os.environ.get("GH_TOKEN")
    saved = list(_CURRENT)
    leak = None

    def state():
        return ("GH_TOKEN" in M.os.environ, M.os.environ.get("GH_TOKEN"))

    try:
        for name, fn in sorted(globals().items()):
            if name == me or not (name.startswith("test_") and callable(fn)):
                continue
            # Force a KNOWN, DISTINCT state before each sibling, and snapshot
            # per sibling rather than once at the top. Two earlier drafts of
            # this guard were both measurably blind, and the second failure is
            # the instructive one:
            #
            #   1. a single snapshot taken when this guard started cannot see a
            #      leak that happened earlier in the run -- which is the common
            #      case, since the leaking test sorts ahead of this one;
            #   2. snapshotting per sibling still cannot see it, because
            #      `_restoring_token` always sets the SAME value. Once a leak
            #      has left GH_TOKEN="t", a sibling that sets "t" and fails to
            #      restore changes nothing, so before == after.
            #
            # Clearing it first makes "set and restored" distinguishable from
            # "set and leaked". Both blind drafts were caught by reverting the
            # fix and finding the suite still green -- a test that cannot fail
            # is worth less than no test, because it reads as coverage.
            M.os.environ.pop("GH_TOKEN", None)
            before = state()
            del _CURRENT[:]
            try:
                fn()
            except Exception:  # noqa: BLE001 -- that test reports its own result
                pass
            if state() != before:
                after_had, after = state()
                was_had, was = before
                leak = "%s left GH_TOKEN=%s (was %s)" % (
                    name,
                    repr(after) if after_had else "<unset>",
                    repr(was) if was_had else "<unset>",
                )
                break
    finally:
        # Restore the environment and this module's finding buffer before
        # reporting, so one leak cannot cascade into every later test.
        if had:
            M.os.environ["GH_TOKEN"] = previous
        else:
            M.os.environ.pop("GH_TOKEN", None)
        del _CURRENT[:]
        _CURRENT.extend(saved)

    check("no sibling test leaks GH_TOKEN", leak is None, leak or "")


def test_main_refuses_without_a_token_rather_than_reporting_clean():
    saved = {k: M.os.environ.pop(k) for k in ("GH_TOKEN", "GITHUB_TOKEN") if k in M.os.environ}
    try:
        check("missing token exits 2", M.main(["--pr", "1"]) == 2)
    finally:
        M.os.environ.update(saved)


# --- the module must not reintroduce the spelling it exists to prevent -------


def test_the_implementation_does_not_FILTER_on_a_foreign_spelling():
    """Source-level guard: the foreign keys may only appear in the refusal list.

    A refactor that started reading `isResolved` as a fallback would make the
    module silently permissive again, and every behavioural test above would
    still pass on the REST fixtures.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('FOREIGN_KEYS = (', 1)[1].split(")", 1)[1]
    for key in ("isResolved", "is_resolved"):
        check(
            "%s must not be read outside FOREIGN_KEYS" % key,
            ('"%s"' % key) not in body and ("'%s'" % key) not in body,
            "found a literal %r in the implementation body" % key,
        )
    check(
        "REST_KEY is the documented REST spelling",
        M.REST_KEY == "resolved",
        M.REST_KEY,
    )


def main():
    print("REST review-thread condition 3 (scripts/review_threads_rest.py):")
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
