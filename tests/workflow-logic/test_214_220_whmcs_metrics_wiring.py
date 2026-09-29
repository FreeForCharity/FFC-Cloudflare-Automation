"""Unit tests for the WHMCS read-only metrics family (#1080 lane 27, #1422).

214, 215, 216, 217 and 220 are structural clones: one `whmcs-prod-read` job
each, one pwsh body each, the same `whmcs-secrets-from-kv` step above it, the
same `$out = "<interpolation>"` assignment inside it, and the same
`path: ${{ inputs.output_file }}` upload below it. Seven free-text dispatch
inputs across the five now arrive through step-level `env:` -- `output_file` in
all five, `charity_gids` in 216 and 220, `throttle_ms` in 217.

WHY ONE MODULE FOR FIVE WORKFLOWS, AND WHAT THAT BUYS
    Splitting them would have produced five near-identical modules and, more to
    the point, five INDEPENDENT copies of the path-guard set. Here
    `test_the_five_lanes_carry_byte_identical_guard_conditions` asserts the five
    conditions are identical across all five bodies, so editing one lane's guard
    is a failure rather than a drift nobody sees. That is 202's coupling and
    #1361's `test_all_three_bodies_carry_the_same_guard_conditions` extended to
    this family, which is what #1380 AC4 asks for.

    The assertion is over CONDITIONS, not whole guard blocks: each `throw`
    message names its own upload step, and 217's is `Upload survey artifact
    (aggregate only)` where the rest are `Upload metrics artifact (aggregate
    only)`. Coupling the messages too would mean renaming a step to satisfy a
    test, which is the tail wagging the dog.

TWO CONSUMERS, WHICH IS THE WHOLE REASON THE PATH GUARDS EXIST
    The body is not the only reader of `output_file`. The upload step reads the
    same input, RAW, in its `path:` -- and a `path:` is an `@actions/glob`
    SELECTOR, not a filename (ledger L298). Moving the value into `env:` closes
    the body's door and leaves the selector's open, which is exactly what #1422
    found on 213 and 218 after both were scored "burned down". So this lane
    ships six conditions per lane -- blank, newline, glob, rooted/PSDrive, `~`,
    `..` -- and `test_the_newline_guard_precedes_the_anchored_ones` pins the
    ordering that makes the anchored three sound: `^` anchors at the start of
    the STRING, so over a multi-line value they examine only its first line.

THREE PAYLOAD SHAPES, ONE PER QUOTING CONTEXT
    Measured on pwsh 7.4.6 against bodies derived from what ships now (ledger
    L47), never from a hand-written "before":

        $out = "${{ inputs.output_file }}"      DOUBLE-quoted, so `$( )` expands
                                                where it stands -- no breakout.
                                                `$null =` swallows the payload's
                                                output so `$out` keeps a legal
                                                path and the log reads ordinary.
        -CharityGids '${{ ... }}'               SINGLE-quoted and TRAILING, so a
          (216, 220)                            breakout is needed and is all
                                                that is needed. The legitimate
                                                call completes BEFORE the
                                                payload runs, so `$LASTEXITCODE`
                                                is the legitimate call's.
        -ThrottleMs ([int]'${{ ... }}')         SINGLE-quoted inside a CAST. The
          (217)                                 cast is not a guard:
                                                `150'); <payload>; ([int]'1`
                                                closes the quote and the
                                                sub-expression and reopens both.

BLANK HANDLING SPLITS BY CONSUMER COUNT, NOT BY TASTE
    `output_file` FAILS CLOSED in all five: a default supplied only in the body
    would write the artifact to the substituted path while the upload still
    looked at the blank one, turning a clear failure into an
    `if-no-files-found: error` two steps later that names neither cause (L254's
    exception, as 201/208/218 already record).

    `charity_gids` and `throttle_ms` take a GATED APPEND, because each callee
    declares the same default the dispatch form declares, so omitting the
    argument yields it from ONE place instead of two that can drift. An
    ordinary dispatch never exercises an in-body fill -- GitHub supplies the
    declared default itself -- which is what makes the drift invisible.

THE ONE BEHAVIOURAL CHANGE, PINNED RATHER THAN ASSERTED IN PROSE
    217 shipped `-ThrottleMs ([int]'<interpolation>')`. Dropping the cast moves
    a non-numeric value's refusal from the body to the callee's
    `[ValidateRange(0, 5000)] [int]$ThrottleMs` binder.
    `test_a_non_numeric_throttle_ms_is_refused_by_the_callees_binder` and
    `test_the_pre_fix_body_refused_a_non_numeric_throttle_ms_too` measure both
    halves, so "equivalent" is a measurement and not an argument from symmetry
    (ledger L260 -- attribution is not evidence).
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

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"


def _load(module_name: str, filename: str):
    """Import a checker by path, registering it before executing it.

    `sys.modules[spec.name] = mod` is not hygiene here: both checkers define
    `@dataclass` types, and `dataclasses` resolves a field's annotation through
    `sys.modules[cls.__module__]`. Skip the registration and the decorator dies
    with `AttributeError: 'NoneType' object has no attribute '__dict__'` —
    an error about module registration, raised from the dataclass machinery,
    naming neither. Measured while writing this module.
    """
    path = _SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"cannot import {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


interp_guard = _load("interp_guard_lane27", "check-workflow-input-interpolation.py")
path_guard = _load("path_guard_lane27", "check-workflow-artifact-path-input-guards.py")


class Lane:
    """One of the five clones, and everything that differs between them."""

    def __init__(
        self,
        workflow: str,
        job: str,
        step: str,
        callee: str,
        upload_step: str,
        legal_out: str,
        extra: tuple | None = None,
    ):
        self.workflow = workflow
        self.job = job
        self.step = step
        self.callee = callee
        self.upload_step = upload_step
        self.legal_out = legal_out
        # (env var, dispatch input, callee parameter, legal value, stub default)
        self.extra = extra

    @property
    def number(self) -> str:
        return self.workflow.split("-", 1)[0]

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<lane {self.number}>"


LANES = (
    Lane(
        "214-whmcs-clients-metrics.yml",
        "clients_metrics",
        "Aggregate client metrics",
        "whmcs-clients-metrics.ps1",
        "Upload metrics artifact (aggregate only)",
        "artifacts/whmcs/whmcs_clients_metrics.json",
    ),
    Lane(
        "215-whmcs-nonprofit-clients-metrics.yml",
        "nonprofit_clients_metrics",
        "Aggregate nonprofit client metrics",
        "whmcs-nonprofit-clients-metrics.ps1",
        "Upload metrics artifact (aggregate only)",
        "artifacts/whmcs/whmcs_nonprofit_clients_metrics.json",
    ),
    Lane(
        "216-whmcs-activity-metrics.yml",
        "activity_metrics",
        "Build activity matrix",
        "whmcs-activity-metrics.ps1",
        "Upload metrics artifact (aggregate only)",
        "artifacts/whmcs/whmcs_activity_metrics.json",
        extra=("IN_CHARITY_GIDS", "charity_gids", "CharityGids", "2,5", "2,5,6,7,8"),
    ),
    Lane(
        "217-whmcs-client-fields-survey.yml",
        "client_fields_survey",
        "Survey client classification fields",
        "whmcs-client-fields-survey.ps1",
        "Upload survey artifact (aggregate only)",
        "artifacts/whmcs/whmcs_client_fields_survey.json",
        extra=("IN_THROTTLE_MS", "throttle_ms", "ThrottleMs", "200", "150"),
    ),
    Lane(
        "220-whmcs-served-metrics.yml",
        "served_metrics",
        "Build served-per-year matrix",
        "whmcs-served-metrics.ps1",
        "Upload metrics artifact (aggregate only)",
        "artifacts/whmcs/whmcs_served_metrics.json",
        extra=("IN_CHARITY_GIDS", "charity_gids", "CharityGids", "2,5", "2,5,6,7,8"),
    ),
)

BY_NUMBER = {lane.number: lane for lane in LANES}

# The five conditions that must be byte-identical across all five bodies. The
# BLANK condition is deliberately in this tuple too: it is the only one whose
# `throw` differs merely by step name, so including it makes the coupling cover
# the whole guard set rather than the interesting half of it.
GUARD_CONDITIONS = (
    "if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {",
    r"if ($env:IN_OUTPUT_FILE -match '[\r\n]') {",
    r"if ($env:IN_OUTPUT_FILE -match '[*?\[\]]') {",
    r"if ($env:IN_OUTPUT_FILE -match '^([A-Za-z]+:|[\\/])') {",
    "if ($env:IN_OUTPUT_FILE -match '^~') {",
    r"if (($env:IN_OUTPUT_FILE -split '[\\/]') -contains '..') {",
)

# The anchored guards, which are sound only because the newline guard runs
# first. Indices into GUARD_CONDITIONS.
NEWLINE_CONDITION = GUARD_CONDITIONS[1]
GLOB_CONDITION = GUARD_CONDITIONS[2]
ANCHORED_CONDITIONS = (GUARD_CONDITIONS[3], GUARD_CONDITIONS[4])

# The dependency fact #1380 asks each lane to record beside its glob guard. It
# is a fact about a package this repo does not depend on, so a test reading
# `node_modules/@actions/glob/...` would SKIP in CI and leave the tree in the
# "asserted nowhere" state the issue was filed about. What is assertable without
# a dependency is that the fact is written where the code relying on it lives.
GLOB_RATIONALE = ("nobrace: true", "noext: true", "#1380")

SENTINEL = "STOLEN-LANE27.txt"

# Removed unless a test supplies them, so an inherited variable cannot satisfy
# an assertion the workflow is supposed to (ledger L199).
CONTROLLED_VARS = ("IN_OUTPUT_FILE", "IN_CHARITY_GIDS", "IN_THROTTLE_MS")

# Stand-ins for what `whmcs-secrets-from-kv` exports to GITHUB_ENV. Always set:
# they are what a payload is supposed to be able to reach, so clearing them
# would make the pre-fix control look harmless while the payload had in fact
# executed perfectly. The assertions read the sentinel's CONTENTS for that
# reason, never merely its existence.
DECOY_CREDENTIALS = {
    "WHMCS_API_SECRET": "whmcs-secret-placeholder-not-a-real-credential",
    "WHMCS_APIM_SUBSCRIPTION_KEY": "apim-key-placeholder-not-a-real-credential",
}

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The epilogue is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that
# omits it pins the wrong exit code and then vouches for it.
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _payload(marker: str, credential: str, tail: str) -> str:
    """A payload that steals `credential` to the sentinel and then looks ordinary."""
    return (
        "$null = Set-Content -Path " + SENTINEL + ' -Value "stolen=$env:' + credential + '"; '
        "Write-Host '" + marker + "'; " + tail
    )


def _workflow(lane: Lane) -> dict:
    return load_workflow(lane.workflow)


def _step(lane: Lane) -> dict:
    return find_step(_workflow(lane), lane.job, lane.step)


def _body(lane: Lane) -> str:
    body = _step(lane).get("run")
    assert isinstance(body, str), (
        f"{lane.workflow} step {lane.step!r} has no `run:` body ({body!r})"
    )
    return body


def _step_env(lane: Lane) -> dict:
    env = _step(lane).get("env")
    assert isinstance(env, dict), (
        f"{lane.workflow} step {lane.step!r} has no `env:` mapping ({env!r})"
    )
    return env


def _upload_path(lane: Lane) -> str:
    """The `path:` of this lane's upload step, asserted to exist."""
    jobs = _workflow(lane).get("jobs") or {}
    assert lane.job in jobs, f"{lane.workflow} has no job {lane.job!r}"
    steps = jobs[lane.job].get("steps") or []
    matches = [
        s
        for s in steps
        if isinstance(s, dict) and (s.get("name") or "") == lane.upload_step
    ]
    assert len(matches) == 1, (
        f"{lane.workflow} has {len(matches)} steps named {lane.upload_step!r}, "
        f"expected exactly one — names: "
        f"{[s.get('name') for s in steps if isinstance(s, dict)]}"
    )
    with_block = matches[0].get("with") or {}
    path = with_block.get("path")
    assert isinstance(path, str), f"{lane.workflow}'s upload step has no path: ({path!r})"
    return path


