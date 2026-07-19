#!/usr/bin/env python3
"""Generate the Agentic OS public status feed (``agentic-os-status.json``).

Part of the Agentic OS visibility layer (backlog #723, Half 1). Emits a
PII-safe, public-by-default JSON snapshot of the whole agentic pipeline so the
``/agentic-os`` page on ffcadmin.org can render it statically (the same
generate-here / deliver-to-ffcadmin pipe the workflow catalog already uses via
workflow 502's ``deliver`` job).

The feed contains:
  * ``backlog_issues`` — open issues labeled ``agentic-os`` (sandboxed agents
    pick these up).
  * ``in_flight_prs`` — open PRs labeled ``agentic-os`` (agent work in flight).
  * ``conductor_log`` — the last 10 comments on the pinned Conductor log issue
    (#719), each body truncated to 500 chars.
  * ``pending_gates`` — workflow runs sitting at an environment approval gate
    (``status=waiting``): workflow name, environment, created-at.

REST only (no ``gh`` CLI, no GraphQL), so it runs anywhere a token is present.
Authentication is a single environment variable, ``GH_TOKEN`` (also accepts
``GITHUB_TOKEN``). Nothing here reads a secret from disk or emits one — the
output is deliberately public.

Examples:
  # Print the feed to stdout:
  GH_TOKEN=... python3 scripts/generate-agentic-os-status.py

  # Write it to a file (what workflow 502 does before the ffcadmin sync PR):
  GH_TOKEN=... python3 scripts/generate-agentic-os-status.py --output agentic-os-status.json

Exit codes: 0 on success, 1 on any API/config error.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_REPO = "FreeForCharity/FFC-Cloudflare-Automation"
LABEL = "agentic-os"
CONDUCTOR_LOG_ISSUE = 719
CONDUCTOR_LOG_LIMIT = 10
COMMENT_TRUNCATE = 500
API_ROOT = "https://api.github.com"
USER_AGENT = "ffc-agentic-os-status-generator"
HTTP_TIMEOUT = 30  # seconds; fail closed rather than hang the daily sync.

# Defense-in-depth redaction for the Conductor-log bodies. The source issue
# (#719) is already a public issue, so nothing here is a *new* disclosure — but
# the aggregated public feed should never propagate a token that was pasted into
# a comment by accident. Only recognizable secret shapes are masked; git SHAs,
# run ids, and ordinary prose are deliberately left intact.
# A full PEM block: BEGIN header, base64 body, and END footer. Matched FIRST and
# non-greedily so the entire key material is masked, not just the header line. A
# fallback for a key whose END line was cut off (e.g. a truncated paste) masks
# the header plus any following base64/whitespace run.
_PEM_RE = re.compile(
    r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----"
    r"|-----BEGIN[ A-Z]*PRIVATE KEY-----[\sA-Za-z0-9+/=]*",
)
_TOKEN_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"  # GitHub PAT / OAuth / server / refresh tokens
    r"|github_pat_[A-Za-z0-9_]{20,}"  # fine-grained PAT
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack tokens
    r"|AKIA[0-9A-Z]{16}"  # AWS access key id
)
_ASSIGN_RE = re.compile(
    r"((?:password|secret|api[_-]?key|token)\s*[:=]\s*)\S{8,}", re.IGNORECASE
)


def redact(text):
    """Mask secret-shaped substrings in free text (PEM blocks, opaque tokens,
    and password/secret/token assignments — see the module-level regexes)."""
    if not text:
        return text
    text = _PEM_RE.sub("[redacted]", text)
    text = _TOKEN_RE.sub("[redacted]", text)
    text = _ASSIGN_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    return text


def _token():
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit(
            "error: set GH_TOKEN (or GITHUB_TOKEN) with repo read access to generate the feed."
        )
    return tok


def _link_rel(link_header, rel):
    """Return the URL for a given ``rel`` (e.g. "next", "last", "prev") from a
    GitHub Link header, or None."""
    if not link_header:
        return None
    target = f'rel="{rel}"'
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().lstrip("<").rstrip(">")
        for seg in segments[1:]:
            if seg.strip() == target:
                return url
    return None


def _build_url(path_or_url, params=None):
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = f"{API_ROOT}/{path_or_url.lstrip('/')}"
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    return url


def _request(path_or_url, token, params=None):
    """Perform ONE GET and return ``(payload, link_header)``. No pagination."""
    url = _build_url(path_or_url, params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload, resp.headers.get("Link")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"error: GitHub API {exc.code} for {url}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach GitHub API ({url}): {exc.reason}")


def rest_get(path_or_url, token, params=None):
    """GET a REST endpoint and return the decoded JSON.

    Pagination is followed **only for array (list) responses** — GitHub list
    endpoints return a JSON array per page, so pages concatenate into one flat
    list. A JSON **object** response (e.g. ``/actions/runs``, which wraps its
    rows in ``{"workflow_runs": [...]}``) is returned as-is from the first page;
    callers that need every row of such an endpoint must page it themselves. The
    only object endpoint used here is the waiting-run list, which is bounded well
    under one page (``per_page=100``)."""
    url = _build_url(path_or_url, params)
    results = []
    while url:
        payload, link = _request(url, token)
        if isinstance(payload, list):
            results.extend(payload)
        else:
            return payload
        url = _link_rel(link, "next")
    return results


def _assignee(item):
    a = item.get("assignee")
    return a.get("login") if a else None


def collect_backlog(repo, token):
    """Open issues labeled agentic-os (PRs excluded — the issues endpoint
    returns both, and PRs carry a ``pull_request`` key)."""
    raw = rest_get(
        f"repos/{repo}/issues",
        token,
        params={"labels": LABEL, "state": "open", "per_page": "100"},
    )
    issues = []
    for it in raw:
        if "pull_request" in it:
            continue
        issues.append(
            {
                "number": it["number"],
                "title": it["title"],
                "state": it["state"],
                "assignee": _assignee(it),
                "updated_at": it["updated_at"],
                "url": it["html_url"],
                "labels": [lbl["name"] for lbl in it.get("labels", [])],
            }
        )
    issues.sort(key=lambda x: x["updated_at"], reverse=True)
    return issues


def collect_in_flight_prs(repo, token):
    """Open PRs carrying the agentic-os label. The pulls endpoint gives us
    ``draft`` and the label set directly."""
    raw = rest_get(
        f"repos/{repo}/pulls",
        token,
        params={"state": "open", "per_page": "100"},
    )
    prs = []
    for pr in raw:
        labels = [lbl["name"] for lbl in pr.get("labels", [])]
        if LABEL not in labels:
            continue
        prs.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "draft": pr.get("draft", False),
                "assignee": _assignee(pr),
                "updated_at": pr["updated_at"],
                "url": pr["html_url"],
                "labels": labels,
            }
        )
    prs.sort(key=lambda x: x["updated_at"], reverse=True)
    return prs


def collect_conductor_log(repo, token):
    """The last N comments on the pinned Conductor log issue, oldest→newest,
    each body redacted + truncated to keep the feed compact and PII-safe.

    Fetches only the **last** page (and the previous one if the last holds fewer
    than N), not the whole thread — so cost stays ~constant (2–3 requests) as
    #719 accumulates hundreds of comments, instead of paging all of them daily."""
    path = f"repos/{repo}/issues/{CONDUCTOR_LOG_ISSUE}/comments"
    first, link = _request(path, token, params={"per_page": "100"})
    raw = first if isinstance(first, list) else []
    last_url = _link_rel(link, "last")
    if last_url:
        last_page, last_link = _request(last_url, token)
        raw = last_page if isinstance(last_page, list) else []
        if len(raw) < CONDUCTOR_LOG_LIMIT:
            prev_url = _link_rel(last_link, "prev")
            if prev_url:
                prev_page, _ = _request(prev_url, token)
                if isinstance(prev_page, list):
                    raw = prev_page + raw
    raw.sort(key=lambda c: c["created_at"])
    recent = raw[-CONDUCTOR_LOG_LIMIT:]
    entries = []
    for c in recent:
        body = redact(c.get("body") or "")
        truncated = len(body) > COMMENT_TRUNCATE
        entries.append(
            {
                "author": (c.get("user") or {}).get("login"),
                "created_at": c["created_at"],
                "body": body[:COMMENT_TRUNCATE] + ("…" if truncated else ""),
                "truncated": truncated,
                "url": c["html_url"],
            }
        )
    return entries


