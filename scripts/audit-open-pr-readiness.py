#!/usr/bin/env python3
"""Audit open PRs for the ways an all-green PR is not actually mergeable.

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

3. **A stale pass with a *current* behind-count.** The guard has two
   independent hard-fail causes, and only one of them is the behind-count. If
   `main` changes a file matching the guard's `CRITICAL_PATH_RE` that the PR
   does not touch, it exits 1 on `CRITICAL_PATHS_HIT` **at any behind-count**.
   On 2026-10-10 (run 246) #1602 merged one `.github/workflows/` file, and all
   14 open PRs -- every one of them 5 behind against a threshold of 5, so
   passing the only cause this script used to model -- went red the moment they
   were re-triggered. This script had cleared them. Modelling half of a guard
   is the same falsely-clean report as modelling none of it.

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

`CRITICAL_PATH_RE` is read the same way and held to the same discipline: absent,
ambiguous or uncompilable means INCOMPLETE, never clean.

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

# GitHub's compare API caps `files` at 300 entries. A truncated list makes
# the critical-path set-difference quietly wrong in both directions, so it is
# treated as "cannot say" rather than as a short answer.
COMPARE_FILE_CAP = 300

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


def critical_path_pattern(workflow_text):
    """The critical-path regex `727` hard-fails on, parsed from its own YAML.

    This is the guard's *other* hard-fail cause, and it is not a function of
    the behind-count: a branch one commit behind fails just as hard as one
    fifty behind if `main` touched a matching file the PR leaves alone. Parsed
    rather than repeated here for the same reason as the threshold (#993) --
    a copy would let this script and the guard disagree while both look right.
    """
    found = re.findall(r"CRITICAL_PATH_RE='([^']*)'", workflow_text)
    if not found:
        raise Incomplete(
            "no `CRITICAL_PATH_RE='...'` assignment found in "
            + GUARD_WORKFLOW
            + " -- the guard's critical-path cause cannot be determined, so a "
            "clean report would only mean this script stopped looking"
        )
    distinct = sorted(set(found))
    if len(distinct) > 1:
        raise Incomplete(
            "%s states %d different critical-path patterns (%s); it is "
            "ambiguous which one a given PR will be judged against"
            % (GUARD_WORKFLOW, len(distinct), ", ".join(distinct))
        )
    try:
        return re.compile(distinct[0])
    except re.error as exc:
        raise Incomplete(
            "%s's CRITICAL_PATH_RE (%s) does not compile here: %s"
            % (GUARD_WORKFLOW, distinct[0], exc)
        )


def critical_phantom_hits(base_touched, pr_touched, pattern):
    """Files `main` changed that this PR does not touch, in a critical path.

    Mirrors the guard's own two steps: the `comm -23` of base-touched against
    PR-touched, then `grep -E` of the survivors against `CRITICAL_PATH_RE`.
    `search`, not `match`, because `grep -E` matches anywhere and the pattern
    carries its own `^`.
    """
    untouched = set(base_touched) - set(pr_touched)
    return sorted(f for f in untouched if pattern.search(f))


# --------------------------------------------------------------------------
# Classification -- pure, so it can be tested without a network
# --------------------------------------------------------------------------


def classify(prs, threshold):
    """Split open PRs into the two blocked sets plus everything else.

    `prs` is a list of dicts with keys: number, title, is_draft, rollup,
    unresolved, behind, critical_hits. `behind` may be None, meaning the
    comparison could not be read, and `critical_hits` may be None, meaning the
    file lists behind the critical-path cause could not be read -- either is an
    `incomplete` finding, never a pass. `critical_hits` is `[]` for "evaluated,
    no hits"; the two are deliberately different values.

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

        hits = pr.get("critical_hits")

        if pr.get("behind") is None:
            out["incomplete"].append(
                dict(pr, reason="branch comparison against main could not be read")
            )
            flagged = True
        elif hits is None:
            out["incomplete"].append(
                dict(
                    pr,
                    reason=(
                        "the files main changed since this branch forked could "
                        "not be read, so 727's critical-path cause could not be "
                        "evaluated -- half a guard is not a pass"
                    ),
                )
            )
            flagged = True
        elif green:
            # Both causes are reported on one row: a reviewer who fixes only the
            # one named first would promote the PR straight back into red.
            causes = []
            if pr["behind"] > threshold:
                causes.append(
                    "the branch is %d behind main against 727's hard threshold "
                    "of %d" % (pr["behind"], threshold)
                )
            if hits:
                shown = ", ".join(hits[:3]) + (", ..." if len(hits) > 3 else "")
                causes.append(
                    "main changed %d critical-path file(s) this PR does not "
                    "touch (%s), which 727 hard-fails on at any behind-count"
                    % (len(hits), shown)
                )
            if causes:
                out["stale_green"].append(
                    dict(
                        pr,
                        reason=(
                            "every check is green, but %s -- the guard's pass "
                            "was measured before main moved and will fail on "
                            "the next run. Fix: PUT /pulls/%d/update-branch"
                            % ("; and ".join(causes), pr["number"])
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
        reviewThreads(first:100) {
          totalCount
          pageInfo { hasNextPage }
          nodes { isResolved }
        }
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

            # The outer `pullRequests` connection is paginated below; this inner
            # one is not, and a silent truncation here is worse than one there.
            # `unresolved` is a COUNT: dropping thread 101 can only ever lower
            # it, and lowering it to 0 turns a queue-blocked PR into a clean row
            # -- the precise falsely-clean report this script exists to prevent.
            # So fail closed rather than paginate: a PR with >100 threads is
            # vanishingly rare and an unreadable input must be stated (#966).
            threads = n["reviewThreads"]
            if threads.get("pageInfo", {}).get("hasNextPage"):
                raise Incomplete(
                    "PR #%d has more than 100 review threads (totalCount=%s); "
                    "the unresolved count would be truncated, and a truncated "
                    "count can only under-report"
                    % (n["number"], threads.get("totalCount", "?"))
                )
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


def _file_list(payload):
    """Filenames from a compare payload, or None if the list is not complete.

    A truncated list is worse than no list: short on the PR side it invents
    phantom candidates that are really touched, short on the base side it hides
    real ones. Both read as a confident answer, so neither is returned.
    """
    files = payload.get("files")
    if not isinstance(files, list):
        return None
    if len(files) >= COMPARE_FILE_CAP:
        return None
    names = [f.get("filename") for f in files if isinstance(f, dict)]
    if len(names) != len(files) or any(not isinstance(n, str) for n in names):
        return None
    return names


def fetch_relation(pr, token, owner=OWNER, repo=REPO, request=_request):
    """What `main...head` says about one PR: behind, fork point, its files.

    Every value may be None, meaning "could not be read". None is a finding,
    not a pass -- see `classify`.
    """
    url = "%s/repos/%s/%s/compare/main...%s" % (
        API,
        owner,
        repo,
        urllib.parse.quote(pr["head"], safe=""),
    )
    blank = {"behind": None, "merge_base": None, "pr_files": None}
    try:
        payload = request(url, token)
    except (urllib.error.URLError, ValueError, KeyError):
        return blank
    if not isinstance(payload, dict):
        return blank
    behind = payload.get("behind_by")
    base = payload.get("merge_base_commit")
    sha = base.get("sha") if isinstance(base, dict) else None
    return {
        "behind": behind if isinstance(behind, int) else None,
        "merge_base": sha if isinstance(sha, str) else None,
        "pr_files": _file_list(payload),
    }


def fetch_base_touched(
    merge_base, token, cache, owner=OWNER, repo=REPO, request=_request
):
    """Files `main` changed since `merge_base`, cached by fork point.

    Open PRs overwhelmingly share a fork point, so this is one read per
    distinct one rather than one per PR.
    """
    if not merge_base:
        return None
    if merge_base in cache:
        return cache[merge_base]
    url = "%s/repos/%s/%s/compare/%s...main" % (
        API,
        owner,
        repo,
        urllib.parse.quote(merge_base, safe=""),
    )
    try:
        payload = request(url, token)
    except (urllib.error.URLError, ValueError, KeyError):
        cache[merge_base] = None
        return None
    out = _file_list(payload) if isinstance(payload, dict) else None
    cache[merge_base] = out
    return out


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
        guard_text = read_guard_workflow(args.repo_root)
        threshold = phantom_revert_threshold(guard_text)
        pattern = critical_path_pattern(guard_text)
        prs = fetch_open_prs(token)
    except Incomplete as exc:
        print("INCOMPLETE: %s" % exc, file=sys.stderr)
        return 1

    if args.label:
        prs = [p for p in prs if args.label in p["labels"]]
    base_cache = {}
    for pr in prs:
        rel = fetch_relation(pr, token)
        pr["behind"] = rel["behind"]
        if rel["behind"] == 0:
            # The guard exits clean before it ever builds a candidate list when
            # the branch is already up to date, so there is nothing to evaluate.
            pr["critical_hits"] = []
            continue
        base_touched = fetch_base_touched(rel["merge_base"], token, base_cache)
        if base_touched is None or rel["pr_files"] is None:
            pr["critical_hits"] = None
        else:
            pr["critical_hits"] = critical_phantom_hits(
                base_touched, rel["pr_files"], pattern
            )

    result = classify(prs, threshold)
    if args.json:
        print(json.dumps({"threshold": threshold, **result}, indent=2))
    else:
        print(render(result, threshold, len(prs), args.label))
    return 1 if has_findings(result) else 0


if __name__ == "__main__":
    sys.exit(main())
