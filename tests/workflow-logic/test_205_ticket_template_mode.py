"""Unit tests for 205's optional `template` input (charity-lane #1618, G3.5).

205 opens a WHMCS ticket under the gated `whmcs-prod` environment. With
`template` set, a `Resolve ticket template` step builds the subject and body from
config/whmcs-ticket-templates.json (through scripts/whmcs-ticket-template.ps1),
appends `extra_message`, and hands both to the `Open ticket` step through that
step's own `env:` mapping (`steps.template.outputs.X || inputs.X`). With
`template` blank the resolve step is skipped and `Open ticket` receives the
dispatched values exactly as before. The pre-existing wiring, injection and
fail-closed contracts of `Open ticket` stay pinned by
`test_205_ticket_open_wiring.py`; this module pins only the new path.

Behavioural cases run the SHIPPED step bodies the way the runner runs them: the
resolve step against the real template script and config, its GITHUB_OUTPUT
parsed (heredoc included), then `Open ticket` with the env the mapping produces,
against a stub `whmcs-ticket-open.ps1`. No WHMCS call.
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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "205-whmcs-ticket-open.yml"
JOB = "open_ticket"
RESOLVE_STEP = "Resolve ticket template"
OPEN_STEP = "Open ticket"

STUB = """[CmdletBinding()]
param(
    [string]$DeptId,
    [string]$Subject,
    [string]$Message,
    [string]$ClientId,
    [string]$Name,
    [string]$Email,
    [string]$Priority,
    [switch]$DryRun
)
Write-Output "CALLED Subject=[$Subject] ClientId=[$ClientId] DryRun=[$DryRun]"
Write-Output "MESSAGE<<$Message>>"
"""

# GitHub's `shell: pwsh` wrapper (see test_205_ticket_open_wiring.py): the
# appended line is what makes a failed NATIVE call fail the step.
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# Every variable the harness must own outright: an inherited one could satisfy
# an assertion the workflow is supposed to.
CONTROLLED_VARS = (
    "TICKET_DEPTID",
    "TICKET_SUBJECT",
    "TICKET_MESSAGE",
    "TICKET_EMAIL",
    "TICKET_CLIENT_ID",
    "TICKET_TEMPLATE",
    "TICKET_EXTRA_MESSAGE",
    "DISPATCH_SUBJECT",
    "DISPATCH_MESSAGE",
    "GITHUB_OUTPUT",
)

PREVIEW_URL = "https://freeforcharity.github.io/FFC-EX-example.org/"


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _resolve_step() -> dict:
    return find_step(_workflow(), JOB, RESOLVE_STEP)


def _open_step() -> dict:
    return find_step(_workflow(), JOB, OPEN_STEP)


def _render(body: str) -> str:
    """The two constrained choice/boolean interpolations, as GitHub renders them."""
    rendered = body.replace("${{ inputs.priority }}", "Medium").replace(
        "${{ inputs.dry_run }}", "true"
    )
    assert "${{" not in rendered, f"an unrendered expression remains: {rendered!r}"
    return rendered


def _pwsh(tmp: pathlib.Path, body: str, env_overrides: dict):
    script = tmp / "step.ps1"
    script.write_text(RUNNER_PREAMBLE + _render(body) + RUNNER_EPILOGUE, encoding="utf-8")
    env = child_env(**env_overrides)
    for var in CONTROLLED_VARS:
        if var not in env_overrides:
            env.pop(var, None)
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return proc.stdout + proc.stderr, proc.returncode


def _parse_github_output(text: str) -> dict:
    """GITHUB_OUTPUT's two forms: `k=v` and `k<<DELIM ... DELIM`."""
    out, lines, i = {}, text.splitlines(), 0
    while i < len(lines):
        line = lines[i].lstrip("﻿")
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)<<(.+)$", line)
        if m:
            key, delim, buf = m.group(1), m.group(2), []
            i += 1
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            assert i < len(lines), f"heredoc for {key!r} never closed: {text!r}"
            out[key] = "\n".join(buf)
        elif "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
        i += 1
    return out


