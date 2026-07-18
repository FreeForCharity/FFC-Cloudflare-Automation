"""Unit tests for the 701 finalize completion-comment logic (github-script).

Pins the serving/content status mapping added in #532: the comment must
report the host that actually served, must not render a false "not serving"
or "skipped" when the verify/content jobs recorded no output, and must keep
the admin-minimal metadata note honest.
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

BASE_ENV = {
    "ISSUE_NUMBER": "42",
    "DOMAIN": "example.org",
    "REPO_FULL": "FreeForCharity/FFC-EX-example.org",
    "RUN_URL": "https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/1",
    "ZONE_CONTROLLED": "true",
    "SERVED": "",
    "SERVED_HOST": "",
    "CONTENT_STATUS": "",
}


def run_finalize(env_overrides: dict) -> dict:
    script = step_github_script("701-website-provision.yml", "finalize", "Comment completion")
    context = {
        "eventName": "issues",
        "serverUrl": "https://github.com",
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {},
    }
    with tempfile.TemporaryDirectory() as td:
        script_file = pathlib.Path(td) / "script.js"
        context_file = pathlib.Path(td) / "context.json"
        script_file.write_text(script)
        context_file.write_text(json.dumps(context))
        env = {
            "TEST_SCRIPT_FILE": str(script_file),
            "TEST_CONTEXT_FILE": str(context_file),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        env.update(BASE_ENV)
        env.update(env_overrides)
        proc = subprocess.run(
            ["node", str(HARNESS)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def body(env_overrides: dict) -> str:
    r = run_finalize(env_overrides)
    assert r["threw"] is None, r["threw"]
    assert len(r["comments"]) == 1, r
    assert r["comments"][0]["issue_number"] == 42, r
    return r["comments"][0]["body"]


def test_served_reports_actual_host_not_assumed_apex():
    b = body({"SERVED": "true", "SERVED_HOST": "www.example.org", "CONTENT_STATUS": "applied"})
    assert "https://www.example.org" in b, b
    assert "Verified live on GitHub Pages" in b, b


def test_served_defaults_to_apex_when_host_missing():
    b = body({"SERVED": "true", "CONTENT_STATUS": "applied"})
    assert "https://example.org" in b, b


def test_explicit_not_serving_names_both_hosts():
    b = body({"SERVED": "false", "CONTENT_STATUS": "applied"})
    assert "Not serving yet" in b, b
    assert "https://example.org" in b and "https://www.example.org" in b, b


def test_missing_serving_result_is_unknown_not_false_negative():
    b = body({"SERVED": "", "CONTENT_STATUS": "applied"})
    assert "did not run or did not record a result" in b, b
    assert "Not serving yet" not in b, b


def test_zone_not_controlled_skips_serving_and_adds_next_steps():
    b = body({"ZONE_CONTROLLED": "false", "CONTENT_STATUS": "applied"})
    assert "Serving check skipped" in b, b
    assert "Next steps:" in b, b


def test_content_applied_note():
    b = body({"SERVED": "true", "CONTENT_STATUS": "applied"})
    assert "was applied to the React template" in b, b


def test_content_skipped_mentions_metadata_still_recorded():
    b = body({"SERVED": "true", "CONTENT_STATUS": "skipped"})
    assert "admin-minimal" in b, b
    assert "ffc-content.json" in b, b


def test_content_failed_is_nonblocking_warning():
    b = body({"SERVED": "true", "CONTENT_STATUS": "failed"})
    assert "did not complete (non-blocking)" in b, b


def test_missing_content_status_is_unknown_not_skipped():
    b = body({"SERVED": "true", "CONTENT_STATUS": ""})
    assert "did not record a status" in b, b
    assert "admin-minimal" not in b, b


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
