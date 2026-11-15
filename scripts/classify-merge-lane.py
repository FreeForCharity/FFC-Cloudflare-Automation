#!/usr/bin/env python3
"""Classify a pull request into a merge lane: AGENT (Claude may enqueue it) or CLARKE.

This is the mechanical half of AGENTS.md **Merge categories**. It answers one
question from the PR's own file list and patches: does anything in this diff
fall outside the categories Clarke approved for merging without him?

It does NOT replace the merge condition (CI green, Copilot reviewed the head,
no open threads, coverage). Run `copilot_review_condition2.py` and
`review_threads_rest.py` for those. It also cannot see the two judgment-only
exclusions -- a Tier 2 effect (money, live DNS cutover, deletes or cancels,
identity tenants) and policy wording (privacy, legal, applicant-facing text) --
so an AGENT verdict always prints that reminder.

Categories (AGENTS.md, Merge categories):

  1 docs        Markdown, docs/, .claude/skills/, .claude/routines/
  2 code        scripts/, tests/ and other code with no workflow change
  3 data        only automated data files (sites-list/, data/); the one
                category a scheduled run may also enqueue
  4 workflow    a .github/workflows/ edit whose changed lines touch no
                permission, environment, secret, variable, trigger or action
                reference, in a workflow that holds no write credential
  5 hooks       .claude/hooks/ or .githooks/ changes that only ADD lines

Anything else is CLARKE, with a reason per file. When the checker cannot read
something (a patch GitHub truncated, a workflow it could not fetch) the answer
is CLARKE, never AGENT: "could not tell" must not read as "safe".

Exit codes: 0 = AGENT, 1 = CLARKE, 2 = could not evaluate (network, auth).

Usage:
    python3 scripts/classify-merge-lane.py --pr 1611
    python3 scripts/classify-merge-lane.py --pr 1611 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OWNER = "FreeForCharity"
REPO = "FFC-Cloudflare-Automation"
USER_AGENT = "ffc-classify-merge-lane"

AGENT = "AGENT"
CLARKE = "CLARKE"

# Paths that always need Clarke, whatever the diff says.
CLARKE_PREFIXES = (
    ".github/actions/",  # credential plumbing (KV -> env)
    ".claude/settings",  # agent permission allow/deny lists
    ".github/CODEOWNERS",
    ".github/rulesets/",
    ".mcp.json",
)

# Files that ARE these rules, or that hold Clarke's own application-gate decisions.
# A change here changes what gets merged or accepted without him.
CLARKE_FILES = {
    "scripts/classify-merge-lane.py": "the merge-lane checker itself",
    "tests/workflow-logic/test_classify_merge_lane.py": "the merge-lane checker's tests",
    "config/charity-lane-gate-policy.json": "application quality gate policy",
    "config/charity-lane-adjudicated-orders.json": "Clarke's recorded application overrides",
    "scripts/charity_lane_gate.py": "application quality gate",
    "scripts/charity-lane-completeness.ps1": "application completeness review",
}

# Workflows that ARE the required checks or the repo's own guard rails. Edits may
# only add lines: removing a step from CI is how a gate gets weakened quietly.
GUARD_WORKFLOWS = {
    ".github/workflows/722-ci.yml",
    ".github/workflows/723-codeql-analysis.yml",
    ".github/workflows/726-repo-rulesets-drift-audit.yml",
    ".github/workflows/727-phantom-revert-guard.yml",
    ".github/workflows/728-ai-agent-hooks-validate.yml",
}

DATA_PREFIXES = ("sites-list/", "data/")
DOCS_PREFIXES = ("docs/", ".claude/skills/", ".claude/routines/")
HOOK_PREFIXES = (".claude/hooks/", ".githooks/")

# A changed workflow line is sensitive if it sets one of these keys...
SENSITIVE_KEY = re.compile(
    r"^\s*-?\s*(permissions|environment|uses|on|secrets|id-token|contents|actions"
    r"|pull-requests|issues|packages|deployments|statuses|checks|attestations"
    r"|workflow_dispatch|workflow_call|workflow_run|pull_request_target|pull_request"
    r"|push|schedule|cron|merge_group|issue_comment|repository_dispatch"
    r"|branches|branches-ignore|tags|paths|paths-ignore|continue-on-error)\s*:"
)
# ...or mentions a credential anywhere on the line.
SENSITIVE_TEXT = re.compile(r"secrets\.|vars\.|GITHUB_TOKEN|GH_TOKEN|pending_deployments")

ENV_INLINE = re.compile(r"^\s*environment\s*:\s*['\"]?([A-Za-z0-9_.${}\- ]*?)['\"]?\s*$")
ENV_NAME = re.compile(r"^\s*name\s*:\s*['\"]?([A-Za-z0-9_.${}\-]+)['\"]?\s*$")
SECRET_REF = re.compile(r"secrets\.([A-Za-z0-9_]+)")


def _code_lines(text):
    """YAML lines with full-line comments dropped (comments mention 'environment' freely)."""
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        yield line


def write_credentials(workflow_text):
    """Return reasons this workflow can act with a write credential (empty list = none).

    A job bound to any environment whose name does not end in `-read`, or a
    reference to any secret other than the ambient GITHUB_TOKEN, makes the whole
    workflow write-capable: a logic edit anywhere in it could change what that
    credential is used for.
    """
    reasons = []
    lines = list(_code_lines(workflow_text))
    for i, line in enumerate(lines):
        m = ENV_INLINE.match(line)
        if not m:
            continue
        value = m.group(1).strip()
        if value == "":
            # block form: `environment:` then `name: x` on a following line
            for nxt in lines[i + 1 : i + 4]:
                n = ENV_NAME.match(nxt)
                if n:
                    value = n.group(1)
                    break
        if value and not value.endswith("-read"):
            reasons.append("uses environment %r" % value)
    for name in sorted(set(SECRET_REF.findall("\n".join(lines)))):
        if name != "GITHUB_TOKEN":
            reasons.append("reads secret %s" % name)
    return list(dict.fromkeys(reasons))


def changed_lines(patch):
    """(added, removed) lists of changed lines from a unified-diff patch, markers stripped."""
    added, removed = [], []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def classify_file(f, head_text=None):
    """Classify one entry of `GET pulls/{n}/files`. Returns (lane, category, reason).

    `head_text` is the workflow's content at the PR head; it is only consulted
    for modified workflow files, and None there means "could not read it".
    """
    name = f["filename"]
    status = f.get("status", "modified")
    patch = f.get("patch")
    previous = f.get("previous_filename")

    if status == "unlisted":
        return CLARKE, None, "GitHub did not list every file; cannot check the rest"

    for p in (name, previous):
        if p and p in CLARKE_FILES:
            return CLARKE, None, CLARKE_FILES[p]
        if p and p.startswith(CLARKE_PREFIXES):
            return CLARKE, None, "reserved path"

    if name.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        if status != "modified":
            return CLARKE, None, "workflow %s (new, deleted or renamed workflow)" % status
        if patch is None:
            return CLARKE, None, "diff too large for GitHub to show; cannot check it"
        added, removed = changed_lines(patch)
        for line in added + removed:
            if line.strip().startswith("#"):
                continue
            if SENSITIVE_KEY.match(line) or SENSITIVE_TEXT.search(line):
                return CLARKE, None, "changes a sensitive line: %s" % line.strip()[:100]
        if name in GUARD_WORKFLOWS and any(l.strip() for l in removed):
            return CLARKE, None, "removes lines from a required-check or guard workflow"
        if head_text is None:
            return CLARKE, None, "could not read the workflow at the PR head"
        creds = write_credentials(head_text)
        if creds:
            return CLARKE, None, "workflow holds a write credential (%s)" % "; ".join(creds)
        return AGENT, "workflow", "logic edit; no permission, trigger, secret or action change"

    if name.startswith(HOOK_PREFIXES):
        if status not in ("modified", "added"):
            return CLARKE, None, "hook file %s" % status
        if patch is None:
            return CLARKE, None, "diff too large for GitHub to show; cannot check it"
        _, removed = changed_lines(patch)
        if removed:
            return CLARKE, None, "deletes %d line(s) from a guard" % len(removed)
        return AGENT, "hooks", "only adds lines"

    if name.startswith(DATA_PREFIXES):
        return AGENT, "data", "automated data file"

    if name.endswith(".md") or name.startswith(DOCS_PREFIXES):
        return AGENT, "docs", "documentation"

    return AGENT, "code", "code or tests with no workflow change"


def classify(files, head_texts=None):
    """Classify a whole PR. `head_texts` maps workflow path -> content at head."""
    head_texts = head_texts or {}
    rows = []
    for f in files:
        lane, cat, reason = classify_file(f, head_texts.get(f["filename"]))
        rows.append({"file": f["filename"], "lane": lane, "category": cat, "reason": reason})
    if not rows:
        return {"lane": CLARKE, "categories": [], "scheduled_ok": False, "files": rows,
                "note": "no files listed; cannot classify an empty diff"}
    lane = CLARKE if any(r["lane"] == CLARKE for r in rows) else AGENT
    cats = sorted({r["category"] for r in rows if r["category"]})
    return {
        "lane": lane,
        "categories": cats,
        # Only a data-only PR may be enqueued by a scheduled run.
        "scheduled_ok": lane == AGENT and cats == ["data"],
        "files": rows,
    }


def _get(path, token, raw=False):
    req = urllib.request.Request(API + path)
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=60) as fh:
        body = fh.read().decode("utf-8")
    return body if raw else json.loads(body)


def evaluate_pr(number, token, owner=OWNER, repo=REPO, get=_get):
    base = "/repos/%s/%s" % (owner, repo)
    pr = get("%s/pulls/%d" % (base, number), token)
    head = pr["head"]["sha"]
    files, page = [], 1
    while True:
        batch = get("%s/pulls/%d/files?per_page=100&page=%d" % (base, number, page), token)
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    if pr.get("changed_files") is not None and len(files) != pr["changed_files"]:
        # The files endpoint caps at 3000; a short list must not read as a small diff.
        files.append({"filename": "(unlisted files)", "status": "unlisted", "patch": None})
    head_texts = {}
    for f in files:
        n = f["filename"]
        if n.startswith(".github/workflows/") and f.get("status") == "modified":
            try:
                head_texts[n] = get("%s/contents/%s?ref=%s" % (
                    base, urllib.parse.quote(n), head), token, raw=True)
            except urllib.error.HTTPError:
                pass  # left out -> classify_file answers CLARKE
    result = classify(files, head_texts)
    result.update({"pr": number, "head": head[:8]})
    return result


def render(result):
    out = ["PR #%s  head %s" % (result.get("pr"), result.get("head"))]
    for r in result["files"]:
        tag = r["category"] or "-"
        out.append("  %-6s %-8s %s  (%s)" % (r["lane"], tag, r["file"], r["reason"]))
    out.append("lane: %s%s" % (result["lane"],
               "  categories: " + ", ".join(result["categories"]) if result["categories"] else ""))
    if result["lane"] == AGENT:
        out.append("scheduled run may enqueue: %s" % ("yes" if result["scheduled_ok"] else "no (live session only)"))
        out.append("still judge by hand: no Tier 2 effect and no policy wording; and the merge condition must hold")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", type=int, required=True, help="pull request number")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--owner", default=OWNER)
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        result = evaluate_pr(args.pr, token, owner=args.owner, repo=args.repo, get=_get)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        print("PR #%d: could not evaluate (%s: %s)" % (args.pr, type(exc).__name__, exc),
              file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0 if result["lane"] == AGENT else 1


if __name__ == "__main__":
    sys.exit(main())
