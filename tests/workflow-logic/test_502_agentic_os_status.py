"""Unit tests for scripts/generate-agentic-os-status.py.

The generator is a standalone REST-only script invoked by workflow 502's
`deliver` job to emit the public `agentic-os-status.json` feed. These tests lock
down its pure transformation logic — no network — by monkeypatching the single
network primitive (`_request`, which returns `(payload, link_header)`) with
URL-keyed fixtures. That exercises the real pagination / last-page logic too.

Locked down here:
  * the backlog and the in-flight PR panels are ORG-WIDE (#925) — sourced from
    one `search/issues` call matching AGENTS.md's pickup query, paginated, and
    aborting rather than publishing a partial set on `incomplete_results`;
  * every backlog and PR row carries its `repo`, and `open_prs_total` is summed
    over exactly the repositories swept, so the fraction's halves agree;
  * a `#N` in a PR body resolves against that PR's OWN repository — the
    cross-repo collision case is pinned, because an integer-keyed match would
    invent links between repos that share issue numbers;
  * backlog excludes PRs (search returns both, and PRs carry `pull_request`);
  * in-flight PRs are matched by label OR by a referenced agentic-os issue, so
    an unlabelled agent PR is counted (#909: nothing labels PRs in this repo,
    so the label-only filter could never match and the panel read a structural
    zero). A PR referencing only a non-agentic issue, or only another PR, is
    still excluded;
  * the feed carries the inclusion rule and the unfiltered open-PR total, so a
    zero panel is distinguishable from a dead one;
  * gates stay hub-only and the feed says so (`pending_gates_scope`);
  * Conductor-log fetch reads only the LAST comment page (+ prev when the last
    holds fewer than the limit) — constant cost as #719 grows, NOT the whole
    thread;
  * Conductor-log bodies are redacted then truncated to 500 chars;
  * redaction masks GitHub/Slack/AWS tokens AND full PEM key blocks (body, not
    just the header) while leaving git SHAs and ordinary prose intact;
  * the ready queue excludes `claimed` as well as filtering on `agent-ready`
    (#974) — the two labels coexist by design, so `agent-ready` alone published
    every live claim as pickable work;
  * waiting runs are shaped into (run_id, workflow_name, environment, ...).

Run: python3 tests/workflow-logic/test_502_agentic_os_status.py
"""

from __future__ import annotations

import builtins
import importlib.util
import json
import pathlib
import tempfile
import re
import sys
import urllib.parse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate-agentic-os-status.py"

# run_all.py compares each module's reported roster against its `def test_*`
# count, so a module that dies partway through cannot pass itself off as a pass
# list (L82). This module is the documented exception: it runs one `main()` of
# inline `check(...)` assertions and prints a single summary line. Declared
# explicitly rather than inferred from the counts -- a module that silently
# stops following the roster convention must be caught, not excused.
RUN_ALL_ROSTER_EXEMPT = (
    "runs a single main() of check(...) assertions and prints one summary line, "
    "not a per-test roster"
)

ORG = "FreeForCharity"
HUB = "FreeForCharity/FFC-Cloudflare-Automation"
ADMIN = "FreeForCharity/FFC-IN-ffcadmin.org"  # the repo #925 found invisible

SEARCH_PAGE2 = "https://api.github.com/search/issues?q=x&per_page=100&page=2"

COMMENTS = "/issues/719/comments"
LAST_URL = (
    "https://api.github.com/repos/FreeForCharity/FFC-Cloudflare-Automation"
    "/issues/719/comments?per_page=100&page=3"
)
PREV_URL = (
    "https://api.github.com/repos/FreeForCharity/FFC-Cloudflare-Automation"
    "/issues/719/comments?per_page=100&page=2"
)

_GH_TOKEN = "ghp_" + "A" * 32  # a fake token shape for the redaction test
# Assemble the fake key markers from fragments so the literal block never appears
# in this source file (the repo's edit hook blocks committed private-key blocks).
_KW = "PRIV" + "ATE KEY"
_PEM = (
    f"-----BEGIN RSA {_KW}-----\n"
    "MIIBOgIBAAJBAKtokenbodyAAAA\nBBBBCCCCDDDD\n"
    f"-----END RSA {_KW}-----"
)


# The exact `READY_RULE` that shipped before #974 — prose promising "unclaimed"
# over a `collect_ready()` that never checked the label. Kept verbatim so the
# rule-wording assertion can be shown to REJECT it; a predicate that accepts this
# string is not testing anything.
PRE_974_READY_RULE = (
    "Open issues labeled 'agent-ready': unclaimed, unblocked, one-PR-scoped and carrying "
    "acceptance criteria — the queue a sandboxed agent can pick from now. A strict subset of "
    "backlog_issues, which stays the full 'agentic-os' topic set (epics, machine-managed rolling "
    "issues, human-blocked items and durable findings all remain counted there)."
)


def _comment(login, ts, body):
    return {"user": {"login": login}, "created_at": ts, "body": body, "html_url": f"c-{ts}"}