def _declared_inputs(lane: Lane) -> dict:
    """`on.workflow_dispatch.inputs`, via the checker's own Norway-safe reader.

    Borrowing `path_guard.dispatch_inputs` rather than re-deriving the
    `True`-or-`"on"` lookup means this module and the checker cannot drift about
    what counts as a declared input.
    """
    inputs = path_guard.dispatch_inputs(_workflow(lane))
    assert inputs, f"{lane.workflow} declares no dispatch inputs"
    return inputs


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input a body reaches through `${{ }}`.

    The CHECKER's own two patterns, not a substring test of this module's
    devising, so a spelling the checker recognises cannot slip past here.
    """
    found = set()
    for match in interp_guard._EXPRESSION.finditer(body):
        found.update(interp_guard._INPUT_REF.findall(match.group(1)))
    return found


# --------------------------------------------------------------------------
# Wiring — pure YAML, runs on a host with no pwsh
# --------------------------------------------------------------------------


def test_every_lane_moves_its_free_text_inputs_into_env():
    for lane in LANES:
        env = _step_env(lane)
        assert env.get("IN_OUTPUT_FILE") == "${{ inputs.output_file }}", (
            f"{lane.workflow}: expected IN_OUTPUT_FILE to carry output_file; "
            f"env is {env!r}"
        )
        if lane.extra:
            var, input_name, _, _, _ = lane.extra
            assert env.get(var) == "${{ inputs." + input_name + " }}", (
                f"{lane.workflow}: expected {var} to carry {input_name}; env is {env!r}"
            )


def test_no_lane_interpolates_a_free_text_input_into_its_body_any_more():
    """The #1080 completion test, asked of the body rather than of the freeze."""
    for lane in LANES:
        free_text = path_guard.free_text_inputs(_workflow(lane))
        assert "output_file" in free_text, (
            f"{lane.workflow}: output_file is no longer a free-text input, so this "
            f"module is asserting something the checker no longer judges — if the "
            f"type was deliberately narrowed, retire these cases in the same PR"
        )
        leaked = _interpolated_inputs(_body(lane)) & free_text
        assert not leaked, (
            f"{lane.workflow}: free-text input(s) {sorted(leaked)} are still "
            f"substituted into the step body as TEXT"
        )


