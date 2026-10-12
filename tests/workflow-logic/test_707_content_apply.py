"""Unit tests for 707 (Website - Content Apply): the job graph and the step bodies.

707 writes a worker agent's content into a charity's FFC-EX repo with the
org-wide PAT. The decision logic lives in scripts/website-content-apply-guard.ps1
and is unit-tested by Pester (scripts/tests/website-content-apply-guard.Tests.ps1).
This module pins what Pester cannot see: how the WORKFLOW wires that guard in.

  * `validate` is credential-free and ungated; `apply` is the only job on
    github-prod, is skipped on a dry run, and re-validates from the dispatch
    inputs instead of trusting `validate`'s outputs.
  * Every free-text input reaches a script body only through `env:` (#1080).
  * A refused payload is reported with workflow commands SUSPENDED, so a
    "path" crafted as a `::command::` line is printed, not executed, and the
    apply step refuses before its first network call.
  * The PR comes from `content/<run_id>`, is not a draft, and the default
    branch is never pushed.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "707-website-content-apply.yml"
FREE_TEXT_INPUTS = ("target_repo", "content_json", "title", "issue_url")
VALIDATE_STEP = "Validate inputs and payload"
APPLY_STEP = "Write the content and open the PR"

RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

GOOD_PAYLOAD = '{"src/data/team/jane-doe.json":"{\\"name\\":\\"Jane Doe\\",\\"role\\":\\"Founder\\"}"}'
# A refused path that is ALSO a workflow command if printed at the start of a
# line. The marker after it is what must never be interpreted.
INJECTED = "::warning::INJECTED-707"
EVIL_PAYLOAD = '{"' + INJECTED + '":"x","package.json":"{}"}'


def _wf() -> dict:
    return load_workflow(WORKFLOW)


def _jobs() -> dict:
    return _wf()["jobs"]


def _all_run_bodies():
    for job_id, job in _jobs().items():
        for step in job.get("steps") or []:
            if "run" in step:
                yield job_id, step


def _run_body(body: str, env_overrides: dict, cwd: pathlib.Path, path_prepend=()):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        out_file = tmp / "github_output.txt"
        summary = tmp / "summary.md"
        out_file.write_text("", encoding="utf-8")
        env = child_env(
            *path_prepend,
            GITHUB_OUTPUT=str(out_file),
            GITHUB_STEP_SUMMARY=str(summary),
            RUNNER_TEMP=str(tmp),
            **env_overrides,
        )
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        return proc.stdout + proc.stderr, proc.returncode, out_file.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Job graph (pure YAML)
# --------------------------------------------------------------------------


def test_validate_holds_no_credential_and_no_environment():
    job = _jobs()["validate"]
    assert "environment" not in job, f"validate must be ungated: {job.get('environment')}"
    perms = job.get("permissions") or _wf().get("permissions") or {}
    assert perms.get("id-token") != "write", "validate must not mint an OIDC token"
    text = str(job)
    assert "wr-all-" not in text and "keyvault" not in text.lower(), (
        "validate reaches a Key Vault credential"
    )


def test_apply_is_the_gated_writer_and_is_skipped_on_a_dry_run():
    job = _jobs()["apply"]
    assert job.get("environment") == "github-prod", job.get("environment")
    assert job.get("needs") in ("validate", ["validate"]), job.get("needs")
    cond = str(job.get("if", ""))
    assert "inputs.dry_run == false" in cond, f"apply must be skipped on a dry run: {cond!r}"
    assert "needs.validate.outputs.changed_count != '0'" in cond, cond


def test_both_jobs_run_the_same_validation():
    validate = find_step(_wf(), "validate", VALIDATE_STEP)["run"]
    apply = find_step(_wf(), "apply", APPLY_STEP)["run"]
    for name, body in (("validate", validate), ("apply", apply)):
        assert ". ./scripts/website-content-apply-guard.ps1" in body, name
        assert "Invoke-ContentApplyValidation" in body, name
        assert "config/website-content-apply-paths.json" in body, name
    # apply reads the dispatch inputs itself, not validate's outputs.
    env = find_step(_wf(), "apply", APPLY_STEP).get("env") or {}
    assert env.get("CONTENT_JSON") == "${{ inputs.content_json }}", env
    assert env.get("TARGET_REPO") == "${{ inputs.target_repo }}", env


def test_free_text_inputs_never_reach_a_script_body():
    for job_id, step in _all_run_bodies():
        for name in FREE_TEXT_INPUTS:
            assert f"inputs.{name}" not in step["run"], (
                f"{job_id} / {step.get('name')}: `inputs.{name}` is interpolated into the "
                f"script text (#1080); map it through env: instead"
            )


def test_the_pr_comes_from_a_run_branch_and_never_pushes_the_default_branch():
    body = find_step(_wf(), "apply", APPLY_STEP)["run"]
    assert '$head = "content/$($env:RUN_ID)"' in body, "branch must be content/<run_id>"
    assert 'push --quiet --force origin "HEAD:refs/heads/$head"' in body, body
    pushes = re.findall(r"git -C \$site push[^\n]*", body)
    assert len(pushes) == 1, f"expected exactly one push, found {pushes}"
    assert "gh pr create" in body and "--draft" not in body, "the PR must not be a draft"


def test_auto_merge_goes_through_the_planner():
    body = find_step(_wf(), "apply", APPLY_STEP)["run"]
    assert "Get-ContentApplyAutoMergePlan" in body
    # Statements that RUN gh pr merge (not the message text that names it).
    merges = re.findall(r"^\s*\$\w+ = gh pr merge[^\n]*", body, re.M)
    assert [m.strip() for m in merges] == [
        '$mergeOut = gh pr merge $prUrl --auto "--$($plan.Method)" 2>&1 | Out-String'
    ], merges
    assert body.index("if ($plan.Enable)") < body.index("= gh pr merge"), (
        "gh pr merge must sit behind the planner's verdict"
    )


def test_land_merges_only_on_the_decision_and_pinned_to_the_pushed_sha():
    step = find_step(_wf(), "apply", "Land the PR once the site CI is green")
    body = step["run"]
    assert "Get-ContentApplyLandDecision" in body
    # The only merge is the REST PUT, behind the 'merge' verdict, pinned to HEAD_SHA.
    assert body.count('gh api -X PUT "repos/$full/pulls/$num/merge"') == 1
    assert body.index("if ($d.Action -eq 'merge')") < body.index("gh api -X PUT")
    assert "sha = $env:HEAD_SHA" in body
    for forbidden in ("--admin", "bypass", "gh pr merge", "--force"):
        assert forbidden not in body, forbidden
    # Landing is skipped when GitHub auto-merge already took the PR, and never
    # runs without a PR.
    cond = " ".join(str(step["if"]).split())
    assert "steps.deliver.outputs.pr_url != ''" in cond
    assert "steps.deliver.outputs.auto_merge != 'enabled'" in cond
    # Free-text never reaches the body: the PR URL and sha come in through env.
    assert "${{" not in body


# --------------------------------------------------------------------------
# Behaviour (pwsh)
# --------------------------------------------------------------------------


def test_validate_accepts_a_good_payload_and_emits_outputs():
    body = find_step(_wf(), "validate", VALIDATE_STEP)["run"]
    out, rc, outputs = _run_body(
        body,
        {"TARGET_REPO": "FFC-EX-example.org", "CONTENT_JSON": GOOD_PAYLOAD, "PR_TITLE": "", "ISSUE_URL": ""},
        REPO_ROOT,
    )
    assert rc == 0, f"a good payload exited {rc}: {out}"
    assert "target_repo=FFC-EX-example.org" in outputs, outputs
    assert "file_count=1" in outputs, outputs
    assert "src/data/team/jane-doe.json" in out, out


def test_validate_refuses_with_workflow_commands_suspended():
    body = find_step(_wf(), "validate", VALIDATE_STEP)["run"]
    out, rc, outputs = _run_body(
        body,
        {"TARGET_REPO": "FFC-EX-example.org", "CONTENT_JSON": EVIL_PAYLOAD, "PR_TITLE": "", "ISSUE_URL": ""},
        REPO_ROOT,
    )
    assert rc == 1, f"a refused payload exited {rc}: {out}"
    assert "::error::content-apply refused the dispatch: 2 problem(s)" in out, out
    stop = re.search(r"^::stop-commands::([0-9a-f]{32})$", out, re.M)
    assert stop, f"refusals were printed without suspending workflow commands: {out}"
    resume = out.index(f"::{stop.group(1)}::")
    injected_at = out.index("INJECTED-707")
    assert stop.start() < injected_at < resume, (
        "the crafted path was printed outside the stop-commands window, where the "
        f"runner would execute it: {out}"
    )
    for line in out.splitlines():
        assert not line.startswith(INJECTED), f"the crafted path reached a line start: {line!r}"
    assert "target_repo=" not in outputs, "a refused dispatch must emit no outputs"


def test_validate_refuses_a_non_ffc_ex_repo():
    body = find_step(_wf(), "validate", VALIDATE_STEP)["run"]
    out, rc, _ = _run_body(
        body,
        {"TARGET_REPO": "FFC-IN-ffcadmin.org", "CONTENT_JSON": GOOD_PAYLOAD, "PR_TITLE": "", "ISSUE_URL": ""},
        REPO_ROOT,
    )
    assert rc == 1, out
    assert "FFC-EX" in out, out


def test_apply_refuses_before_its_first_network_call():
    """The PAT is in the job; a refused payload must not reach gh or git at all."""
    body = find_step(_wf(), "apply", APPLY_STEP)["run"]
    with tempfile.TemporaryDirectory() as td:
        shim = pathlib.Path(td)
        log = shim / "calls.log"
        (shim / "gh").write_text(f'#!/bin/sh\necho "gh $*" >> "{log.as_posix()}"\nexit 0\n', encoding="utf-8")
        (shim / "gh").chmod(0o755)
        (shim / "gh.cmd").write_text(f'@echo off\r\necho gh %* >> "{log}"\r\nexit /b 0\r\n', encoding="utf-8")
        (shim / "git").write_text(f'#!/bin/sh\necho "git $*" >> "{log.as_posix()}"\nexit 0\n', encoding="utf-8")
        (shim / "git").chmod(0o755)
        (shim / "git.cmd").write_text(f'@echo off\r\necho git %* >> "{log}"\r\nexit /b 0\r\n', encoding="utf-8")
        out, rc, _ = _run_body(
            body,
            {
                "TARGET_REPO": "FFC-EX-example.org",
                "CONTENT_JSON": EVIL_PAYLOAD,
                "PR_TITLE": "",
                "ISSUE_URL": "",
                "AUTO_MERGE": "true",
                "RUN_ID": "1",
                "RUN_URL": "https://example.invalid/run/1",
            },
            REPO_ROOT,
            path_prepend=(shim,),
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
    assert rc == 1, f"apply exited {rc} on a refused payload: {out}"
    assert "::error::content-apply refused the dispatch at the apply step" in out, out
    assert calls == "", f"apply called out before refusing: {calls}"
    assert "INJECTED-707" not in out, "apply echoed the crafted path"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

NEEDS_PWSH = {
    "test_validate_accepts_a_good_payload_and_emits_outputs",
    "test_validate_refuses_with_workflow_commands_suspended",
    "test_validate_refuses_a_non_ffc_ex_repo",
    "test_apply_refuses_before_its_first_network_call",
}

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
