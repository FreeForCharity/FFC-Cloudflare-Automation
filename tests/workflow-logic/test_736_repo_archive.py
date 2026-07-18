"""Unit tests for the 736 repo-archive guard script (bash, fake gh).

This workflow is destructive-adjacent (archives repos), so every guard is
pinned by a test: name normalization (owner strip, whitespace), the hard
denylist, dry-run default, the typed confirmation, and honest reporting of
the optional issue close. Regression anchors from the #532 review: internal
whitespace must be REJECTED (never silently stripped into a different repo
name), and the summary must not claim an issue was closed when the close
failed.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

UNARCHIVED_META = '{"archived": false, "visibility": "public", "pushed_at": "2026-01-01T00:00:00Z"}'
ARCHIVED_META = '{"archived": true, "visibility": "public", "pushed_at": "2026-01-01T00:00:00Z"}'

BASE_ENV = {
    "TARGET_ORG": "FreeForCharity",
    "DENYLIST": "FFC-Cloudflare-Automation FFC_Single_Page_Template .github",
    "THIS_REPO": "FreeForCharity/FFC-Cloudflare-Automation",
    "IN_REASON": "test reason",
}


def run_archive(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the archive step with the fake gh. Returns (proc, summary, gh_log)."""
    script = step_run("736-repo-archive.yml", "archive", "Archive repository")
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


def test_empty_repo_rejected():
    proc, _, gh_log = run_archive({"IN_REPO": "  ", "IN_DRY_RUN": "true"})
    assert proc.returncode != 0, proc.stdout
    assert "repo is empty" in proc.stdout, proc.stdout
    assert "PATCH" not in gh_log, gh_log


def test_internal_whitespace_rejected_not_stripped():
    # "FFC-EX-foo bar" must be REJECTED — silently stripping the space would
    # retarget the archive at a different repository (FFC-EX-foobar).
    proc, _, gh_log = run_archive({"IN_REPO": "FFC-EX-foo bar", "IN_DRY_RUN": "true"})
    assert proc.returncode != 0, proc.stdout
    assert "Invalid repository name" in proc.stdout, proc.stdout
    assert "FFC-EX-foobar" not in proc.stdout + gh_log, (proc.stdout, gh_log)
    assert "PATCH" not in gh_log, gh_log


def test_denylist_refused_before_any_api_call():
    for protected in ("FFC-Cloudflare-Automation", "FFC_Single_Page_Template", ".github"):
        proc, _, gh_log = run_archive(
            {"IN_REPO": protected, "IN_DRY_RUN": "false", "IN_CONFIRM": protected}
        )
        assert proc.returncode != 0, (protected, proc.stdout)
        assert "Refusing to archive protected repository" in proc.stdout, (protected, proc.stdout)
        assert gh_log.strip() == "", (protected, gh_log)


def test_owner_prefix_stripped_and_org_forced():
    proc, _, gh_log = run_archive(
        {
            "IN_REPO": "SomeOtherOrg/FFC-EX-example.org",
            "IN_DRY_RUN": "true",
            "TEST_REPO_META": UNARCHIVED_META,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "api repos/FreeForCharity/FFC-EX-example.org" in gh_log, gh_log


def test_dry_run_never_patches():
    proc, summary, gh_log = run_archive(
        {"IN_REPO": "FFC-EX-example.org", "IN_DRY_RUN": "true", "TEST_REPO_META": UNARCHIVED_META}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Dry run" in summary, summary
    assert "PATCH" not in gh_log, gh_log


def test_live_requires_matching_confirmation():
    proc, _, gh_log = run_archive(
        {
            "IN_REPO": "FFC-EX-example.org",
            "IN_DRY_RUN": "false",
            "IN_CONFIRM": "FFC-EX-other.org",
            "TEST_REPO_META": UNARCHIVED_META,
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "must exactly equal repo" in proc.stdout, proc.stdout
    assert "PATCH" not in gh_log, gh_log


def test_live_confirm_accepts_owner_prefix_and_padding():
    # Pasting the owner-qualified name (plus a stray space) into confirm_repo
    # is unambiguous intent and must confirm (review round 4/#532).
    proc, summary, gh_log = run_archive(
        {
            "IN_REPO": "FFC-EX-example.org",
            "IN_DRY_RUN": "false",
            "IN_CONFIRM": " FreeForCharity/FFC-EX-example.org ",
            "TEST_REPO_META": UNARCHIVED_META,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "-X PATCH repos/FreeForCharity/FFC-EX-example.org" in gh_log, gh_log
    assert "Archived" in summary, summary


def test_already_archived_is_a_noop():
    proc, summary, gh_log = run_archive(
        {"IN_REPO": "FFC-EX-example.org", "IN_DRY_RUN": "false", "IN_CONFIRM": "FFC-EX-example.org", "TEST_REPO_META": ARCHIVED_META}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Already archived" in summary, summary
    assert "PATCH" not in gh_log, gh_log


def test_missing_repo_warns_and_exits_cleanly():
    proc, summary, gh_log = run_archive(
        {"IN_REPO": "FFC-EX-ghost.org", "IN_DRY_RUN": "false", "IN_CONFIRM": "FFC-EX-ghost.org", "TEST_REPO_META": "404"}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "not found" in summary, summary
    assert "PATCH" not in gh_log, gh_log


def test_issue_close_success_reported_as_closed():
    proc, summary, gh_log = run_archive(
        {
            "IN_REPO": "FFC-EX-example.org",
            "IN_DRY_RUN": "false",
            "IN_CONFIRM": "FFC-EX-example.org",
            "IN_ISSUE": "146",
            "TEST_REPO_META": UNARCHIVED_META,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Closed issue #146" in summary, summary
    assert "issue close 146" in gh_log, gh_log


def test_issue_close_failure_never_claims_closed():
    # Regression anchor (#532 round 8): a failed close must not be reported
    # as closed; the archive itself stays successful.
    proc, summary, _ = run_archive(
        {
            "IN_REPO": "FFC-EX-example.org",
            "IN_DRY_RUN": "false",
            "IN_CONFIRM": "FFC-EX-example.org",
            "IN_ISSUE": "146",
            "TEST_REPO_META": UNARCHIVED_META,
            "TEST_ISSUE_CLOSE_FAIL": "1",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Closed issue #146" not in summary, summary
    assert "Could not close issue #146" in summary, summary


def test_non_numeric_issue_skipped():
    proc, summary, gh_log = run_archive(
        {
            "IN_REPO": "FFC-EX-example.org",
            "IN_DRY_RUN": "false",
            "IN_CONFIRM": "FFC-EX-example.org",
            "IN_ISSUE": "abc; rm -rf /",
            "TEST_REPO_META": UNARCHIVED_META,
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "is not numeric" in proc.stdout, proc.stdout
    assert "issue close" not in gh_log, gh_log


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
