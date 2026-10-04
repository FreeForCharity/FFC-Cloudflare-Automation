"""Wiring tests for the FFC AI-crawler default on 106 and 110.

THE CLAIM
    Every zone 110 creates, and every zone 106 enforces, ends up ALLOWING AI
    crawlers unless the dispatcher opts out. Cloudflare's own default on a new
    zone is the opposite (Block AI Bots on, AI Labyrinth where the plan has it),
    and nothing in this repo touched that setting until NHEG (ffc-ex #37)
    needed its public site readable by assistants and search engines. So the
    default is the whole point, and it is pinned here as a property of the
    INPUT DECLARATION — `type: choice`, options allow|block|skip, `default:
    allow` — not as a reading of a step comment.

WHAT THE STEP WIRING MUST HOLD
    Both steps shell out to `scripts/cloudflare-ai-crawlers.ps1` through the
    house pattern for a dispatch input: it arrives by step-level `env:`, the
    body never interpolates `inputs.*`, the free-text `domain` is guarded with
    `IsNullOrWhiteSpace` before the native call (L214: an unmapped env: var is
    NO ARGUMENT to a native command, so `-Zone` would bind the posture switch),
    and the choice is mapped through a `switch` whose `default` arm fails. 106
    additionally honours `dry_run` by passing `-DryRun`, so a rehearsal prints
    the plan and writes nothing.

    The behavioural cases run the shipped bodies against a stub callee that
    reports what it was BOUND — the only discriminator that works here, because
    pwsh echoes the offending source back in any parse error, so a substring
    search over stdout matches payload text on a run that executed nothing.
    They need pwsh and SKIP where it is absent (ledger L246: a whole-module
    gate reported green having asserted nothing), which is why the static
    cases above them are separate functions and run everywhere.

WHY A SEPARATE MODULE AND NOT A ROW IN test_110_zone_create_wiring.py
    That module is the #1080 burn-down record for 110's `domain` and its
    fixtures render the `jump_start` branches of ONE step. This is a different
    step in two workflows with one shared callee; the drift that matters here
    is between the two call sites and the script's parameter set, which is
    asserted directly below (`test_the_callee_declares_the_switches_the_steps_pass`).
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
SCRIPT = REPO_ROOT / "scripts" / "cloudflare-ai-crawlers.ps1"
PESTER = REPO_ROOT / "tests" / "cloudflare-ai-crawlers.Tests.ps1"
SAFETY_DOC = REPO_ROOT / "docs" / "workflow-safety-and-approvals.md"

CALLEE = "cloudflare-ai-crawlers.ps1"
STEP = "Set AI-crawler posture"
INPUT = "ai_crawlers"
OPTIONS = ["allow", "block", "skip"]
DEFAULT = "allow"
SKIP_CONDITION = "${{ inputs.ai_crawlers != 'skip' }}"
DOMAIN_EXPRESSION = "${{ inputs.domain }}"
POSTURE_EXPRESSION = "${{ inputs.ai_crawlers }}"

# The bot_management fields the callee governs, as MEASURED on the first live
# run (2026-10-02, zone newheightseducation.org). The response also carried
# enable_js, fight_mode, is_robots_txt_managed, bot_preference_sync_enabled,
# cf_robots_variant, ai_bots_migration_opt_out and using_latest_model, none of
# which is a crawler posture and none of which the callee may write.
POSTURE_FIELDS = (
    "ai_bots_protection",
    "crawler_protection",
    "ai_training",
    "ai_search",
    "ai_user",
    "content_bots_protection",
)

# One entry per workflow that carries the default. `must_follow` is the step
# that makes the zone exist (110) or finishes the DNS standard (106): the
# posture step is ordered after it, and that ordering is asserted rather than
# assumed, because a zone that does not exist yet is a `exit 2` from the callee.
LANES = {
    "110-cloudflare-zone-create.yml": {
        "job": "create-zone",
        "domain_var": "IN_DOMAIN",
        "posture_var": "IN_AI_CRAWLERS",
        "dry_run_var": None,
        "must_follow": "Create zone",
    },
    "106-enforce-standard.yml": {
        "job": "enforce_standard",
        "domain_var": "REC_DOMAIN",
        "posture_var": "REC_AI_CRAWLERS",
        "dry_run_var": "REC_DRY_RUN",
        "must_follow": "Post-Enforce Compliance Audit",
    },
}

# Variables the harness must own outright; an inherited one would satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = (
    "IN_DOMAIN",
    "IN_AI_CRAWLERS",
    "REC_DOMAIN",
    "REC_AI_CRAWLERS",
    "REC_DRY_RUN",
    "CLOUDFLARE_API_TOKEN_FFC",
    "CLOUDFLARE_API_TOKEN_CM",
)

# A permissive stand-in for the real script. It records what it was BOUND. None
# of its parameters is Mandatory on purpose: this stub measures the WORKFLOW's
# guard, and a Mandatory -Zone would measure the callee's binder instead.
STUB = """[CmdletBinding()]
param(
    [string]$Zone,
    [switch]$Audit,
    [switch]$Allow,
    [switch]$Block,
    [switch]$DryRun
)
Write-Output "CALLED Zone=[$Zone] Audit=[$Audit] Allow=[$Allow] Block=[$Block] DryRun=[$DryRun]"
"""


def _step(workflow: str) -> dict:
    return find_step(load_workflow(workflow), LANES[workflow]["job"], STEP)


def _inputs(workflow: str) -> dict:
    # YAML 1.1 parses a bare `on:` key as the boolean True.
    doc = load_workflow(workflow)
    trigger = doc.get(True, doc.get("on"))
    return trigger["workflow_dispatch"]["inputs"]


def _steps(workflow: str) -> list[dict]:
    return load_workflow(workflow)["jobs"][LANES[workflow]["job"]].get("steps", [])


def _index_of(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        label = str(step.get("name") or step.get("uses", ""))
        if needle.lower() in label.lower():
            return i
    raise AssertionError(f"no step matching {needle!r} in {[s.get('name') or s.get('uses') for s in steps]}")


def _run(body: str, **env_overrides: str) -> tuple[str, int]:
    """Run a step body in a temp cwd holding the stub at scripts/<CALLEE>."""
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
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        return proc.stdout + proc.stderr, proc.returncode


def _body(workflow: str) -> str:
    body = _step(workflow).get("run", "")
    # Every value travels by env, so the shipped body must already be free of
    # workflow expressions; pwsh cannot parse `${{` and a body carrying one dies
    # with a non-zero exit that reads like a guard firing.
    assert "${{" not in body, f"{workflow}: body carries a workflow expression: {body!r}"
    return body


# --------------------------------------------------------------------------
# The claim: allow by default, as a property of the input declaration
# --------------------------------------------------------------------------


def test_the_input_is_a_choice_defaulting_to_allow_in_both_workflows():
    for workflow in LANES:
        declared = _inputs(workflow)
        assert INPUT in declared, f"{workflow}: no {INPUT!r} dispatch input; have {sorted(declared)}"
        spec = declared[INPUT]
        assert spec.get("type") == "choice", (
            f"{workflow}: {INPUT} is {spec.get('type')!r}, must be a `choice` so GitHub "
            f"constrains the value (the body maps it through a switch, not a validator)"
        )
        assert list(spec.get("options") or []) == OPTIONS, (
            f"{workflow}: {INPUT} options are {spec.get('options')!r}, expected {OPTIONS}"
        )
        assert spec.get("default") == DEFAULT, (
            f"{workflow}: {INPUT} defaults to {spec.get('default')!r}, not {DEFAULT!r} — "
            f"this is THE claim of the module: FFC zones allow AI crawlers unless opted out"
        )


def test_the_callee_spells_allow_as_both_toggles_off():
    """'allow' means Block AI Bots OFF and AI Labyrinth OFF, pinned in the script.

    The workflows only say `-Allow`; what that WRITES is decided in the callee's
    desired-value map. A future edit that flips one of the two to its 'on'
    value would leave every workflow, input and doc reading 'allow' while the
    zone blocked.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    # The two [ordered] maps, in source order: allow first, then block. Each
    # field is pinned by NAME and VALUE, because 'allow' is only the right
    # posture if every field it names is turned OFF.
    maps = re.findall(r"\[ordered\]@\{(.*?)\}", text, flags=re.S)
    assert len(maps) == 2, f"expected the allow and block maps, found {len(maps)} [ordered] hashtables"
    allow = dict(re.findall(r"(\w+)\s*=\s*'(\w+)'", maps[0]))
    block = dict(re.findall(r"(\w+)\s*=\s*'(\w+)'", maps[1]))
    assert set(allow) == set(POSTURE_FIELDS) == set(block), (allow, block)
    assert all(v == "disabled" for v in allow.values()), f"'allow' must turn every field OFF: {allow}"
    assert block["ai_bots_protection"] == "block" and block["crawler_protection"] == "enabled", block
    assert all(block[f] == "block" for f in POSTURE_FIELDS if f not in ("crawler_protection",)), block


