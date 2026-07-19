"""Unit tests for the 736 repo-archive preflight (bash, fake gh).

Regression anchor from the 2026-07-18 incident: a `dry_run=false` archive of
`FFC-EX-technologymonastery.org` reached the `github-prod` gate while the
only dry-run that day targeted a different, nonexistent repo — the live
target was ACTIVE (pushed 25 minutes earlier, Pages enabled, referenced by
an open issue). Nothing verified the live target matched what had been
previewed. The preflight must, for a live (dry_run=false) archive: require a
matching successful dry_run=true run within 48h, fail fast on a
missing/already-archived repo, and warn (without blocking) when the target
still looks active.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

DORMANT_META = (
    '{"name": "FFC-EX-example.org", "archived": false, "has_pages": false,'
    ' "pushed_at": "2020-01-01T00:00:00Z"}'
)
ARCHIVED_META = (
    '{"name": "FFC-EX-example.org", "archived": true, "has_pages": false,'
    ' "pushed_at": "2020-01-01T00:00:00Z"}'
)

BASE_ENV = {
    "TARGET_ORG": "FreeForCharity",
    "DENYLIST": "FFC-Cloudflare-Automation FFC_Single_Page_Template .github",
    "THIS_REPO": "FreeForCharity/FFC-Cloudflare-Automation",
    "IN_REPO": "FFC-EX-example.org",
    "IN_DRY_RUN": "false",
}


def _recent_runs(display_titles: list[str]) -> str:
    """Build a fake `runs` API payload with created_at inside the last 48h."""
    return json.dumps(
        {
            "workflow_runs": [
                {"display_title": t, "created_at": "2999-01-01T00:00:00Z"} for t in display_titles
            ]
        }
    )


MATCHING_DRY_RUN = _recent_runs(["Archive FFC-EX-example.org (dry_run=true)"])
NO_MATCHING_DRY_RUN = _recent_runs(["Archive FFC-EX-other.org (dry_run=true)"])


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the preflight step. Returns (proc, summary, gh_log)."""
    script = step_run("736-repo-archive.yml", "preflight", "Verify target state")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        gh_log = tdp / "gh.log"
        summary.touch()
        gh_log.touch()
        env = {
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "GITHUB_STEP_SUMMARY": str(summary),
            "TEST_GH_LOG": str(gh_log),
            "HOME": str(tdp),
        }
        env.update(BASE_ENV)
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, summary.read_text(), gh_log.read_text()


def test_dry_run_skips_all_checks():
    # A dry run is itself the evidence a later live archive needs — it must
    # never be blocked here, even with no repo metadata mocked at all.
    proc, summary, gh_log = run_preflight({"IN_DRY_RUN": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Dry run" in summary, summary
    assert gh_log.strip() == "", gh_log


def test_missing_repo_fails_fast():
    proc, summary, _ = run_preflight({"TEST_REPO_META": "404"})
    assert proc.returncode != 0, proc.stdout
    assert "does not exist" in proc.stdout, proc.stdout
    assert "not found" in summary, summary


def test_unparseable_repo_response_fails_safe():
    proc, _, _ = run_preflight({})
    assert proc.returncode != 0, proc.stdout
    assert "failing safe" in proc.stdout.lower(), proc.stdout


def test_already_archived_fails_fast():
    proc, summary, _ = run_preflight({"TEST_REPO_META": ARCHIVED_META})
    assert proc.returncode != 0, proc.stdout
    assert "already archived" in proc.stdout.lower(), proc.stdout
    assert "already archived" in summary.lower(), summary


def test_no_matching_dry_run_refused():
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_WORKFLOW_RUNS": NO_MATCHING_DRY_RUN,
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "No successful dry_run=true run" in proc.stdout, proc.stdout
    assert "no matching dry-run" in summary.lower(), summary


def test_no_prior_runs_at_all_refused():
    proc, _, _ = run_preflight({"TEST_REPO_META": DORMANT_META})
    assert proc.returncode != 0, proc.stdout
    assert "No successful dry_run=true run" in proc.stdout, proc.stdout


def test_runs_list_api_failure_fails_safe():
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_RUNS_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not list prior 736 runs" in proc.stdout.lower(), proc.stdout


def test_matching_dry_run_proceeds():
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_WORKFLOW_RUNS": MATCHING_DRY_RUN,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Preflight passed" in summary, summary


def test_matching_dry_run_is_case_insensitive_and_owner_agnostic():
    # The dry-run dispatch may have used a different raw casing/owner prefix
    # than the live dispatch; matching is on the normalized name, not the
    # literal raw input string.
    runs = _recent_runs(["Archive SomeOtherOrg/ffc-ex-EXAMPLE.org (dry_run=true)"])
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_WORKFLOW_RUNS": runs,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_active_repo_warns_but_does_not_block():
    recent_meta = json.dumps(
        {
            "name": "FFC-EX-example.org",
            "archived": False,
            "has_pages": True,
            "pushed_at": "2999-01-01T00:00:00Z",
        }
    )
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": recent_meta,
            "TEST_WORKFLOW_RUNS": MATCHING_DRY_RUN,
            "TEST_OPEN_ISSUES": "#207 [WEBSITE REQUEST] example.org",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Preflight passed" in summary, summary
    assert "pushed within the last 7 days" in summary.lower(), summary
    assert "github pages is enabled" in summary.lower(), summary
    assert "#207" in summary, summary
    assert "::warning::" in proc.stdout, proc.stdout


def test_dormant_repo_no_warnings():
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_WORKFLOW_RUNS": MATCHING_DRY_RUN,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::warning::" not in proc.stdout, proc.stdout


def test_issue_search_failure_is_non_fatal():
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": DORMANT_META,
            "TEST_WORKFLOW_RUNS": MATCHING_DRY_RUN,
            "TEST_ISSUE_LIST_FAIL": "1",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not search open issues" in proc.stdout.lower(), proc.stdout


def test_denylisted_repo_refused_before_any_api_call():
    proc, _, gh_log = run_preflight(
        {
            "IN_REPO": "FFC-Cloudflare-Automation",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "Refusing to archive protected repository" in proc.stdout, proc.stdout
    assert gh_log.strip() == "", gh_log


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
