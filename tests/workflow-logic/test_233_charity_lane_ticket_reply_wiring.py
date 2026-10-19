"""Unit tests for 233's wiring (charity-lane ticket reply classifier, epic #1618).

233 reads a charity's ticket reply in a PUBLIC repo, so two claims carry the
weight, and these tests pin each where it can drift:

  * READ-ONLY on the existing lane: the ungated `whmcs-prod-read` environment
    (and no other) with the READ-scoped credential (`scope: read` on the
    READ_ALL_FFC_AZURE_* identity, as 219/221/232). The callee's WHMCS actions
    are checked against an allowlist (GetTickets, GetTicket), so a new action
    has to be added here on purpose.
  * NO TEXT EMITTED: no artifact upload; every dispatch input arrives through
    env:, never the script text; bad input values are refused WITHOUT being
    echoed; and the real callee, run on a fixture thread holding a sentinel,
    a name, an email and a phone, emits none of them in its JSON or Markdown.

The classifier and reply selection are covered by Pester in
tests/whmcs-charity-lane-ticket-reply.Tests.ps1 (fixture payloads, no network).
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
WORKFLOW = "233-whmcs-charity-lane-ticket-reply.yml"
JOB = "ticket_reply"
STEP = "Classify the charity's ticket reply"
CALLEE = "whmcs-charity-lane-ticket-reply.ps1"
DEFAULT_API = "https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php"
PREFIXES = ["[Website Preview]", "[Application]"]

# WHMCS actions the callee may send. Read actions only.
READ_ACTIONS = {"GetTickets", "GetTicket"}

# Same wrapper GitHub's `shell: pwsh` uses (Runner.Worker ScriptHandlerHelpers).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"

# `gh` replaced by a function (a function outranks an external command in
# PowerShell's resolution order), logging its arguments instead of posting.
GH_PRELUDE = (
    "function gh { ('GH ' + ($args -join ' ')) | Out-File -FilePath gh.log -Append -Encoding utf8; "
    "$global:LASTEXITCODE = 0 }\n"
)

STUB = r"""param([string]$ApiUrl, [int]$ClientId, [string]$SubjectPrefix, [string]$OutputFile, [string]$MarkdownFile)
Write-Output "CALLED ApiUrl=[$ApiUrl] ClientId=[$ClientId] SubjectPrefix=[$SubjectPrefix]"
Set-Content -Path $MarkdownFile -Value '<!-- charity-lane:ticket-reply -->'
Set-Content -Path $OutputFile -Value '{}'
exit [int]$env:STUB_EXIT
"""

SENTINELS = ["SENTINEL-7731", "Jane", "jane.doe@example.org", "555-123-4567", "ready to review", "ABC-123456"]

# Runs the REAL callee's lookup on a fixture thread and prints what it would
# emit (the JSON result and the Markdown body), nothing else.
NO_TEXT_PROBE = r"""
$ErrorActionPreference = 'Stop'
. ./scripts/whmcs-charity-lane-ticket-reply.ps1
$opening = [pscustomobject]@{ replyid = '0'; date = '2026-10-01 10:00:00'; admin = ''; message = 'Good news: a preview ... ready to review'; name = 'Jane Doe'; email = 'jane.doe@example.org' }
$reply = [pscustomobject]@{ replyid = '12'; date = '2026-10-02 09:05:00'; admin = ''; message = 'Looks great! SENTINEL-7731 Jane Doe jane.doe@example.org 555-123-4567'; name = 'Jane Doe'; email = 'jane.doe@example.org' }
$detail = [pscustomobject]@{ result = 'success'; id = '501'; tid = 'ABC-123456'; date = '2026-10-01 10:00:00'; status = 'Customer-Reply'; subject = '[Website Preview] Your new website is ready to review'; name = 'Jane Doe'; email = 'jane.doe@example.org'; replies = [pscustomobject]@{ reply = @($opening, $reply) } }
$ticket = [pscustomobject]@{ id = '501'; tid = 'ABC-123456'; userid = '701'; status = 'Customer-Reply'; subject = '[Website Preview] Your new website is ready to review'; name = 'Jane Doe'; email = 'jane.doe@example.org' }
$api = {
    param([hashtable]$Body)
    if ($Body.action -eq 'GetTickets') { return [pscustomobject]@{ result = 'success'; totalresults = '1'; tickets = [pscustomobject]@{ ticket = @($ticket) } } }
    if ($Body.action -eq 'GetTicket') { return $detail }
    throw 'unexpected action'
}
$r = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api
Write-Output ($r | ConvertTo-Json -Depth 4)
Write-Output (Format-TicketReplyMarkdown -Result $r)
"""


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _job() -> dict:
    return _workflow()["jobs"][JOB]


def _step() -> dict:
    return find_step(_workflow(), JOB, STEP)


def _body() -> str:
    return _step()["run"]


def _text() -> str:
    return (ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")


def _callee() -> str:
    return (ROOT / "scripts" / CALLEE).read_text(encoding="utf-8")


def _inputs() -> dict:
    wf = _workflow()
    on = wf.get("on", wf.get(True))
    return on["workflow_dispatch"]["inputs"]


def _run(**env_overrides: str):
    """Run the shipped body in a temp cwd holding the stub. Returns (output, gh_log, rc, tmp_dir)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + GH_PRELUDE + _body() + RUNNER_EPILOGUE, encoding="utf-8", newline="\n")
        env = child_env(
            GITHUB_REPOSITORY="FreeForCharity/FFC-Cloudflare-Automation",
            STUB_EXIT=env_overrides.pop("STUB_EXIT", "0"),
            RUNNER_TEMP=str(tmp),
            WHMCS_API_URL=str(_step()["env"]["WHMCS_API_URL"]),
        )
        for var in ("CLIENT_ID", "SUBJECT_PREFIX", "ISSUE_NUMBER"):
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
        return proc.stdout + proc.stderr, gh_log, proc.returncode, str(tmp)