def test_the_upload_step_still_reads_the_raw_input_which_is_why_the_guards_exist():
    """The second consumer is unchanged, deliberately.

    Nothing here "fixes" the `path:`. It stays raw because an artifact selector
    cannot read an env var, and the remedy is to constrain the VALUE before the
    upload runs. A future lane that quietly changed this would make the path
    guards look redundant, so the shape they defend is pinned.
    """
    for lane in LANES:
        assert _upload_path(lane) == "${{ inputs.output_file }}", (
            f"{lane.workflow}: the upload step's path: is "
            f"{_upload_path(lane)!r}, not the raw input the guards are written "
            f"for — if this changed on purpose, revisit the guard rationale too"
        )


def test_the_five_lanes_carry_byte_identical_guard_conditions():
    """The coupling: one rule in five places, not five rules that resemble each other."""
    for condition in GUARD_CONDITIONS:
        for lane in LANES:
            body = _body(lane)
            assert body.count(condition) == 1, (
                f"{lane.workflow}: expected exactly one occurrence of the guard "
                f"condition {condition!r}, found {body.count(condition)}. The five "
                f"lanes are coupled on purpose — change them together or not at all."
            )


def test_the_newline_guard_precedes_the_anchored_ones():
    """`^` is start-of-STRING, so the anchored guards only ever see line one."""
    for lane in LANES:
        body = _body(lane)
        newline_at = body.index(NEWLINE_CONDITION)
        for anchored in ANCHORED_CONDITIONS:
            assert newline_at < body.index(anchored), (
                f"{lane.workflow}: the newline guard must run BEFORE {anchored!r}. "
                f"`^` anchors at the start of the string, not of each line, so a "
                f"multi-line value walks a later anchored guard past its payload."
            )


