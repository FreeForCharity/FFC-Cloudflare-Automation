"""Unit tests for 232's wiring (charity-lane Gate 1/2 verdicts, epic #1618).

232 is READ-ONLY by construction, and these tests pin each leg of that claim
where it can drift:

  * the lane: the ungated `whmcs-prod-read` environment with the READ-scoped
    credential (`scope: read` on the READ_ALL_FFC_AZURE_* identity), the same
    pair 219/221 use. `scope: read` alone would still authenticate as the
    writer identity (see 221's comment), so both halves are asserted;
  * the callee: `scripts/whmcs-charity-lane-verdicts.ps1` calls only WHMCS
    READ actions. The triage library it dot-sources does hold AcceptOrder /
    CancelOrder / SendEmail, but below its own dot-source guard, so they are
    never defined in 232's process; this file's own action set is what can
    change, and it is checked against an allowlist rather than a denylist so a
    new action has to be added here on purpose;
  * the body: no dispatch input is interpolated into the script text, the
    optional `issue_number` fails closed on a non-integer BEFORE the WHMCS
    sweep, a blank one posts nothing, and a failed sweep never posts.

The decision logic itself (verdicts, reason codes, PII-free rows) is covered
by Pester in tests/whmcs-charity-lane-verdicts.Tests.ps1, which dot-sources
the real script with fixtures.
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
JOB = "charity_lane_verdicts"
STEP = "Compute charity-lane verdicts"
CALLEE = "whmcs-charity-lane-verdicts.ps1"
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

STUB = r"""param([string]$ApiUrl, [string]$OutputFile, [string]$MarkdownFile)
Write-Output "CALLED ApiUrl=[$ApiUrl] OutputFile=[$OutputFile] MarkdownFile=[$MarkdownFile]"
foreach ($p in @($OutputFile, $MarkdownFile)) {
    $d = Split-Path -Parent $p
    if ($d) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
Set-Content -Path $MarkdownFile -Value '## table'
Set-Content -Path $OutputFile -Value '{}'
exit [int]$env:STUB_EXIT
"""


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _job() -> dict:
    return _workflow()["jobs"][JOB]


def _body() -> str:
    return find_step(_workflow(), JOB, STEP)["run"]


def _run(**env_overrides: str):
    """Run the shipped body in a temp cwd holding the stub. Returns (output, gh_log, rc)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + GH_PRELUDE + _body() + RUNNER_EPILOGUE, encoding="utf-8", newline="\n")
        env = child_env(
            GITHUB_REPOSITORY="FreeForCharity/FFC-Cloudflare-Automation",
            STUB_EXIT=env_overrides.pop("STUB_EXIT", "0"),
        )
        for var in ("WHMCS_API_URL", "ISSUE_NUMBER"):
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


# --- wiring (pure YAML / text) ---------------------------------------------


def test_runs_on_the_ungated_read_lane():
    assert _job()["environment"] == "whmcs-prod-read"


def test_uses_the_read_scope_on_the_read_identity():
    kv = [s for s in _job()["steps"] if str(s.get("uses", "")).endswith("whmcs-secrets-from-kv")]
    assert len(kv) == 1, "exactly one whmcs-secrets-from-kv step expected"
    w = kv[0]["with"]
    assert w.get("scope") == "read", "scope: read is required (the action defaults to write)"
    assert "READ_ALL_FFC_AZURE_KV_CLIENT_ID" in w["azure-client-id"]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in w["azure-tenant-id"]
    text = (ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")
    assert "WR_ALL" not in text, "232 must never reference the writer identity"


def test_permissions_are_read_plus_issue_comment():
    perms = _workflow()["permissions"]
    assert perms == {"contents": "read", "id-token": "write", "issues": "write"}, perms


def test_no_dispatch_input_is_interpolated_into_the_body():
    assert "${{" not in _body(), "dispatch inputs must arrive through env:, not the script text"


def test_the_artifact_is_the_json_report():
    up = [s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(up) == 1
    assert up[0]["with"]["path"] == "artifacts/whmcs/charity-lane-verdicts.json"


def test_the_callee_sends_only_whmcs_read_actions():
    src = (ROOT / "scripts" / CALLEE).read_text(encoding="utf-8")
    actions = set(re.findall(r"\.action\s*=\s*'([A-Za-z]+)'", src))
    assert actions, "found no WHMCS action assignments; the pattern no longer matches the script"
    assert actions <= READ_ACTIONS, f"non-read WHMCS action(s) in {CALLEE}: {sorted(actions - READ_ACTIONS)}"


# --- behaviour (pwsh) --------------------------------------------------------


def test_blank_inputs_use_the_default_endpoint_and_post_nothing():
    out, gh_log, rc = _run(WHMCS_API_URL="", ISSUE_NUMBER="")
    assert rc == 0, out
    assert f"CALLED ApiUrl=[{DEFAULT_API}]" in out, out
    assert "MarkdownFile=[artifacts/whmcs/charity-lane-verdicts.md]" in out, out
    assert gh_log == "", gh_log


def test_an_issue_number_posts_the_markdown_table():
    out, gh_log, rc = _run(ISSUE_NUMBER="42")
    assert rc == 0, out
    assert "GH issue comment 42 --repo FreeForCharity/FFC-Cloudflare-Automation --body-file artifacts/whmcs/charity-lane-verdicts.md" in gh_log, gh_log


def test_a_non_integer_issue_number_fails_before_the_sweep():
    out, gh_log, rc = _run(ISSUE_NUMBER="12; rm -rf /")
    assert rc != 0, out
    assert "::error::issue_number must be a positive integer" in out, out
    assert "CALLED" not in out, "the WHMCS sweep must not run on a bad issue_number"
    assert gh_log == ""


def test_a_failed_sweep_posts_nothing():
    out, gh_log, rc = _run(ISSUE_NUMBER="42", STUB_EXIT="1")
    assert rc != 0, out
    assert gh_log == "", gh_log


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

NEEDS_PWSH = {
    "test_blank_inputs_use_the_default_endpoint_and_post_nothing",
    "test_an_issue_number_posts_the_markdown_table",
    "test_a_non_integer_issue_number_fails_before_the_sweep",
    "test_a_failed_sweep_posts_nothing",
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
