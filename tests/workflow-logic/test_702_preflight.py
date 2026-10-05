"""Unit tests for the 702 clone-deploy preflight (bash, fake gh + fake curl).

Regression anchors from the 2026-07-18 incident: a clone was dispatched
against FFC-EX-AllTypeTowing.com — an already-completed migration cut over
on its apex — because a stale inventory and a wrong-case Pages probe passed
for verification. The preflight must: require the repo to exist, resolve
canonical casing via the API, and refuse to re-clone a live site without
force=true.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

CANONICAL_META = '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com", "archived": false}'


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the preflight step. Returns (proc, summary, outputs)."""
    script = step_run("702-ffc-ex-clone-deploy.yml", "preflight", "Verify target state")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        outputs = tdp / "output.txt"
        summary.touch()
        outputs.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(outputs),
            HOME=str(tdp),
            TARGET_ORG="FreeForCharity",
            IN_DOMAIN="alltypetowing.com",
            IN_REPO="",
            IN_FORCE="false",
            # The approver's-plan inputs. Defaults mirror the dispatch form's
            # defaults; a scenario overrides what it is about.
            IN_DRY_RUN="true",
            IN_BUILD_CHECK="true",
            IN_DEPTH="8",
            IN_EXCLUDE="",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=60,
        )
        return proc, summary.read_text(encoding="utf-8"), outputs.read_text(encoding="utf-8")


def test_missing_repo_fails_fast_with_create_guidance():
    proc, _, _ = run_preflight({"TEST_REPO_META": "404"})
    assert proc.returncode != 0, proc.stdout
    assert "does not exist" in proc.stdout and "720" in proc.stdout, proc.stdout


def test_unparseable_repo_response_fails_safe():
    # Fake gh's default *repos/* response is a non-JSON settings string.
    proc, _, _ = run_preflight({})
    assert proc.returncode != 0, proc.stdout
    assert "unparseable" in proc.stdout.lower() or "Failing safe" in proc.stdout, proc.stdout