def test_the_glob_rationale_sits_between_the_newline_and_glob_conditions():
    """#1380 AC1, in the form that needs no dependency and therefore cannot skip.

    The fact is about `@actions/glob`, which this repo does not depend on. A
    test reading `node_modules/` would SKIP in CI and leave the tree in exactly
    the "asserted nowhere" state #1380 was filed about, while reading in a diff
    as though the fact were pinned. What is assertable is that the fact is
    written where the code relying on it lives — so this checks the REGION, not
    merely that the strings appear somewhere in the file.
    """
    for lane in LANES:
        body = _body(lane)
        region = body[body.index(NEWLINE_CONDITION) : body.index(GLOB_CONDITION)]
        for token in GLOB_RATIONALE:
            assert token in region, (
                f"{lane.workflow}: {token!r} is not in the text between the newline "
                f"guard and the glob guard. The glob set stops at `* ? [ ]` because "
                f"`@actions/glob` pins nobrace/noext; a reader who cannot see that "
                f"beside the guard sees an arbitrary character list (#1380)."
            )


def test_the_secondary_inputs_take_a_gated_append_not_a_default_fill():
    for lane in LANES:
        if not lane.extra:
            continue
        var, _, param, _, _ = lane.extra
        body = _body(lane)
        gate = (
            "if (-not [string]::IsNullOrWhiteSpace($env:" + var + ")) "
            "{ $cliArgs += @('-" + param + "', $env:" + var + ") }"
        )
        assert gate in body, (
            f"{lane.workflow}: expected the gated append {gate!r}. A default "
            f"filled in the body would duplicate the callee's own default, and "
            f"an ordinary dispatch never exercises an in-body fill, so the two "
            f"would drift unnoticed."
        )
        assert "@cliArgs" in body, (
            f"{lane.workflow}: the arguments are gated but not splatted, so an "
            f"empty value could still vanish as an argument (ledger L214)"
        )


def test_the_in_body_defaults_are_not_duplicated_from_the_dispatch_form():
    """A gated append must NOT also hard-code the declared default.

    The mistake this forbids is subtle and reads as belt-and-braces: writing
    `if (blank) { $env:X = '2,5,6,7,8' }` next to the gate gives the value two
    homes, and the copy here is the one no ordinary dispatch ever exercises.
    """
    for lane in LANES:
        if not lane.extra:
            continue
        var, input_name, _, _, declared = lane.extra
        spec = _declared_inputs(lane).get(input_name) or {}
        assert str(spec.get("default")) == declared, (
            f"{lane.workflow}: {input_name}'s declared default is "
            f"{spec.get('default')!r}, but this module expects {declared!r} — "
            f"update both, or the cases below measure the wrong value"
        )
        fill = "$env:" + var + " = '" + declared + "'"
        assert fill not in _body(lane), (
            f"{lane.workflow}: the body hard-codes {input_name}'s declared "
            f"default alongside the gate. One default, in the callee."
        )


def test_neither_freeze_lists_these_lanes_any_more():
    """Both halves of the #1422 coupling, asserted together.

    Leaving one freeze and staying in the other is the exact defect #1422 was
    filed about, so checking only one of them here would reproduce it.
    """
    for lane in LANES:
        assert lane.workflow not in interp_guard.KNOWN_UNGUARDED, (
            f"{lane.workflow} is still frozen in check-workflow-input-interpolation.py "
            f"while its inputs travel in env: — a stale entry, which that checker "
            f"exits 1 on"
        )
        assert lane.workflow not in path_guard.KNOWN_UNGUARDED, (
            f"{lane.workflow} is still frozen in "
            f"check-workflow-artifact-path-input-guards.py while its artifact path is "
            f"guarded — the two freezes must be left in the same PR (#1422)"
        )


