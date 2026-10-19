"""Unit tests for 234's wiring (charity-lane public site fields, epic #1618, G3.3c).

234 prints a charity's public website fields in full, to a public job log and
optionally a public issue. That is safe only because of four properties, each
pinned here where it can drift:

  * the lane: the ungated `whmcs-prod-read` environment with the READ-scoped
    credential (`scope: read` on the READ_ALL_FFC_AZURE_* identity), the same
    pair 219/232 use. `scope: read` alone would still authenticate as the
    writer identity, so both halves are asserted;
  * the callee: `scripts/whmcs-charity-lane-site-fields.ps1` sends only
    GetClientsDetails and GetClientsProducts (an allowlist, so a new action
    has to be added here on purpose), and reads its allowlist from
    `config/charity-lane-site-fields.json`;
  * the body: no dispatch input is interpolated into any script text; a
    non-digit `client_id` or `issue_number` fails before the credential step
    AND again in the emitting step; a blank `issue_number` posts nothing; a
    failed read posts nothing;
  * the comment is the callee's markdown file, which carries the
    `<!-- charity-lane:site-fields -->` marker.

Which fields can appear, and how each is validated, is covered by Pester in
tests/whmcs-charity-lane-site-fields.Tests.ps1 against fixture payloads.
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
WORKFLOW = "234-whmcs-charity-lane-site-fields.yml"
JOB = "charity_lane_site_fields"
VALIDATE_STEP = "Validate inputs"
STEP = "Emit public site fields"
CALLEE = "whmcs-charity-lane-site-fields.ps1"
DEFAULT_API = "https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php"
MARKER = "<!-- charity-lane:site-fields -->"

READ_ACTIONS = {"GetClientsDetails", "GetClientsProducts"}

RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"

GH_PRELUDE = (
    "function gh { ('GH ' + ($args -join ' ')) | Out-File -FilePath gh.log -Append -Encoding utf8; "
    "$global:LASTEXITCODE = 0 }\n"
)

STUB = r"""param([string]$ClientId, [string]$ApiUrl, [string]$OutputFile, [string]$MarkdownFile, [string]$RunUrl)
Write-Output "CALLED ClientId=[$ClientId] ApiUrl=[$ApiUrl] OutputFile=[$OutputFile] MarkdownFile=[$MarkdownFile]"
foreach ($p in @($OutputFile, $MarkdownFile)) {
    $d = Split-Path -Parent $p
    if ($d) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
Set-Content -Path $MarkdownFile -Value '<!-- charity-lane:site-fields -->'
Set-Content -Path $OutputFile -Value '{"fields":{"tagline":"STUB-JSON"}}'
exit [int]$env:STUB_EXIT
"""


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _job() -> dict:
    return _workflow()["jobs"][JOB]


def _body(step: str = STEP) -> str:
    return find_step(_workflow(), JOB, step)["run"]


def _run(step: str = STEP, **env_overrides: str):
    """Run a shipped step body in a temp cwd holding the stub. Returns (output, gh_log, summary, rc)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + GH_PRELUDE + _body(step) + RUNNER_EPILOGUE, encoding="utf-8", newline="\n")
        summary = tmp / "summary.md"
        env = child_env(
            GITHUB_REPOSITORY="FreeForCharity/FFC-Cloudflare-Automation",
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_RUN_ID="99",
            GITHUB_STEP_SUMMARY=str(summary),
            WHMCS_API_URL=DEFAULT_API,
            STUB_EXIT=env_overrides.pop("STUB_EXIT", "0"),
        )
        for var in ("CLIENT_ID", "ISSUE_NUMBER"):
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
        summ = summary.read_text(encoding="utf-8-sig") if summary.exists() else ""
        return proc.stdout + proc.stderr, gh_log, summ, proc.returncode


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
    assert "WR_ALL" not in text, "234 must never reference the writer identity"


def test_inputs_are_validated_before_the_credential_step():
    names = [s.get("name") or s.get("uses", "") for s in _job()["steps"]]
    v = names.index(VALIDATE_STEP)
    kv = next(i for i, n in enumerate(names) if str(n).endswith("whmcs-secrets-from-kv"))
    assert v < kv, names


