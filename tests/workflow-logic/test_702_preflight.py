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
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

# `has_pages` is read from this same response, so the fixture has to carry it. true is
# the realistic value for every target this workflow runs against: 720 creates the repo
# with the FFC template already deployed, which is what makes the default Pages URL
# answer 200. The two variants below are the cases where it is not true, and the
# preflight treats them differently — see the 404-shaped-denial tests.
CANONICAL_META = (
    '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com", "archived": false,'
    ' "has_pages": true}'
)

# Pages genuinely never enabled on the target. The only shape in which a 404 from
# `repos/<r>/pages` is an affirmative "no custom domain is bound".
NO_PAGES_META = (
    '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com", "archived": false,'
    ' "has_pages": false}'
)

# The field absent altogether — a trimmed or unexpected metadata response. Must fail
# safe rather than be read as either answer.
NO_HAS_PAGES_FIELD_META = (
    '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com", "archived": false}'
)


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
    # A COMPLETED clone: `_disabled_template_routes/` is present, so the 200 on the
    # default URL is the charity's own site and re-cloning would supersede it.
    # Pre-#1538 this scenario needed only the 200; see the pair of tests below for
    # why that was a false positive on every freshly created repo.
    proc, summary, _ = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "200",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_CLONE_MARKER": "present",
        }
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


