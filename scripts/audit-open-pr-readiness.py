#!/usr/bin/env python3
"""Audit open PRs for the two ways an all-green PR is not actually mergeable.

Both were found the expensive way on 2026-08-04/05, one run apart, and both
present as a PR that looks entirely healthy:

1. **Unresolved review threads.** The merge queue refuses a PR with an open
   review thread, and nothing in the check-runs view says so. On 2026-08-04 a
   cloud worker found #1064 and #825 queue-blocked this way; #1064's two threads
   had been *fixed two commits earlier* and merely never resolved. Five of five
   checks green, `mergeable_state` reading only `blocked`.

2. **A stale `Phantom Revert Guard` pass.** This is the subtler one, and it
   defeats the sweep written for (1). The guard's verdict depends on how far the
   branch is behind `main` -- a quantity that changes when *`main`* moves, with
   no event on the PR to re-run the check. So the green stays on file and stops
   being true. On 2026-08-05 (run 97) #1018, #1039 and #1062 were all
   `rollup=SUCCESS`, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`, zero
   unresolved threads -- the exact set a readiness sweep calls "ready except
   nobody promoted it" -- and all three were **9 behind** against a hard
   threshold of **5**. Every one of them was certain to go red the moment anyone
   promoted it. #1018's guard had last passed at 21:17:33Z; `main` took eight
   commits after that.

The general shape, which is why this is a script and not a checklist entry: a
check over the branch's own **content** stays valid until the branch changes,
but a check over the branch's **relationship to `main`** decays on its own. Only
the first kind is safely cacheable, and CI reports them identically.

Read-only. No mutation of any kind: nothing merged, updated, commented or
labelled. It reports and sets an exit code.

The threshold is read from the workflow, never hardcoded
--------------------------------------------------------
`727-phantom-revert-guard.yml` is the authority on its own threshold, so this
script parses it out rather than repeating the number. Hardcoding it here would
reproduce exactly the failure class #993 names -- an assertion that compares our
own text to our own other text and can be confidently wrong while both agree.

The YAML tests `$BEHIND_COUNT` in more than one place (the no-candidates branch
and the candidates branch both hard-fail above it). All occurrences must agree;
if they do not, or if none is found, that is an **incomplete** enumeration and
the run exits non-zero. A guard whose threshold cannot be determined must never
produce a clean report.

Usage
-----
    GH_TOKEN=... python3 scripts/audit-open-pr-readiness.py
    GH_TOKEN=... python3 scripts/audit-open-pr-readiness.py --json
    GH_TOKEN=... python3 scripts/audit-open-pr-readiness.py --label agentic-os

Exit codes: 0 clean, 1 findings or an enumeration that could not be completed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OWNER = "FreeForCharity"
REPO = "FFC-Cloudflare-Automation"
GUARD_WORKFLOW = ".github/workflows/727-phantom-revert-guard.yml"

# The rollup states that mean "CI has nothing against this PR right now".
# PENDING is deliberately excluded: a PR mid-run is not a stale green, it is an
# unfinished measurement, and reporting it would make this audit noisy in
# exactly the way #992 warns about.
GREEN_ROLLUP = {"SUCCESS", None}


# --------------------------------------------------------------------------
# Threshold
# --------------------------------------------------------------------------


class Incomplete(Exception):
    """An enumeration that could not be completed.

    Raised rather than returned so that no caller can accidentally treat a
    missing answer as a clean one -- the failure mode #966 exists to prevent.
    """


def phantom_revert_threshold(workflow_text):
    """The `behind` threshold `727` hard-fails above, parsed from its own YAML.

    Returns an int. Raises `Incomplete` if the workflow states no threshold, or
    states more than one -- both of which mean this script cannot say what the
    guard will do, and must not pretend otherwise.
    """
    found = re.findall(
        r'"\$BEHIND_COUNT"\s+-gt\s+(\d+)', workflow_text
    )
    if not found:
        raise Incomplete(
            "no `\"$BEHIND_COUNT\" -gt N` comparison found in "
            + GUARD_WORKFLOW
            + " -- the guard's threshold cannot be determined, so no PR can be "
            "classified against it"
        )
    distinct = sorted({int(n) for n in found})
    if len(distinct) > 1:
        raise Incomplete(
            "%s states %d different thresholds (%s); it is ambiguous which one "
            "a given PR will be judged against"
            % (GUARD_WORKFLOW, len(distinct), ", ".join(str(d) for d in distinct))
        )
    return distinct[0]


# --------------------------------------------------------------------------
# Classification -- pure, so it can be tested without a network
# --------------------------------------------------------------------------


def classify(prs, threshold):
    """Split open PRs into the two blocked sets plus everything else.

    `prs` is a list of dicts with keys: number, title, is_draft, rollup,
    unresolved, behind. `behind` may be None, meaning the comparison could not
    be read -- which is an `incomplete` finding, never a pass.

    Returns a dict of lists. `stale_green` and `unresolved_threads` are
    deliberately NOT mutually exclusive: a PR can be both, and suppressing one
    would hide half of what a reviewer has to fix.
    """
    out = {
        "stale_green": [],
        "unresolved_threads": [],
        "incomplete": [],
        "ok": [],
    }
    for pr in prs:
        green = pr.get("rollup") in GREEN_ROLLUP
        flagged = False

        if pr.get("behind") is None:
            out["incomplete"].append(
                dict(pr, reason="branch comparison against main could not be read")
            )
            flagged = True
        elif green and pr["behind"] > threshold:
            out["stale_green"].append(
                dict(
                    pr,
                    reason=(
                        "every check is green, but the branch is %d behind main "
                        "against 727's hard threshold of %d -- the guard's pass "
                        "was measured before main moved and will fail on the "
                        "next run. Fix: PUT /pulls/%d/update-branch"
                        % (pr["behind"], threshold, pr["number"])
                    ),
                )
            )
            flagged = True

        if green and pr.get("unresolved", 0) > 0:
            out["unresolved_threads"].append(
                dict(
                    pr,
                    reason=(
                        "every check is green, but %d review thread(s) are "
                        "unresolved; the merge queue refuses the PR and no "
                        "check-run says so"
                        % pr["unresolved"]
                    ),
                )
            )
            flagged = True

        if not flagged:
            out["ok"].append(pr)
    return out


def has_findings(result):
    """True when the run must exit non-zero.

    `ok` never counts. `incomplete` always does: an audit that could not read
    its own input has no verdict, and must not report a clean one.
    """
    return any(
        result[k] for k in ("stale_green", "unresolved_threads", "incomplete")
    )


# --------------------------------------------------------------------------
# Transport -- injected in tests, never exercised there
# --------------------------------------------------------------------------


def _request(url, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", accept)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _graphql(query, variables, token):
    """The PR enumeration's transport. Every failure is a *stated* Incomplete.

    `fetch_behind` already degrades a transport failure into `None`, which
    `classify` reports as a finding. This path had no equivalent: a 401, a 403
    or a rate-limit on the PR read escaped as a raw traceback, so the one
    enumeration the whole report is built from was the one that could not say
    it had failed. The contract survived by luck -- an uncaught exception still
    exits non-zero, so an unreadable input could never be reported clean -- but
    "exits 1 with a stack trace" and "prints `INCOMPLETE: ...`" are different
    promises, and `main()` exists to make the second one.

    Note `urllib.error.HTTPError` subclasses `URLError`, so the auth and
    rate-limit cases that motivated this are covered by the one clause.
    """
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(API + "/graphql", data=body)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise Incomplete("GraphQL request failed: %s" % exc)
    except ValueError as exc:
        raise Incomplete("GraphQL response was not JSON: %s" % exc)
    if not isinstance(payload, dict):
        raise Incomplete("GraphQL response was not an object")
    if payload.get("errors"):
        raise Incomplete(
            "GraphQL errors: %s"
            % "; ".join(e.get("message", "?") for e in payload["errors"])
        )
    if "data" not in payload:
        raise Incomplete("GraphQL response carried neither data nor errors")
    return payload["data"]


PR_QUERY = """
query($owner:String!, $repo:String!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequests(states:OPEN, first:50, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title isDraft headRefName
        labels(first:20) { nodes { name } }
        reviewThreads(first:100) { nodes { isResolved } }
        commits(last:1) { nodes { commit {
          oid statusCheckRollup { state } } } }
      }
    }
  }
}
"""


def fetch_open_prs(token, owner=OWNER, repo=REPO, graphql=_graphql):
    """Every open PR, following `$endCursor` to the end.

    Aborts rather than truncating if a page claims a next page without giving a
    cursor -- run 61 published a verdict off a third of a board that way.
    """
    prs, cursor = [], None
    while True:
        data = graphql(
            PR_QUERY, {"owner": owner, "repo": repo, "cursor": cursor}, token
        )
        block = (data.get("repository") or {}).get("pullRequests")
        if block is None:
            raise Incomplete("no pullRequests block in the GraphQL response")
        for n in block["nodes"]:
            commits = n["commits"]["nodes"]
            rollup = None
            if commits and commits[0]["commit"].get("statusCheckRollup"):
                rollup = commits[0]["commit"]["statusCheckRollup"]["state"]
            prs.append(
                {
                    "number": n["number"],
                    "title": n["title"],
                    "is_draft": n["isDraft"],
                    "head": n["headRefName"],
                    "labels": [l["name"] for l in n["labels"]["nodes"]],
                    "rollup": rollup,
                    "unresolved": sum(
                        1
                        for t in n["reviewThreads"]["nodes"]
                        if not t["isResolved"]
                    ),
                }
            )
        page = block["pageInfo"]
        if not page["hasNextPage"]:
            return prs
        if not page["endCursor"]:
            raise Incomplete(
                "hasNextPage is true with no endCursor; refusing to report a "
                "partial PR list as complete"
            )
        cursor = page["endCursor"]


def fetch_behind(pr, token, owner=OWNER, repo=REPO, request=_request):
    """`behind_by` for one PR's head, or None if it could not be read.

    None is a finding, not a pass -- see `classify`.
    """
    url = "%s/repos/%s/%s/compare/main...%s" % (
        API,
        owner,
        repo,
        urllib.parse.quote(pr["head"], safe=""),
    )
    try:
        payload = request(url, token)
    except (urllib.error.URLError, ValueError, KeyError):
        return None
    behind = payload.get("behind_by") if isinstance(payload, dict) else None
    return behind if isinstance(behind, int) else None


def read_guard_workflow(repo_root):
    path = pathlib.Path(repo_root) / GUARD_WORKFLOW
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Incomplete("cannot read %s: %s" % (GUARD_WORKFLOW, exc))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def render(result, threshold, total, label):
    lines = [
        "Open PR readiness audit -- %s/%s" % (OWNER, REPO),
        "open_prs=%d threshold=%d (parsed from %s)%s"
        % (
            total,
            threshold,
            GUARD_WORKFLOW,
            " label=%s" % label if label else "",
        ),
        "",
    ]
    sections = [
        (
            "stale_green",
            "green, but the guard's pass predates main moving "
            "(will fail on promotion)",
        ),
        (
            "unresolved_threads",
            "green, but queue-blocked on unresolved review threads",
        ),
        ("incomplete", "could not be classified (counts as a finding)"),
    ]
    for key, blurb in sections:
        rows = result[key]
        lines.append("## %s: %d  (%s)" % (key, len(rows), blurb))
        if not rows:
            lines.append("  (none)")
        for r in rows:
            lines.append(
                "  - #%d%s %s" % (r["number"], " [draft]" if r["is_draft"] else "", r["title"][:70])
            )
            lines.append("      %s" % r["reason"])
        lines.append("")
    lines.append("## ok: %d" % len(result["ok"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--label", help="only consider PRs carrying this label")
    ap.add_argument(
        "--repo-root",
        default=str(pathlib.Path(__file__).resolve().parent.parent),
        help="checkout to read the guard workflow from",
    )
    args = ap.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN (or GITHUB_TOKEN) is required", file=sys.stderr)
        return 1

    try:
        threshold = phantom_revert_threshold(read_guard_workflow(args.repo_root))
        prs = fetch_open_prs(token)
    except Incomplete as exc:
        print("INCOMPLETE: %s" % exc, file=sys.stderr)
        return 1

    if args.label:
        prs = [p for p in prs if args.label in p["labels"]]
    for pr in prs:
        pr["behind"] = fetch_behind(pr, token)

    result = classify(prs, threshold)
    if args.json:
        print(json.dumps({"threshold": threshold, **result}, indent=2))
    else:
        print(render(result, threshold, len(prs), args.label))
    return 1 if has_findings(result) else 0


if __name__ == "__main__":
    sys.exit(main())
