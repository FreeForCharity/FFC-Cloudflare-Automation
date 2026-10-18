"""Unit tests for 232's wiring (charity-lane Gate 1/2 verdicts, epic #1618).

232 is READ-ONLY by construction and, since G1.3b, FAIL-CLOSED on FraudLabs Pro.
These tests pin each leg of both claims where it can drift:

  * the lanes: the checks run on the ungated `whmcs-prod-read` environment with
    the READ-scoped credential (`scope: read` on the READ_ALL_FFC_AZURE_*
    identity), the pair 219/221 use; the FraudLabs lookups run on 228's own
    `fraudlabspro-prod-read` environment with 228's key action, again
    `scope: read` on the READ_ALL identity. No job holds both credentials, the
    job that merges and posts holds neither, and no new environment appears;
  * the callees: `scripts/whmcs-charity-lane-verdicts.ps1` calls only WHMCS
    READ actions (an allowlist, so a new action has to be added here on
    purpose); the screening script calls FraudLabs only through 228's
    `Invoke-FraudLabsProApi` and never WHMCS; the merge script calls nothing;
  * fail closed: `finalize` runs when `fraud_screen` FAILED (not after a cancel,
    and only when the verdicts exist), takes FraudLabs results only from a
    screen that SUCCEEDED, and is the only job that publishes the decision
    artifact `charity-lane-verdicts` or posts the issue comment. The WHMCS job
    publishes only the unscreened report (every row ESCALATE) and the lookup keys;
  * the bodies: no dispatch input or job output is interpolated into a script,
    a non-integer `issue_number` fails BEFORE the WHMCS sweep and again before
    posting, a blank one posts nothing, and a failed merge never posts.

The decision logic itself (verdicts, reason codes, FraudLabs outcomes, PII-free
rows) is covered by Pester in tests/whmcs-charity-lane-verdicts.Tests.ps1 and
tests/whmcs-charity-lane-fraud.Tests.ps1, which dot-source the real scripts with
fixtures.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "232-whmcs-charity-lane-verdicts.yml"
VERDICTS_JOB = "charity_lane_verdicts"
SCREEN_JOB = "fraud_screen"
FINAL_JOB = "finalize"
VERDICTS_STEP = "Compute charity-lane verdicts"
SCREEN_STEP = "Screen lane orders with FraudLabs Pro"
FINAL_STEP = "Apply FraudLabs results"
CALLEE = "whmcs-charity-lane-verdicts.ps1"
SCREEN_CALLEE = "whmcs-charity-lane-fraud-screen.ps1"
MERGE_CALLEE = "whmcs-charity-lane-fraud-merge.ps1"
DEFAULT_API = "https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php"

# WHMCS actions the callee may send. Read actions only.
READ_ACTIONS = {"GetOrders", "GetClientsProducts", "GetClientsDetails", "GetClients", "GetClientsDomains"}

# Same wrapper GitHub's `shell: pwsh` uses (Runner.Worker ScriptHandlerHelpers).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"

# `gh` replaced by a function (a function outranks an external command in
# PowerShell's resolution order), logging its arguments instead of posting.
GH_PRELUDE = (
    "function gh { ('GH ' + ($args -join ' ')) | Out-File -FilePath gh.log -Append -Encoding utf8; "
    "$global:LASTEXITCODE = 0 }\n"
)

VERDICTS_STUB = r"""param([string]$ApiUrl, [string]$OutputFile, [string]$MarkdownFile, [string]$OrderIdField, [string]$FraudLookupFile)
Write-Output "CALLED ApiUrl=[$ApiUrl] OutputFile=[$OutputFile] MarkdownFile=[$MarkdownFile] OrderIdField=[$OrderIdField] FraudLookupFile=[$FraudLookupFile]"
exit [int]$env:STUB_EXIT
"""

SCREEN_STUB = r"""param([string]$LookupFile, [string]$OutputFile)
Write-Output "SCREEN LookupFile=[$LookupFile] OutputFile=[$OutputFile]"
exit [int]$env:STUB_EXIT
"""

MERGE_STUB = r"""param([string]$VerdictsFile, [string]$FraudResultsFile, [string]$OutputFile, [string]$MarkdownFile)
Write-Output "MERGE VerdictsFile=[$VerdictsFile] FraudResultsFile=[$FraudResultsFile] OutputFile=[$OutputFile] MarkdownFile=[$MarkdownFile]"
$d = Split-Path -Parent $MarkdownFile
if ($d) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
Set-Content -Path $MarkdownFile -Value '## table'
exit [int]$env:STUB_EXIT
"""


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _text() -> str:
    return (ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")


def _job(job: str) -> dict:
    return _workflow()["jobs"][job]


def _body(job: str, step: str) -> str:
    return find_step(_workflow(), job, step)["run"]


def _uses(job: str, suffix: str) -> list[dict]:
    return [s for s in _job(job)["steps"] if str(s.get("uses", "")).endswith(suffix)]


def _uploads(job: str) -> list[dict]:
    return [s for s in _job(job)["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def _run(job: str, step: str, callee: str, stub: str, **env_overrides: str):
    """Run one shipped body in a temp cwd holding a stub callee. Returns (output, gh_log, rc)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / callee).write_text(stub, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + GH_PRELUDE + _body(job, step) + RUNNER_EPILOGUE, encoding="utf-8", newline="\n")
        env = child_env(
            GITHUB_REPOSITORY="FreeForCharity/FFC-Cloudflare-Automation",
            STUB_EXIT=env_overrides.pop("STUB_EXIT", "0"),
        )
        for var in ("WHMCS_API_URL", "ISSUE_NUMBER", "FRAUD_ORDER_ID_FIELD", "FRAUD_SCREEN_RESULT"):
            env.pop(var, None)
        env.update({k: v for k, v in env_overrides.items() if v is not None})
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        log = tmp / "gh.log"
        gh_log = log.read_text(encoding="utf-8-sig") if log.exists() else ""
        return proc.stdout + proc.stderr, gh_log, proc.returncode