def test_the_artifact_path_checker_credits_every_lane():
    """Agreement with the checker, not just with this module's own reading."""
    sites, unreadable = path_guard.scan()
    assert not unreadable, f"workflows the checker could not read: {unreadable}"
    offenders = {
        site.workflow for site in sites if site.workflow in {lane.workflow for lane in LANES}
    }
    assert not offenders, (
        f"the artifact-path checker still reports unguarded sites in {sorted(offenders)}: "
        f"{[s.describe() for s in sites if s.workflow in offenders]}"
    )


# --------------------------------------------------------------------------
# Pre-fix controls and behaviour — these spawn pwsh
# --------------------------------------------------------------------------


def _stub(lane: Lane) -> str:
    """A stand-in for the callee that records what it was BOUND.

    Binding is the only discriminator that works: pwsh echoes offending source
    back in a binder error, so a marker search over stdout matches the payload's
    text on a run that executed nothing.
    """
    params = [
        "    [Parameter()]\n    [string]$ApiUrl,",
        "    [Parameter()]\n    [string]$OutputFile = '" + lane.legal_out + "',",
    ]
    extra_echo = ""
    if lane.extra:
        _, _, param, _, declared = lane.extra
        if param == "ThrottleMs":
            params.append(
                "    [Parameter()]\n    [ValidateRange(0, 5000)]\n"
                "    [int]$ThrottleMs = " + declared + ","
            )
        else:
            params.append(
                "    [Parameter()]\n    [string]$" + param + " = '" + declared + "',"
            )
        extra_echo = " " + param + "=[$" + param + "]"
    body = "param(\n" + "\n\n".join(p.rstrip(",") + "," for p in params).rstrip(",") + "\n)\n"
    body += (
        '[Console]::Error.WriteLine("CALLED Out=[$OutputFile]' + extra_echo + '")\n'
        "if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {\n"
        "    $dir = Split-Path -Parent $OutputFile\n"
        "    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }\n"
        '    Set-Content -Path $OutputFile -Value \'{"ok":true}\'\n'
        "}\n"
        "exit 0\n"
    )
    return body


def _strip_guards(lane: Lane, body: str) -> str:
    """Remove the six guards, asserting each anchor was present first (L47).

    An anchor that stopped matching must fail loudly rather than silently leave
    the body unchanged and score the control as a pass. Every lookup is asserted
    rather than left to a bare `str.index`: a ValueError is not an
    AssertionError, so the module runner would not catch it — it would abort the
    whole roster and every case after this one would report no outcome at all,
    which a reviewer counting FAIL lines scores as passing (ledger L194).
    """
    for condition in GUARD_CONDITIONS:
        assert body.count(condition) == 1, (
            f"{lane.workflow}: expected exactly one {condition!r} to strip, found "
            f"{body.count(condition)} — this control would otherwise measure a "
            f"body that is still guarded, and its payload would be REFUSED rather "
            f"than executed, proving the opposite of what it claims."
        )
        start = body.index(condition)
        assert "throw" in body[start:], (
            f"{lane.workflow}: the guard at {condition!r} no longer contains a "
            f"`throw`, so this control cannot locate its end"
        )
        end = body.index("}", body.index("throw", start)) + 1
        body = body[:start] + body[end:]
    for condition in GUARD_CONDITIONS:
        assert condition not in body, (
            f"{lane.workflow}: a guard survived the strip, so the control is "
            f"measuring the guarded body"
        )
    assert lane.callee in body, (
        f"{lane.workflow}: the strip removed the invocation itself, so the control "
        f"proves nothing about what the callee was handed"
    )
    return body


def _pre_fix_body(lane: Lane, out_value: str, extra_value: str | None = None) -> str:
    """The body as it SHIPPED, derived from what ships now (ledger L47).

    Never a hand-written "before": a literal copy drifts the moment the real
    body changes, and then vouches for a body nobody runs.
    """
    body = _strip_guards(lane, _body(lane))

    shipped_out = "$out = $env:IN_OUTPUT_FILE"
    assert body.count(shipped_out) == 1, (
        f"{lane.workflow}: expected exactly one {shipped_out!r}, found "
        f"{body.count(shipped_out)}"
    )
    body = body.replace(shipped_out, '$out = "' + out_value + '"', 1)

    if lane.extra:
        var, _, param, _, _ = lane.extra
        gate = (
            "if (-not [string]::IsNullOrWhiteSpace($env:" + var + ")) "
            "{ $cliArgs += @('-" + param + "', $env:" + var + ") }"
        )
        build = "$cliArgs = @('-ApiUrl', $env:WHMCS_API_URL, '-OutputFile', $out)"
        splat = ".\\scripts\\" + lane.callee + " @cliArgs"
        for anchor in (gate, build, splat):
            assert body.count(anchor) == 1, (
                f"{lane.workflow}: expected exactly one {anchor!r} to rewrite, "
                f"found {body.count(anchor)}"
            )
        assert extra_value is not None, (
            f"{lane.workflow} has a secondary input, so the pre-fix control must "
            f"say what to substitute into it"
        )
        # The shipped spelling: 217 wrapped its single-quoted value in a cast,
        # the other two did not. Reproducing the cast matters — it is what makes
        # 217's payload need to close a sub-expression as well as a quote.
        if param == "ThrottleMs":
            argument = "-" + param + " ([int]'" + extra_value + "')"
        else:
            argument = "-" + param + " '" + extra_value + "'"
        direct = (
            ".\\scripts\\" + lane.callee + " -ApiUrl $env:WHMCS_API_URL "
            "-OutputFile $out " + argument
        )
        body = body.replace(gate + "\n", "", 1)
        body = body.replace(build + "\n", "", 1)
        body = body.replace(splat, direct, 1)
        assert "@cliArgs" not in body, (
            f"{lane.workflow}: a splat survived the pre-fix rewrite, so the body "
            f"references an array it no longer builds"
        )
    return body


