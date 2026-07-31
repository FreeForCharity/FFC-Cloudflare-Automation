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
from wf_extract import child_env, step_run

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
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(gh_log),
            HOME=str(td),
        )
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
        return summary.read_text(encoding="utf-8"), proc.stdout, gh_log.read_text(encoding="utf-8")


def run_audit_allow_failure(env_overrides: dict) -> tuple[str, str, int]:
    """Like run_audit but tolerates a non-zero exit.

    Returns (report, combined_output, rc) where combined_output is stdout+stderr
    concatenated. Both streams are needed: `::error::` annotations go to stdout
    while api_get forwards the failed response body to stderr, and a caller
    asserting on either one alone would miss half the evidence.
    """
    script = step_run("726-repo-rulesets-drift-audit.yml", "audit", "Audit org rulesets")
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        summary = td / "summary.md"
        summary.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(td / "gh.log"),
            HOME=str(td),
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120
        )
        return summary.read_text(encoding="utf-8"), proc.stdout + proc.stderr, proc.returncode


def test_unreadable_org_rulesets_fails_and_never_reports_present():
    """#854 regression: a 403 body must NEVER satisfy the 'ruleset present' branch.

    The original bug was subtle: `gh api ... 2>/dev/null || echo "0"` produced
    `{...403...}0`, which is not equal to "0", so the missing-ruleset branch never
    fired and the audit printed a GREEN check containing an HTTP error. Asserting
    only "does not say present" would pass even on a silently-wrong "0", so this
    also pins the non-zero exit.
    """
    report, output, rc = run_audit_allow_failure(
        {
            "TEST_ORG_RULESETS_FAIL": "1",
            "TEST_REPO_LIST": "FFC-EX-example.org	PUBLIC	main",
            "TEST_TEAMS_OUTPUT": ALL_TEAMS,
        }
    )
    assert rc != 0, f"audit must FAIL when it cannot read org rulesets; rc={rc}"
    assert "Org-level branch ruleset present" not in report, report
    assert "cannot read org rulesets" in output, output
    # The error body must never leak into the report as if it were a count.
    assert "403" not in report, report
    assert "Resource not accessible" not in report, report


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


def test_report_step_skips_when_the_audit_never_ran():
    """The reporting step must not fire when the audit step was skipped.

    It runs `if: always()` so a FAILED audit still delivers its INCOMPLETE
    report (#854). But when the preflight or the Azure login bails, there is no
    report AND no GH_TOKEN, so `gh issue list` dies and the step reports
    "Could not search for an existing tracking issue" — a second error that
    reads like an API fault and buries the real cause. Seen for real on run
    30334346136, dispatched against the unprovisioned github-prod-read lane.

    Both halves matter, so both are asserted: drop `always()` and a failed
    audit stops reporting; drop the skip guard and a bailed job emits the
    misleading duplicate-issue error.
    """
    import yaml as _yaml

    from wf_extract import WORKFLOWS as _WORKFLOWS

    wf = _yaml.safe_load(
        (_WORKFLOWS / "726-repo-rulesets-drift-audit.yml").read_text(encoding="utf-8-sig")
    )
    steps = wf["jobs"]["audit"]["steps"]
    report_step = next(s for s in steps if "tracking issue" in str(s.get("name", "")).lower())
    cond = str(report_step.get("if", ""))

    assert "always()" in cond, (
        "the reporting step lost always(): a failed audit must still deliver its "
        f"INCOMPLETE report (#854). Condition is {cond!r}"
    )
    assert "skipped" in cond and "steps.audit" in cond, (
        "the reporting step must not run when the audit step was skipped — "
        "otherwise a preflight/login bail emits a misleading "
        f"'could not search for an existing tracking issue' error. Condition is {cond!r}"
    )


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
