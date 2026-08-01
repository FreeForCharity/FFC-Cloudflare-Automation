#!/usr/bin/env python3
"""Audit the public "Agentic OS" board (org project #9) against the real backlog.

The board has **no auto-add** (`CLAUDE.md`, "Board & PR-creation env facts"):
every item and every Status is placed by hand. So each Conductor run has to
re-derive the same three questions — which open `agentic-os` items are missing
from the board, which board items carry no Status, which are closed or merged
but not `Done`. Hand-rolling that query got it wrong twice in two runs, in two
different ways, and #966 is the record of both.

Read-only. This script issues no mutation of any kind: no items added, no
statuses set, no comments. It reports and sets an exit code.

Why the expected set comes from REST and never from search
----------------------------------------------------------
Run 62 built the "what should be on the board" half from
``gh search issues --label agentic-os`` and got **52** items where the
authoritative REST enumeration returned **53**. The item it dropped was PR #965,
created minutes earlier. GitHub's search index **lags writes**, so a
completeness audit built on it under-reports precisely for the newest items —
which are exactly the ones most likely to be missing from a manually-maintained
board. It fails silently, and in the reassuring direction. See #966.

That rule is why this script also enumerates its **repositories** from REST
(``GET /orgs/{org}/repos``) rather than from the search-derived repo set that
``scripts/generate-agentic-os-status.py`` uses. #966 asks for "the same repo set"
that generator sweeps; taken literally that would reintroduce the very index
this script exists to distrust, one level up — a brand-new repo's brand-new
issue would be invisible to the audit for the same reason PR #965 was. The
org-repo listing is strongly consistent and a strict superset, so the deviation
can only make the audit see more, never less. ``--repo`` pins the set explicitly
when a caller wants to bound the cost.

Cost: one paginated org-repo listing, one issue listing per non-archived repo,
and one paginated GraphQL read of the board — roughly 65 calls against the
shared 5,000/hr budget (AGENTS.md). Bounded, and it does not loop.

Authentication is a single environment variable, ``GH_TOKEN`` (also accepts
``GITHUB_TOKEN``), matching ``scripts/generate-agentic-os-status.py``.

Examples:
  GH_TOKEN=... python3 scripts/audit-agentic-os-board.py
  GH_TOKEN=... python3 scripts/audit-agentic-os-board.py --json
  GH_TOKEN=... python3 scripts/audit-agentic-os-board.py --repo FreeForCharity/FFC-Cloudflare-Automation

Exit codes:
  0  board is consistent with the backlog — all three sets empty
  1  at least one set is non-empty (the audit found something), OR any API,
     auth or config error. **Never 0 on a failed enumeration**: an audit that
     cannot read one of its two sides has no verdict to report, and reporting
     "clean" would be the exact silence-read-as-green shape #966 is about.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ORG = "FreeForCharity"
DEFAULT_PROJECT_NUMBER = 9
HUB_REPO = "FreeForCharity/FFC-Cloudflare-Automation"
LABEL = "agentic-os"
DONE_STATUS = "Done"

API_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "ffc-agentic-os-board-auditor"
HTTP_TIMEOUT = 30  # seconds; fail closed rather than hang a Conductor run.

# Board pages. GraphQL caps `first:` at 100, and the board is already past 200
# items — run 61 read `items(first:100)` as if it were the whole board and
# nearly published "0 statusless" off a third of it (#966).
PAGE_SIZE = 100
# A bound on pagination so a malformed cursor cannot spin. 100 pages x 100 items
# is 10,000 board items; the board is at ~210.
MAX_PAGES = 100

BOARD_QUERY = """
query($org: String!, $number: Int!, $first: Int!, $after: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            __typename
            ... on Issue {
              number
              state
              title
              url
              repository { nameWithOwner }
            }
            ... on PullRequest {
              number
              state
              title
              url
              repository { nameWithOwner }
            }
            ... on DraftIssue { title }
          }
        }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------
# Auth and transport
# --------------------------------------------------------------------------


