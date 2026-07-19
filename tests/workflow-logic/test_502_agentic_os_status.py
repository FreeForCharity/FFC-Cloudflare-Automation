"""Unit tests for scripts/generate-agentic-os-status.py.

The generator is a standalone REST-only script invoked by workflow 502's
`deliver` job to emit the public `agentic-os-status.json` feed. These tests lock
down its pure transformation logic — no network — by monkeypatching the single
network entry point (`rest_get`) with fixtures:

  * backlog excludes PRs (the /issues endpoint returns both);
  * in-flight PRs require the agentic-os label;
  * Conductor-log bodies are truncated to 500 chars with a `truncated` flag;
  * secret-shaped substrings in log bodies are redacted (defense-in-depth —
    #719 is already public, but an accidentally-pasted token must never
    propagate into the aggregated public feed);
  * git SHAs / ordinary prose are NOT redacted;
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


def _load():
    spec = importlib.util.spec_from_file_location("agentic_os_status", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture_rest_get(path, token, params=None):
    base = path.split("?")[0]
    if base.endswith(f"/issues/{719}/comments"):
        return [
            {
                "user": {"login": "clarkemoyer"},
                "created_at": "2026-07-18T23:48:10Z",
                "body": "x" * 600,
                "html_url": "c1",
            },
            {
                "user": {"login": "clarkemoyer"},
                "created_at": "2026-07-19T00:00:00Z",
                "body": "run end; token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 sha 1234567890abcdef1234567890abcdef12345678",
                "html_url": "c2",
            },
        ]
    if base.endswith("/issues"):
        return [
            {
                "number": 730,
                "title": "Stale branch cleanup",
                "state": "open",
                "assignee": None,
                "updated_at": "2026-07-19T01:08:32Z",
                "html_url": "i730",
                "labels": [{"name": "agentic-os"}],
            },
            {  # a PR bleeds into the issues endpoint — must be excluded from backlog
                "number": 900,
                "title": "PR via issues endpoint",
                "state": "open",
                "assignee": {"login": "bot"},
                "updated_at": "2026-07-19T02:00:00Z",
                "html_url": "pr900",
                "labels": [{"name": "agentic-os"}],
                "pull_request": {"url": "..."},
            },
        ]
    if base.endswith("/pulls"):
        return [
            {
                "number": 900,
                "title": "A labeled PR",
                "state": "open",
                "draft": True,
                "assignee": {"login": "bot"},
                "updated_at": "2026-07-19T02:00:00Z",
                "html_url": "pr900",
                "labels": [{"name": "agentic-os"}],
            },
            {  # unlabeled PR must be excluded from in-flight
                "number": 901,
                "title": "Unrelated PR",
                "state": "open",
                "draft": False,
                "assignee": None,
                "updated_at": "2026-07-19T03:00:00Z",
                "html_url": "pr901",
                "labels": [],
            },
        ]
    if "/actions/runs/" in base and base.endswith("pending_deployments"):
        return [{"environment": {"name": "github-prod", "id": 1}}]
    if base.endswith("/actions/runs"):
        return {
            "workflow_runs": [
                {
                    "id": 555,
                    "name": "502. GA Report",
                    "created_at": "2026-07-18T07:00:00Z",
                    "html_url": "r555",
                }
            ]
        }
    raise AssertionError(f"unexpected path: {path}")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    m = _load()

    # --- pure helpers ---
    link = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
    check(m._parse_next_link(link) == "https://api.github.com/x?page=2", "next link")
    check(m._parse_next_link(None) is None, "no link header")
    check(m._parse_next_link('<u>; rel="last"') is None, "no next rel")
    check(m._assignee({"assignee": {"login": "bob"}}) == "bob", "assignee login")
    check(m._assignee({"assignee": None}) is None, "assignee none")

    # --- redaction: masks secret shapes, preserves SHAs/prose ---
    red = m.redact("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 ok")
    check("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in red, "gh token masked")
    check("[redacted]" in red, "redaction marker present")
    sha = "deploy 1234567890abcdef1234567890abcdef12345678 landed"
    check(m.redact(sha) == sha, "git SHA must not be redacted")
    check(m.redact("") == "", "empty body passthrough")

    # --- full feed shaping via fixtures ---
    m.rest_get = _fixture_rest_get
    feed = m.build_feed("FreeForCharity/FFC-Cloudflare-Automation", "tok")

    check([i["number"] for i in feed["backlog_issues"]] == [730], "backlog excludes PRs")
    check([p["number"] for p in feed["in_flight_prs"]] == [900], "in-flight requires label")
    check(feed["in_flight_prs"][0]["draft"] is True, "draft flag carried")

    log = feed["conductor_log"]
    check(len(log) == 2, "conductor log count")
    check(log[0]["truncated"] is True and log[0]["body"].endswith("…"), "truncation")
    check(len(log[0]["body"]) == m.COMMENT_TRUNCATE + 1, "truncation length (+ellipsis)")
    check("[redacted]" in log[1]["body"], "secret in log body redacted")
    check("ghp_" not in log[1]["body"], "raw gh token not in feed")
    check("1234567890abcdef" in log[1]["body"], "SHA preserved in log body")

    gates = feed["pending_gates"]
    check(
        gates
        == [
            {
                "run_id": 555,
                "workflow_name": "502. GA Report",
                "environment": "github-prod",
                "created_at": "2026-07-18T07:00:00Z",
                "url": "r555",
            }
        ],
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
