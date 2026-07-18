"""Unit tests for the 726 drift-audit script (bash, run with a fake gh).

Regression anchor: the visibility enum bug — gh returns PUBLIC/PRIVATE
(uppercase) but the script's public-only checks compared against
lowercase 'public', so `no-merge-queue` drift never fired in production
(visible in issue #667: mq column ✗ with no matching flag).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

ALL_TEAMS = (
    "ffc-orgwide-admins,ffc-orgwide-maintainers,ffc-orgwide-writers,"
    "ffc-orgwide-triagers,ffc-orgwide-readers"
)


def run_audit(env_overrides: dict) -> tuple[str, str, str]:
    """Run the audit step with the fake gh. Returns (report, stdout, gh_log)."""
    script = step_run("726-repo-rulesets-drift-audit.yml", "audit", "Audit org rulesets")
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        summary = td / "summary.md"
        gh_log = td / "gh.log"
        summary.touch()
        env = {
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "GITHUB_STEP_SUMMARY": str(summary),
            "TEST_GH_LOG": str(gh_log),
            "HOME": str(td),
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(f"audit script exited {proc.returncode}: {proc.stderr}")
        return summary.read_text(), proc.stdout, gh_log.read_text()


def test_uppercase_public_visibility_triggers_no_merge_queue_flag():
    # gh emits PUBLIC (uppercase). Before the fix this row silently skipped
    # the public-only merge-queue check.
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-EX-example.org\tPUBLIC\tmain",
            "TEST_HAS_MQ": "0",
            "TEST_TEAMS_OUTPUT": ALL_TEAMS,
        }
    )
    assert "no-merge-queue" in report, report


def test_teams_fetch_failure_reports_distinct_flag_not_missing_teams():
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-EX-example.org\tPUBLIC\tmain",
            "TEST_HAS_MQ": "1",
            "TEST_TEAMS_FAIL": "1",
        }
    )
    assert "teams-check-failed" in report, report
    assert "missing-teams" not in report, report


def test_paginated_teams_join_completely():
    # Two --paginate pages; a partial read would report false missing-teams.
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-EX-example.org\tPUBLIC\tmain",
            "TEST_HAS_MQ": "1",
            "TEST_TEAMS_OUTPUT": (
                "ffc-orgwide-admins,ffc-orgwide-maintainers,ffc-orgwide-writers\n"
                "ffc-orgwide-triagers,ffc-orgwide-readers"
            ),
        }
    )
    assert "missing-teams" not in report, report
    assert "teams-check-failed" not in report, report


def test_public_repo_missing_teams_flagged():
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-EX-example.org\tPUBLIC\tmain",
            "TEST_HAS_MQ": "1",
            "TEST_TEAMS_OUTPUT": "ffc-orgwide-admins,ffc-orgwide-readers",
        }
    )
    assert "missing-teams:" in report, report
    for expected in ("maintainers", "writers", "triagers"):
        assert expected in report, report


def test_private_repo_with_orgwide_team_flagged():
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-IN-secret\tPRIVATE\tmain",
            "TEST_HAS_MQ": "0",
            "TEST_TEAMS_OUTPUT": "ffc-orgwide-readers",
        }
    )
    assert "private-repo-has-orgwide-teams" in report, report
    # Private repos must not be held to the public merge-queue standard.
    assert "no-merge-queue" not in report, report


def test_clean_public_repo_reports_no_drift_flags():
    report, _, _ = run_audit(
        {
            "TEST_REPO_LIST": "FFC-EX-clean.org\tPUBLIC\tmain",
            "TEST_HAS_MQ": "1",
            "TEST_TEAMS_OUTPUT": ALL_TEAMS,
        }
    )
    for flag in ("no-merge-queue", "missing-teams", "teams-check-failed", "squash-enabled"):
        assert flag not in report, (flag, report)


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
