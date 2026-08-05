"""Unit tests for 729's collaborator-grant bash step (run with a fake gh).

Regression anchor: ledger L02 / #854 / #889. The role read-back used
`gh api … 2>/dev/null || echo "$perm"`, so when the read failed the reported
role was the API's error body with the requested permission appended — printed
to the log and into the job summary as the role the user now holds. The grant
itself had already succeeded, which is what made the wrong value plausible.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def run_step(env_overrides: dict) -> tuple[str, str, str, str, int]:
    """Run the grant step. Returns (summary, stdout, stderr, gh_log, rc)."""
    script = step_run("729-repo-add-collaborator.yml", "add-collaborator", "Add collaborator")
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
            GH_TOKEN="fake-token",
            ORG_DEFAULT="FreeForCharity",
            IN_REPO="FFC-EX-example.org",
            IN_USER="octocat",
            IN_PERM="maintain",
            IN_DRY="false",
            IN_SOFT="false",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=120,
        )
        return (
            summary.read_text(encoding="utf-8"),
            proc.stdout,
            proc.stderr,
            gh_log.read_text(encoding="utf-8"),
            proc.returncode,
        )


def test_unreadable_role_falls_back_to_the_requested_permission():
    summary, out, err, _log, rc = run_step({"TEST_PERMISSION_FAIL": "1"})
    assert rc == 0, out + err
    assert "Added octocat to FreeForCharity/FFC-EX-example.org as maintain." in out, out
    assert "could not read back the effective role" in out, out
    # The defect: the 404 body reported as the granted role, in the log and in
    # the summary a human reads to confirm the grant.
    assert "404" not in out, out
    assert "404" not in summary, summary
    assert "octocat (maintain)" in summary, summary
    # Not silently discarded either — the failure is on stderr.
    assert "404" in err, err


def test_a_readable_role_is_reported_as_read():
    """The read-back exists because the effective role can differ from the
    requested one; the fallback must not paper over that."""
    summary, out, _err, _log, rc = run_step({"TEST_PERMISSION_ROLE": "write"})
    assert rc == 0, out
    assert "as write." in out, out
    assert "octocat (write)" in summary, summary
    assert "could not read back" not in out, out


def test_an_invitation_skips_the_role_read_entirely():
    """An outside collaborator gets a 201 + invitation body; the /permission
    endpoint reports an interim `read` for a pending invite, so it is not
    consulted at all."""
    summary, out, _err, log, rc = run_step({"TEST_COLLAB_PUT_BODY": '{"id":99}'})
    assert rc == 0, out
    assert "invitation #99" in out, out
    assert "octocat (invited as maintain, pending)" in summary, summary
    assert "/permission" not in log, log


def test_a_failed_grant_is_reported_as_failed():
    summary, out, _err, _log, rc = run_step(
        {"TEST_COLLAB_PUT_FAIL": "1", "IN_SOFT": "true"}
    )
    assert rc == 0, out  # soft_fail: warn, don't fail the caller
    assert "Could not add 'octocat'" in out, out
    assert "Failed: octocat" in summary, summary
    assert "Added/invited" not in summary, summary


def test_a_dry_run_grants_nothing():
    summary, out, _err, log, rc = run_step({"IN_DRY": "true"})
    assert rc == 0, out
    assert "[DRY RUN] Would set octocat = maintain" in out, out
    assert "octocat (dry-run)" in summary, summary
    assert "-X PUT" not in log, log


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
