"""Unit tests for the 732 bulk-create preflight (bash, fake gh).

732 loops the same collision + sibling-domain checks 720's preflight already
uses (see test_720_preflight.py) across a whole domain list, so this module
does not re-prove those individual checks in isolation — it proves the part
unique to the loop: input normalization/dedup, per-domain skip without
aborting the batch, aggregate skip counting, and the all-skipped fail-closed
case.

The fake `gh` in harness/gh returns ONE canned response per call SHAPE
(it cannot vary its answer by which repo name was asked), so scenarios below
that need "domain A exists, domain B doesn't" use the SIBLING check instead
of the exists check — the sibling grep runs entirely in this script's own
bash against a shared org-repo listing, so it genuinely differentiates by
domain even though the fake `gh` cannot.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, dict]:
    script = step_run("732-bulk-create-repos.yml", "preflight", "Parse and check each requested domain")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        outputs = tdp / "outputs.txt"
        outputs.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(outputs),
            HOME=str(tdp),
            IN_DOMAINS="example.org",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        out = {}
        for line in outputs.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
        return proc, summary.read_text(encoding="utf-8"), out


def test_multiple_free_domains_all_survive():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org,three.org",
            "TEST_REPO_META": "404",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["one.org", "two.org", "three.org"], clean
    assert out["clean_count"] == "3", out
    assert out["skipped_count"] == "0", out


def test_normalization_dedup_and_case():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "  https://One.org/ ,\nwww.two.org\nONE.org,two.org",
            "TEST_REPO_META": "404",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    # First occurrence wins; scheme/www/trailing-slash stripped; lowercased.
    assert clean == ["one.org", "two.org"], clean


def test_uppercase_scheme_stripped_before_lowercase():
    # Regression: scheme/www stripping is case-sensitive bash parameter expansion,
    # so it must run AFTER lowercasing, not before, or an uppercase scheme like
    # "HTTPS://" survives into the domain (Copilot review finding on PR #1250).
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "HTTPS://Example.org/",
            "TEST_REPO_META": "404",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["example.org"], clean


def test_empty_domains_input_fails():
    proc, _, _ = run_preflight({"IN_DOMAINS": "   ,, \n "})
    assert proc.returncode != 0, proc.stdout
    assert "No usable domains parsed" in proc.stdout, proc.stdout


def test_sibling_match_skips_only_that_domain():
    # Sibling scan runs per-domain against one shared org-repo listing, so a
    # name matching only "two.org" must not touch "one.org" or "three.org".
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org,three.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "FFC-EX-two.org-legacy",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["one.org", "three.org"], clean
    assert out["skipped_count"] == "1", out
    assert "sibling repo(s) match" in summary, summary


def test_force_bypasses_sibling_check_for_every_domain():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "FFC-EX-one.org-legacy\nFFC-EX-two.org-legacy",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["one.org", "two.org"], clean


def test_all_domains_already_exist_fails_the_whole_batch():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert out.get("clean_domains") == "[]", out
    assert out.get("skipped_count") == "2", out
    assert "No domains are free to create" in proc.stdout, proc.stdout


def test_org_list_failure_fails_safe_without_force():
    proc, _, _ = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "Could not list org repos" in proc.stdout, proc.stdout


def test_org_list_failure_bypassed_by_force():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS_FAIL": "1",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["one.org", "two.org"], clean


def test_hyphenated_domain_prefix_strip_keeps_full_domain():
    # Same trap as 720's own test: a sibling FFC-EX-trendylittlegeek.com must
    # not block FFC-EX-the-trendylittlegeek.com (only the literal FFC-EX-
    # prefix is stripped before the sibling match).
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "the-trendylittlegeek.com",
            "TEST_REPO_META": "404",
            "TEST_ORG_REPOS": "FFC-EX-trendylittlegeek.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clean = json.loads(out["clean_domains"])
    assert clean == ["the-trendylittlegeek.com"], clean


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