def test_sibling_refusal_overridden_by_force_sibling_only():
    scenario = {
        "IN_DOMAIN": "technologymonastery.org",
        "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-technologymonastery.org"}',
        "TEST_PAGES_CODE": "404",
        "TEST_APEX_SERVER": "cloudflare",
        "TEST_ORG_REPOS": "TechnologyMonastery.org",
    }
    # force_sibling=true is the only flag that skips the scan.
    proc, _, outputs = run_preflight({**scenario, "IN_FORCE_SIBLING": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=" in outputs, outputs


def test_force_no_longer_skips_the_sibling_scan():
    # The security half of #1538. force exists to get past a live-site verdict;
    # it used to ALSO drop "the org already hosts another repo for this domain --
    # one of which may be the charity's real site". An operator forced past a
    # cosmetic false positive lost a guard they never meant to drop.
    proc, _, _ = run_preflight(
        {
            "IN_DOMAIN": "technologymonastery.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-technologymonastery.org"}',
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_SERVER": "cloudflare",
            "TEST_ORG_REPOS": "TechnologyMonastery.org",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "TechnologyMonastery.org" in proc.stdout, proc.stdout
    assert "force_sibling=true" in proc.stdout, proc.stdout


# --- #1538: the two false positives, as a decision table ---------------------
#
# Measured on the seven 702 runs left waiting at github-prod from the 2026-09-29
# batch: five of the seven had been dispatched with force=true, every one of them
# after an un-forced attempt failed at one of the two guards below. Both guards
# fired on the NORMAL pre-cutover state, and force -- the only way past either --
# also switched off the unrelated sibling-domain scan.
#
# The shape of a freshly created, never-cloned FFC-EX target: workflow 720 made
# the repo with the generic FFC template already deployed, so the default Pages
# URL answers 200; no custom domain is bound yet, because binding it is what the
# cutover does, so nothing answers the apex and Pages reports cname=null.
FRESH_TARGET = {
    "TEST_REPO_META": CANONICAL_META,
    "TEST_PAGES_CODE": "200",
    "TEST_APEX_UNRESOLVED": "1",
    "TEST_PAGES_CNAME": "",
}


def test_fresh_720_target_passes_unforced():
    # The whole point of #1538: this is the case that needed force=true, twice over.
    proc, summary, outputs = run_preflight(FRESH_TARGET)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs, outputs
    assert "Refused" not in summary, summary
    assert "what approving the `github-prod` gate will run" in summary, summary


def test_unbound_apex_is_not_a_probe_failure():
    # AC1. An empty apex_code with cname=null is the expected pre-cutover state,
    # not an incomplete probe -- so the probe-failure branch must not be entered.
    proc, summary, _ = run_preflight({**FRESH_TARGET, "TEST_PAGES_CODE": "404"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "probes" not in summary.lower(), summary
    assert "apex is expected to be silent pre-cutover" in summary, summary


def test_unbound_apex_refusal_no_longer_recommends_force():
    # AC4. The remedy for this case is for the guard not to fire, so no refusal
    # text offering force=true may appear on the pre-cutover shape at all.
    proc, summary, _ = run_preflight({**FRESH_TARGET, "TEST_PAGES_CODE": "404"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not verify target state" not in summary, summary
    assert "Re-dispatch with force=true only if you have verified" not in proc.stdout, proc.stdout


def test_silent_apex_with_a_bound_domain_still_refuses():
    # The inverse of AC1, and the reason the fix reads cname rather than just
    # dropping the check: a BOUND custom domain should answer. Silence there is
    # unexplained, so it must still refuse -- and without leading with force.
    proc, summary, _ = run_preflight(
        {**FRESH_TARGET, "TEST_PAGES_CNAME": "alltypetowing.com"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "bound on Pages" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary
    assert "Re-dispatch with force=true only if you have verified" not in proc.stdout, proc.stdout


def test_unreadable_pages_config_with_silent_apex_fails_safe():
    # cname unknown => the unbound case is NOT established, so the old refusal
    # stands. Fails safe rather than cloning over a possibly-live target.
    proc, summary, _ = run_preflight(
        {**FRESH_TARGET, "TEST_PAGES_GET_FAIL": "1"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not be read" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_pages_not_configured_counts_as_unbound():
    # A 404 from repos/<r>/pages WHEN the repo metadata agrees Pages is not set up
    # means no custom domain is bound either -- it must not read as an unreadable
    # config. `has_pages: false` is what makes this the genuine case rather than the
    # denial below; the two are indistinguishable from the 404 alone.
    proc, _, outputs = run_preflight(
        {
            **FRESH_TARGET,
            "TEST_REPO_META": NO_PAGES_META,
            "TEST_PAGES_CODE": "404",
            "TEST_PAGES_NOT_CONFIGURED": "1",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=" in outputs, outputs


def test_a_404_shaped_denial_of_the_pages_read_refuses_rather_than_reading_as_unbound():
    """The failure mode this guard's own fail-safe depended on not existing.

    `repos/<canonical>/pages` is a CROSS-REPO read and this job is ungated with
    `github.token`, which holds no permissions on `$canonical`. GitHub answers 404 --
    not 403 -- for a repository resource a token cannot see, so a denial arrives in
    exactly the shape the code read as "Pages is not configured, therefore unbound".

    `has_pages: true` with a 404 from /pages is that denial: the endpoint should have
    answered. It is not an affirmative read of anything, so it must not downgrade a
    silent apex to "expected" -- otherwise the mid-cutover case (custom domain bound,
    DNS not yet propagated) passes through to the gate, which is the one state
    `test_silent_apex_with_a_bound_domain_still_refuses` exists to stop.
    """
    proc, summary, _ = run_preflight(
        {**FRESH_TARGET, "TEST_PAGES_NOT_CONFIGURED": "1"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not be read" in proc.stdout, proc.stdout
    assert "has_pages=true" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_an_absent_has_pages_field_fails_safe_rather_than_picking_an_answer():
    # The disambiguator is only as good as the field being there. Absent => neither
    # cause is established => refuse, the same as any other unreadable config.
    proc, summary, _ = run_preflight(
        {
            **FRESH_TARGET,
            "TEST_REPO_META": NO_HAS_PAGES_FIELD_META,
            "TEST_PAGES_NOT_CONFIGURED": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not be read" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_the_pages_read_is_not_made_twice_to_answer_the_has_pages_question():
    """`has_pages` comes out of the repo-existence response, so the disambiguation
    costs no extra API call. Pinned because the obvious alternative -- a second
    `gh api repos/<r>` -- would pass every test above while doubling the calls this
    ungated preflight makes against a repo it cannot read."""
    with tempfile.TemporaryDirectory() as td:
        log = pathlib.Path(td) / "gh.log"
        proc, _, _ = run_preflight({**FRESH_TARGET, "TEST_GH_LOG": str(log)})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
    # Matched by shape, not by name: the metadata read uses the DISPATCHED casing
    # ("…-alltypetowing.com"), because resolving the canonical name is what it is for.
    meta_reads = [c for c in calls if re.fullmatch(r"api repos/[^/ ]+/[^/ ]+", c.strip())]
    assert len(meta_reads) == 1, f"repo metadata read {len(meta_reads)} times:\n" + "\n".join(calls)


def test_default_url_200_on_the_template_seed_is_not_a_completed_migration():
    # AC2, the positive half: marker absent AND the generic template title.
    proc, summary, outputs = run_preflight(FRESH_TARGET)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "already serves a live site" not in proc.stdout, proc.stdout
    assert "720's template seed" in summary, summary
    assert "repo_name=" in outputs, outputs


def test_default_url_200_with_a_branded_title_refuses_even_without_the_marker():
    # AC2, the other discriminator: a site migrated by some other means carries no
    # `_disabled_template_routes/`, so the marker alone would fail open on it. The
    # served title is what catches it.
    proc, summary, _ = run_preflight(
        {
            **FRESH_TARGET,
            "TEST_PAGES_BODY": "<html><head><title>BusiNeighbor | Local Business Support</title></head></html>",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout
    assert "BusiNeighbor" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_default_url_200_with_an_unreadable_body_refuses():
    # Both discriminators must AGREE before a 200 is treated as the seed, so an
    # empty body is "not established as the seed" and refuses. The failure
    # direction matters: a missed read must not authorize cloning over a site.
    proc, _, _ = run_preflight({**FRESH_TARGET, "TEST_PAGES_BODY": ""})
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout


def test_default_url_200_with_an_unreadable_marker_refuses():
    # Same, for the other discriminator: a 403 on the contents read leaves the
    # marker unknown, which is not "absent".
    proc, _, _ = run_preflight({**FRESH_TARGET, "TEST_CLONE_MARKER": "error"})
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout
    assert "unknown" in proc.stdout, proc.stdout


def test_cutover_apex_refuses_regardless_of_the_seed_discriminators():
    # The AllTypeTowing regression must not be weakened by the AC2 relaxation: an
    # apex serving from GitHub Pages is a genuinely live custom domain, and the
    # seed discriminators are about the DEFAULT url only. Marker absent, template
    # title, 200 on the default URL -- and it still refuses, on the apex alone.
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "200",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "GitHub.com",
            "TEST_PAGES_CNAME": "alltypetowing.com",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout


def test_live_refusal_does_not_depend_on_the_sibling_scan():
    # Required explicitly by the issue's verification list. The live verdict is
    # reached before the sibling scan runs, so a clean org listing cannot rescue
    # a live target and a dirty one cannot be what refuses it.
    proc, _, _ = run_preflight(
        {
            **FRESH_TARGET,
            "TEST_CLONE_MARKER": "present",
            "TEST_ORG_REPOS": "",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout
    assert "sibling" not in proc.stdout.lower(), proc.stdout


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
    # force covers the live-site and probe-failure guards and says exactly that.
    # It must NOT claim the sibling scan, which is now force_sibling's (#1538 AC3):
    # one flag silently switching off three unrelated guards is the defect.
    proc, summary, _ = run_preflight(
        {**PASSING, "TEST_PAGES_CODE": "200", "TEST_CLONE_MARKER": "present", "IN_FORCE": "true"}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| force | `true` — the live-site and probe-failure guards above were SKIPPED |" in summary, summary
    assert "sibling-repo guards above were SKIPPED" not in summary, summary
    assert "| force_sibling | `false` |" in summary, summary


def test_plan_flags_force_sibling_separately():
    proc, summary, _ = run_preflight({**PASSING, "IN_FORCE_SIBLING": "true"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| force_sibling | `true` — the sibling-repo guard above was SKIPPED |" in summary, summary
    assert "| force | `false` |" in summary, summary
    assert "force_sibling=true: sibling-domain scan skipped." in summary, summary


def test_refused_run_prints_no_plan():
    # A refusal must not leave a plan behind that reads as "safe to approve".
    proc, summary, _ = run_preflight(
        {
            **PASSING,
            "TEST_PAGES_CODE": "200",
            "TEST_CLONE_MARKER": "present",
            "IN_DRY_RUN": "false",
        }
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