def _token():
    """The API token, or abort.

    Aborting is the point. An audit that runs unauthenticated would enumerate
    zero private repos and read an empty board, then report three empty sets —
    a clean bill of health produced by having no access at all."""
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit(
            "error: set GH_TOKEN (or GITHUB_TOKEN) with repo + project read access. "
            "Refusing to run: an unauthenticated sweep reports an empty board as a clean one."
        )
    return tok


def _link_rel(link_header, rel):
    """Return the URL for a given ``rel`` from a GitHub Link header, or None."""
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
    """Perform ONE REST GET and return ``(payload, link_header)``.

    There is no ``soft_fail`` here on purpose. Every call this script makes is
    load-bearing for one side of a comparison, so there is no HTTP error whose
    correct handling is "carry on with a smaller set"."""
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


def rest_get_all(path_or_url, token, params=None):
    """GET a REST **list** endpoint, following Link ``rel="next"`` to the end.

    Both callers here are list endpoints, so a non-list payload is a shape
    error rather than something to tolerate — an object where a list was
    expected is how a 200-with-an-error-body would slip through as zero rows."""
    url = _build_url(path_or_url, params)
    results = []
    while url:
        payload, link = _request(url, token)
        if not isinstance(payload, list):
            raise SystemExit(
                f"error: expected a JSON array from {url}, got {type(payload).__name__}. "
                "Refusing to treat an unexpected response shape as an empty page."
            )
        results.extend(payload)
        url = _link_rel(link, "next")
    return results