# --------------------------------------------------------------------------
# Wiring, statically
# --------------------------------------------------------------------------


def test_the_step_is_wired_through_env_and_never_interpolates():
    for workflow, lane in LANES.items():
        step = _step(workflow)
        env = step.get("env") or {}
        body = _body(workflow)
        assert step.get("shell") == "pwsh", f"{workflow}: step shell is {step.get('shell')!r}"
        assert step.get("if") == SKIP_CONDITION, (
            f"{workflow}: step `if:` is {step.get('if')!r}, expected {SKIP_CONDITION!r} — "
            f"'skip' must never reach the body (the switch has no arm for it, by design)"
        )
        assert env.get(lane["domain_var"]) == DOMAIN_EXPRESSION, (
            f"{workflow}: {lane['domain_var']} must map to {DOMAIN_EXPRESSION}; env is {env!r}"
        )
        assert env.get(lane["posture_var"]) == POSTURE_EXPRESSION, (
            f"{workflow}: {lane['posture_var']} must map to {POSTURE_EXPRESSION}; env is {env!r}"
        )
        assert "inputs." not in body, f"{workflow}: body interpolates a dispatch input: {body!r}"
        guard = f"if ([string]::IsNullOrWhiteSpace($env:{lane['domain_var']}))"
        assert body.count(guard) == 1, (
            f"{workflow}: expected exactly one emptiness guard {guard!r}, found {body.count(guard)}"
        )
        assert "exit 1" in body[body.index(guard) :].split("}", 1)[0], (
            f"{workflow}: the emptiness guard does not fail closed (no `exit 1` in its block)"
        )
        call = f"pwsh -NoProfile -File scripts/{CALLEE} -Zone $env:{lane['domain_var']} $posture"
        assert call in body, f"{workflow}: expected the native call {call!r} in the body: {body!r}"
        for posture, flag in (("allow", "-Allow"), ("block", "-Block")):
            arm = f"'{posture}' {{ $posture = '{flag}' }}"
            assert arm in body, f"{workflow}: missing switch arm {arm!r}"
        assert re.search(r"default\s*\{[^}]*(exit 1|throw )", body), (
            f"{workflow}: the switch's default arm neither exits 1 nor throws: {body!r}"
        )


