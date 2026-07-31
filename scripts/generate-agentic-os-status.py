#!/usr/bin/env python3
"""Generate the Agentic OS public status feed (``agentic-os-status.json``).

Part of the Agentic OS visibility layer (backlog #723, Half 1). Emits a
PII-safe, public-by-default JSON snapshot of the whole agentic pipeline so the
``/agentic-os`` page on ffcadmin.org can render it statically (the same
generate-here / deliver-to-ffcadmin pipe the workflow catalog already uses via
workflow 502's ``deliver`` job).

Scope is **org-wide** for the two panels that mirror the agent pickup query, and
deliberately hub-only for the one that does not (#925). AGENTS.md tells agents to
pick work with ``org:FreeForCharity label:agentic-os is:open``; a public page
titled "the backlog" that showed one repository of that set was quietly partial —
not empty, not visibly broken, and with no denominator that would reveal it.

The feed contains:
  * ``backlog_issues`` — open issues labeled ``agentic-os`` across **every** repo
    in the org (sandboxed agents pick these up). Each row carries its ``repo``,
    because ``#744`` is ambiguous across repositories.
  * ``in_flight_prs`` — open PRs that are agent work on that backlog: labeled
    ``agentic-os``, or referencing an ``agentic-os``-labeled issue **in the same
    repository**. The rule is emitted alongside the rows as
    ``in_flight_prs_rule``, and the unfiltered open-PR count as
    ``open_prs_total`` — over exactly the same repositories, since a fraction
    whose halves span different scopes is worse than no fraction — so a panel
    reading zero can be told apart from a panel that is broken (#909).
  * ``repos`` — the repositories those two panels actually swept, so the next
    partial-panel defect is visible on the page rather than only in the code.
  * ``conductor_log`` — the last 10 comments on the pinned Conductor log issue
    (#719), each body truncated to 500 chars. Hub-only: there is one log.
  * ``pending_gates`` — workflow runs sitting at an environment approval gate
    (``status=waiting``): workflow name, environment, created-at. **Hub-only by
    design** — FFC environments exist only on the automation repo — and
    ``pending_gates_scope`` says so in the feed rather than leaving a reader to
    assume gates are org-wide too.

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

# The hub. Gates and the Conductor log are hub concepts; the backlog and the
# in-flight PRs are not (see DEFAULT_ORG).
DEFAULT_REPO = "FreeForCharity/FFC-Cloudflare-Automation"
DEFAULT_ORG = "FreeForCharity"
LABEL = "agentic-os"
# The band label (#922). `agentic-os` stays the program-wide topic label on
# everything; `agent-ready` marks the strict subset an agent can pick up now.
READY_LABEL = "agent-ready"
READY_RULE = (
    "Open issues labeled 'agent-ready': unclaimed, unblocked, one-PR-scoped and carrying "
    "acceptance criteria — the queue a sandboxed agent can pick from now. A strict subset of "
    "backlog_issues, which stays the full 'agentic-os' topic set (epics, machine-managed rolling "
    "issues, human-blocked items and durable findings all remain counted there)."
)
CONDUCTOR_LOG_ISSUE = 719
CONDUCTOR_LOG_LIMIT = 10
COMMENT_TRUNCATE = 500
API_ROOT = "https://api.github.com"
USER_AGENT = "ffc-agentic-os-status-generator"
HTTP_TIMEOUT = 30  # seconds; fail closed rather than hang the daily sync.

# Where a run has to live to count as an FFC gate. GitHub parks its own
# platform agents in `status=waiting` too — the Copilot code-review agent runs
# at `dynamic/agents/copilot-pull-request-reviewer` on a platform-managed
# `copilot` environment, which no FFC reviewer can approve or decline and which
# appears in no row of docs/workflow-safety-and-approvals.md. Publishing it as
# "a workflow run paused awaiting a human" is simply false, and because Copilot
# reviews every push the window is open constantly.
WORKFLOW_PATH_PREFIX = ".github/workflows/"

# The in-flight inclusion rule, stated in the feed itself. A public panel that
# renders a bare count gives a reader no way to tell "nothing is in flight" from
# "the filter is dead" — which is exactly how #909 survived unnoticed.
IN_FLIGHT_RULE = (
    "Open pull requests that are agent work on this backlog, across every repository listed "
    "in `repos` — the same org-wide scope as the backlog above, and as the pickup query in "
    "AGENTS.md. A PR is listed when it carries the agentic-os label, or when its body "
    "references an agentic-os issue in its OWN repository (e.g. 'Closes #123' / 'Refs #123'); "
    "a bare #123 is repo-local, so the same number in two repos is two different issues. "
    "Referenced-issue labels are what decide it — nothing labels the pull requests "
    "themselves. `open_prs_total` is the unfiltered open-PR count over exactly these same "
    "repositories."
)
# Criterion 5 of #925: gates are legitimately hub-only, and the page has to say
# so. Left implicit, a reader who has just been told the backlog is org-wide will
# read an empty gate panel as "no gate anywhere in the org".
PENDING_GATES_SCOPE = (
    "Environment approval gates are a hub concept: FFC's deployment environments live on "
    "{repo} alone, so — unlike the backlog and in-flight PR panels above, which are "
    "org-wide — this panel covers only that repository."
)
# Bare `#N` mentions are matched, not just Closes/Refs/Fixes, because agent PR
# bodies routinely reference an issue in prose or in a comma-joined list
# ("Refs #889, #841"). The 1-5 digit bound is what excludes the common
# 6-hex-digit colour (`#909090` never matches); a 3-digit all-numeric colour
# (`#909`) and any bare number that is not an issue still can, and those are
# dropped when the lookup 404s — see _issue_is_agentic.
_ISSUE_REF_RE = re.compile(r"(?:^|[^\w&])#(\d{1,5})\b")
# Bound on per-run lookups for referenced numbers not already in the backlog.
# The backlog answers most of them for free; this caps the tail so a PR body
# full of `#N` noise cannot turn the daily feed into a rate-budget problem.
MAX_ISSUE_LOOKUPS = 30

# GitHub's Search API returns at most this many results for any one query and
# then simply stops paginating — WITHOUT setting `incomplete_results`. See
# _assert_search_complete: this is why the flag alone is not a sufficient guard.
SEARCH_RESULT_CAP = 1000

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


def _request(path_or_url, token, params=None, soft_fail=False):
    """Perform ONE GET and return ``(payload, link_header)``. No pagination.

    ``soft_fail=True`` returns ``(None, None)`` on an HTTP error instead of
    aborting the run. Used only for speculative lookups (does referenced number
    N name an agentic-os issue?), where a 404 is an expected answer — "no" — and
    must never take the whole daily feed down with it."""
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
        if soft_fail:
            return None, None
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


def _repo_full_name(item):
    """``owner/repo`` for a search result, from its ``repository_url``.

    Search results are the only place the repo is not already in hand, and the
    API gives it as an API URL rather than a name."""
    url = item.get("repository_url") or ""
    marker = "/repos/"
    idx = url.find(marker)
    if idx == -1:
        return None
    return url[idx + len(marker) :].strip("/") or None


def search_agentic_items(org, token):
    """Every OPEN issue AND pull request in ``org`` carrying the agentic-os label.

    Deliberately one search with no ``is:issue`` filter: the search endpoint
    returns both kinds, a PR is distinguishable by its ``pull_request`` key, and
    that single call therefore yields the backlog *and* the set of repositories
    worth sweeping for in-flight PRs. Matching AGENTS.md's pickup query
    (``org:FreeForCharity label:agentic-os is:open``) is the point — this is the
    query the public page claims to be showing.

    Using search rather than a hardcoded repo list is what keeps #925 fixed: a
    list would have to be edited the first time an agent files an issue in a new
    repo, and nothing would fail if it were not.

    Aborts on ``incomplete_results``, and separately on any shortfall against
    the reported ``total_count`` — see ``_assert_search_complete`` for why one
    flag is not enough."""
    query = f"org:{org} label:{LABEL} is:open"
    url = _build_url("search/issues", {"q": query, "per_page": "100"})
    items = []
    total_count = None
    while url:
        payload, link = _request(url, token)
        if not isinstance(payload, dict):
            raise SystemExit(f"error: unexpected search response shape for query {query!r}")
        if payload.get("incomplete_results"):
            raise SystemExit(
                f"error: GitHub reported incomplete_results for {query!r}. The backlog "
                "would be published as complete while missing rows; re-run rather than "
                "shipping a silently-truncated feed."
            )
        if total_count is None:
            total_count = payload.get("total_count")
        items.extend(payload.get("items", []))
        url = _link_rel(link, "next")
    _assert_search_complete(query, items, total_count)
    return items


def _assert_search_complete(query, items, total_count):
    """Abort unless every result the search reports was actually retrieved.

    ``incomplete_results`` covers exactly one truncation mode — a server-side
    timeout. The Search API **also** caps any query at ``SEARCH_RESULT_CAP``
    results and simply stops paginating, with ``incomplete_results`` false: a
    clean, complete-looking response that is missing rows. A guard that trusted
    the flag alone would publish a partial org-wide backlog as the whole one,
    which is precisely the defect this module was rewritten to make impossible.

    Comparing what came back against the ``total_count`` GitHub itself reports
    catches both modes with one check — the same "denominator over the same
    scope" discipline #925 applies to ``open_prs_total``.

    Only a **shortfall** aborts. Retrieving more than ``total_count`` means an
    item was added mid-pagination, which costs nothing; the reverse (an item
    closed mid-pagination) is a narrow race that a re-run clears, and the
    message says so rather than sending an operator hunting a cap that is not
    the cause."""
    if total_count is None:
        raise SystemExit(
            f"error: the search response for {query!r} carried no total_count, so the "
            "result set cannot be confirmed complete. Refusing to publish a backlog whose "
            "completeness is unknown."
        )
    if len(items) >= total_count:
        return
    if total_count > SEARCH_RESULT_CAP:
        raise SystemExit(
            f"error: {query!r} matches {total_count} items, past the Search API's "
            f"{SEARCH_RESULT_CAP}-result cap; only {len(items)} could be retrieved. One "
            "query can no longer cover the org at this size — split the sweep (e.g. "
            "per-repo) rather than publishing a partial backlog."
        )
    raise SystemExit(
        f"error: retrieved {len(items)} of {total_count} results for {query!r}. The "
        "backlog would be published as complete while missing rows. If the set changed "
        "mid-pagination, a re-run clears this."
    )


def collect_backlog(items):
    """Open issues labeled agentic-os, org-wide.

    ``items`` is ``search_agentic_items``' output; PRs are excluded here (they
    carry a ``pull_request`` key) and picked up by ``collect_in_flight_prs``
    instead. Every row carries its ``repo`` — "#744" names a different issue in
    each repository, so a bare number is not an identifier on an org-wide page."""
    issues = []
    for it in items:
        if "pull_request" in it:
            continue
        issues.append(
            {
                "repo": _repo_full_name(it),
                "number": it["number"],
                "title": it["title"],
                "state": it["state"],
                "assignee": _assignee(it),
                "updated_at": it["updated_at"],
                "url": it["html_url"],
                "labels": [lbl["name"] for lbl in it.get("labels", [])],
            }
        )
    # Repo + number break ties so the daily feed is stable rather than
    # re-ordering itself whenever two repos share an updated_at.
    issues.sort(key=lambda x: (x["updated_at"], x["repo"] or "", x["number"]), reverse=True)
    return issues


def collect_ready(backlog):
    """The subset of the backlog a sandboxed agent can actually pick up (#922).

    ``agentic-os`` is one label doing five jobs — executable work-items, epics,
    machine-managed rolling issues, items blocked on a human, and findings kept
    as durable records. Only the first is what the Conductor's "keep 5-15 open"
    band was ever about, so counting the label counted the wrong set: the band
    read 46 while the executable queue was ~30, and it drifted upward for eight
    consecutive runs of trimming that could never converge.

    ``agent-ready`` carries that meaning explicitly: unclaimed, unblocked,
    one-PR-scoped, with acceptance criteria. This filters rather than reclassifies
    — the label is applied by a human or a conductor run, never inferred here,
    because "can an agent finish this in one PR" is a judgement and guessing it
    from an issue body is how the original conflation happened.
    """
    return [i for i in backlog if READY_LABEL in (i.get("labels") or [])]


def scope_repos(items, hub_repo):
    """Repositories the org-wide panels sweep: every repo carrying an open
    agentic-os item, plus the hub.

    The hub is unconditional so ``open_prs_total`` does not silently lose its
    largest contributor on a day when every hub backlog issue happens to be
    closed."""
    repos = {r for r in (_repo_full_name(i) for i in items) if r}
    repos.add(hub_repo)
    return sorted(repos)


def referenced_issue_numbers(body):
    """Issue numbers referenced by a PR body, in first-seen order."""
    if not body:
        return []
    seen, out = set(), []
    for match in _ISSUE_REF_RE.finditer(body):
        num = int(match.group(1))
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def _issue_is_agentic(repo, number, token, cache):
    """Does ``number`` name an agentic-os-labeled ISSUE (not a PR)?

    Cached per run, and deliberately fail-closed on anything unresolvable: a
    404 (the number was a hex colour or a nonexistent issue), a soft-failed
    request, or a number that turns out to be a pull request all answer False.
    Counting a PR reference would be circular — PR bodies cross-reference each
    other constantly.

    The cache is keyed on ``(repo, number)``, not on the bare integer. A `#N` in
    a PR body resolves against that PR's OWN repository, so #730 in ffcadmin and
    #730 in the hub are different issues; an integer-keyed cache would let the
    first one answered decide the other, inventing a link that does not exist.
    The ``MAX_ISSUE_LOOKUPS`` budget stays global — it bounds requests per run,
    and requests cost the same whichever repo they hit."""
    key = (repo, number)
    if key in cache:
        return cache[key]
    if len(cache) >= MAX_ISSUE_LOOKUPS:
        return False
    payload, _ = _request(f"repos/{repo}/issues/{number}", token, soft_fail=True)
    verdict = False
    if isinstance(payload, dict) and "pull_request" not in payload:
        verdict = LABEL in [lbl.get("name") for lbl in payload.get("labels", [])]
    cache[key] = verdict
    return verdict


def collect_in_flight_prs(repos, token, backlog_issues=None):
    """Open PRs that are agent work on this backlog, across ``repos``.

    A PR qualifies when it carries the agentic-os label **or** its body
    references an agentic-os-labeled issue in its own repo. See
    ``IN_FLIGHT_RULE``.

    The label alone was the previous rule and it could never match: workflow 737
    labels linked *issues*, and nothing labels the pull requests, so the public
    panel read 0 while a dozen agent PRs were open (#909). Resolution now runs
    in the direction the data actually flows — from the PR to the issue it
    references — so the panel does not depend on labelling discipline that has
    never held.

    ``backlog_issues`` (the already-collected open agentic-os issues) answers
    most references for free; only numbers outside that set cost a lookup, and
    those are bounded and cached. It is matched on ``(repo, number)`` for the
    reason given in ``_issue_is_agentic``: agent PRs in different repos routinely
    carry overlapping issue numbers, and an integer-keyed match would cross them.

    Returns ``(prs, open_prs_total)``. The total is unfiltered but spans exactly
    the repositories swept here, so the fraction it denominates is honest — and a
    zero panel stays distinguishable from a dead one."""
    known = {(i["repo"], i["number"]) for i in (backlog_issues or ())}
    cache = {}
    prs = []
    open_prs_total = 0
    for repo in repos:
        raw = rest_get(
            f"repos/{repo}/pulls",
            token,
            params={"state": "open", "per_page": "100"},
        )
        open_prs_total += len(raw)
        for pr in raw:
            labels = [lbl["name"] for lbl in pr.get("labels", [])]
            linked = []
            for num in referenced_issue_numbers(pr.get("body")):
                if (repo, num) in known or _issue_is_agentic(repo, num, token, cache):
                    linked.append(num)
            if LABEL not in labels and not linked:
                continue
            prs.append(
                {
                    "repo": repo,
                    "number": pr["number"],
                    "title": pr["title"],
                    "state": pr["state"],
                    "draft": pr.get("draft", False),
                    "assignee": _assignee(pr),
                    "updated_at": pr["updated_at"],
                    "url": pr["html_url"],
                    "labels": labels,
                    "linked_agentic_issues": linked,
                }
            )
    prs.sort(key=lambda x: (x["updated_at"], x["repo"], x["number"]), reverse=True)
    return prs, open_prs_total


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


def is_repo_workflow_run(run):
    """True when a waiting run is one of this repo's own workflows.

    Discriminates on `path` rather than on the environment or workflow name: a
    denylist of environment names would need editing for every future
    platform-managed agent, while `.github/workflows/` is where every FFC
    workflow lives by definition (AGENTS.md, "Adding or changing a workflow").

    Fails **open** on an absent path. Hiding a gate that really is waiting on a
    reviewer is a worse failure for this page than showing one extra row, so an
    unrecognisable run is published rather than dropped.
    """
    path = run.get("path")
    if not path:
        return True
    return path.startswith(WORKFLOW_PATH_PREFIX)


def collect_pending_gates(repo, token):
    """Workflow runs waiting at an environment approval gate. The waiting run
    list names the workflow; the per-run pending_deployments call names the
    environment(s) still awaiting approval.

    Only this repo's own workflows are reported — see `is_repo_workflow_run`."""
    runs_payload = rest_get(
        f"repos/{repo}/actions/runs",
        token,
        params={"status": "waiting", "per_page": "100"},
    )
    runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, dict) else []
    gates = []
    for run in runs:
        if not is_repo_workflow_run(run):
            continue
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


def build_feed(repo, token, org=DEFAULT_ORG):
    """Assemble the feed. ``repo`` is the hub (Conductor log + gates); ``org`` is
    the scope of the backlog and in-flight PR panels."""
    items = search_agentic_items(org, token)
    backlog = collect_backlog(items)
    repos = scope_repos(items, repo)
    in_flight, open_prs_total = collect_in_flight_prs(repos, token, backlog_issues=backlog)
    # Derive the ready queue once: the list and its count must describe the same
    # selection, and two calls could drift if collect_ready ever gains ordering
    # or side effects.
    ready = collect_ready(backlog)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "org": org,
        "repo": repo,
        "repos": repos,
        "backlog_issues": backlog,
        # ADDITIVE (#922). `backlog_issues` keeps its exact shape and contents —
        # the ffcadmin renderer reads it, and narrowing it would silently drop
        # epics and records from the public page. The ready queue is a strict
        # subset alongside it.
        "ready_issues": ready,
        "ready_count": len(ready),
        "ready_rule": READY_RULE,
        "in_flight_prs": in_flight,
        "in_flight_prs_rule": IN_FLIGHT_RULE,
        "open_prs_total": open_prs_total,
        "conductor_log": collect_conductor_log(repo, token),
        "pending_gates": collect_pending_gates(repo, token),
        "pending_gates_scope": PENDING_GATES_SCOPE.format(repo=repo),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="Hub owner/repo — Conductor log and approval gates (default: FFC automation)",
    )
    ap.add_argument(
        "--org",
        default=DEFAULT_ORG,
        help="Org swept for the backlog and in-flight PRs (default: %(default)s)",
    )
    ap.add_argument("--output", help="Write JSON here instead of stdout")
    args = ap.parse_args()

    token = _token()
    feed = build_feed(args.repo, token, org=args.org)
    text = json.dumps(feed, indent=2, ensure_ascii=False) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        counts = (
            f"{len(feed['backlog_issues'])} issues, "
            f"{len(feed['in_flight_prs'])} of {feed['open_prs_total']} open PRs "
            f"across {len(feed['repos'])} repo(s), "
            f"{len(feed['conductor_log'])} log entries, "
            f"{len(feed['pending_gates'])} pending gates"
        )
        print(f"Wrote {args.output} ({counts}).")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
