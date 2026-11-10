#!/usr/bin/env python3
"""Decide AGENTS.md merge condition 2: has Copilot really reviewed this head?

Canonical implementation of the clause in AGENTS.md "Who merges" -> "The merge
condition" -> item 2. Anything that needs the answer (a landing sweep, the
condition-2 check #1648 wants in `audit-open-pr-readiness.py`) calls this rather
than re-deriving the body test, because the body test is the part that has
already been wrong twice in opposite directions.

Why this is a module and not three lines at each call site
----------------------------------------------------------
The rule used to read "its body must open with `<!-- ccr-overview-v2 -->`".
Copilot began prefixing genuine, complete reviews with a tier banner:

    > [!NOTE]
    > Copilot was unable to complete this review at the selected tier due to
    > model policy or plan restrictions.

    <!-- ccr-overview-v2 -->

    ### 🟢 Approval recommended

The marker is still there, at offset 119 instead of 0. A `startswith` test calls
that a refusal. Measured on 2026-11-06 (#1721) it was wrong on **2 of 12** open
`agentic-os` PRs, both of them 🟢 `Approval recommended` with every thread
resolved -- i.e. it blocked exactly the PRs that were ready. Re-measured on
2026-11-09 the banner was on **10 of 10** reviewed PRs in the same cohort, so the
defect went from occasional to total in three days.

The failure direction is what makes it expensive rather than noisy:

1. It holds landable PRs, reporting "not reviewed -- do not enqueue".
2. It spends the capped re-request budget (#1645 caps it at 3/run) on PRs that
   need nothing.
3. A re-request on a PR that already holds a real review can REPLACE it with a
   refusal, so the false negative can destroy the asset it was trying to obtain.

So the body test is `contains`, never `startswith`, and the check run -- not the
prose -- is what decides refusal.

The check run is the primary signal
-----------------------------------
`AGENTS.md` already names the `copilot-pull-request-reviewer` check run on the
head SHA as the read that distinguishes "not armed" from "wait" from "done",
because `requested_reviewers` is empty both before arming and after delivery.
This module keeps that precedence: a `failure`/`absent` check run is a refusal
whatever the body says, and a `success` check run is never overridden by prose
that merely looks like an error. On the 12-PR cohort of #1721 the check-run
conclusion agreed with the true state **12/12**; `startswith` on the body
disagreed on 2.

Pure functions, no network, no `gh`, no `subprocess` -- so the tests run
offline and on the Conductor's Windows host, where the bash-invoking modules in
`tests/workflow-logic/` are red for platform reasons (#1119). `main()` is the
only thing that touches the network.

Usage
-----
    python3 scripts/copilot_review_condition2.py --pr 1720
    python3 scripts/copilot_review_condition2.py --pr 1720 --json

Exit codes: 0 condition 2 is satisfied, 1 it is not (including "wait").
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

REVIEWER_LOGIN = "copilot-pull-request-reviewer[bot]"

#: The marker a real Copilot review body carries. It is tested for with
#: ``in``, never ``startswith`` -- see the module docstring.
OVERVIEW_MARKER = "<!-- ccr-overview-v2 -->"

#: Substrings that identify Copilot's genuine refusal bodies, matched
#: case-insensitively. These are a SECONDARY signal only: a refusal also shows
#: up as a ``failure`` or absent check run, which is what this module decides
#: on. They are kept so that a refusal arriving with an unexpectedly ``success``
#: check run is still caught, and so the reported reason names the cause.
#:
#: Each is anchored on "Copilot" on purpose. An earlier draft matched the bare
#: fragment ``"encountered an error"``, which a *real* review can say in its own
#: prose -- a review of error-handling code especially -- and that would have
#: turned a genuine review into a refusal. Anchoring costs nothing: every
#: refusal body measured on this repo names Copilot as the subject.
REFUSAL_SUBSTRINGS = (
    "copilot wasn't able to review",
    "copilot was not able to review",
    "copilot encountered an error",
)

#: The tier-degradation banner, matched case-insensitively. Its presence means
#: the review ran at a lower effort tier -- it does NOT mean the review is
#: missing. Recorded so that a caller can surface the degradation without
#: treating it as a refusal.
TIER_NOTICE_SUBSTRING = "unable to complete this review at the selected tier"

# Verdicts.
SATISFIED = "SATISFIED"  # a real review on the head; condition 2 holds
WAIT = "WAIT"  # queued / in_progress -- not an answer yet
NOT_ARMED = "NOT_ARMED"  # no check run on this head
REFUSED = "REFUSED"  # check run failure, or a refusal body
NO_REVIEW = "NO_REVIEW"  # check run success but no usable review body


def _says(body, substring):
    """Case-insensitive substring test, so capitalisation drift cannot flip a
    verdict. The marker itself is never matched this way -- it is an HTML
    comment emitted by a machine, and an exact match is correct for it."""
    return bool(body) and substring in body.casefold()


def body_is_refusal(body):
    """True when ``body`` is one of Copilot's refusal bodies.

    Only meaningful when the marker is ABSENT. Every refusal measured on this
    repo -- the prompt-size refusal (#1596, 18x) and the generic error body
    (#1589) -- carries no ``<!-- ccr-overview-v2 -->``, so the marker's presence
    is the stronger signal and ``body_is_real_review`` does not consult this.
    """
    return any(_says(body, s) for s in REFUSAL_SUBSTRINGS)


def body_is_real_review(body):
    """True when ``body`` is a real Copilot review body.

    ``contains``, not ``startswith``: the tier banner legitimately precedes the
    marker.

    The marker alone decides. An earlier draft also required that no refusal
    phrase appear, which inverts the evidence: a refusal body has no marker, so
    the conjunction only ever fired on a *real* review that happened to discuss
    an error in its prose. The marker is machine-emitted and a refusal never
    carries it, so its presence is sufficient. Raised by Copilot on #1725.
    """
    return bool(body) and OVERVIEW_MARKER in body


def body_is_tier_degraded(body):
    """True when the review ran at a reduced effort tier. Not a refusal."""
    return _says(body, TIER_NOTICE_SUBSTRING)


def classify(check_status, check_conclusion, body):
    """Return ``(verdict, reason)`` for one head SHA.

    ``check_status`` is the ``copilot-pull-request-reviewer`` check run's status
    on the head SHA, or ``None`` when no such check run exists. The check run is
    the primary signal; ``body`` is consulted only once it reports success.
    """
    if check_status is None:
        return NOT_ARMED, "no copilot-pull-request-reviewer check run on this head"
    if check_status != "completed":
        return WAIT, f"check run is {check_status}"
    if check_conclusion != "success":
        return REFUSED, f"check run concluded {check_conclusion}"
    # Check run says success. Only now does the body matter, and only to confirm
    # a usable review is actually attached.
    if body_is_real_review(body):
        if body_is_tier_degraded(body):
            return SATISFIED, "real review on head (degraded effort tier)"
        return SATISFIED, "real review on head"
    if body_is_refusal(body):
        return REFUSED, "refusal body despite a success check run"
    return NO_REVIEW, f"success check run but no {OVERVIEW_MARKER} body on this head"


# --------------------------------------------------------------------------
# Live read (the only part that touches the network)
# --------------------------------------------------------------------------


def select_check_run(runs):
    """Pick the run that represents the state of the reviewer, or ``None``.

    A run that has not completed WINS, because "a review is in flight" is the
    answer whenever one is. Sorting by ``started_at`` and taking the last is
    wrong twice over: a queued run has ``started_at: null``, which sorts to the
    FRONT, so the newest run is discarded and an older ``completed/success``
    beside it is reported as the state -- `SATISFIED` while a review is still
    running, which is the one verdict that lets a PR be enqueued too early.
    Raised by Copilot on #1725 and reproduced before the fix.
    """
    mine = [c for c in runs if c.get("name") == "copilot-pull-request-reviewer"]
    if not mine:
        return None
    pending = [c for c in mine if c.get("status") != "completed"]
    if pending:
        # Any in-flight run is the answer; among several, report the newest.
        return max(pending, key=lambda c: c.get("started_at") or "")
    return max(
        mine, key=lambda c: (c.get("completed_at") or "", c.get("started_at") or "")
    )


def review_commit_is_acceptable(reviewed_sha, head_sha, pr_commits, is_base_ancestor):
    """Does a review on ``reviewed_sha`` still count for ``head_sha``?

    AGENTS.md condition 2 accepts a review whose ``commit_id`` is the head **or
    an earlier commit when everything since is merges of `main`**. Requiring an
    exact head match drops that clause, and the clause is the common case in
    this repo: a PR updated with ``update-branch`` gains a merge commit and
    keeps a perfectly good review, so an exact-match reading reports
    ``NO_REVIEW`` on a PR whose review is valid per the rule this module claims
    to implement. Raised by Copilot on #1725.

    ``pr_commits`` is the PR's commits oldest-first, each ``{"sha", "parents"}``.
    ``is_base_ancestor(sha)`` says whether ``sha`` is contained in the base
    branch -- that is what makes a merge commit a merge *of `main`* rather than
    of some other branch, which would carry code the review never saw.
    """
    if reviewed_sha == head_sha:
        return True, "review is on the head"
    shas = [c["sha"] for c in pr_commits]
    if reviewed_sha not in shas:
        return False, "the reviewed commit is not in this PR's history"
    for commit in pr_commits[shas.index(reviewed_sha) + 1 :]:
        parents = [
            p["sha"] if isinstance(p, dict) else p for p in commit.get("parents", [])
        ]
        if len(parents) < 2:
            return False, f"{commit['sha'][:8]} after the review is not a merge commit"
        # EVERY non-first parent must be contained in the base branch, not just
        # one of them. `any()` credits an octopus merge -- parents
        # [prev, base, feature] -- because the base parent satisfies it, while
        # the feature parent brought code the review never saw. That is the
        # exact hole this function exists to close, and the docstring already
        # said "merges of the base branch". Raised by Copilot on #1725.
        outside = [p for p in parents[1:] if not is_base_ancestor(p)]
        if outside:
            return (
                False,
                f"{commit['sha'][:8]} merges {outside[0][:8]}, which is not in the base branch",
            )
    return True, "review predates only merges of the base branch"


def _get(path, token):
    req = urllib.request.Request(API + path)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def evaluate_pr(number, token, owner=OWNER, repo=REPO, get=_get):
    """Classify condition 2 for one open PR, reading only REST."""
    pr = get(f"/repos/{owner}/{repo}/pulls/{number}", token)
    head = pr["head"]["sha"]
    base_ref = pr["base"]["ref"]

    runs = get(
        f"/repos/{owner}/{repo}/commits/{head}/check-runs?per_page=100", token
    ).get("check_runs", [])
    chosen = select_check_run(runs)
    status = chosen["status"] if chosen else None
    conclusion = chosen.get("conclusion") if chosen else None

    # The PR's own commits, oldest-first, for the "everything since is merges of
    # `main`" clause. 250 is GitHub's cap on this endpoint.
    pr_commits, page = [], 1
    while page <= 3:
        batch = get(
            f"/repos/{owner}/{repo}/pulls/{number}/commits?per_page=100&page={page}",
            token,
        )
        if not batch:
            break
        pr_commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    _ancestor_cache = {}

    def is_base_ancestor(sha):
        """Is ``sha`` contained in the base branch? `compare` answers it: a
        base...sha comparison of `identical` or `behind` means sha is reachable
        from the base ref."""
        if sha not in _ancestor_cache:
            try:
                cmp = get(f"/repos/{owner}/{repo}/compare/{base_ref}...{sha}", token)
                _ancestor_cache[sha] = cmp.get("status") in ("identical", "behind")
            except urllib.error.HTTPError:
                # Cannot say -> do not credit the merge. Failing closed costs a
                # re-review; failing open credits a merge of an arbitrary
                # branch whose code the review never saw.
                _ancestor_cache[sha] = False
        return _ancestor_cache[sha]

    # Reviews are paginated and the history on a long-lived PR exceeds one page;
    # a single-page read understates it (#1596 recorded exactly that). Collect
    # every Copilot review, then take the newest ACCEPTABLE one.
    found, page = [], 1
    while page <= 10:
        batch = get(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews?per_page=100&page={page}",
            token,
        )
        if not batch:
            break
        for r in batch:
            if r.get("user", {}).get("login") == REVIEWER_LOGIN:
                found.append((r.get("commit_id"), r.get("body") or ""))
        if len(batch) < 100:
            break
        page += 1

    body, on_commit, why_commit = None, None, "no Copilot review on this PR"
    for sha, candidate in reversed(found):
        ok, why = review_commit_is_acceptable(sha, head, pr_commits, is_base_ancestor)
        if ok:
            body, on_commit, why_commit = candidate, sha, why
            break

    verdict, reason = classify(status, conclusion, body)
    return {
        "pr": number,
        "head": head,
        "review_commit": on_commit,
        "review_commit_basis": why_commit,
        "check_status": status,
        "check_conclusion": conclusion,
        "tier_degraded": body_is_tier_degraded(body),
        "verdict": verdict,
        "reason": reason,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", type=int, required=True, help="pull request number")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--owner", default=OWNER)
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN (or GITHUB_TOKEN) must be set", file=sys.stderr)
        return 1

    try:
        result = evaluate_pr(args.pr, token, owner=args.owner, repo=args.repo)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} reading PR #{args.pr}: {e.reason}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"PR #{result['pr']}  head {result['head'][:8]}")
        print(f"  check run: {result['check_status']}/{result['check_conclusion']}")
        rc = result["review_commit"]
        print(f"  review on: {rc[:8] if rc else '-'} ({result['review_commit_basis']})")
        if result["tier_degraded"]:
            print("  tier:      DEGRADED (a notice, not a refusal)")
        print(f"  condition 2: {result['verdict']} -- {result['reason']}")
    return 0 if result["verdict"] == SATISFIED else 1


if __name__ == "__main__":
    sys.exit(main())