def test_the_step_runs_after_the_zone_exists_and_after_the_token_loader():
    for workflow, lane in LANES.items():
        steps = _steps(workflow)
        posture_at = _index_of(steps, STEP)
        loader_at = _index_of(steps, "cloudflare-tokens-from-kv")
        follow_at = _index_of(steps, lane["must_follow"])
        assert loader_at < posture_at, (
            f"{workflow}: the token loader (step {loader_at}) must run before the posture "
            f"step ({posture_at}); the callee reads CLOUDFLARE_API_TOKEN_* from the process env"
        )
        assert follow_at < posture_at, (
            f"{workflow}: the posture step ({posture_at}) must follow {lane['must_follow']!r} "
            f"({follow_at}) — the callee exits 2 on a zone it cannot see"
        )
        assert posture_at == len(steps) - 1, (
            f"{workflow}: the posture step is not last ({posture_at} of {len(steps)}); "
            f"a later step would run after an AI-crawler failure only if `if:` says so — "
            f"re-read the ordering before moving it"
        )


def test_106_honours_dry_run_and_110_has_no_dry_run():
    step106 = _step("106-enforce-standard.yml")
    env106 = step106.get("env") or {}
    body106 = _body("106-enforce-standard.yml")
    assert env106.get("REC_DRY_RUN") == "${{ inputs.dry_run }}", env106
    assert "$env:REC_DRY_RUN" in body106 and "'-DryRun'" in body106, (
        f"106's posture step must pass -DryRun under dry_run: {body106!r}"
    )
    assert _inputs("106-enforce-standard.yml")["dry_run"].get("default") is True, (
        "106's dry_run no longer defaults to true, so the posture step would write on a default dispatch"
    )
    body110 = _body("110-cloudflare-zone-create.yml")
    assert "DryRun" not in body110, (
        "110 has no dry_run input; its posture step must not reference -DryRun"
    )