def _in(repo, item):
    """Tag a search-result item with the repo it lives in, the way GitHub does."""
    return dict(item, repository_url=f"https://api.github.com/repos/{repo}")


def _load():
    spec = importlib.util.spec_from_file_location("agentic_os_status", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_fake_request(m, call_log):
    """Return a fake _request(path_or_url, token, params=None) -> (payload, link)."""
    # Conductor-log pages. Last page (page 3) has 2 comments (< limit) so the
    # collector must also fetch prev (page 2) with 8 fillers -> 10 total.
    prev_page = [_comment("clarkemoyer", f"2026-07-18T00:0{i}:00Z", f"filler {i}") for i in range(8)]
    last_page = [
        _comment("clarkemoyer", "2026-07-19T00:00:00Z", "x" * 600),
        _comment(
            "clarkemoyer",
            "2026-07-19T00:01:00Z",
            f"run end; token={_GH_TOKEN} key {_PEM} sha 1234567890abcdef1234567890abcdef12345678",
        ),
    ]

    # --- org-wide search, served across TWO pages so the pagination loop runs ---
    # Page 1 is the hub; page 2 is ffcadmin, the repo #925 found invisible. If
    # the collector stopped at page 1 (or dropped `rel=next`), ffcadmin would
    # vanish exactly as it did on the live page.
    search_page1 = [
        _in(HUB, {
            "number": 730, "title": "Stale branch cleanup", "state": "open", "assignee": None,
            "updated_at": "2026-07-19T01:08:32Z", "html_url": "i730",
            "labels": [{"name": "agentic-os"}],
        }),
        _in(HUB, {  # a labelled PR comes back from search too — not backlog
            "number": 900, "title": "PR via search", "state": "open",
            "assignee": {"login": "bot"}, "updated_at": "2026-07-19T02:00:00Z",
            "html_url": "pr900", "labels": [{"name": "agentic-os"}], "pull_request": {"url": "..."},
        }),
        # #974: an agent-ready issue with a live claim, and its unclaimed twin.
        # Both belong in `backlog_issues` (the full topic set the ffcadmin page
        # renders); only the second is pickable work. Carried through the real
        # build_feed so the two panels are pinned against the same input.
        _in(HUB, {
            "number": 977, "title": "Ready but claimed", "state": "open", "assignee": None,
            "updated_at": "2026-07-19T00:30:00Z", "html_url": "i977",
            "labels": [{"name": "agentic-os"}, {"name": "agent-ready"}, {"name": "claimed"}],
        }),
        _in(HUB, {
            "number": 978, "title": "Ready and unclaimed", "state": "open", "assignee": None,
            "updated_at": "2026-07-19T00:20:00Z", "html_url": "i978",
            "labels": [{"name": "agentic-os"}, {"name": "agent-ready"}],
        }),
    ]
    search_page2 = [
        _in(ADMIN, {
            "number": 745, "title": "Campaign registry", "state": "open", "assignee": None,
            "updated_at": "2026-07-19T09:00:00Z", "html_url": "i745",
            "labels": [{"name": "agentic-os"}],
        }),
    ]

    pulls_by_repo = {HUB: [
        {
            "number": 900, "title": "A labeled PR", "state": "open", "draft": True,
            "assignee": {"login": "bot"}, "updated_at": "2026-07-19T02:00:00Z",
            "html_url": "pr900", "labels": [{"name": "agentic-os"}], "body": "no refs",
        },
        {  # unlabeled PR referencing nothing agentic -> excluded
            "number": 901, "title": "Unrelated PR", "state": "open", "draft": False,
            "assignee": None, "updated_at": "2026-07-19T03:00:00Z", "html_url": "pr901",
            "labels": [], "body": "housekeeping, no issue link",
        },
        {  # THE #909 CASE: unlabeled, but references an OPEN backlog issue.
            "number": 902, "title": "Unlabeled agent PR", "state": "open", "draft": True,
            "assignee": None, "updated_at": "2026-07-19T04:00:00Z", "html_url": "pr902",
            "labels": [], "body": "Refs #730, #4040\n\nfixes the thing",
        },
        {  # unlabeled, references a CLOSED agentic-os issue (not in the backlog)
            "number": 903, "title": "References a closed agentic issue", "state": "open",
            "draft": False, "assignee": None, "updated_at": "2026-07-19T05:00:00Z",
            "html_url": "pr903", "labels": [], "body": "Closes #999",
        },
        {  # references a PR and a non-agentic issue -> still excluded
            "number": 904, "title": "Cross-references only", "state": "open", "draft": False,
            "assignee": None, "updated_at": "2026-07-19T06:00:00Z", "html_url": "pr904",
            "labels": [], "body": "supersedes #901 and relates to #777; colour #909090",
        },
    ], ADMIN: [
        {  # unlabelled, references ffcadmin's OWN open backlog issue -> included
            "number": 300, "title": "Campaign registry work", "state": "open", "draft": True,
            "assignee": None, "updated_at": "2026-07-19T07:00:00Z", "html_url": "pr300",
            "labels": [], "body": "Refs #745",
        },
        {  # THE CROSS-REPO COLLISION: #730 is agentic in the HUB, and this PR
           # lives in ffcadmin, where #730 is an ordinary bug. Must be EXCLUDED.
           # With a bare-integer `known` set the hub's #730 answers for this one
           # and the PR is wrongly listed — a link that exists nowhere.
            "number": 301, "title": "Same number, different repo", "state": "open",
            "draft": False, "assignee": None, "updated_at": "2026-07-19T08:00:00Z",
            "html_url": "pr301", "labels": [], "body": "follows on from #730",
        },
        {  # THE SAME COLLISION ONE LAYER DOWN, in the LOOKUP CACHE rather than
           # the backlog set. #999 is outside the open backlog in both repos, so
           # both cost a request: agentic in the hub (PR 903 links it), ordinary
           # here. Repos are swept in sorted order, so the hub answers first —
           # an integer-keyed cache would hand its True straight to this PR.
            "number": 302, "title": "Cache collision", "state": "open", "draft": False,
            "assignee": None, "updated_at": "2026-07-19T06:30:00Z", "html_url": "pr302",
            "labels": [], "body": "Closes #999",
        },
    ]}
    # Single-issue lookups for numbers outside the open backlog, keyed by
    # (repo, number) — the same keying the collector must use.
    lone_issues = {
        (HUB, 999): {"number": 999, "labels": [{"name": "agentic-os"}]},  # closed but labeled
        (HUB, 777): {"number": 777, "labels": [{"name": "enhancement"}]},  # not agentic
        (HUB, 901): {
            "number": 901, "labels": [{"name": "agentic-os"}], "pull_request": {"url": "..."},
        },
        (HUB, 909090): {"number": 909090, "labels": [{"name": "agentic-os"}]},  # never fetched
        # ffcadmin's #730 — a real issue, not agentic-os. The collision's answer.
        (ADMIN, 730): {"number": 730, "labels": [{"name": "bug"}]},
        # ffcadmin's #999 — same number as the hub's agentic-os one, different
        # verdict. Pins the lookup cache's keying.
        (ADMIN, 999): {"number": 999, "labels": [{"name": "documentation"}]},
    }
    # Three waiting runs: one real FFC workflow, one of GitHub's own platform
    # agents (the Copilot reviewer, which parks in `status=waiting` on a
    # platform-managed `copilot` environment nobody at FFC can approve), and one
    # with no `path` at all to pin the fail-open branch.
    runs_obj = {
        "workflow_runs": [
            {
                "id": 555, "name": "502. GA Report", "created_at": "2026-07-18T07:00:00Z",
                "html_url": "r555", "path": ".github/workflows/502-google-analytics-report.yml",
            },
            {
                "id": 556, "name": "Running Copilot Code Review",
                "created_at": "2026-07-18T08:00:00Z", "html_url": "r556",
                "path": "dynamic/agents/copilot-pull-request-reviewer",
            },
            {
                "id": 557, "name": "Unrecognisable", "created_at": "2026-07-18T09:00:00Z",
                "html_url": "r557",
            },
        ]
    }
    # Per-run, so the copilot run cannot be excluded by accident of returning an
    # FFC environment name — the exclusion must come from the run itself.
    deployments_by_run = {
        555: [{"environment": {"name": "github-prod", "id": 1}}],
        556: [{"environment": {"name": "copilot", "id": 2}}],
        557: [{"environment": {"name": "cloudflare-prod-write", "id": 3}}],
    }

    def fake_request(path_or_url, token, params=None, soft_fail=False):
        url = m._build_url(path_or_url, params)
        call_log.append(url)
        # Search first: its path also contains "/issues".
        if "/search/issues" in url:
            if "page=2" in url:
                return {"total_count": 3, "incomplete_results": False,
                        "items": search_page2}, None
            return (
                {"total_count": 3, "incomplete_results": False, "items": search_page1},
                f'<{SEARCH_PAGE2}>; rel="next", <{SEARCH_PAGE2}>; rel="last"',
            )
        lone = re.search(r"/repos/([^/]+/[^/]+)/issues/(\d+)$", url)
        if lone:
            repo, num = lone.group(1), int(lone.group(2))
            if (repo, num) in lone_issues:
                return lone_issues[(repo, num)], None
            # Unknown number (e.g. #4040): GitHub answers 404, which _request
            # turns into (None, None) under soft_fail. A hard failure here
            # would take the whole daily feed down over a stray `#N`.
            check(soft_fail, f"speculative lookup of {repo}#{num} must use soft_fail")
            return None, None
        if COMMENTS in url:
            if "page=3" in url:  # last page -> point back to prev
                return last_page, f'<{PREV_URL}>; rel="prev", <{LAST_URL}>; rel="last"'
            if "page=2" in url:  # prev page
                return prev_page, f'<{PREV_URL}>; rel="prev"'
            # initial page 1 -> advertise the last page (contents intentionally ignored)
            return [], f'<{LAST_URL}>; rel="last", <{PREV_URL}>; rel="prev"'
        if "pending_deployments" in url:
            rid = int(re.search(r"/runs/(\d+)/pending_deployments", url).group(1))
            return deployments_by_run[rid], None
        if "/actions/runs" in url:
            return runs_obj, None
        pulls_m = re.search(r"/repos/([^/]+/[^/]+)/pulls", url)
        if pulls_m:
            repo = pulls_m.group(1)
            check(repo in pulls_by_repo, f"pulls fetched for unswept repo {repo}")
            return pulls_by_repo[repo], None
        # No generic "/issues" branch on purpose: the backlog now comes from
        # search, so a per-repo issues-list call means the single-repo path came
        # back and must fail loudly rather than being quietly served.
        raise AssertionError(f"unexpected url: {url}")

    return fake_request


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    m = _load()

    # --- link parsing ---
    link = f'<{PREV_URL}>; rel="prev", <{LAST_URL}>; rel="last"'
    check(m._link_rel(link, "last") == LAST_URL, "rel=last")
    check(m._link_rel(link, "prev") == PREV_URL, "rel=prev")
    check(m._link_rel(link, "next") is None, "missing rel=next -> None")
    check(m._link_rel(None, "last") is None, "no header -> None")
    check(m._assignee({"assignee": {"login": "bob"}}) == "bob", "assignee login")
    check(m._assignee({"assignee": None}) is None, "assignee none")

    # --- redaction ---
    red = m.redact(f"token={_GH_TOKEN} ok")
    check(_GH_TOKEN not in red and "[redacted]" in red, "gh token masked")
    pem_red = m.redact(f"here is a key {_PEM} end")
    check(_KW not in pem_red, "PEM header masked")
    check("tokenbody" not in pem_red and "BBBBCCCC" not in pem_red, "PEM body masked, not just header")
    sha = "deploy 1234567890abcdef1234567890abcdef12345678 landed"
    check(m.redact(sha) == sha, "git SHA must not be redacted")
    check(m.redact("") == "", "empty body passthrough")

    # --- repo extraction from a search result (pure) ---
    check(m._repo_full_name(_in(ADMIN, {})) == ADMIN, "repo parsed from repository_url")
    check(m._repo_full_name({}) is None, "no repository_url -> None")
    check(m._repo_full_name({"repository_url": "garbage"}) is None, "unparseable -> None")
    # The hub counts even when the search returns nothing for it, so the
    # denominator cannot lose its largest contributor on a quiet day.
    check(m.scope_repos([], HUB) == [HUB], "hub is always in scope")
    check(m.scope_repos([_in(ADMIN, {})], HUB) == sorted([ADMIN, HUB]), "scope is the union")

    # --- full feed via fake _request ---
    call_log = []
    m._request = _make_fake_request(m, call_log)
    feed = m.build_feed(HUB, "tok", org=ORG)

    # --- #925: the panels are org-wide, and every row says where it lives ---
    check(feed["repos"] == sorted([HUB, ADMIN]), f"both repos swept, got {feed['repos']}")
    check(feed["org"] == ORG, "org recorded in the feed")
    backlog = feed["backlog_issues"]
    # ffcadmin's 745 (09:00) is newer than the hub's 730 (01:08).
    check([i["number"] for i in backlog] == [745, 730, 977, 978],
          "backlog spans both repos, PRs excluded")
    check([i["repo"] for i in backlog] == [ADMIN, HUB, HUB, HUB],
          "every backlog row carries its repo")

    # --- the ready panel, at feed level (#974) ---------------------------
    # 977 is `agent-ready` AND `claimed`; 978 is `agent-ready` alone. The whole
    # point of the fix is that these two land differently in `ready_issues`
    # while landing identically in `backlog_issues` — asserted here against the
    # real build_feed output rather than a hand-built list, so it pins what the
    # published feed actually carries.
    check(977 in [i["number"] for i in backlog],
          "a claimed row must stay in backlog_issues — the ffcadmin page renders that set")
    ready_numbers = [i["number"] for i in feed["ready_issues"]]
    check(ready_numbers == [978], f"only the unclaimed agent-ready issue is ready: {ready_numbers}")
    check(feed["ready_count"] == 1, f"ready_count must match ready_issues: {feed['ready_count']}")
    # The assertion that fails against the pre-#925 single-repo generator: the
    # ffcadmin backlog was invisible on the public page while agents were told
    # to pick from it.
    check(any(i["repo"] == ADMIN for i in backlog), "the second repo's backlog is present")
    # Search paginated: page 2 must have been fetched, or ffcadmin vanishes.
    searches = [u for u in call_log if "/search/issues" in u]
    check(len(searches) == 2, f"search must follow rel=next, got {len(searches)} call(s)")
    q = urllib.parse.unquote_plus(searches[0])
    check(f"org:{ORG}" in q and f"label:{m.LABEL}" in q and "is:open" in q,
          f"search must match AGENTS.md's pickup query, got {q}")
    check("is:issue" not in q, "the single search deliberately returns PRs too")

    # --- in-flight PRs (#909) ---
    # Newest first across repos: 300 (07:00), 903 (05:00), 902 (04:00), 900 (02:00).
    # 901 and 904 excluded (hub); 301 excluded (the cross-repo collision).
    in_flight = feed["in_flight_prs"]
    numbers = [p["number"] for p in in_flight]
    check(numbers == [300, 903, 902, 900], f"in-flight set/order, got {numbers}")
    by_num = {p["number"]: p for p in in_flight}
    check(all(p["repo"] for p in in_flight), "every PR row carries its repo")
    check(by_num[300]["repo"] == ADMIN, "an ffcadmin PR is in flight")
    check(by_num[300]["linked_agentic_issues"] == [745], "linked via its own repo's backlog")

    # --- the cross-repo collision, keyed on (repo, number) ---
    # ffcadmin#301 references #730. The HUB's #730 is agentic-os; ffcadmin's is a
    # plain bug. Listing 301 would assert a link between two repos that share
    # nothing but an integer.
    check(301 not in by_num, "a #N matching another repo's agentic issue does not qualify")
    check(
        any(u.endswith(f"/repos/{ADMIN}/issues/730") for u in call_log),
        "ffcadmin#730 must be resolved against ffcadmin, not answered by the hub's backlog",
    )
    check(
        not any(u.endswith(f"/repos/{HUB}/issues/730") for u in call_log),
        "the hub's own #730 is still answered by the backlog without a request",
    )
    # ...and the same collision in the LOOKUP CACHE. Both repos resolve #999 by
    # request; the hub's is agentic, ffcadmin's is not. An integer-keyed cache
    # would let the hub's verdict answer for ffcadmin and list 302.
    check(302 not in by_num, "the lookup cache must not carry a verdict across repos")
    check(by_num[903]["linked_agentic_issues"] == [999], "the hub's own #999 still links")
    check(
        any(u.endswith(f"/repos/{ADMIN}/issues/999") for u in call_log),
        "ffcadmin#999 must be resolved against ffcadmin, not served from the hub's cache",
    )
    # The assertion that fails against the label-only filter: an unlabelled PR
    # referencing an open agentic-os issue is agent work and must be counted.
    check(by_num[902]["labels"] == [], "902 is genuinely unlabelled")
    check(by_num[902]["linked_agentic_issues"] == [730], "902 counted via its referenced issue")
    check(by_num[903]["linked_agentic_issues"] == [999], "closed agentic issue still links")
    check(by_num[900]["linked_agentic_issues"] == [], "label alone is sufficient")
    check(901 not in by_num, "a PR with no agentic reference stays out")
    check(904 not in by_num, "a reference to a PR or a non-agentic issue does not qualify")
    check(by_num[900]["draft"] is True, "draft flag carried")
    check(by_num[902]["draft"] is True, "draft flag carried for reference-matched PRs")

    # The anti-silence fields: rule text + unfiltered denominator, over the SAME
    # scope as the rows it denominates (5 hub + 2 ffcadmin). A denominator drawn
    # from a narrower scope than its numerator is worse than none at all.
    check(feed["open_prs_total"] == 8, "open_prs_total spans every swept repo, unfiltered")
    check(feed["open_prs_total"] >= len(in_flight), "denominator cannot be smaller than numerator")
    check(isinstance(feed["in_flight_prs_rule"], str) and feed["in_flight_prs_rule"],
          "the inclusion rule ships with the data it describes")
    check("agentic-os" in feed["in_flight_prs_rule"], "rule names the label it uses")
    # Backticked, because the bare substring "repos" is also inside the word
    # "repositories" — which the rule says either way, so the loose form passed
    # against a rule that had stopped naming the `repos` field at all.
    check("`repos`" in feed["in_flight_prs_rule"],
          "the rule must point at the `repos` field it is scoped by (#925 crit. 4)")
    check("org-wide" in feed["in_flight_prs_rule"], "the rule states the scope in words")

    # Cost + correctness of the lookup path: only numbers outside the backlog
    # are fetched, each at most once, and a hex-colour-shaped token never is.
    lookups = [u for u in call_log if re.search(r"/issues/\d+$", u)]
    _lookup_re = re.compile(r"/repos/([^/]+/[^/]+)/issues/(\d+)$")
    fetched = sorted(
        (_lookup_re.search(u).group(1), int(_lookup_re.search(u).group(2))) for u in lookups
    )
    check(
        fetched == sorted([(HUB, 777), (HUB, 901), (HUB, 999), (HUB, 4040),
                          (ADMIN, 730), (ADMIN, 999)]),
        f"unexpected lookup set: {fetched}",
    )
    check(len(lookups) == len(set(lookups)), "each referenced number resolved at most once")
    check(not any("909090" in u for u in call_log), "a hex colour is not an issue reference")

    # --- reference extraction (pure) ---
    check(m.referenced_issue_numbers("Refs #889, #841 and #889") == [889, 841], "dedup, order")
    check(m.referenced_issue_numbers(None) == [], "no body -> no refs")
    check(m.referenced_issue_numbers("## 1. Heading") == [], "a markdown heading is not a ref")

    log = feed["conductor_log"]
    check(len(log) == 10, "last(2)+prev(8) = 10 log entries")
    trunc = [e for e in log if e["truncated"]]
    check(len(trunc) == 1 and trunc[0]["body"].endswith("…"), "one truncated entry")
    check(len(trunc[0]["body"]) == m.COMMENT_TRUNCATE + 1, "truncation length (+ellipsis)")
    secret_entry = [e for e in log if "[redacted]" in e["body"]][0]
    check("ghp_" not in secret_entry["body"], "raw gh token not in feed")
    check(_KW not in secret_entry["body"], "PEM not in feed")
    check("1234567890abcdef" in secret_entry["body"], "SHA preserved in log body")

    # The ordering is load-bearing and was previously stated only in a docstring
    # the artifact's readers never see. A Conductor run read element 0 as the
    # newest, measured it 8.5h older than `generated_at`, and nearly filed a
    # staleness finding against a healthy generator (L154). Assert the ORDER
    # itself, then assert the published rule says which end is which — a rule
    # that merely exists is the failure mode this feed already had once (#909).
    stamps = [e["created_at"] for e in log]
    check(stamps == sorted(stamps), "conductor_log is oldest-first")
    # The strict `<` below is what rules out a REVERSED list: `<=` is satisfied
    # by any all-equal sequence, in either direction. GitHub stamps at
    # second granularity, so two real comments can legitimately tie — assert the
    # fixture's endpoints differ FIRST, so editing the fixture into a tie fails
    # here with the reason, instead of one line later as a bogus ordering fault.
    check(stamps[0] != stamps[-1],
          "fixture precondition: the log's endpoints must differ for the strict check below")
    check(log[0]["created_at"] < log[-1]["created_at"],
          "element 0 is the oldest entry, the last element is the newest")
    rule = feed["conductor_log_rule"]
    check(isinstance(rule, str) and rule, "the ordering rule ships with the data it describes")
    # Both ends named. "oldest" alone would pass against a rule that says the
    # list is oldest-first without ever telling a reader where to find the
    # latest entry, which is the exact question that went wrong.
    check("OLDEST FIRST" in rule, "the rule states the ordering in words")
    check("element 0" in rule and "LAST element" in rule,
          "the rule names both ends, not just the direction")
    check(str(m.CONDUCTOR_LOG_LIMIT) in rule, "the rule states the window size")
    check(str(m.CONDUCTOR_LOG_ISSUE) in rule, "the rule names the issue it tails")
    # The tail semantics: an absent entry means "off the window", not "never
    # written" -- the other reading a shortened list invites.
    check("tail" in rule, "the rule says this is a tail, not the whole thread")

    # cost: only page1 + last + prev were fetched, never a middle/all-pages walk.
    comment_calls = [u for u in call_log if COMMENTS in u]
    check(len(comment_calls) == 3, f"expected 3 comment fetches, got {len(comment_calls)}: {comment_calls}")
    check(not any("page=4" in u or "page=5" in u for u in comment_calls), "no walk past the last page")

    gates = feed["pending_gates"]
    check(
        gates == [
            {
                "run_id": 555, "workflow_name": "502. GA Report", "environment": "github-prod",
                "created_at": "2026-07-18T07:00:00Z", "url": "r555",
            },
            {
                "run_id": 557, "workflow_name": "Unrecognisable",
                "environment": "cloudflare-prod-write",
                "created_at": "2026-07-18T09:00:00Z", "url": "r557",
            },
        ],
        f"gate shaping + platform-agent exclusion, got {gates}",
    )
    # A gate panel that publishes GitHub's own Copilot reviewer tells readers a
    # human is holding something up when nobody at FFC can act on it at all.
    check(
        not any(g["environment"] == "copilot" for g in gates),
        "the platform-managed copilot environment is not an FFC gate",
    )
    check(556 not in [g["run_id"] for g in gates], "the Copilot reviewer run is excluded")
    # Fail-open: an unrecognisable run is still published. Silently dropping a
    # run that really is waiting on a reviewer is the worse failure here.
    check(557 in [g["run_id"] for g in gates], "a run with no path is published, not dropped")
    check(m.is_repo_workflow_run({"path": ".github/workflows/111-x.yml"}), "repo workflow included")
    check(not m.is_repo_workflow_run({"path": "dynamic/agents/x"}), "dynamic agent excluded")
    check(m.is_repo_workflow_run({}), "absent path fails open")
    # Gates stay hub-only (#925 crit. 5) and the feed says so, so an empty panel
    # is not read as "no gate anywhere in the org" now that its neighbours are.
    check(HUB in feed["pending_gates_scope"], "gate scope names the hub it covers")
    check(
        not any(f"/repos/{ADMIN}/actions/runs" in u for u in call_log),
        "gates must not be swept org-wide",
    )
    check(
        not any(f"/repos/{ADMIN}/issues/719/comments" in u for u in call_log),
        "the Conductor log is hub-only — there is one log",
    )
    check(feed["generated_at"].endswith("Z"), "timestamp shape")
    json.dumps(feed)  # must be JSON-serializable

    # --- a partial search aborts rather than publishing (#925) ---
    # incomplete_results means GitHub timed out and truncated the set. Shipping
    # it would recreate this exact defect with a fresher timestamp on it.
    def _incomplete(path_or_url, token, params=None, soft_fail=False):
        return {"total_count": 99, "incomplete_results": True, "items": []}, None

    m._request = _incomplete
    try:
        m.search_agentic_items(ORG, "tok")
    except SystemExit as exc:
        check("incomplete" in str(exc), f"abort must name the cause, got: {exc}")
    else:
        raise AssertionError("a truncated search must abort, not publish a partial backlog")

    def _not_a_dict(path_or_url, token, params=None, soft_fail=False):
        return [], None

    m._request = _not_a_dict
    try:
        m.search_agentic_items(ORG, "tok")
    except SystemExit:
        pass
    else:
        raise AssertionError("an unexpected search response shape must abort")

    # --- the OTHER truncation mode: the 1,000-result cap ---
    # The Search API stops paginating at SEARCH_RESULT_CAP and does NOT set
    # incomplete_results, so the flag alone lets a clean-looking but partial
    # response through. Only a total_count comparison catches it.
    def _short(total, got, link=None):
        def fake(path_or_url, token, params=None, soft_fail=False):
            return (
                {"total_count": total, "incomplete_results": False,
                 "items": [_in(HUB, {"number": i}) for i in range(got)]},
                link,
            )

        return fake

    m._request = _short(1500, 3)
    try:
        m.search_agentic_items(ORG, "tok")
    except SystemExit as exc:
        check(str(m.SEARCH_RESULT_CAP) in str(exc) and "1500" in str(exc),
              f"a capped search must name the cap and the true total, got: {exc}")
    else:
        raise AssertionError("the 1,000-result cap must abort — incomplete_results stays false")

    # A shortfall below the cap is a different cause and gets a different remedy.
    m._request = _short(9, 3)
    try:
        m.search_agentic_items(ORG, "tok")
    except SystemExit as exc:
        check("3 of 9" in str(exc), f"a short read must name both counts, got: {exc}")
        check(str(m.SEARCH_RESULT_CAP) not in str(exc),
              "a sub-cap shortfall must not blame the cap — that sends operators the wrong way")
    else:
        raise AssertionError("retrieving fewer results than total_count must abort")

    # No total_count at all: completeness is unknown, which is not the same as
    # complete. Refuse rather than assume.
    def _no_total(path_or_url, token, params=None, soft_fail=False):
        return {"incomplete_results": False, "items": []}, None

    m._request = _no_total
    try:
        m.search_agentic_items(ORG, "tok")
    except SystemExit as exc:
        check("total_count" in str(exc), f"abort must name the missing field, got: {exc}")
    else:
        raise AssertionError("a response with no total_count must abort")

    # ...but an OVER-read is harmless: an item added mid-pagination costs
    # nothing, and aborting on it would make the daily feed flaky for no gain.
    m._request = _short(2, 3)
    check(len(m.search_agentic_items(ORG, "tok")) == 3, "an over-read must not abort")

    # --- the ready queue (#922) ------------------------------------------
    # `agentic-os` is one label doing five jobs, so counting it counted epics,
    # machine-managed rolling issues, human-blocked items and durable findings
    # as though an agent could pick them up. `agent-ready` is the subset the
    # Conductor's 5-15 band was always about.
    backlog = [
        {"repo": "o/r", "number": 1, "labels": ["agentic-os", "agent-ready"]},
        {"repo": "o/r", "number": 2, "labels": ["agentic-os"]},
        {"repo": "o/r", "number": 3, "labels": ["agentic-os", "agent-ready", "bug"]},
        {"repo": "o/r", "number": 4, "labels": []},
    ]
    ready = m.collect_ready(backlog)
    check([i["number"] for i in ready] == [1, 3], f"ready must filter on agent-ready: {ready}")

    # A row with no labels key at all must not explode — the feed is generated
    # from search results, and a missing field is a data problem, not a crash.
    check(m.collect_ready([{"number": 9}]) == [], "a row with no labels must be skipped, not raise")

    # --- a live claim is NOT pickable work (#974) -------------------------
    # 737 adds `claimed` while a linked PR is open and removes it when the last
    # one closes; `agent-ready` deliberately stays put throughout, so the two
    # coexist and filtering on `agent-ready` alone published every live claim as
    # ready — the #939 duplicate-work class, aimed at the one surface sandboxed
    # agents and the public pick from. Both directions are asserted: dropping the
    # exclusion flips the first, and an always-empty collect_ready flips the
    # second, so neither can pass vacuously.
    claimed_backlog = [
        {"repo": "o/r", "number": 10, "labels": ["agentic-os", "agent-ready", "claimed"]},
        {"repo": "o/r", "number": 11, "labels": ["agentic-os", "agent-ready"]},
        {"repo": "o/r", "number": 12, "labels": ["agentic-os", "claimed"]},
    ]
    claimed_ready = m.collect_ready(claimed_backlog)
    numbers = [i["number"] for i in claimed_ready]
    check(10 not in numbers, f"an agent-ready issue carrying `claimed` must not be ready: {numbers}")
    check(11 in numbers, f"an agent-ready issue without `claimed` must stay ready: {numbers}")
    check(numbers == [11], f"only the unclaimed agent-ready row is ready: {numbers}")
    # ready_count is derived from this same list in build_feed, so excluding a row
    # from `ready_issues` uncounts it — pinned here so the two cannot drift apart.
    check(len(claimed_ready) == 1, f"ready_count must not count the claimed row: {claimed_ready}")
    # `backlog_issues` keeping its claimed rows is pinned at feed level above,
    # against real build_feed output — a length check on this literal could only
    # ever catch in-place mutation, which the shared-fixture case already covers.
    # What is worth pinning here is that the rows handed back are the SAME
    # objects, not copies: the feed publishes both panels, and a copy would let
    # them drift while every count still agreed.
    check(claimed_ready[0] is claimed_backlog[1], "ready must return backlog rows, not copies")

    # The published rule is what readers act on, so it has to describe the filter
    # the code actually applies. A reworded rule that stops saying so is the
    # defect #974 was: prose promising `unclaimed` over code that never checked.
    # A plain `"claimed" in READY_RULE` cannot express that, because the pre-#974
    # rule read "unclaimed, unblocked, …" and "unclaimed" CONTAINS "claimed" — the
    # one string this assertion exists to reject is a string that satisfies it.
    # Match the label as a word not preceded by "un", and pin that by running the
    # same predicate over the real pre-#974 literal, so a future weakening back to
    # a substring test fails here instead of passing quietly.
    def names_exclusion(rule):
        return re.search(r"(?<!un)\bclaimed\b", rule) is not None

    check(
        names_exclusion(m.READY_RULE),
        "ready_rule must state the claimed exclusion it now implements",
    )
    check(
        not names_exclusion(PRE_974_READY_RULE),
        "the pre-#974 rule promised 'unclaimed' over code that never checked — it must not pass",
    )

    # The ready queue is a STRICT SUBSET: `backlog_issues` must keep every row.
    # The ffcadmin renderer reads that key, and narrowing it would silently drop
    # epics and records from the public page (#909's class).
    check(len(backlog) == 4, "collect_ready must not mutate the backlog it filters")
    check(
        all(r in backlog for r in ready),
        "every ready row must still be a backlog row — ready is a subset, not a replacement",
    )

    # --- write_feed pins UTF-8 AND LF, and the LF half cannot be tested by
    # --- running it here (see the docstring on write_feed).
    #
    # This asserts on the CALL and its keywords rather than on the bytes on
    # disk, and that is deliberate. CI is `ubuntu-latest`, where text mode never
    # translates `\n`, so a round-trip test would write LF and pass **whether or
    # not `newline=` is set** — green for the wrong reason on the only platform
    # that runs it, which is the L75 trap. The contract is the keyword; the
    # keyword is what gets checked.
    #
    # NB: the L07 encoding guard in test_lessons_ledger.py scans this file as
    # TEXT, so writing the builtin's name followed by parentheses in a comment
    # is reported as an undeclared text-I/O call. Spelled around deliberately —
    # a fail-closed encoding guard is not worth loosening to accommodate prose.
    opened = {}
    real_open = builtins.open

    def recording_open(path, mode="r", *args, **kwargs):
        opened["path"], opened["mode"], opened["kwargs"] = path, mode, kwargs
        return real_open(path, mode, *args, **kwargs)

    with tempfile.TemporaryDirectory() as td:
        target = pathlib.Path(td) / "feed.json"
        builtins.open = recording_open
        try:
            m.write_feed(str(target), '{"a": 1}\n')
        finally:
            builtins.open = real_open

        check(opened.get("mode") == "w", "write_feed must open for writing")
        check(
            opened.get("kwargs", {}).get("encoding") == "utf-8",
            "write_feed must pin encoding='utf-8' — the host default is cp1252 and the "
            "feed carries emoji issue titles",
        )
        check(
            opened.get("kwargs", {}).get("newline") == "\n",
            "write_feed must pin newline='\\n' — without it the Windows hand-delivery path "
            "emits CRLF, and neither Linux CI nor ffcadmin's eol=lf .gitattributes can see it",
        )
        # The round-trip is still worth asserting: it is the half that would
        # catch a regression on the Conductor's own Windows host, where it DOES
        # discriminate. It proves nothing in CI, and says so.
        with real_open(target, "rb") as fh:
            check(
                fh.read() == b'{"a": 1}\n',
                "write_feed round-trip must be byte-exact (discriminating on Windows only)",
            )

    print("test_502_agentic_os_status: all assertions passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"::error::test_502_agentic_os_status FAILED: {exc}")
        sys.exit(1)