def test_live_on_default_url_refused_without_force():
    proc, summary, _ = run_preflight(
        {"TEST_REPO_META": CANONICAL_META, "TEST_PAGES_CODE": "200", "TEST_APEX_SERVER": "cloudflare"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout
    assert "force=true" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_cutover_apex_refused_without_force():
    # Default URL 404 (custom-domain builds move to root paths) but apex serves
    # from GitHub Pages — the AllTypeTowing shape exactly.
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "GitHub.com",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout


def test_force_overrides_live_refusal_and_emits_canonical_name():
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "200",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "GitHub.com",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs, outputs


def test_not_yet_live_proceeds_with_canonical_casing():
    # Lower-case input resolves to the canonical CamelCase name via the API —
    # github.io paths are case-sensitive, so downstream must use this value.
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "cloudflare",
            "IN_REPO": "ffc-ex-alltypetowing.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs, outputs


def test_sibling_repo_for_same_domain_refused_without_force():
    # The TechnologyMonastery.org shape: an un-prefixed org repo is the charity's
    # real site while the FFC-EX target is empty. Must refuse and name it.
    proc, summary, _ = run_preflight(
        {
            "IN_DOMAIN": "technologymonastery.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-technologymonastery.org"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_ORG_REPOS": "TechnologyMonastery.org\nFFC-EX-other.org",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "TechnologyMonastery.org" in proc.stdout, proc.stdout
    assert "sibling" in summary.lower(), summary


def test_sibling_scan_full_domain_no_tld_false_positive():
    # letsdanceactivities.com must NOT block letsdanceactivities.org.
    proc, _, outputs = run_preflight(
        {
            "IN_DOMAIN": "letsdanceactivities.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-letsdanceactivities.org"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_ORG_REPOS": "FFC-EX-letsdanceactivities.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-letsdanceactivities.org" in outputs, outputs


def test_missing_server_header_is_noncutover_not_a_crash():
    # No Server header is a valid non-cutover outcome; under set -e/pipefail
    # the grep pipeline must not kill the preflight.
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-alltypetowing.com"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_NO_SERVER": "1",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs or "repo_name=FFC-EX-alltypetowing.com" in outputs, outputs


def test_probe_failure_fails_safe_not_notlive():
    # A curl-level failure (000 / empty) is a failed PROBE, not a "not live"
    # verdict — must refuse rather than fail open past the gate.
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-alltypetowing.com"}',
            "TEST_PAGES_CODE": "000",
            "TEST_APEX_SERVER": "cloudflare",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "failing safe" in proc.stdout.lower(), proc.stdout
    assert "probes failed" in summary, summary


def test_probe_failure_bypassed_by_force():
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-alltypetowing.com"}',
            "TEST_PAGES_CODE": "000",
            "TEST_APEX_SERVER": "cloudflare",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=" in outputs, outputs


def test_archived_target_refused_even_with_force():
    # Archived = read-only: the gated job would fail at push/PR after consuming
    # an approval, and no flag makes it writable — so force must NOT bypass.
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-alltypetowing.com", "archived": true}',
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "archived" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_org_list_failure_fails_safe():
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-alltypetowing.com"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_ORG_REPOS_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not list org repos" in proc.stdout, proc.stdout


def test_sibling_refusal_overridden_by_force():
    proc, _, outputs = run_preflight(
        {
            "IN_DOMAIN": "technologymonastery.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-technologymonastery.org"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_ORG_REPOS": "TechnologyMonastery.org",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=" in outputs, outputs


# --- the approver's plan ------------------------------------------------------
#
# The github-prod reviewer sees the run name and the run summary and nothing
# else before "Approve". Run 36569414510 (stylesbysheba.com, dry_run=false)
# reached the gate with a three-line summary that named neither the branch,
# the PR, the credential nor the fact that the run would write. These pin the
# plan the preflight now prints, and that it prints it ONLY on a pass.

PASSING = {
    "TEST_REPO_META": CANONICAL_META,
    "TEST_PAGES_CODE": "404",
    "TEST_APEX_CODE": "200",
    "TEST_APEX_SERVER": "cloudflare",
}


def test_live_run_plan_names_target_branch_draft_pr_and_credential():
    proc, summary, _ = run_preflight({**PASSING, "IN_DRY_RUN": "false"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "what approving the `github-prod` gate will run" in summary, summary
    # The write, stated as a write, with every concrete name the approver needs.
    assert "dry_run=false — this run WRITES" in summary, summary
    assert "clone/static-from-live-<timestamp>" in summary, summary
    assert "FreeForCharity/FFC-EX-AllTypeTowing.com" in summary, summary
    assert "**draft** pull request against `main`" in summary, summary
    assert "Static clone of live alltypetowing.com (replaces template scaffold)" in summary, summary
    # By role and vault, not by `wr-all-…` literal: test_dry_run_skips_write_gates
    # reads such a literal in a job body as that job loading a writer credential.
    assert "cross-repo GitHub PAT (CBM_TOKEN), loaded from Key Vault `kv-ffc-admin-prod-cbm`" in summary, summary
    assert "never: pushes to or merges into `main`" in summary, summary
    # And the same verdict as an annotation beside the "Review deployments" button.
    assert "::notice title=Approving github-prod will::dry_run=false" in proc.stdout, proc.stdout
    assert "opens a DRAFT PR against main" in proc.stdout, proc.stdout


def test_dry_run_plan_says_nothing_is_written():
    proc, summary, _ = run_preflight({**PASSING, "IN_DRY_RUN": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dry_run=true — nothing is written" in summary, summary
    assert "No branch is pushed and no PR is opened" in summary, summary
    assert "this run WRITES" not in summary, summary
    assert "::notice title=Approving github-prod will::dry_run=true" in proc.stdout, proc.stdout
    assert "writes NOTHING" in proc.stdout, proc.stdout


def test_plan_echoes_every_input_the_gated_job_will_use():
    proc, summary, _ = run_preflight(
        {**PASSING, "IN_BUILD_CHECK": "false", "IN_DEPTH": "3", "IN_EXCLUDE": "/beta,/members"}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| build_check | `false` |" in summary, summary
    assert "| httrack depth / exclude | `3` / `/beta,/members` |" in summary, summary
    assert "Build check: **skipped** (build_check=false)" in summary, summary
    assert "depth 3, exclude: /beta,/members" in summary, summary


def test_plan_shows_blank_exclude_as_none_and_build_check_as_run():
    proc, summary, _ = run_preflight(PASSING)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "`8` / `(none)`" in summary, summary
    assert "Build check: runs `next build` and the self-contained gate" in summary, summary


def test_plan_flags_force_as_guards_skipped():
    proc, summary, _ = run_preflight({**PASSING, "TEST_PAGES_CODE": "200", "IN_FORCE": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| force | `true` — the live-site, probe-failure and sibling-repo guards above were SKIPPED |" in summary, summary


def test_refused_run_prints_no_plan():
    # A refusal must not leave a plan behind that reads as "safe to approve".
    proc, summary, _ = run_preflight(
        {**PASSING, "TEST_PAGES_CODE": "200", "IN_DRY_RUN": "false"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "Refused" in summary, summary
    assert "what approving" not in summary, summary
    assert "Approving github-prod will" not in proc.stdout, proc.stdout


def test_empty_dry_run_mapping_fails_closed():
    # The one line the approver most needs is whether the run writes. A missing
    # or misnamed env: mapping must refuse, not print a plan with it blank.
    proc, summary, _ = run_preflight({**PASSING, "IN_DRY_RUN": ""})
    assert proc.returncode != 0, proc.stdout
    assert "IN_DRY_RUN is empty" in proc.stdout, proc.stdout
    assert "what approving" not in summary, summary


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