def _run(lane: Lane, body: str, **env_overrides):
    """Run a body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an UNSET variable scores the
    same as one written from the live credential, and which credential the
    payload reached is the whole claim.

    `stdin=DEVNULL` is load-bearing rather than hygiene. An unsatisfied
    parameter makes PowerShell PROMPT, and on an interactive stdin the call
    blocks forever; a runner's stdin is not a terminal either, so it is also the
    faithful shape.

    A variable is REMOVED when its override is None rather than set to "": the
    two blank forms differ and conflating them is what ledger L291 is about.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / lane.callee).write_text(_stub(lane), encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8", newline="\n"
        )
        concrete = {k: v for k, v in env_overrides.items() if v is not None}
        env = child_env(**DECOY_CREDENTIALS, **concrete)
        for var in CONTROLLED_VARS:
            if var not in concrete:
                env.pop(var, None)
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
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _bound(output: str) -> str:
    """The stub's `CALLED …` line, or "" when the callee never ran."""
    for line in output.splitlines():
        if line.startswith("CALLED "):
            return line
    return ""


def test_the_pre_fix_output_file_payload_ran_and_the_step_still_exited_zero():
    """The double-quoted assignment: `$( )` expands, no breakout needed."""
    for lane in LANES:
        payload = (
            "$(" + _payload("INJECTED-" + lane.number, "WHMCS_API_SECRET",
                            "'" + lane.legal_out + "'") + ")"
        )
        extra = lane.extra[3] if lane.extra else None
        out, stolen, rc = _run(lane, _pre_fix_body(lane, payload, extra))
        assert stolen is not None, (
            f"{lane.workflow}: the pre-fix payload did not run, so this control "
            f"proves nothing. Output: {out!r}"
        )
        assert DECOY_CREDENTIALS["WHMCS_API_SECRET"] in stolen, (
            f"{lane.workflow}: the payload ran but reached no credential "
            f"({stolen!r}) — the control would pass while proving the harmless "
            f"half of the claim"
        )
        assert rc == 0, (
            f"{lane.workflow}: the injected run should be indistinguishable from an "
            f"ordinary one; rc={rc}. Output: {out!r}"
        )
        assert "Out=[" + lane.legal_out + "]" in _bound(out), (
            f"{lane.workflow}: the legitimate call should still have been made with "
            f"the legitimate path. Bound: {_bound(out)!r}"
        )


def test_the_shipped_body_binds_the_same_payload_as_data():
    """The remedy: the same string arrives as ONE literal filename, unexecuted.

    The discriminator is what the callee was BOUND, never a marker search over
    stdout. The payload's own text necessarily appears in that binding — it is
    the filename now — so `"INJECTED-…" not in output` is false on a perfectly
    remedied run, and asserting it fails in the alarming direction. (Written
    that way first, and it reported a pass as a failure.) pwsh also echoes
    offending source back in binder errors, so the same substring test matches
    on a run that executed nothing at all. Binding is the only reading that
    separates the two.

    The exit code is deliberately not asserted: the value is a legal argument
    now, so whether the STUB can create a file with that name is a property of
    the host filesystem rather than of the remedy.
    """
    for lane in LANES:
        payload = (
            "$(" + _payload("INJECTED-" + lane.number, "WHMCS_API_SECRET",
                            "'" + lane.legal_out + "'") + ")"
        )
        kwargs = {"IN_OUTPUT_FILE": payload}
        expected = "Out=[" + payload + "]"
        if lane.extra:
            var, _, param, legal, _ = lane.extra
            kwargs[var] = legal
            expected += " " + param + "=[" + legal + "]"
        out, stolen, _rc = _run(lane, _body(lane), **kwargs)
        assert stolen is None, (
            f"{lane.workflow}: the payload executed through the env mapping "
            f"({stolen!r}) — the remedy does not hold"
        )
        assert _bound(out) == "CALLED " + expected, (
            f"{lane.workflow}: the payload should reach the callee as exactly one "
            f"verbatim argument. Expected {('CALLED ' + expected)!r}, bound "
            f"{_bound(out)!r}"
        )


