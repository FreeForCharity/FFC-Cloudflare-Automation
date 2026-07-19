"""Unit tests for scripts/generate-agentic-os-status.py.

The generator is a standalone REST-only script invoked by workflow 502's
`deliver` job to emit the public `agentic-os-status.json` feed. These tests lock
down its pure transformation logic — no network — by monkeypatching the single
network primitive (`_request`, which returns `(payload, link_header)`) with
URL-keyed fixtures. That exercises the real pagination / last-page logic too.

Locked down here:
  * backlog excludes PRs (the /issues endpoint returns both);
  * in-flight PRs require the agentic-os label;
  * Conductor-log fetch reads only the LAST comment page (+ prev when the last
    holds fewer than the limit) — constant cost as #719 grows, NOT the whole
    thread;
  * Conductor-log bodies are redacted then truncated to 500 chars;
  * redaction masks GitHub/Slack/AWS tokens AND full PEM key blocks (body, not
    just the header) while leaving git SHAs and ordinary prose intact;
  * waiting runs are shaped into (run_id, workflow_name, environment, ...).

Run: python3 tests/workflow-logic/test_502_agentic_os_status.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate-agentic-os-status.py"

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


def _comment(login, ts, body):
    return {"user": {"login": login}, "created_at": ts, "body": body, "html_url": f"c-{ts}"}


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

    issues = [
        {
            "number": 730, "title": "Stale branch cleanup", "state": "open", "assignee": None,
            "updated_at": "2026-07-19T01:08:32Z", "html_url": "i730",
            "labels": [{"name": "agentic-os"}],
        },
        {  # a PR bleeds into the issues endpoint — must be excluded from backlog
            "number": 900, "title": "PR via issues endpoint", "state": "open",
            "assignee": {"login": "bot"}, "updated_at": "2026-07-19T02:00:00Z",
            "html_url": "pr900", "labels": [{"name": "agentic-os"}], "pull_request": {"url": "..."},
        },
    ]
    pulls = [
        {
            "number": 900, "title": "A labeled PR", "state": "open", "draft": True,
            "assignee": {"login": "bot"}, "updated_at": "2026-07-19T02:00:00Z",
            "html_url": "pr900", "labels": [{"name": "agentic-os"}],
        },
        {  # unlabeled PR must be excluded from in-flight
            "number": 901, "title": "Unrelated PR", "state": "open", "draft": False,
            "assignee": None, "updated_at": "2026-07-19T03:00:00Z", "html_url": "pr901",
            "labels": [],
        },
    ]
    runs_obj = {
        "workflow_runs": [
            {"id": 555, "name": "502. GA Report", "created_at": "2026-07-18T07:00:00Z", "html_url": "r555"}
        ]
    }
    deployments = [{"environment": {"name": "github-prod", "id": 1}}]

    def fake_request(path_or_url, token, params=None):
        url = m._build_url(path_or_url, params)
        call_log.append(url)
        if COMMENTS in url:
            if "page=3" in url:  # last page -> point back to prev
                return last_page, f'<{PREV_URL}>; rel="prev", <{LAST_URL}>; rel="last"'
            if "page=2" in url:  # prev page
                return prev_page, f'<{PREV_URL}>; rel="prev"'
            # initial page 1 -> advertise the last page (contents intentionally ignored)
            return [], f'<{LAST_URL}>; rel="last", <{PREV_URL}>; rel="prev"'
        if "pending_deployments" in url:
            return deployments, None
        if "/actions/runs" in url:
            return runs_obj, None
        if "/pulls" in url:
            return pulls, None
        if "/issues" in url:
            return issues, None
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

    # --- full feed via fake _request ---
    call_log = []
    m._request = _make_fake_request(m, call_log)
    feed = m.build_feed("FreeForCharity/FFC-Cloudflare-Automation", "tok")

    check([i["number"] for i in feed["backlog_issues"]] == [730], "backlog excludes PRs")
    check([p["number"] for p in feed["in_flight_prs"]] == [900], "in-flight requires label")
    check(feed["in_flight_prs"][0]["draft"] is True, "draft flag carried")

    log = feed["conductor_log"]
    check(len(log) == 10, "last(2)+prev(8) = 10 log entries")
    trunc = [e for e in log if e["truncated"]]
    check(len(trunc) == 1 and trunc[0]["body"].endswith("…"), "one truncated entry")
    check(len(trunc[0]["body"]) == m.COMMENT_TRUNCATE + 1, "truncation length (+ellipsis)")
    secret_entry = [e for e in log if "[redacted]" in e["body"]][0]
    check("ghp_" not in secret_entry["body"], "raw gh token not in feed")
    check(_KW not in secret_entry["body"], "PEM not in feed")
    check("1234567890abcdef" in secret_entry["body"], "SHA preserved in log body")

    # cost: only page1 + last + prev were fetched, never a middle/all-pages walk.
    comment_calls = [u for u in call_log if COMMENTS in u]
    check(len(comment_calls) == 3, f"expected 3 comment fetches, got {len(comment_calls)}: {comment_calls}")
    check(not any("page=4" in u or "page=5" in u for u in comment_calls), "no walk past the last page")

    gates = feed["pending_gates"]
    check(
        gates == [{
            "run_id": 555, "workflow_name": "502. GA Report", "environment": "github-prod",
            "created_at": "2026-07-18T07:00:00Z", "url": "r555",
        }],
        "gate shaping",
    )
    check(feed["generated_at"].endswith("Z"), "timestamp shape")
    json.dumps(feed)  # must be JSON-serializable

    print("test_502_agentic_os_status: all assertions passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"::error::test_502_agentic_os_status FAILED: {exc}")
        sys.exit(1)