def _run_verdicts(**env):
    return _run(VERDICTS_JOB, VERDICTS_STEP, CALLEE, VERDICTS_STUB, **env)


def _run_screen(**env):
    return _run(SCREEN_JOB, SCREEN_STEP, SCREEN_CALLEE, SCREEN_STUB, **env)


def _run_final(**env):
    return _run(FINAL_JOB, FINAL_STEP, MERGE_CALLEE, MERGE_STUB, **env)


# --- lanes and credentials (pure YAML / text) --------------------------------


def test_the_three_jobs_and_their_order():
    jobs = _workflow()["jobs"]
    assert set(jobs) == {VERDICTS_JOB, SCREEN_JOB, FINAL_JOB}, list(jobs)
    assert jobs[SCREEN_JOB]["needs"] == VERDICTS_JOB
    assert sorted(jobs[FINAL_JOB]["needs"]) == sorted([VERDICTS_JOB, SCREEN_JOB])


def test_each_job_runs_on_its_existing_lane():
    assert _job(VERDICTS_JOB)["environment"] == "whmcs-prod-read"
    assert _job(SCREEN_JOB)["environment"] == "fraudlabspro-prod-read", "FraudLabs is read on 228's own environment"
    assert "environment" not in _job(FINAL_JOB), "finalize holds no credential and needs no environment"
    # Only environments that already exist (228 and 219/221 use these two).
    envs = set(re.findall(r"^\s*environment:\s*(\S+)\s*$", _text(), re.M))
    assert envs == {"whmcs-prod-read", "fraudlabspro-prod-read"}, envs


