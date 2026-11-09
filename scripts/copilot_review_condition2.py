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

#: Substrings that identify Copilot's genuine refusal bodies. These are a
#: SECONDARY signal only: a refusal also shows up as a ``failure`` or absent
#: check run, which is what this module decides on. They are kept so that a
#: refusal arriving with an unexpectedly ``success`` check run is still caught,
#: and so the reported reason names the cause.
REFUSAL_SUBSTRINGS = (
    "wasn't able to review",
    "was not able to review",
    "encountered an error",
)

#: The tier-degradation banner. Its presence means the review ran at a lower
#: effort tier -- it does NOT mean the review is missing. Recorded so that a
#: caller can surface the degradation without treating it as a refusal.
TIER_NOTICE_SUBSTRING = "unable to complete this review at the selected tier"

# Verdicts.
SATISFIED = "SATISFIED"  # a real review on the head; condition 2 holds
WAIT = "WAIT"  # queued / in_progress -- not an answer yet
NOT_ARMED = "NOT_ARMED"  # no check run on this head
REFUSED = "REFUSED"  # check run failure, or a refusal body
NO_REVIEW = "NO_REVIEW"  # check run success but no usable review body


def body_is_real_review(body):
    """True when ``body`` is a real Copilot review body.

    ``contains``, not ``startswith``: the tier banner legitimately precedes the
    marker. A body carrying a known refusal phrase is rejected even if the
    marker is present, since Copilot has emitted both together.
    """
    if not body:
        return False
    if OVERVIEW_MARKER not in body:
        return False
    return not any(s in body for s in REFUSAL_SUBSTRINGS)


def body_is_tier_degraded(body):
    """True when the review ran at a reduced effort tier. Not a refusal."""
    return bool(body) and TIER_NOTICE_SUBSTRING in body


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
    if body and any(s in body for s in REFUSAL_SUBSTRINGS):
        return REFUSED, "refusal body despite a success check run"
    return NO_REVIEW, f"success check run but no {OVERVIEW_MARKER} body on this head"


# --------------------------------------------------------------------------
# Live read (the only part that touches the network)
# --------------------------------------------------------------------------


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

    runs = get(
        f"/repos/{owner}/{repo}/commits/{head}/check-runs?per_page=100", token
    ).get("check_runs", [])
    mine = [c for c in runs if c.get("name") == "copilot-pull-request-reviewer"]
    # Several runs can share a head; the newest is the state of the reviewer.
    mine.sort(key=lambda c: c.get("started_at") or "")
    status = mine[-1]["status"] if mine else None
    conclusion = mine[-1].get("conclusion") if mine else None

    # Reviews are paginated and the history on a long-lived PR exceeds one page;
    # a single-page read understates it (#1596 recorded exactly that).
    body, page = None, 1
    while page <= 10:
        batch = get(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews?per_page=100&page={page}",
            token,
        )
        if not batch:
            break
        for r in batch:
            if r.get("user", {}).get("login") == REVIEWER_LOGIN and r.get(
                "commit_id"
            ) == head:
                body = r.get("body") or ""
        if len(batch) < 100:
            break
        page += 1

    verdict, reason = classify(status, conclusion, body)
    return {
        "pr": number,
        "head": head,
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
        print(
            f"  check run: {result['check_status']}/{result['check_conclusion']}"
        )
        if result["tier_degraded"]:
            print("  tier:      DEGRADED (a notice, not a refusal)")
        print(f"  condition 2: {result['verdict']} -- {result['reason']}")
    return 0 if result["verdict"] == SATISFIED else 1


if __name__ == "__main__":
    sys.exit(main())
