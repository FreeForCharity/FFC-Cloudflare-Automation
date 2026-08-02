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

Latency is not drift (#992)
---------------------------
The missing-card set answers "which items have no card", and that question has
two very different answers folded into it: a PR a worker opened 35 minutes ago
that nobody has been awake to place, and run 62's board, which sat six items
short for six hours. Only the second is drift. With ~9.6 new agentic-os items a
day and a 07:47Z cron, failing on the first makes this audit red most mornings —
and 745 is on 740's watch list, so a red morning becomes a rolling alert issue
that reopens daily. An alert that fires most days is one nobody reads, and the
day it catches a real six-hour gap it will look like the twenty days before it.

So ``--grace-minutes`` splits the missing set. An uncarded item younger than the
window is reported under ``recently_opened_not_yet_carded`` and does **not**
fail the run; the same item, older, is a finding exactly as before. The window
applies to the missing set **only**: a card with no Status is not "recently
added", it is misfiled from the moment it exists, and finished work whose card
still reads live is not excused by elapsed time.

``self_referential_alerts`` closes the other half, which the window cannot reach
(#992, AC8). 740 upserts ``🚨 Scheduled workflow failing: 745…`` labelled
``agentic-os``; nothing auto-adds it to the board; so 745's next run reports its
own failure alert as a finding, stays red, and keeps the alert open. That loop
sustains itself and has done so twice. The alert is created minutes *after* a
07:47Z tick and is therefore ~22h old at the next one — far outside any sane
grace window — so age is the wrong lever. 745's own alert is instead recognised
by 740's body marker and deferred at any age. Alerts about *other* workflows are
ordinary uncarded items and keep failing: the exclusion is exactly as wide as
the feedback loop and no wider.

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
  0  board is consistent with the backlog — all three FAILING sets empty. The
     two deferred sets may be non-empty here; that is the point of them.
  1  at least one failing set is non-empty (the audit found something), OR any
     API, auth or config error. **Never 0 on a failed enumeration**: an audit
     that cannot read one of its two sides has no verdict to report, and
     reporting "clean" would be the exact silence-read-as-green shape #966 is
     about.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_ORG = "FreeForCharity"
DEFAULT_PROJECT_NUMBER = 9
HUB_REPO = "FreeForCharity/FFC-Cloudflare-Automation"
LABEL = "agentic-os"
DONE_STATUS = "Done"

# How long an uncarded item is treated as latency rather than drift. 90 minutes
# is the cron-to-Conductor gap being tolerated, not a guess at how long carding
# "should" take — the audit ticks at 07:47Z and a human gets to the board when
# they get to it. Widening this trades detection latency for quiet; it must
# never widen so far that run 62's six-hour gap would be deferred.
DEFAULT_GRACE_MINUTES = 90

# 740 marks each rolling alert issue with the watched workflow's name, so one
# workflow's alert can never be confused with another's
# (740-scheduled-workflow-failure-alert.yml). This is the marker for THIS
# audit's own workflow — the one item whose presence in the finding set is
# caused by the finding set.
#
# Pinned as a literal rather than read from the YAML because this script must
# stay import-clean and dependency-free; `test_745_board_audit.py` asserts the
# name against `.github/workflows/745-agentic-os-board-audit.yml`, so a rename
# fails a test rather than silently reopening the loop.
SELF_WORKFLOW_NAME = "745. Repo - Agentic OS Board Audit [GH]"
SELF_ALERT_MARKER = f"<!-- scheduled-workflow-failure-alert:{SELF_WORKFLOW_NAME} -->"

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
                # The grace window's input. Kept as the raw API string so the
                # comparison lives in `audit()`, where it is testable without a
                # network.
                "created_at": it.get("created_at") or "",
                # Resolved HERE, and the body deliberately not carried forward:
                # every value in this dict is serialized into the --json report,
                # and 740's alert bodies grow an entry per failed run.
                "is_self_alert": is_self_referential_alert(it),
            }
    return expected


def is_self_referential_alert(issue):
    """True when ``issue`` is 740's rolling failure alert **for this audit**.

    Matched on 740's body marker rather than on the title, the author, or the
    ``bug`` label. The title carries an emoji and the workflow's display name
    and would drift on any rename; the author is whatever token 740 ran under.
    The marker is the identifier 740 itself relies on to keep one workflow's
    alert from closing another's, so it is the narrowest true statement
    available — and it names the workflow, which is what makes this test
    *self*-referential rather than "ignore bot issues".

    A pull request is never one of these: 740 opens issues, and `open.find`
    in 740 explicitly skips PRs for the same reason (a PR quoting the marker
    must not be mistaken for the alert)."""
    if not isinstance(issue, dict):
        return False
    if "pull_request" in issue:
        return False
    body = issue.get("body")
    return isinstance(body, str) and SELF_ALERT_MARKER in body


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


def parse_timestamp(value):
    """A GitHub ISO-8601 timestamp as an aware UTC datetime, or ``None``.

    ``None`` for anything unparseable, and every caller treats ``None`` as "too
    old to defer" — an item whose age cannot be established must not be excused
    by a window that is defined in terms of its age."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_in_minutes(created_at, now):
    """Minutes between ``created_at`` and ``now``, or ``None`` if unparseable."""
    created = parse_timestamp(created_at)
    if created is None:
        return None
    return (now - created).total_seconds() / 60.0


def audit(expected, board_rows, grace_minutes=0, now=None):
    """Compare the two sides and return the five sets plus both denominators.

    ``expected`` is ``collect_expected``'s mapping; ``board_rows`` is
    ``normalize_board_items``' output. Nothing here does I/O, so the comparison
    that ships is the comparison the tests run — hence ``now`` being injectable
    rather than read from the clock inside.

    Three of the five sets fail the run (see ``has_findings``). The other two —
    ``recently_opened_not_yet_carded`` and ``self_referential_alerts`` — are
    reported and do not. ``grace_minutes`` defaults to 0 so that calling this
    with two arguments keeps the pre-#992 behaviour exactly: no deferral at all.

    Note the ORDER of the two deferral tests below. The self-alert check comes
    first and is unconditional on age, because the 740/745 loop is not a latency
    problem and a window that would catch it would have to be ~22 hours wide,
    which would defer run 62's six-hour gap as well — the exact regression #992
    warns the fix must not degrade into."""
    now = now or datetime.now(timezone.utc)
    board_keys = {r["key"] for r in board_rows if r["key"]}

    missing_from_board = []
    recently_opened_not_yet_carded = []
    self_referential_alerts = []

    for key in sorted(expected, key=lambda k: (k[0], k[1])):
        if key in board_keys:
            continue
        item = dict(expected[key])
        age = age_in_minutes(item.get("created_at"), now)
        item["age_minutes"] = None if age is None else round(age, 1)

        if item.get("is_self_alert"):
            self_referential_alerts.append(item)
        elif age is not None and age < grace_minutes:
            recently_opened_not_yet_carded.append(item)
        else:
            missing_from_board.append(item)

    # No window on either of these, at any age. A card with no Status is not
    # "recently added" — it is misfiled from the moment it exists — and closed
    # work whose card still reads live is not excused by having been closed a
    # long time ago. #992 AC4.
    statusless = [r for r in board_rows if not r["status"]]

    closed_not_done = [
        r for r in board_rows if r["state"] in ("CLOSED", "MERGED") and r["status"] != DONE_STATUS
    ]

    return {
        "expected_count": len(expected),
        "board_count": len(board_rows),
        "grace_minutes": grace_minutes,
        "missing_from_board": missing_from_board,
        "statusless": statusless,
        "closed_not_done": closed_not_done,
        "recently_opened_not_yet_carded": recently_opened_not_yet_carded,
        "self_referential_alerts": self_referential_alerts,
    }


# The sets that fail the run, and the sets that are reported without failing it.
# Named once, here, so `has_findings`, `render` and the JS library cannot drift
# into disagreeing about which is which.
FAILING_SECTIONS = ("missing_from_board", "statusless", "closed_not_done")
DEFERRED_SECTIONS = ("recently_opened_not_yet_carded", "self_referential_alerts")


def has_findings(result):
    """True when any of the three FAILING sets is non-empty — the exit code.

    Separated from ``main`` so the non-zero path can be asserted in-process. A
    test that runs the script as a subprocess against a clean board only ever
    exercises the zero path, and would pass against a ``main`` that returned 0
    unconditionally (the lesson recorded on #912/#927).

    The deferred sets are deliberately absent from this expression. That is the
    whole change in #992, and it is also the sentence to read twice before
    adding a fourth section: anything listed here fails the run and therefore
    feeds 740."""
    return any(result[key] for key in FAILING_SECTIONS)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _describe(row):
    """One report line for a board row.

    Three kinds of card reach this, and they need three different remedies, so
    they must not share a label. A `repo` means a real issue or PR. A
    `DraftIssue` is a card someone typed straight onto the board — give it a
    Status, or convert it. Anything else has **null content**: the issue or PR
    was deleted, or it lives in a repository this token cannot read. Calling
    that third case "(draft item)" — as the first version of this function did —
    sends whoever triages the statusless set hunting for a draft that does not
    exist, and hides the one row that may need the card deleted outright."""
    if row.get("repo"):
        label = f"{row['repo']}#{row['number']}"
    elif row.get("type") == "DraftIssue":
        label = "(draft item)"
    else:
        label = "(no readable content: deleted, or in a repo this token cannot see)"
    status = row.get("status") or "-"
    title = (row.get("title") or "").strip()
    if len(title) > 70:
        title = title[:67] + "..."
    # The age is what makes a deferred row auditable: a reader has to be able to
    # see that the thing was deferred for being 20 minutes old and not for being
    # invisible. Suppressing these rows entirely would trade a noisy monitor for
    # a blind one (#992 AC3).
    age = format_age(row.get("age_minutes"))
    suffix = f" ({age} old)" if age else ""
    return f"{label} [{status}] {title}{suffix}"


def format_age(minutes):
    """A short human age for a report line, or ``""`` when it is unknown."""
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
        return ""
    if minutes < 0:
        return "0m"
    if minutes < 90:
        return f"{int(round(minutes))}m"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / 1440:.1f}d"


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
        f"repos_swept={len(repos)} grace_minutes={result.get('grace_minutes', 0)}"
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
    deferred = [
        (
            "recently opened, not yet carded",
            result.get("recently_opened_not_yet_carded") or [],
            f"younger than the {result.get('grace_minutes', 0)}-minute grace window — "
            "latency, not drift; does NOT fail the run",
        ),
        (
            "this audit's own failure alert",
            result.get("self_referential_alerts") or [],
            "740's rolling alert for 745 — counting it would keep 745 red and the alert "
            "open; does NOT fail the run",
        ),
    ]
    for name, rows, blurb in sections + deferred:
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
    ap.add_argument(
        "--grace-minutes",
        type=int,
        default=DEFAULT_GRACE_MINUTES,
        metavar="N",
        help="Treat an uncarded item younger than N minutes as latency rather than "
        "drift: report it separately and do not fail the run (default: %(default)s). "
        "Applies to the missing-card set ONLY — a card with no Status, or closed work "
        "not marked Done, is drift at any age. Pass 0 to disable.",
    )
    ap.add_argument("--json", action="store_true", help="Emit the result as JSON instead of prose")
    args = ap.parse_args(argv)

    if args.grace_minutes < 0:
        # Not clamped to 0: a negative window is a caller who meant something
        # this script cannot do, and silently reinterpreting it as "no window"
        # would hide that.
        ap.error("--grace-minutes must be >= 0 (0 disables the window)")

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
    result = audit(expected, board_rows, grace_minutes=args.grace_minutes)

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
