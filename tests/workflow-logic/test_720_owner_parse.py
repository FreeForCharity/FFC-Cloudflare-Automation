"""Unit tests for the 720 create-repo tier-team grant step (pwsh).

Skipped when pwsh is unavailable (local sandboxes); always runs in CI
(ubuntu-latest ships PowerShell).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"


def run_grant_step(repo_name: str) -> tuple[str, str]:
    """Run the grant step with RepoName substituted. Returns (stdout, gh_log)."""
    step = find_step(load_workflow("720-create-repo.yml"), "create-repo", "Grant FFC-ORGWIDE")
    script = step["run"].replace("${{ inputs.RepoName }}", repo_name)
    with tempfile.TemporaryDirectory() as td:
        gh_log = pathlib.Path(td) / "gh.log"
        gh_log.touch()
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            env={
                "PATH": f"{HARNESS_DIR}:/usr/bin:/bin:/usr/local/bin",
                "TEST_GH_LOG": str(gh_log),
                "HOME": td,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(f"grant step exited {proc.returncode}: {proc.stderr}")
        return proc.stdout, gh_log.read_text()


def test_bare_repo_name_grants_all_five_teams():
    out, log = run_grant_step("FFC-EX-example.org")
    calls = [l for l in log.splitlines() if "teams/" in l]
    assert len(calls) == 5, (calls, out)
    for call in calls:
        assert "repos/FreeForCharity/FFC-EX-example.org" in call, call


def test_foreign_owner_skips_without_api_calls():
    out, log = run_grant_step("some-other-org/their-repo")
    assert "Skipping" in out, out
    assert log.strip() == "", log


def test_qualified_own_org_name_is_parsed():
    out, log = run_grant_step("FreeForCharity/FFC-EX-x.org")
    calls = [l for l in log.splitlines() if "teams/" in l]
    assert len(calls) == 5, (calls, out)
    for call in calls:
        assert "repos/FreeForCharity/FFC-EX-x.org" in call, call
        assert "FreeForCharity/FreeForCharity" not in call, call


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed in this environment; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
