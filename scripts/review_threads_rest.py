#!/usr/bin/env python3
"""Decide AGENTS.md merge condition 3 -- "resolved review threads" -- over REST.

Canonical REST implementation of the thread half of the merge condition. A
landing sweep calls this rather than re-deriving the test, because the test has
exactly one dangerous failure mode and it is reachable by a one-word mistake.

Why this exists: the field is spelled differently in the two protocols
-------------------------------------------------------------------------
`main` requires resolved review threads, so every landing decision in this repo
reads them. The two routes that carry them do not agree on the field name:

    GraphQL   pullRequest.reviewThreads.nodes[].isResolved
    REST/CCR  GET /repos/{o}/{r}/pulls/{n}/ccr/review_threads -> [{"resolved": ...}]

`scripts/audit-open-pr-readiness.py` and `scripts/Test-PrMergeReadiness.ps1`
both read `isResolved`, correctly -- they are GraphQL scripts. But **GraphQL is
unavailable to a scheduled cloud worker** (the proxy refuses it with HTTP 403;
the class of session that runs every landing sweep is exactly the class that
cannot run those two scripts -- the same gap as #1686). So a worker reaching for
thread state necessarily ports the check to the CCR route, and the obvious port
carries the GraphQL spelling across.

That port fails silently, in the reassuring direction. A filter written

    [t for t in threads if t["isResolved"] is False]      # KeyError, loud
    [t for t in threads if t.get("isResolved") is False]  # always [], SILENT
    jq '.[] | select(.is_resolved == false)'              # always empty, SILENT

returns "no unresolved threads" for **every** PR, because `None is False` is
false and jq's `null == false` is false. Nothing errors, nothing is empty in a
way that looks wrong, and the verdict is "clean".

Measured on the 2026-11-10 cohort of 12 open `agentic-os` PRs: the `is_resolved`
spelling reported **0 unresolved threads on all 12**, including #1647, which has
a genuinely open thread at `.claude/hooks/guard_bash.py:2336` and is sitting at
`mergeable_state: blocked` because of it. So the mistake does not merely lose a
finding -- it promotes a PR that `main` itself refuses to merge into a "ready to
enqueue" punch list, which is the one error a landing sweep must not make.

This is the same family as CLAUDE.md's "a wrong `gh api` query-parameter NAME is
dropped in silence, and the answer looks plausible": a correct-looking read of a
real endpoint, answering a question nobody asked, flatteringly.

So the guard here is not the filter -- it is the **refusal**
------------------------------------------------------------
`unresolved_threads()` does not trust that it understood the payload. It asserts
the threads carry a recognised resolution key *before* filtering, and raises
`ThreadShapeError` when they do not. A caller therefore cannot receive `0` from
a payload this module failed to understand; it receives an exception naming the
keys it actually saw. The GraphQL spellings are detected by name so the message
can say which mistake was made rather than "unexpected shape".

Two further properties of the route, both measured 2026-11-10 and both load-
bearing for a caller:

  * **It is unpaginated.** The response is a flat JSON array with no `Link`
    header, carrying every thread (verified against #1586's 8 and #1596's 6).
    There is nothing to page, so a caller must not add page-walking logic whose
    absence would look like truncation.
  * **It silently ignores `per_page`.** `?per_page=2` on a PR with 8 threads
    returns all 8. So `per_page` is not a usable cap, and -- more to the point --
    a caller must not read a short array as "the first page", nor pass
    `per_page` believing it bounded the read.

`outdated` is reported but never used to excuse an unresolved thread. The
GraphQL helper `Test-PrMergeReadiness.ps1:320` skips `isOutdated` threads; this
module keeps them in the blocking set and surfaces the subset separately,
because GitHub's own "require conversation resolution" gate is what decides the
merge and an outdated-but-open thread has not been resolved. A caller that wants
the looser reading has `outdated_unresolved` to subtract; a caller that forgets
gets the stricter answer, which is the safe direction.

Pure functions, no network, no `gh`, no `subprocess` -- so the tests run offline
and on the Conductor's Windows host, where the bash-invoking modules in
`tests/workflow-logic/` are red for platform reasons (#1119). `main()` is the
only thing that touches the network.

Usage
-----
    python3 scripts/review_threads_rest.py --pr 1647
    python3 scripts/review_threads_rest.py --pr 1647 --json

Exit codes: 0 condition 3 is satisfied, 1 it is not, 2 the payload could not be
understood (never confused with "satisfied").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OWNER = "FreeForCharity"
REPO = "FFC-Cloudflare-Automation"

#: The key the REST/CCR route uses. Tested for by presence, never assumed.
REST_KEY = "resolved"

#: Spellings that mean the same thing on OTHER routes. Present here only so a
#: payload carrying one can be refused with a message that names the mistake,
#: rather than being filtered to a silent, plausible zero.
FOREIGN_KEYS = ("isResolved", "is_resolved", "isresolved")


class ThreadShapeError(Exception):
    """The payload was not a thread list this module understands.

    Raised instead of returning an empty unresolved set, because an empty set is
    indistinguishable from "all threads resolved" and that is the one answer a
    landing sweep acts on.
    """


def _threads(payload):
    """Normalise the response body to a list of thread dicts.

    The route returns a bare JSON array. An object wrapper (``{"review_threads":
    [...]}``) is accepted because a proxy or a future revision could add one, and
    guessing wrong there would be refused by the key check below anyway.
    """
    if isinstance(payload, dict):
        for key in ("review_threads", "reviewThreads", "threads"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            raise ThreadShapeError(
                "payload is a JSON object with no review-thread list in it; "
                "keys were %s" % sorted(payload)
            )
    if not isinstance(payload, list):
        raise ThreadShapeError(
            "payload is %s, not a list of threads" % type(payload).__name__
        )
    for i, thread in enumerate(payload):
        if not isinstance(thread, dict):
            raise ThreadShapeError(
                "thread %d is %s, not an object" % (i, type(thread).__name__)
            )
    return payload


def _assert_resolution_key(threads):
    """Refuse a payload whose threads do not carry ``resolved``.

    This is the whole point of the module. An empty thread list is fine -- a PR
    with no threads trivially satisfies condition 3 -- but a NON-empty list whose
    threads lack the key means the read did not measure what the caller thinks,
    and returning zero unresolved would be a false clean.
    """
    if not threads:
        return
    missing = [i for i, t in enumerate(threads) if REST_KEY not in t]
    if not missing:
        return
    seen = sorted({k for t in threads for k in t})
    foreign = [k for k in FOREIGN_KEYS if k in seen]
    if foreign:
        raise ThreadShapeError(
            "threads carry %s but not %r: that is the GraphQL/other-route "
            "spelling, and filtering on it silently reports every PR as "
            "thread-clean. Read %r from the REST/CCR route "
            "(pulls/{n}/ccr/review_threads). Keys seen: %s"
            % (" and ".join(repr(k) for k in foreign), REST_KEY, REST_KEY, seen)
        )
    raise ThreadShapeError(
        "%d of %d threads carry no %r key, so their resolution state was never "
        "read; refusing rather than reporting 0 unresolved. Keys seen: %s"
        % (len(missing), len(threads), REST_KEY, seen)
    )


def unresolved_threads(payload):
    """The threads that are not resolved. Raises rather than guessing.

    Only ``resolved is False`` counts as unresolved. A ``None`` resolution is an
    unread value, not a resolved one, so it is refused by the key check above
    when the key is absent and treated as unresolved when the key is present but
    null -- in both directions the module declines to call an unknown "clean".
    """
    threads = _threads(payload)
    _assert_resolution_key(threads)
    return [t for t in threads if t.get(REST_KEY) is not True]


def outdated_unresolved(payload):
    """The unresolved subset that GitHub marks ``outdated``.

    Reported so a caller can apply the looser GraphQL-helper reading
    deliberately. Not subtracted here -- see the module docstring.
    """
    return [t for t in unresolved_threads(payload) if t.get("outdated") is True]


def describe(thread):
    """One short line for a thread, for an operator-facing report."""
    path = thread.get("path") or "?"
    line = thread.get("line")
    where = "%s:%s" % (path, line) if line is not None else path
    ids = thread.get("comment_ids") or []
    flags = " (outdated)" if thread.get("outdated") is True else ""
    return "%s%s -- %d comment(s)" % (where, flags, len(ids))


def condition3(payload):
    """``(satisfied, reason)`` for AGENTS.md merge condition 3.

    Never returns ``True`` for a payload it could not understand; that path
    raises ``ThreadShapeError`` and is reported by ``main()`` as exit 2.
    """
    threads = _threads(payload)
    _assert_resolution_key(threads)
    open_threads = unresolved_threads(threads)
    if not threads:
        return True, "no review threads on this PR"
    if not open_threads:
        return True, "all %d review thread(s) resolved" % len(threads)
    stale = len([t for t in open_threads if t.get("outdated") is True])
    detail = "; ".join(describe(t) for t in open_threads)
    return False, "%d of %d review thread(s) unresolved (%d outdated): %s" % (
        len(open_threads),
        len(threads),
        stale,
        detail,
    )


def _get(path, token):
    req = urllib.request.Request(API + path)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def evaluate_pr(number, token, owner=OWNER, repo=REPO, get=_get):
    """Read one PR's threads over REST and classify condition 3.

    No `per_page` is sent: the route ignores it (measured), and passing it would
    imply a bound that does not exist.
    """
    payload = get(f"/repos/{owner}/{repo}/pulls/{number}/ccr/review_threads", token)
    threads = _threads(payload)
    satisfied, reason = condition3(threads)
    return {
        "pr": number,
        "threads": len(threads),
        "unresolved": [describe(t) for t in unresolved_threads(threads)],
        "outdated_unresolved": len(outdated_unresolved(threads)),
        "condition3": satisfied,
        "reason": reason,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", type=int, required=True, help="pull request number")
    parser.add_argument("--owner", default=OWNER)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN (or GITHUB_TOKEN) must be set", file=sys.stderr)
        return 2

    try:
        # `get=_get` is passed explicitly rather than left to `evaluate_pr`'s
        # default, which is bound at def time and therefore not substitutable.
        # Resolving it here, in `main`'s body, keeps the network the only thing
        # a test has to replace -- and a test that cannot replace it is a test
        # that silently makes live API calls, which is how this line was found.
        result = evaluate_pr(
            args.pr, token, owner=args.owner, repo=args.repo, get=_get
        )
    except ThreadShapeError as exc:
        print("PR #%d: payload NOT UNDERSTOOD -- %s" % (args.pr, exc), file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        print("PR #%d: HTTP %s reading threads" % (args.pr, exc.code), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("PR #%d" % result["pr"])
        print("  threads:   %d" % result["threads"])
        for line in result["unresolved"]:
            print("  OPEN:      %s" % line)
        print(
            "  condition 3: %s -- %s"
            % ("SATISFIED" if result["condition3"] else "NOT SATISFIED", result["reason"])
        )
    return 0 if result["condition3"] else 1


if __name__ == "__main__":
    sys.exit(main())