def test_the_pre_fix_secondary_payload_ran_on_the_trailing_argument():
    """216/220's single-quoted breakout, and 217's breakout out of a cast."""
    for lane in LANES:
        if not lane.extra:
            continue
        _, _, param, legal, _ = lane.extra
        stolen_line = _payload(
            "INJECTED-" + lane.number + "-EXTRA", "WHMCS_APIM_SUBSCRIPTION_KEY", "#"
        )
        if param == "ThrottleMs":
            # Close the quote AND the sub-expression, then reopen both so the
            # line still parses — the cast is not a guard.
            payload = legal + "'); " + stolen_line
        else:
            payload = legal + "'; " + stolen_line
        out, stolen, rc = _run(lane, _pre_fix_body(lane, lane.legal_out, payload))
        assert stolen is not None, (
            f"{lane.workflow}: the secondary payload did not run, so this control "
            f"proves nothing. Output: {out!r}"
        )
        assert DECOY_CREDENTIALS["WHMCS_APIM_SUBSCRIPTION_KEY"] in stolen, (
            f"{lane.workflow}: the payload ran but reached no credential ({stolen!r})"
        )
        assert rc == 0, (
            f"{lane.workflow}: a trailing-argument payload lets the legitimate call "
            f"finish first, so the step should still exit 0; rc={rc}. Output: {out!r}"
        )


def test_the_shipped_body_binds_the_secondary_payload_as_data():
    for lane in LANES:
        if not lane.extra:
            continue
        var, _, param, legal, _ = lane.extra
        payload = legal + "'; " + _payload(
            "INJECTED-" + lane.number + "-EXTRA", "WHMCS_APIM_SUBSCRIPTION_KEY", "#"
        )
        out, stolen, rc = _run(
            lane, _body(lane), IN_OUTPUT_FILE=lane.legal_out, **{var: payload}
        )
        assert stolen is None, (
            f"{lane.workflow}: the secondary payload executed through the env "
            f"mapping ({stolen!r}) — the remedy does not hold"
        )
        if param == "ThrottleMs":
            # A quote-breakout string is not an integer, so the binder refuses
            # it. That is the cast's job moving to the callee, measured.
            assert rc != 0, (
                f"{lane.workflow}: a non-numeric ThrottleMs should be refused; "
                f"rc={rc}. Output: {out!r}"
            )
        else:
            assert rc == 0, (
                f"{lane.workflow}: the payload is data now, so an ordinary run "
                f"should succeed; rc={rc}. Output: {out!r}"
            )
            assert param + "=[" + payload + "]" in _bound(out), (
                f"{lane.workflow}: the payload should have been bound verbatim as "
                f"one argument. Bound: {_bound(out)!r}"
            )


def test_the_shipped_body_passes_ordinary_values_through():
    for lane in LANES:
        kwargs = {"IN_OUTPUT_FILE": lane.legal_out}
        expected = "Out=[" + lane.legal_out + "]"
        if lane.extra:
            var, _, param, legal, _ = lane.extra
            kwargs[var] = legal
            expected += " " + param + "=[" + legal + "]"
        out, stolen, rc = _run(lane, _body(lane), **kwargs)
        assert rc == 0, f"{lane.workflow}: an ordinary run failed; rc={rc}. {out!r}"
        assert stolen is None, f"{lane.workflow}: an ordinary run wrote a sentinel"
        assert expected in _bound(out), (
            f"{lane.workflow}: expected the callee to bind {expected!r}. "
            f"Bound: {_bound(out)!r}"
        )


def test_a_blank_output_file_fails_closed_and_never_calls_the_callee():
    for lane in LANES:
        kwargs = {"IN_OUTPUT_FILE": "   "}
        if lane.extra:
            kwargs[lane.extra[0]] = lane.extra[3]
        out, _, rc = _run(lane, _body(lane), **kwargs)
        assert rc != 0, f"{lane.workflow}: a whitespace path should fail closed; rc={rc}"
        assert "output_file is blank" in out, (
            f"{lane.workflow}: the step exited non-zero without naming the cause, so "
            f"a real refusal cannot be told apart from a broken harness. "
            f"Output: {out!r}"
        )
        assert _bound(out) == "", (
            f"{lane.workflow}: failing closed means not calling the callee. "
            f"Bound: {_bound(out)!r}"
        )


def test_an_unset_output_file_fails_closed_too():
    """The other blank form, measured rather than assumed equal (L291)."""
    for lane in LANES:
        kwargs = {"IN_OUTPUT_FILE": None}
        if lane.extra:
            kwargs[lane.extra[0]] = lane.extra[3]
        out, _, rc = _run(lane, _body(lane), **kwargs)
        assert rc != 0, f"{lane.workflow}: an unset path should fail closed; rc={rc}"
        assert "output_file is blank" in out, (
            f"{lane.workflow}: exited non-zero without naming the cause. Output: {out!r}"
        )
        assert _bound(out) == "", f"{lane.workflow}: the callee ran. {_bound(out)!r}"