def test_whmcs_read_scope_on_the_read_identity_in_the_verdicts_job_only():
    kv = _uses(VERDICTS_JOB, "whmcs-secrets-from-kv")
    assert len(kv) == 1, "exactly one whmcs-secrets-from-kv step expected"
    w = kv[0]["with"]
    assert w.get("scope") == "read", "scope: read is required (the action defaults to write)"
    assert "READ_ALL_FFC_AZURE_KV_CLIENT_ID" in w["azure-client-id"]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in w["azure-tenant-id"]
    assert not _uses(SCREEN_JOB, "whmcs-secrets-from-kv"), "the FraudLabs job must not hold the WHMCS credential"
    assert not _uses(FINAL_JOB, "whmcs-secrets-from-kv")
    assert "WR_ALL" not in _text(), "232 must never reference the writer identity"


def test_fraudlabs_key_exactly_as_228_in_the_screen_job_only():
    kv = _uses(SCREEN_JOB, "fraudlabspro-keys-from-kv")
    assert len(kv) == 1, "exactly one fraudlabspro-keys-from-kv step expected"
    w = kv[0]["with"]
    assert w.get("scope") == "read"
    # Repository Variables, never `secrets.*`: fraudlabspro-prod-read holds no secrets (#912).
    assert w["azure-client-id"].replace(" ", "") == "${{vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID}}"
    assert w["azure-tenant-id"].replace(" ", "") == "${{vars.READ_ALL_FFC_AZURE_TENANT_ID}}"
    assert not _uses(VERDICTS_JOB, "fraudlabspro-keys-from-kv")
    assert not _uses(FINAL_JOB, "fraudlabspro-keys-from-kv")
    assert "secrets.READ_ALL" not in _text()


def test_permissions_are_least_privilege_per_job():
    assert _workflow()["permissions"] == {"contents": "read"}
    assert _job(VERDICTS_JOB)["permissions"] == {"contents": "read", "id-token": "write"}
    assert _job(SCREEN_JOB)["permissions"] == {"contents": "read", "id-token": "write"}
    assert _job(FINAL_JOB)["permissions"] == {"contents": "read", "issues": "write"}, "only finalize posts"


# --- fail closed (pure YAML) -------------------------------------------------


def test_finalize_runs_after_a_failed_screen_but_not_after_a_cancel():
    cond = str(_job(FINAL_JOB)["if"]).replace(" ", "")
    assert "!cancelled()" in cond
    assert f"needs.{VERDICTS_JOB}.result=='success'" in cond
    assert "always()" not in cond, "always() would also run after a cancel"
    assert SCREEN_JOB not in cond, "finalize must run whatever fraud_screen's result, and fail closed itself"


def test_finalize_takes_results_only_from_a_screen_that_succeeded():
    steps = _job(FINAL_JOB)["steps"]
    dl = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact") and s["with"]["name"] == "charity-lane-fraud-results"]
    assert len(dl) == 1
    assert str(dl[0].get("if", "")).replace(" ", "") == f"${{{{needs.{SCREEN_JOB}.result=='success'}}}}"
    env = find_step(_workflow(), FINAL_JOB, FINAL_STEP)["env"]
    assert env["FRAUD_SCREEN_RESULT"].replace(" ", "") == f"${{{{needs.{SCREEN_JOB}.result}}}}"


def test_only_finalize_publishes_the_decision_artifact():
    final = _uploads(FINAL_JOB)
    assert [u["with"]["name"] for u in final] == ["charity-lane-verdicts"]
    assert final[0]["with"]["path"] == "artifacts/whmcs/charity-lane-verdicts.json"
    first = {u["with"]["name"]: u["with"] for u in _uploads(VERDICTS_JOB)}
    assert set(first) == {"charity-lane-verdicts-unscreened", "charity-lane-fraud-lookups"}, set(first)
    assert first["charity-lane-verdicts-unscreened"]["path"] == "artifacts/whmcs/charity-lane-verdicts-unscreened.json"
    screen = {u["with"]["name"]: u["with"] for u in _uploads(SCREEN_JOB)}
    assert set(screen) == {"charity-lane-fraud-results"}, set(screen)
    # Transport artifacts are short-lived.
    for w in list(first.values()) + list(screen.values()):
        assert w.get("retention-days") == 1, w


