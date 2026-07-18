"""Unit tests for the 701 website-provision resolve/parse script.

Covers the three event paths (workflow_dispatch, repository_dispatch,
issues) end to end at the parse level — the layer where every prior
silent failure in this workflow started (refs issue #419).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_github_script

HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "github_script_shim.mjs"


def run_parse(context: dict) -> dict:
    script = step_github_script("701-website-provision.yml", "resolve", "Parse Website Request")
    with tempfile.TemporaryDirectory() as td:
        script_file = pathlib.Path(td) / "script.js"
        context_file = pathlib.Path(td) / "context.json"
        script_file.write_text(script)
        context_file.write_text(json.dumps(context))
        proc = subprocess.run(
            ["node", str(HARNESS)],
            env={"TEST_SCRIPT_FILE": str(script_file), "TEST_CONTEXT_FILE": str(context_file), "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def ctx(event_name: str, payload: dict, actor: str = "test-actor") -> dict:
    return {
        "eventName": event_name,
        "actor": actor,
        "payload": payload,
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
    }


def test_manual_dispatch_valid_domain():
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "example.org"}}))
    assert r["failed"] is None, r
    assert r["threw"] is None, r
    assert r["outputs"]["domain"] == "example.org", r
    assert r["outputs"]["repo_name"] == "FFC-EX-example.org", r
    assert r["outputs"].get("skip") != "true", r
    # Manual runs must never try to comment on a (nonexistent) issue.
    assert r["outputs"]["post_comments"] == "false", r


def test_manual_dispatch_strips_scheme_and_www():
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "https://www.example.org/"}}))
    assert r["failed"] is None, r
    assert r["outputs"]["domain"] == "example.org", r


def test_repository_dispatch_reads_client_payload():
    payload = {
        "action": "ffcadmin-website-provision",
        "client_payload": {
            "domain": "charity.org",
            "technical_poc_github_username": "@some-user",
            "charity_title": "Some Charity",  # extra keys must be ignored, not fatal
            "ffcadmin_issue": 123,  # non-string values must coerce safely
        },
    }
    r = run_parse(ctx("repository_dispatch", payload))
    assert r["failed"] is None, r
    assert r["threw"] is None, r
    assert r["outputs"]["domain"] == "charity.org", r
    assert r["outputs"]["repo_name"] == "FFC-EX-charity.org", r
    assert r["outputs"]["technical_poc_github_username"] == "some-user", r
    assert r["outputs"]["post_comments"] == "false", r


def test_repository_dispatch_missing_domain_fails_loudly():
    r = run_parse(ctx("repository_dispatch", {"client_payload": {"sponsor": "someone"}}))
    assert r["failed"] is not None and "Missing required field: domain" in r["failed"], r


def test_repository_dispatch_empty_payload_fails_loudly():
    r = run_parse(ctx("repository_dispatch", {}))
    assert r["failed"] is not None and "Missing required field: domain" in r["failed"], r
    assert r["threw"] is None, r


def test_issues_event_non_website_request_skips():
    payload = {
        "issue": {
            "number": 42,
            "title": "Unrelated bug report",
            "body": "something else",
            "labels": [],
            "user": {"login": "reporter"},
        }
    }
    r = run_parse(ctx("issues", payload))
    assert r["outputs"].get("skip") == "true", r
    assert r["outputs"].get("post_comments") == "false", r
    assert r["failed"] is None, r


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    sys.exit(1 if failures else 0)
