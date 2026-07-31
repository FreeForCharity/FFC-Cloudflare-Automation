"""Unit tests for 730's environment-gate audit bash step (run with a fake gh).

Regression anchor: ledger L02 / #854 / #889, in its pipeline variant. The
environment list was read with

    names=$(gh api "repos/$REPO/environments" --paginate --jq '…' 2>/dev/null | sort || true)

and `gh` writes its error body to STDOUT, so a 403 flowed *through the pipe*
into `$names`. The loop below then iterated over the lines of an API error
message as if they were environment names — and, worse, an empty-looking
result rendered as a table row rather than as a failure, so an audit that
could not read a single gate looked like an audit that found none.

This is the workflow that answers "which environments require a human
approval?", so misreporting gated as ungated is the whole cost.
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

GATED_ENV = (
    '{"protection_rules":[{"type":"required_reviewers","reviewers":'
    '[{"reviewer":{"login":"clarkemoyer"}}]},{"type":"wait_timer","wait_timer":5}],'
    '"deployment_branch_policy":{"protected_branches":true}}'
)


def run_step(env_overrides: dict) -> tuple[str, str, str, int]:
    """Run the audit step. Returns (summary, stdout, stderr, rc)."""
    script = step_run(
        "730-repo-audit-environment-gates.yml", "audit", "Audit environment protection rules"
    )
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        summary = td / "summary.md"
        summary.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(td / "gh.log"),
            HOME=str(td),
            GH_TOKEN="fake-token",
            REPO="FreeForCharity/FFC-Cloudflare-Automation",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return summary.read_text(encoding="utf-8"), proc.stdout, proc.stderr, proc.returncode


def test_an_unreadable_environment_list_fails_the_audit():
    summary, out, err, rc = run_step({"TEST_ENVIRONMENTS_FAIL": "1"})
    assert rc != 0, "an audit that measured nothing must be red\n" + summary
    assert "::error::Cannot read environments" in out, out
    assert "could not read the environment list" in summary, summary
    assert "403" in err, err


def test_an_unreadable_list_never_renders_as_no_gates():
    """The specific misreport: 'cannot see the environments' collapsing into
    'this repo has no environments', which reads as a clean bill of health."""
    summary, _out, _err, _rc = run_step({"TEST_ENVIRONMENTS_FAIL": "1"})
    assert "no environments defined" not in summary, summary
    # And the error body must never be rendered as an environment name.
    assert "Resource not accessible" not in summary, summary
    assert "| 403" not in summary, summary


def test_the_summary_is_still_published_when_the_read_fails():
    """Failing is right; failing silently is not — the step exits non-zero only
    after writing the table, so the run has something to show."""
    summary, out, _err, rc = run_step({"TEST_ENVIRONMENTS_FAIL": "1"})
    assert rc != 0
    assert "Environment approval gates" in summary, summary
    assert "Read-only audit; no changes made." in summary, summary
    assert "----- summary (also above) -----" in out, out


def test_a_repo_with_no_environments_is_a_clean_pass():
    summary, out, _err, rc = run_step({"TEST_ENVIRONMENTS": ""})
    assert rc == 0, out
    assert "no environments defined on this repository" in summary, summary
    assert "could not read" not in summary, summary


def test_gated_environments_are_rendered_with_their_reviewers():
    summary, out, _err, rc = run_step(
        {"TEST_ENVIRONMENTS": "whmcs-prod\ngithub-prod", "TEST_ENV_DETAIL": GATED_ENV}
    )
    assert rc == 0, out
    assert "| github-prod | clarkemoyer | 5 | protected only |" in summary, summary
    assert "| whmcs-prod | clarkemoyer | 5 | protected only |" in summary, summary
    # Sorted, so two runs of the audit are diffable against each other.
    assert summary.index("| github-prod |") < summary.index("| whmcs-prod |"), summary


def test_an_ungated_environment_reads_as_ungated():
    summary, _out, rc_err, rc = run_step({"TEST_ENVIRONMENTS": "github-prod-read"})
    assert rc == 0, rc_err
    assert "| github-prod-read | — | — | any |" in summary, summary


def test_a_per_environment_read_failure_is_marked_not_assumed_open():
    """A single environment that cannot be read must say so in its own row —
    rendering it as 'no reviewers / any' would report a gated environment as
    ungated, which is the inverse of this audit's purpose."""
    summary, _out, _err, rc = run_step(
        {"TEST_ENVIRONMENTS": "whmcs-prod", "TEST_ENV_DETAIL_FAIL": "1"}
    )
    assert rc == 0, summary  # the list was readable; only one row is unknown
    assert "| whmcs-prod | ⚠️ failed to read protection rules | — | — |" in summary, summary
    assert "| whmcs-prod | — |" not in summary, summary


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