# --- wiring (pure YAML / text) ---------------------------------------------


def test_runs_only_on_the_ungated_read_lane():
    jobs = _workflow()["jobs"]
    assert list(jobs) == [JOB], f"one job expected, got {list(jobs)}"
    assert _job()["environment"] == "whmcs-prod-read"
    envs = set(re.findall(r"^\s*environment:\s*(\S+)", _text(), re.M))
    assert envs == {"whmcs-prod-read"}, f"no environment beyond whmcs-prod-read: {envs}"


def test_uses_the_read_scope_on_the_read_identity():
    kv = [s for s in _job()["steps"] if str(s.get("uses", "")).endswith("whmcs-secrets-from-kv")]
    assert len(kv) == 1, "exactly one whmcs-secrets-from-kv step expected"
    w = kv[0]["with"]
    assert w.get("scope") == "read", "scope: read is required (the action defaults to write)"
    assert "READ_ALL_FFC_AZURE_KV_CLIENT_ID" in w["azure-client-id"]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in w["azure-tenant-id"]
    assert "WR_ALL" not in _text(), "233 must never reference the writer identity"
    assert "secrets." not in _text(), "233 needs no GitHub secret (Key Vault via OIDC only)"


def test_permissions_are_read_plus_issue_comment():
    assert _workflow()["permissions"] == {"contents": "read", "id-token": "write", "issues": "write"}


def test_inputs_are_the_three_documented_ones():
    inputs = _inputs()
    assert set(inputs) == {"client_id", "subject_prefix", "issue_number"}, set(inputs)
    assert inputs["client_id"]["required"] is True
    sp = inputs["subject_prefix"]
    assert sp["type"] == "choice" and sp["options"] == PREFIXES and sp["default"] == "[Website Preview]"
    assert inputs["issue_number"].get("required", False) is False


def test_every_input_reaches_the_script_only_through_env():
    assert "${{" not in _body(), "dispatch inputs must arrive through env:, not the script text"
    env = _step()["env"]
    assert env["CLIENT_ID"] == "${{ inputs.client_id }}"
    assert env["SUBJECT_PREFIX"] == "${{ inputs.subject_prefix }}"
    assert env["ISSUE_NUMBER"] == "${{ inputs.issue_number }}"
    assert env["WHMCS_API_URL"] == DEFAULT_API, "the endpoint is fixed, not an input"


def test_nothing_is_uploaded():
    for s in _job()["steps"]:
        assert "upload-artifact" not in str(s.get("uses", "")), "233 must not upload an artifact"


def test_the_callee_sends_only_whmcs_read_actions():
    actions = set(re.findall(r"\.action\s*=\s*'([A-Za-z]+)'", _callee()))
    assert actions, "found no WHMCS action assignments; the pattern no longer matches the script"
    assert actions <= READ_ACTIONS, f"non-read WHMCS action(s) in {CALLEE}: {sorted(actions - READ_ACTIONS)}"