def test_inputs_are_client_id_and_issue_number_only():
    # PyYAML (YAML 1.1) loads a bare `on:` key as the boolean True.
    wf = _workflow()
    inputs = wf.get(True, wf.get("on"))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"client_id", "issue_number"}, sorted(inputs)
    assert inputs["client_id"]["required"] is True


def test_permissions_are_read_plus_issue_comment():
    perms = _workflow()["permissions"]
    assert perms == {"contents": "read", "id-token": "write", "issues": "write"}, perms


def test_no_dispatch_input_is_interpolated_into_any_body():
    for step in (VALIDATE_STEP, STEP):
        assert "${{" not in _body(step), f"{step}: dispatch inputs must arrive through env:, not the script text"


def test_the_artifact_is_the_json_report():
    up = [s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(up) == 1
    assert up[0]["with"]["path"] == "artifacts/whmcs/charity-lane-site-fields.json"
    assert up[0]["with"]["name"] == "charity-lane-site-fields"


def test_the_callee_sends_only_the_two_read_actions_and_uses_the_allowlist():
    src = (ROOT / "scripts" / CALLEE).read_text(encoding="utf-8")
    actions = set(re.findall(r"\.action\s*=\s*'([A-Za-z]+)'", src))
    assert actions == READ_ACTIONS, f"{CALLEE} WHMCS actions: {sorted(actions)}"
    assert "config/charity-lane-site-fields.json" in src
    assert MARKER in src
    assert (ROOT / "config" / "charity-lane-site-fields.json").is_file()


# --- behaviour (pwsh) --------------------------------------------------------


def test_validate_step_refuses_bad_inputs():
    for env in ({"CLIENT_ID": ""}, {"CLIENT_ID": "12a"}, {"CLIENT_ID": "0"}, {"CLIENT_ID": "42", "ISSUE_NUMBER": "7; rm -rf /"}):
        out, _, _, rc = _run(VALIDATE_STEP, **env)
        assert rc != 0, (env, out)
        assert "::error::" in out, (env, out)
    out, _, _, rc = _run(VALIDATE_STEP, CLIENT_ID="4242", ISSUE_NUMBER="")
    assert rc == 0, out


def test_blank_issue_prints_json_to_log_and_summary_and_posts_nothing():
    out, gh_log, summary, rc = _run(CLIENT_ID="4242", ISSUE_NUMBER="")
    assert rc == 0, out
    assert f"CALLED ClientId=[4242] ApiUrl=[{DEFAULT_API}]" in out, out
    assert "MarkdownFile=[artifacts/whmcs/charity-lane-site-fields.md]" in out, out
    assert "STUB-JSON" in out, "the allowlisted JSON must be printed to the job log"
    assert MARKER in summary, summary
    assert gh_log == "", gh_log


def test_an_issue_number_posts_the_marked_markdown():
    out, gh_log, _, rc = _run(CLIENT_ID="4242", ISSUE_NUMBER="1618")
    assert rc == 0, out
    assert (
        "GH issue comment 1618 --repo FreeForCharity/FFC-Cloudflare-Automation --body-file artifacts/whmcs/charity-lane-site-fields.md"
        in gh_log
    ), gh_log


def test_the_emit_step_refuses_a_bad_client_id_on_its_own():
    out, gh_log, _, rc = _run(CLIENT_ID="42; Remove-Item -Recurse /", ISSUE_NUMBER="1618")
    assert rc != 0, out
    assert "::error::client_id must be digits" in out, out
    assert "CALLED" not in out
    assert gh_log == ""


def test_a_failed_read_posts_nothing():
    out, gh_log, _, rc = _run(CLIENT_ID="4242", ISSUE_NUMBER="1618", STUB_EXIT="1")
    assert rc != 0, out
    assert gh_log == "", gh_log


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

NEEDS_PWSH = {
    "test_validate_step_refuses_bad_inputs",
    "test_blank_issue_prints_json_to_log_and_summary_and_posts_nothing",
    "test_an_issue_number_posts_the_marked_markdown",
    "test_the_emit_step_refuses_a_bad_client_id_on_its_own",
    "test_a_failed_read_posts_nothing",
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