def collect_pending_gates(repo, token):
    """Workflow runs waiting at an environment approval gate. The waiting run
    list names the workflow; the per-run pending_deployments call names the
    environment(s) still awaiting approval."""
    runs_payload = rest_get(
        f"repos/{repo}/actions/runs",
        token,
        params={"status": "waiting", "per_page": "100"},
    )
    runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, dict) else []
    gates = []
    for run in runs:
        rid = run["id"]
        pend = rest_get(f"repos/{repo}/actions/runs/{rid}/pending_deployments", token)
        if not isinstance(pend, list):
            continue
        for dep in pend:
            env = dep.get("environment") or {}
            gates.append(
                {
                    "run_id": rid,
                    "workflow_name": run.get("name"),
                    "environment": env.get("name"),
                    "created_at": run.get("created_at"),
                    "url": run.get("html_url"),
                }
            )
    gates.sort(key=lambda g: g["created_at"] or "")
    return gates


def build_feed(repo, token):
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "backlog_issues": collect_backlog(repo, token),
        "in_flight_prs": collect_in_flight_prs(repo, token),
        "conductor_log": collect_conductor_log(repo, token),
        "pending_gates": collect_pending_gates(repo, token),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo (default: FFC automation)")
    ap.add_argument("--output", help="Write JSON here instead of stdout")
    args = ap.parse_args()

    token = _token()
    feed = build_feed(args.repo, token)
    text = json.dumps(feed, indent=2, ensure_ascii=False) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        counts = (
            f"{len(feed['backlog_issues'])} issues, "
            f"{len(feed['in_flight_prs'])} PRs, "
            f"{len(feed['conductor_log'])} log entries, "
            f"{len(feed['pending_gates'])} pending gates"
        )
        print(f"Wrote {args.output} ({counts}).")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