def test_the_live_runner_never_touches_a_message():
    src = _callee()
    guard = "if ($MyInvocation.InvocationName -eq '.') { return }"
    assert src.count(guard) == 1, "dot-source guard missing or duplicated"
    runner = src.split(guard, 1)[1]
    assert not re.search(r"message", runner.replace("Get-TicketReplySafeError -Message $_.Exception.Message", ""), re.I), (
        "the runner (below the guard) must not reference reply messages; only the result object leaves it"
    )


# --- behaviour (pwsh) --------------------------------------------------------


def test_valid_inputs_call_the_callee_and_post_nothing_without_an_issue():
    out, gh_log, rc, _ = _run(CLIENT_ID="701", SUBJECT_PREFIX="[Website Preview]", ISSUE_NUMBER="")
    assert rc == 0, out
    assert f"CALLED ApiUrl=[{DEFAULT_API}] ClientId=[701] SubjectPrefix=[[Website Preview]]" in out, out
    assert gh_log == "", gh_log


def test_the_application_prefix_is_passed_through():
    out, _, rc, _ = _run(CLIENT_ID="701", SUBJECT_PREFIX="[Application]")
    assert rc == 0, out
    assert "SubjectPrefix=[[Application]]" in out, out


def test_an_issue_number_posts_the_markdown_file():
    out, gh_log, rc, tmp = _run(CLIENT_ID="701", SUBJECT_PREFIX="[Website Preview]", ISSUE_NUMBER="42")
    assert rc == 0, out
    assert "GH issue comment 42 --repo FreeForCharity/FFC-Cloudflare-Automation --body-file" in gh_log, gh_log
    assert "charity-lane-ticket-reply.md" in gh_log, gh_log


def test_bad_client_ids_fail_before_the_call_without_echoing_the_value():
    for bad in ("", "   ", "abc", "12; rm -rf /", "-5", "0", "1e3", "jane.doe@example.org"):
        out, gh_log, rc, _ = _run(CLIENT_ID=bad, SUBJECT_PREFIX="[Website Preview]", ISSUE_NUMBER="42")
        assert rc != 0, (bad, out)
        assert "::error::client_id must be a positive integer" in out, (bad, out)
        assert "CALLED" not in out, (bad, out)
        assert gh_log == "", (bad, gh_log)
        if len(bad.strip()) >= 3:
            assert bad not in out, f"the rejected value {bad!r} was echoed"


def test_a_prefix_outside_the_choices_fails_before_the_call():
    for bad in ("", "[website preview]", "[Website Preview] Your new website", "Re:"):
        out, _, rc, _ = _run(CLIENT_ID="701", SUBJECT_PREFIX=bad)
        assert rc != 0, (bad, out)
        assert "::error::subject_prefix must be one of" in out, (bad, out)
        assert "CALLED" not in out, (bad, out)


def test_a_bad_issue_number_fails_before_the_call():
    out, gh_log, rc, _ = _run(CLIENT_ID="701", SUBJECT_PREFIX="[Website Preview]", ISSUE_NUMBER="12; rm -rf /")
    assert rc != 0, out
    assert "::error::issue_number must be a positive integer" in out, out
    assert "CALLED" not in out
    assert "rm -rf" not in out
    assert gh_log == ""


def test_a_failed_classification_posts_nothing():
    out, gh_log, rc, _ = _run(CLIENT_ID="701", SUBJECT_PREFIX="[Website Preview]", ISSUE_NUMBER="42", STUB_EXIT="1")
    assert rc != 0, out
    assert gh_log == "", gh_log


def test_the_real_callee_emits_no_reply_text_names_emails_or_phones():
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", NO_TEXT_PROBE],
        cwd=ROOT,
        env=child_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert '"classification": "APPROVED"' in out, out
    assert "<!-- charity-lane:ticket-reply -->" in out, out
    for s in SENTINELS:
        assert s not in out, f"{s!r} leaked into 233's output"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

NEEDS_PWSH = {
    "test_valid_inputs_call_the_callee_and_post_nothing_without_an_issue",
    "test_the_application_prefix_is_passed_through",
    "test_an_issue_number_posts_the_markdown_file",
    "test_bad_client_ids_fail_before_the_call_without_echoing_the_value",
    "test_a_prefix_outside_the_choices_fails_before_the_call",
    "test_a_bad_issue_number_fails_before_the_call",
    "test_a_failed_classification_posts_nothing",
    "test_the_real_callee_emits_no_reply_text_names_emails_or_phones",
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