def test_the_screen_job_reads_only_the_lookup_keys():
    dls = [s for s in _job(SCREEN_JOB)["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact")]
    assert [s["with"]["name"] for s in dls] == ["charity-lane-fraud-lookups"]


def test_only_finalize_posts_to_an_issue():
    assert "gh issue comment" not in _body(VERDICTS_JOB, VERDICTS_STEP)
    assert "gh issue comment" not in _body(SCREEN_JOB, SCREEN_STEP)
    assert "gh issue comment" in _body(FINAL_JOB, FINAL_STEP)
    assert "GH_TOKEN" not in str(_job(VERDICTS_JOB)) and "GH_TOKEN" not in str(_job(SCREEN_JOB))


def test_no_dispatch_input_or_job_output_is_interpolated_into_a_body():
    for job, step in ((VERDICTS_JOB, VERDICTS_STEP), (SCREEN_JOB, SCREEN_STEP), (FINAL_JOB, FINAL_STEP)):
        assert "${{" not in _body(job, step), f"{job}: values must arrive through env:, not the script text"


def test_the_order_id_field_is_a_closed_choice_defaulting_to_228s():
    inp = _workflow()[True]["workflow_dispatch"]["inputs"]["fraud_order_id_field"]
    assert inp["type"] == "choice"
    assert inp["options"] == ["ordernum", "id"]
    assert inp["default"] == "ordernum"


# --- callees (text) ----------------------------------------------------------


def test_the_callee_sends_only_whmcs_read_actions():
    src = _script(CALLEE)
    actions = set(re.findall(r"\.action\s*=\s*'([A-Za-z]+)'", src))
    assert actions, "found no WHMCS action assignments; the pattern no longer matches the script"
    assert actions <= READ_ACTIONS, f"non-read WHMCS action(s) in {CALLEE}: {sorted(actions - READ_ACTIONS)}"


def test_the_screen_script_reads_fraudlabs_as_228_does_and_never_whmcs():
    src = _script(SCREEN_CALLEE)
    assert ". (Join-Path $PSScriptRoot 'fraudlabspro-api-common.ps1')" in src
    assert "Invoke-FraudLabsProApi -OrderId $LookupId" in src
    assert "Resolve-FraudLabsProApiKey" in src
    for banned in ("Invoke-WhmcsApi", "whmcs-api-common", ".action", "Invoke-RestMethod", "Invoke-WebRequest", "HttpClient"):
        assert banned not in src, f"{SCREEN_CALLEE} must reach FraudLabs only through 228's function, never {banned}"


def test_the_merge_script_calls_nothing():
    src = _script(MERGE_CALLEE)
    for banned in ("Invoke-WhmcsApi", "Invoke-FraudLabsProApi", "Invoke-RestMethod", "Invoke-WebRequest", "HttpClient", "gh "):
        assert banned not in src, f"{MERGE_CALLEE} must make no call ({banned})"


# --- behaviour (pwsh) --------------------------------------------------------


def test_verdicts_blank_inputs_use_the_defaults_and_post_nothing():
    out, gh_log, rc = _run_verdicts(WHMCS_API_URL="", ISSUE_NUMBER="", FRAUD_ORDER_ID_FIELD="")
    assert rc == 0, out
    assert f"CALLED ApiUrl=[{DEFAULT_API}]" in out, out
    assert "OutputFile=[artifacts/whmcs/charity-lane-verdicts-unscreened.json]" in out, out
    assert "OrderIdField=[ordernum]" in out, out
    assert "FraudLookupFile=[artifacts/whmcs/charity-lane-fraud-lookups.json]" in out, out
    assert gh_log == "", gh_log


