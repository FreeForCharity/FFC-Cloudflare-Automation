"""Wiring tests for 110's "Enable FFC custom nameservers" step.

THE CLAIM
    Every FFC zone delegates to the account custom nameservers
    ns1/ns2.freeforcharity.org. 102 hands those names to the registrar and the
    onboarding docs hand them to charities, but the Cloudflare side is a
    per-zone toggle nothing in this repo set, so a zone 110 created came up on
    its assigned pair (measured 2026-10-02, newheightseducation.org:
    dane/zainab.ns.cloudflare.com). The default is the point, pinned as a
    property of the input declaration: `type: choice`, options enable|skip,
    `default: enable`.

WHAT THE WIRING MUST HOLD
    The house pattern for a dispatch input: by step-level `env:`, never
    interpolated; `IsNullOrWhiteSpace` guard on the free-text domain before the
    native call (L214); a `switch` whose `default` arm fails; `skip` is an
    `if:` and never reaches the body. The step runs after the create step and
    the token loader, and before the AI-crawler posture step (which
    test_cloudflare_ai_crawlers_wiring.py pins as last).

    The behavioural cases run the shipped body against a stub callee that
    reports what it was BOUND; they need pwsh and SKIP where it is absent
    (L246), so the static cases above them are separate functions.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "cloudflare-custom-nameservers.ps1"
PESTER = REPO_ROOT / "tests" / "cloudflare-custom-nameservers.Tests.ps1"
SAFETY_DOC = REPO_ROOT / "docs" / "workflow-safety-and-approvals.md"

WORKFLOW = "110-cloudflare-zone-create.yml"
JOB = "create-zone"
STEP = "Enable FFC custom nameservers"
CALLEE = "cloudflare-custom-nameservers.ps1"
INPUT = "custom_nameservers"
OPTIONS = ["enable", "skip"]
DEFAULT = "enable"
SKIP_CONDITION = "${{ inputs.custom_nameservers != 'skip' }}"
DOMAIN_VAR = "IN_DOMAIN"
MODE_VAR = "IN_CUSTOM_NS"
CONTROLLED_VARS = (DOMAIN_VAR, MODE_VAR, "CLOUDFLARE_API_TOKEN_FFC", "CLOUDFLARE_API_TOKEN_CM")

STUB = """[CmdletBinding()]
param([string]$Zone, [switch]$Audit, [switch]$Enable, [switch]$Disable, [switch]$DryRun)
Write-Output "CALLED Zone=[$Zone] Enable=[$Enable] Disable=[$Disable] DryRun=[$DryRun]"
"""


def _doc() -> dict:
    return load_workflow(WORKFLOW)


def _step() -> dict:
    return find_step(_doc(), JOB, STEP)


def _inputs() -> dict:
    doc = _doc()
    trigger = doc.get(True, doc.get("on"))
    return trigger["workflow_dispatch"]["inputs"]


def _index_of(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle.lower() in str(step.get("name") or step.get("uses", "")).lower():
            return i
    raise AssertionError(f"no step matching {needle!r}")


def _body() -> str:
    body = _step().get("run", "")
    assert "${{" not in body, f"body carries a workflow expression: {body!r}"
    return body


def _run(body: str, **env_overrides: str) -> tuple[str, int]:
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        return proc.stdout + proc.stderr, proc.returncode


def test_the_input_is_a_choice_defaulting_to_enable():
    spec = _inputs()[INPUT]
    assert spec.get("type") == "choice", spec
    assert list(spec.get("options") or []) == OPTIONS, spec
    assert spec.get("default") == DEFAULT, (
        f"{INPUT} defaults to {spec.get('default')!r}, not {DEFAULT!r} - the claim of this module "
        f"is that every zone 110 creates lands on ns1/ns2.freeforcharity.org"
    )


def test_the_step_is_wired_through_env_and_never_interpolates():
    step = _step()
    env = step.get("env") or {}
    body = _body()
    assert step.get("shell") == "pwsh", step.get("shell")
    assert step.get("if") == SKIP_CONDITION, step.get("if")
    assert env.get(DOMAIN_VAR) == "${{ inputs.domain }}", env
    assert env.get(MODE_VAR) == "${{ inputs.custom_nameservers }}", env
    assert "inputs." not in body, body
    guard = f"if ([string]::IsNullOrWhiteSpace($env:{DOMAIN_VAR}))"
    assert body.count(guard) == 1, body
    assert "exit 1" in body[body.index(guard):].split("}", 1)[0]
    assert f"pwsh -NoProfile -File scripts/{CALLEE} -Zone $env:{DOMAIN_VAR} $mode" in body, body
    assert "'enable' { $mode = '-Enable' }" in body, body
    assert re.search(r"default\s*\{[^}]*exit 1", body), body


def test_the_step_runs_after_the_create_step_and_before_the_posture_step():
    steps = _doc()["jobs"][JOB]["steps"]
    here = _index_of(steps, STEP)
    assert _index_of(steps, "cloudflare-tokens-from-kv") < here
    assert _index_of(steps, "Create zone") < here, "the zone must exist before its custom_ns is toggled"
    assert here < _index_of(steps, "Set AI-crawler posture"), (
        "the posture step is pinned as LAST by test_cloudflare_ai_crawlers_wiring.py"
    )


def test_the_callee_declares_the_switches_the_step_passes():
    text = SCRIPT.read_text(encoding="utf-8")
    for switch in ("Audit", "Enable", "Disable", "DryRun"):
        assert re.search(r"\[switch\]\$" + switch + r"\b", text), f"callee no longer declares -{switch}"
    assert "function Resolve-CustomNsPatch" in text
    assert "Action = 'refuse'" in text and "'enabled'" in text, "the fail-closed refusal is gone"
    assert "/custom_ns" in text, "the callee no longer targets /zones/{id}/custom_ns"
    assert "Resolve-CustomNsPatch" in PESTER.read_text(encoding="utf-8")


def test_the_safety_table_names_the_input():
    for line in SAFETY_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 110 "):
            assert f"`{INPUT}`" in line and f"`{DEFAULT}`" in line, line[:200]
            return
    raise AssertionError("safety table has no row for 110")


def test_enable_reaches_the_script_as_a_switch():
    out, rc = _run(_body(), IN_DOMAIN="example.org", IN_CUSTOM_NS="enable")
    assert rc == 0, out
    assert "CALLED Zone=[example.org] Enable=[True] Disable=[False] DryRun=[False]" in out, out


def test_an_unknown_mode_fails_before_the_call():
    out, rc = _run(_body(), IN_DOMAIN="example.org", IN_CUSTOM_NS="maybe")
    assert rc != 0, out
    assert INPUT in out, out
    assert "CALLED" not in out, out


def test_a_missing_domain_mapping_fails_closed():
    out, rc = _run(_body(), IN_CUSTOM_NS="enable")
    assert rc != 0, out
    assert DOMAIN_VAR in out and "CALLED" not in out, out


NEEDS_PWSH = {
    "test_enable_reaches_the_script_as_a_switch",
    "test_an_unknown_mode_fails_before_the_call",
    "test_a_missing_domain_mapping_fails_closed",
}
TOOL_CASES = {"pwsh": NEEDS_PWSH}

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    absent = {tool for tool in TOOL_CASES if shutil.which(tool) is None}
    failures = 0
    for t in TESTS:
        wanted = sorted(tool for tool, names in TOOL_CASES.items() if t.__name__ in names and tool in absent)
        if wanted:
            print(f"  SKIP {t.__name__} ({', '.join(wanted)} not installed in this environment; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