def _run(
    *,
    template: str,
    extra: str = "",
    subject: str = "",
    message: str = "",
    deptid: str = "1",
    client_id: str = "",
):
    """Both steps as the runner sequences them. Returns (output, rc, outputs)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "config").mkdir()
        (tmp / "scripts" / "whmcs-ticket-open.ps1").write_text(STUB, encoding="utf-8")
        shutil.copy(REPO_ROOT / "scripts" / "whmcs-ticket-template.ps1", tmp / "scripts")
        shutil.copy(REPO_ROOT / "config" / "whmcs-ticket-templates.json", tmp / "config")

        outputs: dict = {}
        log = ""
        # `if: inputs.template != ''`: GitHub skips the step on a blank value.
        if template != "":
            gh_out = tmp / "github_output.txt"
            gh_out.write_text("", encoding="utf-8")
            log, rc = _pwsh(
                tmp,
                _resolve_step()["run"],
                {
                    "TICKET_TEMPLATE": template,
                    "TICKET_EXTRA_MESSAGE": extra,
                    "DISPATCH_SUBJECT": subject,
                    "DISPATCH_MESSAGE": message,
                    "GITHUB_OUTPUT": str(gh_out),
                },
            )
            if rc != 0:
                return log, rc, outputs
            outputs = _parse_github_output(gh_out.read_text(encoding="utf-8"))

        # `steps.template.outputs.X || inputs.X`
        open_log, rc = _pwsh(
            tmp,
            _open_step()["run"],
            {
                "TICKET_DEPTID": deptid,
                "TICKET_SUBJECT": outputs.get("subject") or subject,
                "TICKET_MESSAGE": outputs.get("message") or message,
                "TICKET_EMAIL": "",
                "TICKET_CLIENT_ID": client_id,
            },
        )
        return log + open_log, rc, outputs


# --------------------------------------------------------------------------
# Wiring (pure YAML)
# --------------------------------------------------------------------------


def test_template_inputs_are_optional_and_default_blank():
    wf = _workflow()
    inputs = wf.get("on", wf.get(True))["workflow_dispatch"]["inputs"]
    for name in ("template", "extra_message"):
        assert name in inputs, f"205 lost its `{name}` input"
        assert inputs[name].get("required") is False, f"`{name}` must be optional"
        assert inputs[name].get("default") == "", f"`{name}` must default to blank"


def test_the_resolve_step_is_skipped_without_a_template_and_runs_before_the_credential():
    steps = _workflow()["jobs"][JOB]["steps"]
    names = [s.get("name") or s.get("uses") for s in steps]
    resolve_at = names.index(RESOLVE_STEP)
    kv_at = names.index("./.github/actions/whmcs-secrets-from-kv")
    assert resolve_at < kv_at, (
        "template resolution must run before the WHMCS credential is minted, so a "
        f"refused template costs no Key Vault read. Order: {names}"
    )
    step = steps[resolve_at]
    assert step.get("id") == "template", step
    assert str(step.get("if", "")).replace(" ", "") == "inputs.template!=''", step


def test_free_text_inputs_travel_in_env_and_are_never_interpolated():
    step = _resolve_step()
    env = step.get("env") or {}
    assert env.get("TICKET_TEMPLATE") == "${{ inputs.template }}", env
    assert env.get("TICKET_EXTRA_MESSAGE") == "${{ inputs.extra_message }}", env
    assert env.get("DISPATCH_SUBJECT") == "${{ inputs.subject }}", env
    assert env.get("DISPATCH_MESSAGE") == "${{ inputs.message }}", env
    for body in (step["run"], _open_step()["run"]):
        assert "inputs.template" not in body and "inputs.extra_message" not in body, (
            "a free-text input is interpolated into a script body (#1080)"
        )


def test_open_ticket_takes_the_template_output_first_and_the_dispatch_otherwise():
    env = _open_step().get("env") or {}
    assert (
        env.get("TICKET_SUBJECT") == "${{ steps.template.outputs.subject || inputs.subject }}"
    ), env
    assert (
        env.get("TICKET_MESSAGE") == "${{ steps.template.outputs.message || inputs.message }}"
    ), env


# --------------------------------------------------------------------------
# Behaviour (pwsh)
# --------------------------------------------------------------------------


def test_preview_ready_opens_a_ticket_with_the_template_subject_and_url():
    out, rc, outputs = _run(
        template="preview_ready", extra=f"Preview: {PREVIEW_URL}", client_id="419"
    )
    assert rc == 0, f"template mode exited {rc}: {out}"
    assert (
        outputs.get("subject") == "[Website Preview] Your new website is ready to review"
    ), outputs
    assert "CALLED Subject=[[Website Preview] Your new website is ready to review]" in out, out
    assert "ClientId=[419]" in out, out
    assert f"Preview: {PREVIEW_URL}>>" in out, f"the preview URL did not reach the message: {out}"
    assert "phone" in outputs.get("message", ""), "the body did not survive the heredoc"
    assert "DryRun=[True]" in out, f"the dry-run switch was lost: {out}"


def test_template_with_a_subject_or_message_is_refused_before_any_call():
    for extra in ({"subject": "Mine"}, {"message": "Mine"}):
        out, rc, _ = _run(template="preview_ready", extra=PREVIEW_URL, **extra)
        assert rc == 1, f"{extra} with a template exited {rc}: {out}"
        assert "::error::template is set, so subject and message must be left blank" in out, out
        assert "CALLED" not in out, f"the ticket script ran anyway: {out}"


def test_a_whitespace_template_fails_closed_and_says_so():
    out, rc, _ = _run(template="   ")
    assert rc == 1, out
    assert "::error::TICKET_TEMPLATE is empty" in out, out
    assert "CALLED" not in out, out


def test_a_reply_only_template_is_refused_before_any_call():
    out, rc, _ = _run(template="need_info")
    assert rc != 0, f"a reply-only template opened a ticket (rc {rc}): {out}"
    assert "reply-only" in out, out
    assert "CALLED" not in out, out


def test_preview_ready_without_the_url_is_refused_before_any_call():
    out, rc, _ = _run(template="preview_ready", extra="")
    assert rc != 0, f"preview_ready opened a ticket with no preview URL (rc {rc}): {out}"
    assert "requires extra_message" in out, out
    assert "CALLED" not in out, out


def test_without_a_template_the_dispatched_subject_and_message_are_used():
    out, rc, outputs = _run(
        template="",
        subject="Site down",
        message="Please investigate",
        extra="ignored without a template",
    )
    assert outputs == {}, "the resolve step ran without a template"
    assert rc == 0, f"the plain path exited {rc}: {out}"
    assert "CALLED Subject=[Site down]" in out, out
    assert "MESSAGE<<Please investigate>>" in out, (
        f"extra_message leaked into a non-template ticket: {out}"
    )


def test_without_a_template_a_blank_subject_still_fails_closed():
    out, rc, _ = _run(template="", subject="", message="x")
    assert rc == 1, out
    assert "::error::TICKET_SUBJECT is empty" in out, out
    assert "CALLED" not in out, out


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn pwsh; the wiring cases are pure YAML and run
# on a host without it (#1182).
NEEDS_PWSH = {
    "test_preview_ready_opens_a_ticket_with_the_template_subject_and_url",
    "test_template_with_a_subject_or_message_is_refused_before_any_call",
    "test_a_whitespace_template_fails_closed_and_says_so",
    "test_a_reply_only_template_is_refused_before_any_call",
    "test_preview_ready_without_the_url_is_refused_before_any_call",
    "test_without_a_template_the_dispatched_subject_and_message_are_used",
    "test_without_a_template_a_blank_subject_still_fails_closed",
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