def test_verdicts_passes_the_id_field_through():
    out, _, rc = _run_verdicts(FRAUD_ORDER_ID_FIELD="id")
    assert rc == 0, out
    assert "OrderIdField=[id]" in out, out


def test_verdicts_refuses_an_unknown_order_id_field_before_the_sweep():
    out, _, rc = _run_verdicts(FRAUD_ORDER_ID_FIELD="email")
    assert rc != 0, out
    assert "::error::fraud_order_id_field must be" in out, out
    assert "CALLED" not in out


def test_verdicts_a_non_integer_issue_number_fails_before_the_sweep():
    out, gh_log, rc = _run_verdicts(ISSUE_NUMBER="12; rm -rf /")
    assert rc != 0, out
    assert "::error::issue_number must be a positive integer" in out, out
    assert "CALLED" not in out, "the WHMCS sweep must not run on a bad issue_number"
    assert gh_log == ""


def test_verdicts_a_failed_sweep_fails_the_job():
    out, _, rc = _run_verdicts(STUB_EXIT="1")
    assert rc != 0, out


def test_screen_runs_the_lookup_file_through_the_screen_script():
    out, _, rc = _run_screen()
    assert rc == 0, out
    assert "SCREEN LookupFile=[artifacts/whmcs/charity-lane-fraud-lookups.json] OutputFile=[artifacts/whmcs/charity-lane-fraud-results.json]" in out, out


def test_screen_a_failed_screen_fails_the_job():
    out, _, rc = _run_screen(STUB_EXIT="1")
    assert rc != 0, out


def test_finalize_uses_the_results_of_a_successful_screen():
    out, gh_log, rc = _run_final(FRAUD_SCREEN_RESULT="success")
    assert rc == 0, out
    assert "FraudResultsFile=[artifacts/fraudlabs/charity-lane-fraud-results.json]" in out, out
    assert "VerdictsFile=[artifacts/whmcs/charity-lane-verdicts-unscreened.json]" in out, out
    assert "OutputFile=[artifacts/whmcs/charity-lane-verdicts.json]" in out, out
    assert gh_log == "", gh_log


def test_finalize_passes_no_results_when_the_screen_did_not_succeed():
    for result in ("failure", "skipped", "cancelled", ""):
        out, _, rc = _run_final(FRAUD_SCREEN_RESULT=result)
        assert rc == 0, out
        assert "FraudResultsFile=[]" in out, (result, out)
        assert "every row is ESCALATE FRAUD_CHECK_FAILED" in out, (result, out)


def test_finalize_posts_the_table_to_the_issue():
    out, gh_log, rc = _run_final(FRAUD_SCREEN_RESULT="success", ISSUE_NUMBER="42")
    assert rc == 0, out
    assert "GH issue comment 42 --repo FreeForCharity/FFC-Cloudflare-Automation --body-file artifacts/whmcs/charity-lane-verdicts.md" in gh_log, gh_log


def test_finalize_a_non_integer_issue_number_fails_before_the_merge():
    out, gh_log, rc = _run_final(FRAUD_SCREEN_RESULT="success", ISSUE_NUMBER="7 --body x")
    assert rc != 0, out
    assert "MERGE" not in out
    assert gh_log == ""


def test_finalize_a_failed_merge_posts_nothing():
    out, gh_log, rc = _run_final(FRAUD_SCREEN_RESULT="success", ISSUE_NUMBER="42", STUB_EXIT="1")
    assert rc != 0, out
    assert gh_log == "", gh_log


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

NEEDS_PWSH = {name for name in globals() if name.startswith(("test_verdicts_", "test_screen_", "test_finalize_")) and name not in {
    "test_finalize_runs_after_a_failed_screen_but_not_after_a_cancel",
    "test_finalize_takes_results_only_from_a_screen_that_succeeded",
}}

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
