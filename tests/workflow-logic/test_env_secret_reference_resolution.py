"""Unit tests for scripts/check-env-secret-references.py (#912).

The checker is the shipped implementation — this module imports it rather than
re-deriving its rules, so the logic under test is the logic that runs in CI.

What the guard is for: `228. WHMCS Fraud Review` had **never** succeeded. Its
`fetch_fraud_orders` job read the Azure OIDC identifiers from `vars.*`; its
`fraud_review` job, in the same file, read them from `secrets.*` against
`fraudlabspro-prod-read`, an environment holding no secrets at all. Valid YAML,
`actionlint` clean, three scheduled failures, nobody told. #834 was the same
defect twice before that (726, and 502's `deliver` job).

Two directions, both load-bearing:

  * A **new** lane wired with `secrets.<identifier>` against an unprovisioned
    environment fails `test_the_guard_flags_228s_pre_fix_shape` — built by
    mutating the real 228 back to how it shipped, so the case is pinned by a
    test rather than by leaving the live defect in the tree for the guard to
    point at.
  * A **stale exemption** fails `test_an_unused_exemption_is_reported`. An
    environment that no longer needs its entry has to lose it, or the list rots
    into a permanent allowance for the thing it was written to catch.

The false-positive surface is asserted too, because it is where the first draft
of this scan was wrong: `$secrets.Count` in a pwsh block and the word "secrets"
in a comment are not references, and a guard that flagged them would have to be
switched off to land anything.
"""

from __future__ import annotations

import copy
import importlib.util
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-env-secret-references.py"