def graphql(query, variables, token, _urlopen=None):
    """POST one GraphQL **query** and return its ``data``.

    A POST, but not a write: this script sends no ``mutation`` — see the
    module docstring and the guard in
    ``tests/workflow-logic/test_agentic_os_board_audit.py``.

    GraphQL answers 200 with an ``errors`` array, so a status-only check reads
    a permission denial as success and yields an empty board.

    ``_urlopen`` is a seam for the tests: the ``errors``-on-200 path cannot be
    reached through ``fetch_board_items``' ``_graphql`` seam, and a test that
    merely greps this function for the check passes against
    ``if False and payload.get("errors")``."""
    opener = _urlopen or urllib.request.urlopen
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(GRAPHQL_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with opener(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"error: GitHub GraphQL {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach GitHub GraphQL: {exc.reason}")
    if payload.get("errors"):
        raise SystemExit(f"error: GitHub GraphQL returned errors: {json.dumps(payload['errors'])}")
    data = payload.get("data")
    if data is None:
        raise SystemExit("error: GitHub GraphQL response carried no `data`.")
    return data


# --------------------------------------------------------------------------
# The expected set (REST — never search; see the module docstring / #966)
# --------------------------------------------------------------------------


def list_org_repos(org, token):
    """Every non-archived repository in ``org``, as ``owner/name``.

    Archived repos are dropped: their issues cannot be edited, so an archived
    item that never reached the board is not work anyone can do."""
    rows = rest_get_all(f"orgs/{org}/repos", token, params={"per_page": "100", "type": "all"})
    repos = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("archived"):
            continue
        name = r.get("full_name")
        if name:
            repos.append(name)
    return sorted(set(repos))


def collect_expected(repos, token):
    """Open items labeled ``agentic-os`` across ``repos``, keyed ``(repo, number)``.

    The **issues** endpoint deliberately returns pull requests as well (they
    carry a ``pull_request`` key), and that is not incidental here: run 62's
    single missing item was a PR. Filtering PRs out would have reproduced the
    bug it is meant to catch.

    Keyed on ``(repo, number)`` and never on the bare integer — ``#744`` names a
    different item in each repository, so an integer-keyed comparison would
    silently mark a real gap as present because some other repo happens to have
    that number on the board."""
    expected = {}
    for repo in repos:
        rows = rest_get_all(
            f"repos/{repo}/issues",
            token,
            params={"state": "open", "labels": LABEL, "per_page": "100"},
        )
        for it in rows:
            number = it.get("number")
            if number is None:
                continue
            expected[(repo, number)] = {
                "repo": repo,
                "number": number,
                "title": it.get("title") or "",
                "url": it.get("html_url") or "",
                "is_pr": "pull_request" in it,
            }
    return expected


# --------------------------------------------------------------------------
# The board set (GraphQL, paginated)
# --------------------------------------------------------------------------


def fetch_board_items(org, project_number, token, _graphql=graphql):
    """Every item on project ``project_number``, following ``$endCursor``.

    ``items(first:100)`` with no cursor loop returned 100 of 208 on run 61 and
    nearly produced "0 statusless" off a third of the board (#966). The whole
    point of this function is the ``while`` below."""
    nodes = []
    cursor = None
    title = None
    for _ in range(MAX_PAGES):
        data = _graphql(
            BOARD_QUERY,
            {"org": org, "number": project_number, "first": PAGE_SIZE, "after": cursor},
            token,
        )
        org_data = (data or {}).get("organization")
        if not org_data:
            raise SystemExit(f"error: org {org!r} not readable via GraphQL (no `organization`).")
        project = org_data.get("projectV2")
        if not project:
            raise SystemExit(
                f"error: project #{project_number} not found on {org!r}, or not readable "
                "with this token's scopes. Refusing to report an empty board as a clean one."
            )
        title = project.get("title") or title
        items = project.get("items") or {}
        nodes.extend(items.get("nodes") or [])
        page = items.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return title, nodes
        cursor = page.get("endCursor")
        if not cursor:
            raise SystemExit(
                "error: the board reported hasNextPage with no endCursor; the remaining "
                "items cannot be read, and a partial board must not be audited."
            )
    raise SystemExit(
        f"error: board pagination exceeded {MAX_PAGES} pages ({len(nodes)} items read). "
        "Refusing to loop; re-run or raise MAX_PAGES if the board really is this large."
    )


def _as_dict(value):
    """``value`` when it is a dict, else ``{}`` — GraphQL nulls, uniformly."""
    return value if isinstance(value, dict) else {}


def normalize_board_items(nodes):
    """Shape raw project nodes into rows the set arithmetic can use.

    Draft issues have no repository and no number — they are real board items
    that can be statusless, so they are kept with ``key=None`` rather than
    dropped. Dropping them would understate the statusless set; giving them a
    key would invent a match against a repo item."""
    rows = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        # `fieldValueByName` is null for an item with no Status, and `content`
        # is null for an item whose issue was deleted. Coerce both to {} once,
        # rather than guarding at each field: GraphQL nulls are the normal case
        # here, not an error to detect.
        status_field = _as_dict(node.get("fieldValueByName"))
        content = _as_dict(node.get("content"))
        repository = _as_dict(content.get("repository"))

        status = status_field.get("name")
        repo = repository.get("nameWithOwner")
        number = content.get("number")
        state = content.get("state") or ""
        rows.append(
            {
                "id": node.get("id"),
                "key": (repo, number) if repo and number is not None else None,
                "repo": repo,
                "number": number,
                "type": content.get("__typename"),
                "title": content.get("title") or "",
                "url": content.get("url") or "",
                "status": status or None,
                # GraphQL reports OPEN / CLOSED for an Issue and OPEN / CLOSED /
                # MERGED for a PullRequest. A merged PR is finished work whose
                # card should read Done just as much as a closed issue's.
                "state": state.upper(),
            }
        )
    return rows


# --------------------------------------------------------------------------
# The set arithmetic — pure, and where the tests live
# --------------------------------------------------------------------------


def audit(expected, board_rows):
    """Compare the two sides and return the three sets plus both denominators.

    ``expected`` is ``collect_expected``'s mapping; ``board_rows`` is
    ``normalize_board_items``' output. Nothing here does I/O, so the comparison
    that ships is the comparison the tests run."""
    board_keys = {r["key"] for r in board_rows if r["key"]}

    ordered = sorted(expected, key=lambda k: (k[0], k[1]))
    missing_from_board = [expected[k] for k in ordered if k not in board_keys]

    statusless = [r for r in board_rows if not r["status"]]

    closed_not_done = [
        r for r in board_rows if r["state"] in ("CLOSED", "MERGED") and r["status"] != DONE_STATUS
    ]

    return {
        "expected_count": len(expected),
        "board_count": len(board_rows),
        "missing_from_board": missing_from_board,
        "statusless": statusless,
        "closed_not_done": closed_not_done,
    }


def has_findings(result):
    """True when any of the three sets is non-empty — the exit-code decision.

    Separated from ``main`` so the non-zero path can be asserted in-process. A
    test that runs the script as a subprocess against a clean board only ever
    exercises the zero path, and would pass against a ``main`` that returned 0
    unconditionally (the lesson recorded on #912/#927)."""
    return bool(result["missing_from_board"] or result["statusless"] or result["closed_not_done"])


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _describe(row):
    label = f"{row.get('repo')}#{row.get('number')}" if row.get("repo") else "(draft item)"
    status = row.get("status") or "-"
    title = (row.get("title") or "").strip()
    if len(title) > 70:
        title = title[:67] + "..."
    return f"{label} [{status}] {title}"


def render(result, org, project_number, board_title, repos):
    """Human-readable report. Both denominators go on the first line.

    Printing ``expected=N board=M`` is not decoration: run 62's audit reported
    "0 missing" off an expected set that was one item short, and the only way
    that was ever going to be visible was a denominator a reader could find
    suspicious (#966)."""
    lines = []
    lines.append(
        f"Agentic OS board audit — {org} project #{project_number}"
        + (f" ({board_title})" if board_title else "")
    )
    lines.append(
        f"expected={result['expected_count']} board={result['board_count']} "
        f"repos_swept={len(repos)}"
    )
    lines.append("")

    sections = [
        (
            "missing from board",
            result["missing_from_board"],
            "open agentic-os items with no card on the board",
        ),
        (
            "on board with no Status",
            result["statusless"],
            "cards that exist but were never placed in a column",
        ),
        (
            "closed/merged but not Done",
            result["closed_not_done"],
            "finished work whose card still reads as live",
        ),
    ]
    for name, rows, blurb in sections:
        lines.append(f"## {name}: {len(rows)}  ({blurb})")
        for row in rows:
            lines.append(f"  - {_describe(row)}")
        if not rows:
            lines.append("  (none)")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit the Agentic OS board (read-only).")
    ap.add_argument("--org", default=DEFAULT_ORG, help="Org owning the board (default: %(default)s)")
    ap.add_argument(
        "--project",
        type=int,
        default=DEFAULT_PROJECT_NUMBER,
        help="Project number (default: %(default)s)",
    )
    ap.add_argument(
        "--repo",
        action="append",
        dest="repos",
        help="Pin the swept repo set to owner/repo (repeatable). Default: every "
        "non-archived repo in the org, enumerated via REST.",
    )
    ap.add_argument("--json", action="store_true", help="Emit the result as JSON instead of prose")
    args = ap.parse_args(argv)

    # Issue titles in this org routinely carry emoji — the alert workflows open
    # issues titled "🚨 Scheduled workflow failing: …". Python encodes stdout
    # with the OS codepage (cp1252 on the Conductor's Windows host), which
    # raised UnicodeEncodeError on the sibling status generator until the caller
    # remembered PYTHONUTF8=1 (#945, ledger L35).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    token = _token()

    repos = sorted(set(args.repos)) if args.repos else list_org_repos(args.org, token)
    if HUB_REPO not in repos and not args.repos:
        # The hub is where the backlog lives; a listing that somehow omits it is
        # a broken enumeration, not a small one.
        raise SystemExit(
            f"error: org repo listing did not include {HUB_REPO}. Refusing to audit "
            "against a repo set that is missing the hub."
        )

    expected = collect_expected(repos, token)
    board_title, nodes = fetch_board_items(args.org, args.project, token)
    board_rows = normalize_board_items(nodes)
    result = audit(expected, board_rows)

    if args.json:
        print(
            json.dumps(
                {
                    "org": args.org,
                    "project": args.project,
                    "board_title": board_title,
                    "repos_swept": repos,
                    **result,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render(result, args.org, args.project, board_title, repos))

    return 1 if has_findings(result) else 0


if __name__ == "__main__":
    sys.exit(main())
