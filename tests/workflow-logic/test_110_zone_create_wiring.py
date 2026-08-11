"""Unit tests for 110's `domain` call sites (#1080 burn-down).

110 creates a Cloudflare zone. Its one free-text dispatch input, `domain`, used
to be interpolated into a pwsh body TWICE — once in each branch of the step's
`jump_start` conditional. Both now arrive through step-level `env:`.

WHERE THE CREDENTIAL SAT
    This is the first lane in the burn-down whose injection point sits in a
    process holding WRITE-scoped Cloudflare tokens. The job is
    `environment: cloudflare-prod-write` and calls

        ./.github/actions/cloudflare-tokens-from-kv   with   scope: write

    which resolves the `wr-all-*` Key Vault secrets and exports
    CLOUDFLARE_API_TOKEN_FFC and CLOUDFLARE_API_TOKEN_CM through GITHUB_ENV. So
    an injected body here held DNS write across BOTH Cloudflare accounts — the
    whole FFC estate plus CM — after an approver spent the gate on what looked
    like one zone creation.

    It is also the #1161 blindness repeating: those tokens are in the process
    environment of every later step in the job and appear in NO `env:` block, so
    a sweep of this file for `secrets.` in a `run:` body finds nothing at the
    site that matters. Ranking this entry by what its `env:` block is spelled
    with scores it as holding nothing.

    Measured against the bodies as they shipped, with
    `domain = ffcworkingsite1.org$(Set-Content -Path <sentinel> -Value
    $env:CLOUDFLARE_API_TOKEN_FFC)`, in BOTH branches:

        * the write-scoped Cloudflare token was written to a file;
        * the callee was then invoked with `Domain=[ffcworkingsite1.org]`, a
          legal domain, because the side effect left no residue;
        * the step exited **0**.

    A pwsh double-quoted string expands `$( )`, so the payload never had to
    break out of the quotes — being INSIDE them was enough. This is the exact
    shape of the `720-create-repo.yml` exemplar in #1080's body.

TWO BRANCHES IN ONE STEP, AND WHY A VERBATIM RUN ONLY SEES ONE
    Unlike the earlier lanes, 110's two call sites are in ONE step, selected at
    run time by

        if ('${{ inputs.jump_start }}' -eq 'true') { ... } else { ... }

    Running the step body as it appears in the YAML — which is what every
    module in this directory does — exercises only the `else` branch, and
    silently. `${{ inputs.jump_start }}` inside single quotes is a pwsh string
    LITERAL, not a variable reference, so the comparison is false on every
    unsubstituted run. Measured: `BRANCH=else`. A module that ran the raw body
    would report full coverage of a step whose `-JumpStart` invocation it had
    never executed — including the payload test, which is the one that has to
    hold for both. `_render` substitutes the branch selector and asserts its
    anchor count BEFORE substituting (ledger L47);
    `test_an_unrendered_body_only_reaches_the_else_branch` is the positive
    control proving the renderer is doing something.

WHAT STAYS INTERPOLATED, DELIBERATELY
    `account` and `zone_type` are still interpolated into the body, and that is
    not an oversight: both are `type: choice`, so GitHub constrains the value to
    the declared options and it cannot carry a payload. Their safety rests
    ENTIRELY on that type declaration — flip either to `type: string` and the
    body is injectable again through a parameter nobody re-reads.
    `test_the_still_interpolated_inputs_are_type_constrained` pins the
    declarations rather than leaving that as a reading of a comment. (The repo
    guard would also flag it, because 110 has left KNOWN_UNGUARDED — the two are
    deliberate overlap, and the guard cannot say WHICH input was expected to be
    constrained.)

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete the `env:` mapping and nothing is interpolated, so the checker
    is honestly green over a step that can no longer receive its input. Only a
    per-step assertion on the wiring notices, which is `_assert_wiring`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

WORKFLOW = "110-cloudflare-zone-create.yml"
JOB = "create-zone"
STEP = "Create zone"
ENVIRONMENT = "cloudflare-prod-write"
CALLEE = "cloudflare-zone-create.ps1"

# The env var the input must travel in, and the expression it must be mapped from.
ENV_VAR = "IN_DOMAIN"
EXPRESSION = "${{ inputs.domain }}"

# The credential the injection point could reach. It is in NO `env:` block in the
# workflow: the composite action exports it through GITHUB_ENV, so it is in the
# process environment of every later step in the job.
CF_TOKEN_VAR = "CLOUDFLARE_API_TOKEN_FFC"
SENTINEL = "STOLEN-110.txt"

# Deliberately NOT shaped like the credential it stands in for. A token-shaped
# literal is treated as a secret by scanners and by the tooling that renders CI
# output, so it comes back REDACTED — and the one place this value is ever
# printed is an assertion message on a failing run, which is the moment the
# reader needs to see whether the sentinel holds the credential or an empty
# string. The tests only ever compare it for equality.
FAKE_CF_TOKEN = "cf-write-placeholder-not-a-real-token"

# The branch selector, as it appears in the shipped body, and the two values a
# real dispatch can substitute into it.
BRANCH_ANCHOR = "'${{ inputs.jump_start }}'"
BRANCHES = {"jumpstart": "true", "plain": "false"}

# The constrained inputs still interpolated into the body, and the value the
# harness substitutes for each. GitHub replaces every `${{ }}` before pwsh ever
# sees the body; the harness has to do the same, and NOT because it is tidier:
# `${{` is not parseable pwsh at all (`${` opens a braced variable name), so a
# body carrying one dies with a ParserError before a single statement runs. Each
# value is checked against the input's declared `options` in `_render`, so a
# widened or renamed option cannot leave the harness substituting something a
# real dispatch could never produce.
CHOICE_VALUES = {"account": "FFC", "zone_type": "full"}

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (ENV_VAR, CF_TOKEN_VAR, "CLOUDFLARE_API_TOKEN_CM")

# The body as it shipped BEFORE the burn-down, kept verbatim as the positive
# control for the inertness tests below. `DOMAIN_HERE` stands where GitHub
# substituted `${{ inputs.domain }}` — inside double quotes, where pwsh expands
# `$( )` without any quote break-out — and `BRANCH_HERE` where it substituted
# `${{ inputs.jump_start }}`.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
if ('BRANCH_HERE' -eq 'true') {
  pwsh -NoProfile -File scripts/cloudflare-zone-create.ps1 `
    -Domain "DOMAIN_HERE" `
    -Account "FFC" `
    -ZoneType "full" `
    -JumpStart
}
else {
  pwsh -NoProfile -File scripts/cloudflare-zone-create.ps1 `
    -Domain "DOMAIN_HERE" `
    -Account "FFC" `
    -ZoneType "full"
}
"""

