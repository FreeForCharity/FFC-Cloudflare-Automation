"""Unit tests for 120's `legacy_apex_ip` input on the DNS-flip step.

`scripts/bulk-cutover-to-github-pages.ps1` deletes only the apex A records whose
content equals `-HostPapaIp` (default 216.222.200.253), adds the four GitHub
Pages A records, and leaves every other apex A record "untouched" with a
warning. 120 never passed that parameter, so a site whose origin is NOT HostPapa
came out of a live run with its old A record beside the four Pages records: the
apex answered from two hosts, and roughly one resolver in five reached the old
origin. Measured against a tamkeensports.org-shaped zone (InterServer
174.138.190.170, proxied) by running the real script against a stubbed
Cloudflare library.

The fix is wiring only. An optional `legacy_apex_ip` input travels through
step-level `env:` (#1080 — the step holds `cloudflare-prod-write`), is checked
as a dotted-quad IPv4, is refused if it names a GitHub Pages address (deleting
one would remove the cutover target itself), and is then bound to
`-HostPapaIp`. A blank input omits the parameter, so the script's own default
still applies — the fleet's existing behaviour.

The stub records whether `-HostPapaIp` was BOUND, not only its value: an empty
string passed in the splat and a parameter omitted altogether render the same
way, and only the second keeps the script's default.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD_PATH = REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "120-bulk-cutover-to-github-pages.yml"
JOB = "dns-flip"
STEP = "Flip Cloudflare apex DNS (skip CNAME in this job)"
ENV_VAR = "IN_LEGACY_APEX_IP"
EXPRESSION = "${{ inputs.legacy_apex_ip }}"
CALLEE = "bulk-cutover-to-github-pages.ps1"
COMMON = "cloudflare-api-common.ps1"

INTERSERVER_IP = "174.138.190.170"
SENTINEL = "STOLEN-120.txt"

STUB = """[CmdletBinding()]
param(
    [string]$Domains,
    [switch]$SkipCname,
    [switch]$DryRun,
    [string]$HostPapaIp = '216.222.200.253'
)
$bound = $PSBoundParameters.ContainsKey('HostPapaIp')
Write-Output "CALLED HostPapaIp=[$HostPapaIp] Bound=[$bound] DryRun=[$DryRun]"
"""

# GitHub's `shell: pwsh` wrapper (see test_119 for why the epilogue matters).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _rendered_body() -> str:
    """The step body with the boolean `dry_run` expression substituted.

    The anchor's count is asserted before substituting (ledger L47), so a body
    that moved it fails loudly instead of running a literal `${{ }}`.
    """
    body = _step()["run"]
    anchor = "'${{ inputs.dry_run }}'"
    assert body.count(anchor) == 1, f"expected one {anchor} in the body"
    rendered = body.replace(anchor, "'true'")
    assert "${{" not in rendered, f"unsubstituted expression left: {rendered!r}"
    return rendered


def _run(value: str | None):
    """Run the shipped body in a temp cwd holding the stub and the REAL library.

    The real `cloudflare-api-common.ps1` is copied rather than stubbed so the
    Pages-address refusal is measured against the canonical list, not a copy of
    it that could drift.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "config").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        shutil.copy(REPO_ROOT / "scripts" / COMMON, tmp / "scripts" / COMMON)
        (tmp / "config" / "ffc-ex-cutover-domains.json").write_text(
            '{"domains": ["example.org"]}', encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + _rendered_body() + RUNNER_EPILOGUE, encoding="utf-8"
        )
        env = child_env(IN_DOMAINS="tamkeensports.org")
        env.pop(ENV_VAR, None)
        if value is not None:
            env[ENV_VAR] = value
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        stolen = (tmp / SENTINEL).exists()
        return proc.stdout + proc.stderr, proc.returncode, stolen


# --------------------------------------------------------------------------
# Wiring — no tool needed
# --------------------------------------------------------------------------