def test_each_path_guard_refuses_its_own_hazard_and_says_which_one():
    """One probe per hazard, minimal in exactly ONE property (ledger L306).

    A probe carrying several hazardous properties at once is refused by whichever
    guard is checked first, which certifies the guard it never exercised. So each
    value below is legal in every respect but one, and the assertion reads the
    firing guard's own message fragment rather than merely a non-zero exit.
    """
    probes = (
        ("artifacts/whmcs/out.json\nartifacts/whmcs/other.json", "carriage return or newline"),
        ("artifacts/whmcs/[m]sal_token_cache.json", "glob metacharacter"),
        ("/etc/passwd", "workspace-relative path"),
        ("Temp:/out.json", "workspace-relative path"),
        ("~/.azure/msal_token_cache.json", "must not begin with '~'"),
        ("artifacts/whmcs/../../.azure/out.json", "'..' segment"),
    )
    for lane in LANES:
        for value, fragment in probes:
            kwargs = {"IN_OUTPUT_FILE": value}
            if lane.extra:
                kwargs[lane.extra[0]] = lane.extra[3]
            out, _, rc = _run(lane, _body(lane), **kwargs)
            assert rc != 0, (
                f"{lane.workflow}: {value!r} was accepted; rc={rc}. Output: {out!r}"
            )
            assert fragment in out, (
                f"{lane.workflow}: {value!r} was refused, but not by the guard it "
                f"was written for — expected a message containing {fragment!r}. "
                f"A refusal for the wrong reason scores the hazard class as covered "
                f"when it is open (ledger L306). Output: {out!r}"
            )
            assert _bound(out) == "", (
                f"{lane.workflow}: the callee ran despite {value!r}. {_bound(out)!r}"
            )


def test_a_blank_secondary_input_falls_back_to_the_callees_declared_default():
    for lane in LANES:
        if not lane.extra:
            continue
        var, _, param, _, declared = lane.extra
        for blank in ("", "   ", None):
            out, _, rc = _run(
                lane, _body(lane), IN_OUTPUT_FILE=lane.legal_out, **{var: blank}
            )
            assert rc == 0, (
                f"{lane.workflow}: a blank {param} is a routine dispatch and should "
                f"succeed on the callee's default; rc={rc} for {blank!r}. {out!r}"
            )
            assert param + "=[" + declared + "]" in _bound(out), (
                f"{lane.workflow}: a blank {param} ({blank!r}) should leave the "
                f"argument off so the callee's own default applies. "
                f"Bound: {_bound(out)!r}"
            )


def test_a_non_numeric_throttle_ms_is_refused_by_the_callees_binder():
    """Where the dropped `[int]` cast went, measured on the shipped body."""
    lane = BY_NUMBER["217"]
    out, _, rc = _run(
        lane, _body(lane), IN_OUTPUT_FILE=lane.legal_out, IN_THROTTLE_MS="abc"
    )
    assert rc != 0, f"217: a non-numeric throttle_ms should be refused; rc={rc}. {out!r}"
    assert "ThrottleMs" in out, (
        f"217: the refusal does not name the parameter, so a caller cannot tell "
        f"which input was wrong. Output: {out!r}"
    )
    assert _bound(out) == "", f"217: the callee body ran anyway. {_bound(out)!r}"


def test_the_pre_fix_body_refused_a_non_numeric_throttle_ms_too():
    """The other half of "equivalent": the shipped body refused it as well.

    Without this, "the cast moved to the binder" is an argument from symmetry
    rather than a measurement (ledger L260).
    """
    lane = BY_NUMBER["217"]
    out, _, rc = _run(lane, _pre_fix_body(lane, lane.legal_out, "abc"))
    assert rc != 0, (
        f"217: the shipped body's `[int]` cast should have refused a non-numeric "
        f"value too; rc={rc}. Output: {out!r}"
    )
    assert _bound(out) == "", f"217: the callee ran in the pre-fix body. {_bound(out)!r}"


def test_an_out_of_range_throttle_ms_is_still_refused():
    """The `[ValidateRange(0, 5000)]` half, which the cast never provided."""
    lane = BY_NUMBER["217"]
    out, _, rc = _run(
        lane, _body(lane), IN_OUTPUT_FILE=lane.legal_out, IN_THROTTLE_MS="99999"
    )
    assert rc != 0, f"217: 99999 is outside the callee's range; rc={rc}. {out!r}"
    assert _bound(out) == "", f"217: the callee body ran anyway. {_bound(out)!r}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and checker-agreement
# cases are pure YAML and must run on a host without pwsh (#1182 — a
# whole-module `shutil.which` gate turns "could not run" into "everything
# passed"). Scoped to exactly the cases that call `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_output_file_payload_ran_and_the_step_still_exited_zero",
    "test_the_shipped_body_binds_the_same_payload_as_data",
    "test_the_pre_fix_secondary_payload_ran_on_the_trailing_argument",
    "test_the_shipped_body_binds_the_secondary_payload_as_data",
    "test_the_shipped_body_passes_ordinary_values_through",
    "test_a_blank_output_file_fails_closed_and_never_calls_the_callee",
    "test_an_unset_output_file_fails_closed_too",
    "test_each_path_guard_refuses_its_own_hazard_and_says_which_one",
    "test_a_blank_secondary_input_falls_back_to_the_callees_declared_default",
    "test_a_non_numeric_throttle_ms_is_refused_by_the_callees_binder",
    "test_the_pre_fix_body_refused_a_non_numeric_throttle_ms_too",
    "test_an_out_of_range_throttle_ms_is_still_refused",
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