def _load_checker():
    """Import the hyphenated script as a module (same shape as test_502)."""
    spec = importlib.util.spec_from_file_location("check_env_secret_references", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8-sig")) or {}


# --- the live invariants ----------------------------------------------------


def test_the_tree_has_no_unresolvable_secret_references():
    findings, _ = checker.audit_tree()
    assert not findings, "\n".join(str(f) for f in findings)


def test_no_exemption_is_stale():
    _, usage = checker.audit_tree()
    stale = checker.unused_allowed_environments(usage)
    assert not stale, (
        f"listed in IDENTIFIER_SECRET_ENVIRONMENTS but no longer referenced: "
        f"{stale} — delete the entries."
    )


def test_the_scan_actually_finds_references():
    """Anti-vacuity: a broken matcher would pass every assertion above."""
    _, usage = checker.audit_tree()
    assert usage, (
        "the scan found ZERO OIDC-identifier secret references in the whole "
        "tree. There are dozens; the matcher has stopped seeing them, and every "
        "other test in this module is passing on an empty set."
    )
    assert sum(usage.values()) >= 40, (
        f"only {sum(usage.values())} identifier secret reference(s) found across "
        f"{sorted(usage)} — suspiciously few for this tree."
    )


def test_every_exemption_is_justified_in_writing():
    """The list records evidence, not preference — an empty reason is not one."""
    thin = sorted(
        env
        for env, reason in checker.IDENTIFIER_SECRET_ENVIRONMENTS.items()
        if len(reason.strip()) < 40
    )
    assert not thin, (
        f"these exemptions carry no real evidence: {thin}. An environment is "
        "listed because something ran green against it; say what."
    )


def test_228_and_the_candid_lanes_now_use_repository_variables():
    """The three converted lanes, pinned by name.

    `test_the_tree_has_no_unresolvable_secret_references` would also fail if one
    regressed, but it would report it as a generic finding. These three are the
    live instances #912 was opened about, and a regression on them is a
    regression against a specific fix.
    """
    lanes = {
        "228-whmcs-fraud-review.yml": "fraud_review",
        "801-candid-charity-check.yml": "charity_check",
        "802-candid-essentials-search.yml": "essentials_search",
    }
    problems = []
    for filename, job_id in lanes.items():
        job = (_workflow(filename).get("jobs") or {})[job_id]
        rendered = yaml.safe_dump(job, default_flow_style=False, allow_unicode=True)
        references = checker.context_references(rendered)
        identifiers = {
            (context, name)
            for context, name in references
            if checker.is_oidc_identifier(name)
        }
        assert identifiers, f"{filename}:{job_id} reads no OIDC identifier at all"
        wrong = sorted(name for context, name in identifiers if context == "secrets")
        if wrong:
            problems.append(
                f"{filename}: job '{job_id}' is back on secrets.{wrong} — it must "
                "read the repository Variables (vars.*), which resolve without a "
                "per-environment secret copy."
            )
    assert not problems, "\n".join(problems)


# --- the guard can actually fail -------------------------------------------


def test_the_guard_flags_228s_pre_fix_shape():
    """Mutate the real 228 back to how it shipped; the guard must catch it."""
    workflow = copy.deepcopy(_workflow("228-whmcs-fraud-review.yml"))
    job = workflow["jobs"]["fraud_review"]

    # Rewrite every `vars.READ_ALL_FFC_AZURE_*` in the job back to `secrets.*`,
    # which is exactly the diff this PR reverses.
    rendered = yaml.safe_dump(job, default_flow_style=False, allow_unicode=True)
    reverted = rendered.replace("vars.READ_ALL_FFC_AZURE", "secrets.READ_ALL_FFC_AZURE")
    assert reverted != rendered, (
        "the mutation changed nothing — 228's fraud_review job no longer reads "
        "vars.READ_ALL_FFC_AZURE_*, so this test is asserting on the wrong shape."
    )
    workflow["jobs"]["fraud_review"] = yaml.safe_load(reverted)

    findings = checker.audit_workflow("228-whmcs-fraud-review.yml", workflow)
    assert findings, (
        "the guard passed 228's pre-fix shape — the defect it exists for. "
        "R1 is not firing."
    )
    text = "\n".join(str(f) for f in findings)
    for expected in (
        "fraud_review",
        "fraudlabspro-prod-read",
        "READ_ALL_FFC_AZURE_KV_CLIENT_ID",
        "vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID",
        "EMPTY STRING",
    ):
        assert expected in text, (
            f"the finding does not mention {expected!r}, so it does not tell the "
            f"reader what is wrong or how to fix it:\n{text}"
        )


def test_an_unresolvable_reference_in_a_new_environment_is_flagged():
    """The general R1 case: a lane wired against an unlisted environment."""
    workflow = {
        "jobs": {
            "probe": {
                "environment": "some-new-prod-read",
                "steps": [
                    {
                        "uses": "./.github/actions/whmcs-secrets-from-kv",
                        "with": {
                            "azure-client-id": (
                                "${{ secrets.READ_ALL_FFC_AZURE_KV_CLIENT_ID }}"
                            )
                        },
                    }
                ],
            }
        }
    }
    findings = checker.audit_workflow("999-new.yml", workflow)
    assert len(findings) == 1, [str(f) for f in findings]
    assert findings[0].rule == "R1"
    assert "some-new-prod-read" in findings[0].message


def test_a_secret_with_no_environment_is_flagged():
    """R2: this repo holds no repo-level secrets, so the value is empty."""
    workflow = {
        "jobs": {
            "publish": {
                "steps": [{"run": "echo ${{ secrets.SOME_DEPLOY_TOKEN }}"}],
            }
        }
    }
    findings = checker.audit_workflow("999-new.yml", workflow)
    assert len(findings) == 1, [str(f) for f in findings]
    assert findings[0].rule == "R2"
    assert "SOME_DEPLOY_TOKEN" in findings[0].message


def test_an_identifier_with_no_environment_is_flagged_as_r1():
    """The identifier form of the same mistake keeps its own remediation.

    R2's advice ("declare the environment that holds it") is wrong for an
    identifier — the fix is `vars.*`, which needs no environment at all. A guard
    that gave the R2 message here would send the reader to provision secret
    copies that should not exist.
    """
    workflow = {
        "jobs": {
            "probe": {
                "steps": [
                    {"run": "echo ${{ secrets.READ_ALL_FFC_AZURE_TENANT_ID }}"}
                ],
            }
        }
    }
    findings = checker.audit_workflow("999-new.yml", workflow)
    assert len(findings) == 1, [str(f) for f in findings]
    assert findings[0].rule == "R1"
    assert "vars.READ_ALL_FFC_AZURE_TENANT_ID" in findings[0].message


def test_the_bracket_syntax_is_not_a_bypass():
    """`secrets['NAME']` is identical at run time to `secrets.NAME`."""
    workflow = {
        "jobs": {
            "probe": {
                "environment": "some-new-prod-read",
                "steps": [
                    {"run": "echo ${{ secrets['READ_ALL_FFC_AZURE_TENANT_ID'] }}"}
                ],
            }
        }
    }
    findings = checker.audit_workflow("999-new.yml", workflow)
    assert len(findings) == 1, [str(f) for f in findings]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in findings[0].message


def test_an_unused_exemption_is_reported():
    """A listed environment nothing references must lose its entry."""
    usage = {env: 3 for env in checker.IDENTIFIER_SECRET_ENVIRONMENTS}
    assert checker.unused_allowed_environments(usage) == []

    listed = sorted(checker.IDENTIFIER_SECRET_ENVIRONMENTS)
    usage.pop(listed[0])
    assert checker.unused_allowed_environments(usage) == [listed[0]]


# --- the false-positive surface --------------------------------------------


def test_a_provisioned_environment_is_left_alone():
    """101 reads `secrets.READ_ALL_*` under cloudflare-prod-read and works.

    Named explicitly because the tempting version of this rule — "identifiers
    are repository Variables, so `secrets.<identifier>` is always wrong", which
    is what CLAUDE.md says — flags 51 jobs across 5 environments that run green
    in production.
    """
    findings = checker.audit_workflow("101-domain-status.yml", _workflow("101-domain-status.yml"))
    assert not findings, [str(f) for f in findings]


def test_powershell_property_access_is_not_a_reference():
    """`$secrets.Count` is a property read, not a context lookup."""
    workflow = {
        "jobs": {
            "inventory": {
                "steps": [
                    {
                        "shell": "pwsh",
                        "run": (
                            "$secrets = az keyvault secret list --vault-name kv | "
                            "ConvertFrom-Json\n"
                            "Write-Host \"$($secrets.Count) secrets\"\n"
                            "foreach ($s in $secrets) { Write-Host $s.name }\n"
                            "# reads /repositories/{id}/environments/{env}/secrets\n"
                        ),
                    }
                ],
            }
        }
    }
    assert checker.audit_workflow("999-new.yml", workflow) == []
    assert checker.context_references(
        yaml.safe_dump(workflow, allow_unicode=True)
    ) == set()


def test_the_ambient_token_needs_no_environment():
    workflow = {"jobs": {"audit": {"steps": [{"run": "gh api repos/x", "env": {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}}]}}}
    assert checker.audit_workflow("999-new.yml", workflow) == []


def test_repository_variables_are_never_a_finding():
    """`vars.*` is the remedy; flagging it would make the fix unlandable."""
    workflow = {
        "jobs": {
            "probe": {
                "environment": "some-new-prod-read",
                "steps": [
                    {
                        "uses": "./.github/actions/whmcs-secrets-from-kv",
                        "with": {
                            "azure-client-id": (
                                "${{ vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID }}"
                            ),
                            "azure-tenant-id": (
                                "${{ vars.READ_ALL_FFC_AZURE_TENANT_ID }}"
                            ),
                        },
                    }
                ],
            }
        }
    }
    assert checker.audit_workflow("999-new.yml", workflow) == []


def test_the_script_runs_as_a_command_and_reports_its_verdict():
    """The entrypoint a human (or another workflow) invokes, not just the API.

    Every assertion above imports functions directly, so a broken `main()` —
    a typo in the summary line, a wrong exit code — would ship green.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "Environment secret references OK" in proc.stdout, proc.stdout
    assert "::error::" not in proc.stdout, proc.stdout


def test_main_fails_the_build_when_there_is_a_finding():
    """The green path above cannot prove the red one.

    Run against the real tree, `main()` returns 0 — so a mutation that made it
    return 0 *unconditionally* would leave every other test in this module
    passing while CI stopped failing on the defect. The exit code is the entire
    interface CI consumes, so it is asserted on a substituted verdict rather
    than on the tree, which is clean by construction after this PR.
    """
    original = checker.audit_tree
    usage = {env: 1 for env in checker.IDENTIFIER_SECRET_ENVIRONMENTS}
    checker.audit_tree = lambda: (
        [checker.Finding("999-new.yml", "probe", "R1", "a planted finding")],
        usage,
    )
    try:
        assert checker.main() == 1, (
            "main() returned 0 with a finding in hand — CI would go green over "
            "the exact defect this script exists to fail."
        )
        checker.audit_tree = lambda: ([], usage)
        assert checker.main() == 0
    finally:
        checker.audit_tree = original


def test_an_environment_object_form_is_understood():
    """`environment: {name: …, url: …}` is the same declaration."""
    job = {"environment": {"name": "cloudflare-prod-read", "url": "https://x"}}
    assert checker.job_environment(job) == "cloudflare-prod-read"
    assert checker.job_environment({"environment": "m365-prod"}) == "m365-prod"
    assert checker.job_environment({}) is None


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