def test_the_input_is_optional_free_text_with_an_empty_default():
    # YAML 1.1 parses a bare `on` key as the boolean True.
    doc = load_workflow(WORKFLOW)
    trigger = doc.get(True, doc.get("on"))
    spec = trigger["workflow_dispatch"]["inputs"]["legacy_apex_ip"]
    assert spec.get("type") == "string", spec
    assert spec.get("required") is False, spec
    assert spec.get("default") == "", spec


def test_the_input_travels_through_env_and_is_never_interpolated():
    step = _step()
    env = step.get("env") or {}
    body = step.get("run", "")
    assert env.get(ENV_VAR) == EXPRESSION, f"env: block is {env!r}"
    assert f"$env:{ENV_VAR}" in body, "env var is mapped but never read"
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    assert "legacy_apex_ip" not in found, (
        "legacy_apex_ip is interpolated into a cloudflare-prod-write body (#1080)"
    )


def test_the_step_sits_in_the_gated_cloudflare_write_job():
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == "cloudflare-prod-write", job.get("environment")


def test_the_value_reaches_the_scripts_host_papa_parameter():
    body = _step()["run"]
    assert "$params.HostPapaIp" in body, (
        "the validated value is never bound to -HostPapaIp, so the script keeps "
        "deleting only the HostPapa record"
    )


# --------------------------------------------------------------------------
# Behaviour — needs pwsh
# --------------------------------------------------------------------------


def test_a_blank_input_leaves_the_script_default_in_place():
    for value in (None, "", "   "):
        out, rc, _ = _run(value)
        assert rc == 0, f"value={value!r} rc={rc}: {out}"
        assert "CALLED HostPapaIp=[216.222.200.253] Bound=[False]" in out, (
            f"value={value!r}: {out}"
        )


def test_a_valid_ip_is_bound_trimmed():
    out, rc, _ = _run(f"  {INTERSERVER_IP} ")
    assert rc == 0, out
    assert f"CALLED HostPapaIp=[{INTERSERVER_IP}] Bound=[True]" in out, out


def test_malformed_values_fail_the_step_before_the_script_runs():
    for value in ("174.138.190", "174.138.190.256", "2001:db8::1", "010.1.1.1", "x"):
        out, rc, _ = _run(value)
        assert rc != 0, f"value={value!r} was accepted: {out}"
        assert "CALLED" not in out, f"value={value!r} reached the script: {out}"
        assert "dotted-quad IPv4" in out, f"value={value!r}: {out}"


def test_a_github_pages_address_is_refused():
    out, rc, _ = _run("185.199.108.153")
    assert rc != 0, out
    assert "CALLED" not in out, out
    assert "GitHub Pages address" in out, out


def test_an_injection_payload_is_rejected_as_data():
    payload = f"{INTERSERVER_IP}'; Set-Content -Path '{SENTINEL}' -Value x; '"
    out, rc, stolen = _run(payload)
    assert not stolen, "the payload executed"
    assert rc != 0 and "CALLED" not in out, out
    assert "dotted-quad IPv4" in out, out


NEEDS_PWSH = {
    "test_a_blank_input_leaves_the_script_default_in_place",
    "test_a_valid_ip_is_bound_trimmed",
    "test_malformed_values_fail_the_step_before_the_script_runs",
    "test_a_github_pages_address_is_refused",
    "test_an_injection_payload_is_rejected_as_data",
}
TOOL_CASES = {"pwsh": NEEDS_PWSH}


def test_the_tool_case_list_names_tests_that_exist():
    declared = {k for k in globals() if k.startswith("test_")}
    for tool, names in sorted(TOOL_CASES.items()):
        unknown = sorted(names - declared)
        assert not unknown, f"{tool} case list names missing tests: {unknown}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    absent = {tool for tool in TOOL_CASES if shutil.which(tool) is None}
    failures = 0
    for t in TESTS:
        wanted = sorted(
            tool
            for tool, names in TOOL_CASES.items()
            if t.__name__ in names and tool in absent
        )
        if wanted:
            print(
                f"  SKIP {t.__name__} ({', '.join(wanted)} not installed in "
                f"this environment; runs in CI)"
            )
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