# A permissive stand-in for the real script: it records what it was BOUND, which
# is the only discriminator that works here. A marker-string search over stdout
# is not one — pwsh echoes the offending source line back in a ParserError, so
# any substring predicate matches the payload text on a run that executed
# nothing.
#
# `-Domain` and `-Account` are deliberately NOT Mandatory here even though the
# real cloudflare-zone-create.ps1 declares both so, because this stub is here to
# measure the WORKFLOW's guard and a Mandatory parameter would measure the
# callee's binder instead. MANDATORY_STUB below is the one that reproduces the
# callee, and it is used by exactly one control.
STUB = """[CmdletBinding()]
param(
    [string]$Domain,
    [string]$Account,
    [string]$ZoneType,
    [switch]$JumpStart
)
Write-Output "CALLED Domain=[$Domain] Account=[$Account] ZoneType=[$ZoneType] JumpStart=[$JumpStart]"
"""

# The real script's shape, reproduced only for the silent-success control below.
MANDATORY_STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [ValidateSet('FFC', 'CM')]
    [string]$Account,

    [Parameter()]
    [ValidateSet('full', 'partial')]
    [string]$ZoneType = 'full',

    [Parameter()]
    [switch]$JumpStart
)
Write-Output "CALLED Domain=[$Domain] Account=[$Account] ZoneType=[$ZoneType] JumpStart=[$JumpStart]"
"""


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _dispatch_inputs() -> dict:
    """The workflow's declared dispatch inputs.

    `on` is read through `doc.get(True, doc.get("on"))` because YAML 1.1 parses
    a bare `on:` key as the BOOLEAN True, so `doc["on"]` raises KeyError on a
    perfectly well-formed workflow.
    """
    doc = load_workflow(WORKFLOW)
    trigger = doc.get(True, doc.get("on"))
    return trigger["workflow_dispatch"]["inputs"]


def _render(body: str, jump_start: str) -> str:
    """Substitute every `${{ }}` the runner would, so the body can be executed.

    Two different jobs, and only the first is obvious:

    * the branch selector picks which invocation runs, so without it every
      rendered run takes the same branch and the module reports two-branch
      coverage it does not have;
    * the remaining `${{ inputs.* }}` expressions must go because pwsh cannot
      PARSE `${{` — it reads `${` as the start of a braced variable name and
      aborts the whole file. That failure lands on whichever line happens to
      hold one, in either branch, and it exits non-zero, so a test asserting
      only a non-zero code cannot tell it from the step correctly refusing a
      payload.

    Every anchor's occurrence count is asserted BEFORE substituting (ledger
    L47), and the rendered body is asserted to hold no expression at all
    afterwards — an anchor that stopped matching must fail loudly rather than
    leave an unparseable body that fails for a reason nobody reads.
    """
    assert jump_start in ("true", "false"), f"bad branch value {jump_start!r}"
    count = body.count(BRANCH_ANCHOR)
    assert count == 1, (
        f"expected exactly one {BRANCH_ANCHOR} branch selector to substitute, "
        f"found {count} — this module would otherwise run the same branch twice "
        f"and score it as two. Body: {body!r}"
    )
    rendered = body.replace(BRANCH_ANCHOR, f"'{jump_start}'")

    declared = _dispatch_inputs()
    for name, value in CHOICE_VALUES.items():
        options = declared[name].get("options") or []
        assert value in options, (
            f"the harness substitutes {value!r} for inputs.{name}, which is no "
            f"longer one of its declared options {options!r} — a real dispatch "
            f"could not produce this value, so the run would prove nothing"
        )
        anchor = "${{ inputs." + name + " }}"
        found = rendered.count(anchor)
        assert found == len(BRANCHES), (
            f"expected {anchor} once per invocation branch "
            f"({len(BRANCHES)}), found {found} — the body's shape has changed "
            f"and this module is no longer rendering what ships. "
            f"Body: {rendered!r}"
        )
        rendered = rendered.replace(anchor, value)

    assert "${{" not in rendered, (
        f"the rendered body still carries a workflow expression, which pwsh "
        f"cannot parse — every run against it would die before executing a "
        f"statement, and would do so with a non-zero exit code that reads like "
        f"a guard firing. Rendered: {rendered!r}"
    )
    return rendered


def _assert_wiring(step: dict) -> None:
    """The input travels in env, both branches read it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would call the script with an empty -Domain.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    name = step.get("name")
    assert env.get(ENV_VAR) == EXPRESSION, (
        f"step {name!r} must map {ENV_VAR} to {EXPRESSION} — its env: mapping "
        f"is {env!r}"
    )
    # BOTH branches, not merely "the body mentions it once". The two invocations
    # are copies of one call, and a burn-down that converted the branch it
    # happened to read would leave the other interpolating.
    reads = body.count(f"$env:{ENV_VAR}")
    assert reads >= 3, (
        f"step {name!r} reads $env:{ENV_VAR} {reads} times, expected at least 3 "
        f"(the emptiness guard plus one per invocation branch) — a branch that "
        f"does not read it is a branch the mapping never reaches. Body: {body!r}"
    )
    assert "inputs.domain" not in body, (
        f"step {name!r} interpolates inputs.domain into its script body again "
        f"(#1080): under {ENVIRONMENT!r}, with write-scoped Cloudflare tokens "
        f"for both accounts already in the process environment, that is "
        f"dispatcher text executed after the approval. Body: {body!r}"
    )


