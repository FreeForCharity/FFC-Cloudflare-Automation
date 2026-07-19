"""Unit tests for the 720 create-repo preflight (bash, fake gh).

Regression anchor from the 2026-07-18 incident: four creates were dispatched
(and human-approved) for repos that already existed. The preflight must
refuse when the name is taken in ANY casing — before the approval gate —
and report the canonical name.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str]:
    script = step_run("720-create-repo.yml", "preflight", "Verify target name is free")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        env = {
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "GITHUB_STEP_SUMMARY": str(summary),
            "HOME": str(tdp),
            "IN_REPO": "FFC-EX-example.org",
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, summary.read_text()


def test_existing_repo_refused_with_canonical_name():
    # Case-variant input must be refused with the CANONICAL name in the message.
    proc, summary = run_preflight(
        {
            "IN_REPO": "ffc-ex-alltypetowing.com",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com"}',
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already exists as FreeForCharity/FFC-EX-AllTypeTowing.com" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_unparseable_exists_response_still_refuses():
    # rc=0 with a non-JSON body (fake gh default): the name is evidently taken —
    # refuse with the input-derived name rather than proceeding.
    proc, _ = run_preflight({})
    assert proc.returncode != 0, proc.stdout
    assert "already exists" in proc.stdout, proc.stdout


def test_free_name_proceeds():
    proc, summary = run_preflight({"TEST_REPO_META": "404"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "is free" in summary, summary


def test_sibling_repo_for_same_domain_refused_without_force():
    # Name is free, but an un-prefixed org repo already serves this domain —
    # the exact trap that created a redundant FFC-EX-technologymonastery.org.
    proc, summary = run_preflight(
        {
            "IN_REPO": "FFC-EX-technologymonastery.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "TechnologyMonastery.org",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "TechnologyMonastery.org" in proc.stdout, proc.stdout
    assert "sibling" in summary.lower(), summary


def test_sibling_refusal_overridden_by_force():
    proc, summary = run_preflight(
        {
            "IN_REPO": "FFC-EX-technologymonastery.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "TechnologyMonastery.org",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "is free" in summary, summary


def test_hyphenated_domain_prefix_strip_keeps_full_domain():
    # Only the literal FFC-EX- prefix is stripped: FFC-EX-the-trendylittlegeek.com
    # must scan for "the-trendylittlegeek.com" (NOT "trendylittlegeek.com"), so the
    # unrelated trendylittlegeek.com repo does not block this create.
    proc, summary = run_preflight(
        {
            "IN_REPO": "FFC-EX-the-trendylittlegeek.com",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "FFC-EX-trendylittlegeek.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "is free" in summary, summary


def test_org_list_failure_fails_safe():
    proc, _ = run_preflight({"TEST_REPO_META": "404", "TEST_ORG_REPOS_FAIL": "1"})
    assert proc.returncode != 0, proc.stdout
    assert "could not list org repos" in proc.stdout, proc.stdout


def test_org_list_failure_bypassed_by_force():
    proc, summary = run_preflight(
        {"TEST_REPO_META": "404", "TEST_ORG_REPOS_FAIL": "1", "IN_FORCE": "true"}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "scan skipped" in summary, summary


def test_sibling_scan_full_domain_no_tld_false_positive():
    proc, summary = run_preflight(
        {
            "IN_REPO": "FFC-EX-letsdanceactivities.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "FFC-EX-letsdanceactivities.com\ntrendylittlegeek.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "is free" in summary, summary


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