def test_the_callee_declares_the_switches_the_steps_pass():
    """Call sites and callee move together, or this fails at the seam."""
    text = SCRIPT.read_text(encoding="utf-8")
    for switch in ("Audit", "Allow", "Block", "DryRun"):
        assert re.search(r"\[switch\]\$" + switch + r"\b", text), f"callee no longer declares -{switch}"
    assert re.search(r"\[string\]\$Zone\b", text), "callee no longer declares -Zone"
    assert "function Resolve-AiCrawlerPatch" in text, "callee lost its pure decision function"
    assert "ai_bots_protection" in text and "crawler_protection" in text
    # Fail-closed on an unrecognised response shape: the refusal exists and names the field.
    assert "Action = 'refuse'" in text and "'ai_bots_protection'" in text, (
        "the callee no longer refuses a bot_management response without ai_bots_protection"
    )
    pester = PESTER.read_text(encoding="utf-8")
    assert "Resolve-AiCrawlerPatch" in pester, (
        "the Pester module no longer names the function it extracts from the callee"
    )


def test_the_safety_table_names_the_input_on_both_rows():
    rows = {}
    for line in SAFETY_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 106 ") or line.startswith("| 110 "):
            rows[line.split("|")[1].strip()] = line
    for number in ("106", "110"):
        assert number in rows, f"safety table has no row for {number}"
        assert f"`{INPUT}`" in rows[number] and f"`{DEFAULT}`" in rows[number], (
            f"row {number} of docs/workflow-safety-and-approvals.md does not name "
            f"`{INPUT}` and its `{DEFAULT}` default: {rows[number][:200]!r}"
        )


# --------------------------------------------------------------------------
# Behaviour (pwsh)
# --------------------------------------------------------------------------


def test_allow_and_block_reach_the_script_as_switches():
    for workflow, lane in LANES.items():
        for posture, bound in (("allow", "Allow=[True] Block=[False]"), ("block", "Allow=[False] Block=[True]")):
            overrides = {lane["domain_var"]: "example.org", lane["posture_var"]: posture}
            if lane["dry_run_var"]:
                overrides[lane["dry_run_var"]] = "false"
            out, rc = _run(_body(workflow), **overrides)
            assert rc == 0, f"{workflow}/{posture}: rc={rc}: {out}"
            assert "CALLED Zone=[example.org]" in out, f"{workflow}/{posture}: {out}"
            assert bound in out, f"{workflow}/{posture}: expected {bound!r} bound: {out}"
            if posture == "allow":
                assert "DryRun=[False]" in out, f"{workflow}: a live run must not pass -DryRun: {out}"


def test_106_dry_run_passes_dryrun_and_a_live_run_does_not():
    body = _body("106-enforce-standard.yml")
    for value, expected in (("true", "DryRun=[True]"), ("false", "DryRun=[False]")):
        out, rc = _run(body, REC_DOMAIN="example.org", REC_AI_CRAWLERS="allow", REC_DRY_RUN=value)
        assert rc == 0, f"dry_run={value}: rc={rc}: {out}"
        assert expected in out and "Allow=[True]" in out, f"dry_run={value}: {out}"


def test_an_unknown_posture_fails_before_the_call():
    for workflow, lane in LANES.items():
        overrides = {lane["domain_var"]: "example.org", lane["posture_var"]: "maybe"}
        if lane["dry_run_var"]:
            overrides[lane["dry_run_var"]] = "false"
        out, rc = _run(_body(workflow), **overrides)
        assert rc != 0, f"{workflow}: an unsupported posture exited 0: {out}"
        # Not merely a non-zero code (that is also what a broken harness returns):
        # the body must say what it refused, and the stub must never have run.
        assert INPUT in out, f"{workflow}: the refusal does not name {INPUT!r}: {out}"
        assert "CALLED" not in out, f"{workflow}: the callee ran despite an unsupported posture: {out}"


def test_a_missing_domain_mapping_fails_closed():
    for workflow, lane in LANES.items():
        overrides = {lane["posture_var"]: "allow"}
        if lane["dry_run_var"]:
            overrides[lane["dry_run_var"]] = "false"
        out, rc = _run(_body(workflow), **overrides)
        assert rc != 0, f"{workflow}: an unset {lane['domain_var']} exited 0: {out}"
        assert lane["domain_var"] in out, f"{workflow}: the refusal does not name the variable: {out}"
        assert "CALLED" not in out, (
            f"{workflow}: the callee was invoked with an empty -Zone — the arity shift L214 "
            f"describes would have bound '-Allow' as the zone name: {out}"
        )


# Cases that shell out to pwsh; everything else is a static assertion over the
# workflow YAML and the script text and must run on a host without pwsh (L246).
NEEDS_PWSH = {
    "test_allow_and_block_reach_the_script_as_switches",
    "test_106_dry_run_passes_dryrun_and_a_live_run_does_not",
    "test_an_unknown_posture_fails_before_the_call",
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