def _payload() -> str:
    """A payload valid in the position it lands in.

    It does not need to escape the quotes: pwsh expands `$( )` inside a
    double-quoted string, which is where the pre-fix body put the value. It
    reads the write-scoped Cloudflare token and evaluates to the empty string,
    so what reaches the callee is a legal domain and the run looks clean.
    """
    return (
        "ffcworkingsite1.org$(Set-Content -Path '"
        + SENTINEL
        + "' -Value $env:"
        + CF_TOKEN_VAR
        + ")"
    )


def _run(body: str, stub: str = STUB, **env_overrides: str) -> tuple[str, str | None, int]:
    """Run a pwsh body in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test
    is which credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(stub, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAIN would
        # make the fail-closed test pass for the wrong reason, and an inherited
        # token would let a theft assertion pass without the workflow supplying
        # anything.
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
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_the_call_site_is_wired_through_env():
    _assert_wiring(_step())


def test_the_job_enters_the_write_environment_with_write_scoped_tokens():
    """This module's central claim about blast radius, pinned rather than prose.

    Two halves, and the second is the one a reviewer cannot see from the step:
    the job is gated on a WRITE environment, and the composite action ahead of
    the step is asked for `scope: write`, which is what puts DNS-write tokens
    for both Cloudflare accounts in the process environment through GITHUB_ENV.
    Drop either and the docstring's account of what an injected body could reach
    stops being true.
    """
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} no longer sits in the environment this module is about: "
        f"it declares {job.get('environment')!r}, expected {ENVIRONMENT!r}"
    )
    loaders = [
        step
        for step in job.get("steps", [])
        if "cloudflare-tokens-from-kv" in str(step.get("uses", ""))
    ]
    assert len(loaders) == 1, (
        f"expected exactly one cloudflare-tokens-from-kv step ahead of the call "
        f"site, found {len(loaders)}"
    )
    scope = (loaders[0].get("with") or {}).get("scope")
    assert scope == "write", (
        f"the token loader now requests scope {scope!r}, not 'write' — the "
        f"credential in reach of the call site has changed, so re-read this "
        f"module's docstring before trusting it"
    )
    # And the loader really does run BEFORE the call site: the exposure claim is
    # an ordering claim, not merely a co-presence one.
    names = [str(s.get("name") or s.get("uses", "")) for s in job.get("steps", [])]
    loader_at = next(i for i, n in enumerate(names) if "cloudflare-tokens-from-kv" in n)
    call_at = next(i for i, n in enumerate(names) if STEP.lower() in n.lower())
    assert loader_at < call_at, (
        f"the token loader runs after the call site ({loader_at} vs {call_at}), "
        f"so the tokens are not in that step's environment: {names}"
    )


def test_the_still_interpolated_inputs_are_type_constrained():
    """`account` and `zone_type` stay in the body, and only their TYPE makes that safe.

    They are not findings because GitHub constrains a `choice` value to the
    declared options, so it cannot carry a payload. That is the entire argument,
    and it is a property of the input declaration rather than of the body — so a
    later edit widening either to free text re-opens an injection point in a
    step nobody would think to re-read. `jump_start` is asserted for the same
    reason: it is substituted into a pwsh comparison in the branch selector.
    """
    inputs = _dispatch_inputs()
    body = _step().get("run", "")
    for name, expected in (
        ("account", "choice"),
        ("zone_type", "choice"),
        ("jump_start", "boolean"),
    ):
        assert inputs[name]["type"] == expected, (
            f"input {name!r} is now type {inputs[name]['type']!r}, not "
            f"{expected!r} — it is interpolated straight into a pwsh body, and "
            f"a free-text value there is a #1080 injection point"
        )
        assert f"inputs.{name}" in body, (
            f"input {name!r} is no longer interpolated into the body, so this "
            f"test is guarding a call site that has moved. Body: {body!r}"
        )


def test_no_step_interpolates_a_secret_into_its_body():
    """A `run:` body is a file on the runner before it is a program.

    So an interpolated secret is a credential written to the workspace — the
    same workspace an injected `domain` payload would be running in. The two
    compound rather than being independent, which is why the sweep belongs in
    this module and not in a tidiness pass (#1141).
    """
    jobs = load_workflow(WORKFLOW)["jobs"]
    offenders = [
        (job, step.get("name"))
        for job, spec in jobs.items()
        for step in spec.get("steps", [])
        if "secrets." in str(step.get("run", ""))
    ]
    assert not offenders, (
        f"these steps interpolate a secret into their script body, which writes "
        f"it to a file in the runner workspace: {offenders}"
    )
    # Positive control: the sweep must be looking at a real body. Without it a
    # change that made `run` unreadable would report zero offenders and pass.
    bodies = [
        step.get("run", "") for spec in jobs.values() for step in spec.get("steps", [])
    ]
    assert sum(1 for b in bodies if b.strip()) >= 1, (
        f"the sweep found no run: bodies to check, so its empty result says "
        f"nothing: {bodies!r}"
    )


# --------------------------------------------------------------------------
# The branch mechanic
# --------------------------------------------------------------------------


def test_a_verbatim_body_does_not_parse_and_says_so_only_in_its_output():
    """Why `_render` exists, and a live case of CLAUDE.md's exit-code rule.

    The house pattern in this directory is to run a step's body exactly as it
    appears in the YAML. That is correct for every earlier lane and wrong here,
    because 110's body keeps two constrained `${{ inputs.* }}` expressions after
    the burn-down. pwsh reads `${` as the start of a braced variable name, so
    `${{ inputs.account }}` is a ParserError — the file is rejected before any
    statement runs, in EITHER branch, since parsing precedes execution.

    The failure mode is the point. It exits non-zero, which is exactly what
    `test_the_step_fails_closed_when_the_mapping_is_missing` asserts, so a
    fail-closed test written against `rc == 1` alone passes here while the step
    under test never ran at all. Measured during this PR: it did pass, and only
    the assertion on the error text caught it.
    """
    body = _step()["run"]
    out, _, rc = _run(body, IN_DOMAIN="ffcworkingsite1.org")
    assert rc != 0, (
        f"the verbatim body now runs, so pwsh accepts `${{{{` or the body no "
        f"longer carries one — _render's second job may be obsolete, and this "
        f"control is measuring nothing. Output: {out}"
    )
    assert "ParserError" in out, (
        f"the verbatim body failed for some reason OTHER than the parse error "
        f"this control is about, so it no longer demonstrates it: {out}"
    )
    assert f"::error::{ENV_VAR} is empty" not in out, (
        f"the run reported the emptiness guard, so it got far enough to "
        f"execute — that would make the exit code above ambiguous in the "
        f"opposite direction: {out}"
    )


def test_the_branch_selector_is_an_inert_literal_until_substituted():
    """`_render`'s first job, pinned by behaviour rather than by reading pwsh docs.

    The selector sits inside SINGLE quotes, so pwsh treats it as a string
    literal rather than as anything to expand, and the comparison is false on
    every unsubstituted run. If the parse error above were ever fixed some other
    way — say by moving `account` to `env:` too — a module running the body
    verbatim would then execute cleanly and silently cover only the `else`
    invocation, never touching the `-JumpStart` call. The anchor is taken from
    the shipped body, so this cannot drift away from the text it describes.
    """
    body = _step()["run"]
    assert body.count(BRANCH_ANCHOR) == 1, (
        f"the branch selector {BRANCH_ANCHOR} is not in the shipped body, so "
        f"this probe describes nothing. Body: {body!r}"
    )
    probe = (
        f"if ({BRANCH_ANCHOR} -eq 'true') {{ Write-Output 'BRANCH=jumpstart' }}"
        f" else {{ Write-Output 'BRANCH=else' }}\n"
    )
    out, _, rc = _run(probe)
    assert rc == 0, f"the selector probe exited {rc}: {out}"
    assert "BRANCH=else" in out, (
        f"the unsubstituted selector compared TRUE, so it is no longer an inert "
        f"literal and _render's premise needs re-deriving: {out}"
    )


def test_both_branches_are_reachable_after_rendering():
    """And the renderer really does select — otherwise every test below is one test."""
    step = _step()
    seen = {}
    for label, value in BRANCHES.items():
        out, _, rc = _run(
            _render(step["run"], value), IN_DOMAIN="ffcworkingsite1.org"
        )
        assert rc == 0, f"branch {label!r} exited {rc}: {out}"
        seen[label] = "JumpStart=[True]" in out
    assert seen == {"jumpstart": True, "plain": False}, (
        f"rendering did not select distinct branches: {seen}"
    )


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_values_reach_the_script_in_both_branches():
    step = _step()
    _assert_wiring(step)
    for label, value in BRANCHES.items():
        out, _, rc = _run(
            _render(step["run"], value), IN_DOMAIN="ffcworkingsite1.org"
        )
        assert rc == 0, f"branch {label!r} exited {rc}: {out}"
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, f"branch {label!r}: {out}"


def test_injected_payload_arrives_as_data_in_both_branches():
    """The whole point: the payload binds verbatim and runs nothing, in each branch."""
    step = _step()
    _assert_wiring(step)
    payload = _payload()
    for label, value in BRANCHES.items():
        out, stolen, rc = _run(
            _render(step["run"], value),
            IN_DOMAIN=payload,
            **{CF_TOKEN_VAR: FAKE_CF_TOKEN},
        )
        assert stolen is None, (
            f"branch {label!r}: the payload EXECUTED — {SENTINEL} was written "
            f"from a value that reached the body through env:, containing "
            f"{stolen!r}. Output: {out}"
        )
        assert f"CALLED Domain=[{payload}]" in out, (
            f"branch {label!r}: payload did not arrive verbatim, so something "
            f"re-parsed it (rc={rc}). Output: {out}"
        )


def test_pre_fix_body_is_the_positive_control_in_both_branches():
    """The same payload against the PRE-FIX body executes and steals the token.

    Without this, the inertness test above proves nothing: a payload that never
    worked in the first place also writes no sentinel. It pins all three halves
    of the finding — that the code ran, that what it reached was a write-scoped
    Cloudflare API token, and that the run then looked clean.
    """
    assert PRE_FIX_BODY.count("DOMAIN_HERE") == 2, (
        f"the pre-fix control has {PRE_FIX_BODY.count('DOMAIN_HERE')} "
        f"substitution points, expected 2 (one per branch)"
    )
    payload = _payload()
    for label, value in BRANCHES.items():
        body = PRE_FIX_BODY.replace("BRANCH_HERE", value).replace(
            "DOMAIN_HERE", payload
        )
        out, stolen, rc = _run(body, **{CF_TOKEN_VAR: FAKE_CF_TOKEN})
        assert stolen is not None, (
            f"branch {label!r}: the pre-fix body did NOT execute the payload, "
            f"so this control cannot support the inertness test above. "
            f"Output: {out}"
        )
        # WHAT was stolen, not merely that something was written: a sentinel
        # written from an unset variable would score identically.
        assert FAKE_CF_TOKEN in stolen, (
            f"branch {label!r}: the payload ran but reached nothing — the "
            f"sentinel holds {stolen!r}, expected the {CF_TOKEN_VAR} value"
        )
        # The residue is a LEGAL domain and the step succeeds — which is why no
        # triage heuristic that looks for a red step would ever have found this.
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, (
            f"branch {label!r}: expected the payload to leave a legal-looking "
            f"value behind. Output: {out}"
        )
        assert rc == 0, (
            f"branch {label!r}: expected the exploited pre-fix run to exit 0 — "
            f"the whole point is that it looks clean. rc={rc}. Output: {out}"
        )


def test_the_step_fails_closed_when_the_mapping_is_missing():
    """An unmapped or misnamed env: var must stop the step, not shift the call.

    Ledger L214/L220: swapping a quoted interpolation for a bare `$env:X` is not
    a pure substitution. An UNSET variable renders as no argument at all, so
    `-Account` binds one parameter to the left and the diagnosis arrives as a
    call-signature error; an EMPTY one binds, and the zone create would run
    against an empty -Domain. `IsNullOrWhiteSpace` catches both, and it is only
    load-bearing in states production cannot reach — `domain` is
    `required: true`, so a real dispatch cannot produce them and a deleted
    `env:` mapping can. Hence its own test.
    """
    step = _step()
    _assert_wiring(step)
    for label, value in BRANCHES.items():
        body = _render(step["run"], value)
        for case, overrides in (
            ("unset", {}),
            ("empty", {ENV_VAR: ""}),
            ("blank", {ENV_VAR: "   "}),
        ):
            out, stolen, rc = _run(body, **overrides)
            assert rc == 1, (
                f"branch {label!r} case {case!r} exited {rc}, expected 1: {out}"
            )
            # Asserting on the output, not only the code: a harness that cannot
            # start also exits non-zero, and this must be distinguishable from
            # the step refusing to run (CLAUDE.md's exit-code rule).
            assert f"::error::{ENV_VAR} is empty" in out, (
                f"branch {label!r} case {case!r} exited 1 without naming "
                f"{ENV_VAR} as the cause, so the failure is indistinguishable "
                f"from a broken harness: {out}"
            )
            assert "CALLED Domain=" not in out, (
                f"branch {label!r} case {case!r} called the script anyway after "
                f"reporting the empty mapping: {out}"
            )
            assert stolen is None, f"branch {label!r} case {case!r} wrote the sentinel: {out}"


def test_without_the_guard_a_missing_mapping_is_a_silent_success():
    """Positive control for the fail-closed test: prove the guard is load-bearing.

    Without it, the assertions above prove only that a step which exits 1 exits
    1. This runs the SAME body with the emptiness check stripped out and the
    callee's real `Mandatory` attributes in place — the configuration a reader
    would assume is already safe, because "the parameter is Mandatory" reads as
    a defence.

    It is not. An unset `$env:X` vanishes as an argument entirely, the inner
    pwsh reports a call-signature problem for a value problem, and
    `$ErrorActionPreference = 'Stop'` does not propagate a native command's exit
    code — so the step goes GREEN having never run the script.
    """
    body = _step()["run"]
    for label, value in BRANCHES.items():
        stripped = _strip_guard(_render(body, value))
        out, _, rc = _run(stripped, stub=MANDATORY_STUB)  # IN_DOMAIN unset
        assert rc == 0, (
            f"branch {label!r}: expected the UNGUARDED body to exit 0 with "
            f"{ENV_VAR} unset — if it now fails on its own, the emptiness check "
            f"is no longer what stands between a deleted mapping and a silent "
            f"success, and this control is measuring something else. rc={rc}: "
            f"{out}"
        )
        assert "CALLED Domain=[" not in out, (
            f"branch {label!r}: the script was invoked despite an empty "
            f"-Domain, so the arity shift this control is about did not "
            f"happen: {out}"
        )


def _strip_guard(body: str) -> str:
    """Remove the emptiness check from the step body, asserting it was there.

    Anchored on the smallest span that is unique to the guard, and the
    occurrence count is asserted BEFORE substituting (ledger L47): an anchor
    that stopped matching must fail loudly rather than silently leave the body
    unchanged and score the control as a pass.
    """
    anchor = f"if ([string]::IsNullOrWhiteSpace($env:{ENV_VAR}))"
    assert body.count(anchor) == 1, (
        f"expected exactly one emptiness guard to strip, found "
        f"{body.count(anchor)} — this control would otherwise test an "
        f"unmodified body. Body: {body!r}"
    )
    start = body.index(anchor)
    end = body.index("}", body.index("exit 1", start)) + 1
    stripped = body[:start] + body[end:]
    assert f"IsNullOrWhiteSpace($env:{ENV_VAR})" not in stripped, (
        f"the guard survived the strip, so the control is measuring the guarded "
        f"body. Stripped: {stripped!r}"
    )
    # The call under test must still be there — a strip that removed it would
    # also produce "no CALLED line" and pass for the wrong reason.
    assert CALLEE in stripped, (
        f"the strip removed the invocation itself, so the control proves "
        f"nothing. Stripped: {stripped!r}"
    )
    return stripped


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed in this environment; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
