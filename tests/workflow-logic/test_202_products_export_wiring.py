"""Unit tests for 202's two dispatch-input call sites (#1080 lane 25).

202 exports the WHMCS product catalog and the client-products list to one CSV
artifact. Its two free-text output-path inputs used to be interpolated into the
one `whmcs-prod-read` pwsh body, each into a DOUBLE-quoted assignment; both now
arrive through step-level `env:` (IN_PRODUCTS_OUTPUT_FILE /
IN_CLIENT_PRODUCTS_OUTPUT_FILE).

TWO VALUES THROUGH ONE GUARD BODY — THE SHAPE THAT MAKES THIS LANE DIFFERENT
    Every earlier lane guarded a single path input, so its checks could be
    written inline. 202 is the first with TWO, and duplicated guards are how one
    value silently keeps a check the other gains: the six checks live in a
    `foreach ($spec in @(...))` over both (name, value) pairs, so coverage is
    structural rather than reviewed. Every behavioural case below is therefore
    run against BOTH inputs, and each asserts the refusal names the input the
    dispatcher actually typed — a loop that validated one value twice would pass
    a per-input count and fail that.

    `test_the_guard_loop_covers_both_inputs_and_only_them` pins the spec list
    itself, so a third input added to this step without a spec entry is a
    failure here rather than an unguarded path in production.

    EVERY LOOKUP IN THIS MODULE ASSERTS PRESENCE BEFORE INDEXING
    Not style. A `str.index` miss raises ValueError, which is not an
    AssertionError, so the module runner does not catch it and the roster
    ABORTS — the remaining cases report no outcome at all, and a reviewer
    counting FAIL lines scores that as passing (ledger L194). This module's own
    mutation run produced exactly that: deleting the tilde guard truncated the
    roster to 16 outcomes for 24 defined tests instead of naming one failure.

BOTH SITES WERE LIVE, AND NEITHER NEEDED A QUOTE BREAKOUT
    Measured on pwsh 7.4.6 against the body as it shipped, with decoy
    credentials in the environment and a stub callee. Both sites read
    `$x = "${{ inputs.… }}"`, double quoted, so `$( )` expands where it stands:

        products_output_file         `artifacts/whmcs/p.csv$(… Set-Content
                                     -Path SENTINEL …)` wrote the credential to
                                     a file. Set-Content emits nothing, so the
                                     path itself was unchanged: the callee bound
                                     `Products=[artifacts/whmcs/p.csv]` and the
                                     step exited **0**.
        client_products_output_file  identical at the second site, with the
                                     first path still binding its legitimate
                                     value — so the run reads as an ordinary
                                     export from every observable it has.

WHY BOTH INPUTS FAIL CLOSED RATHER THAN DEFAULT-FILLING
    Both declare defaults, so L254 would ordinarily say default-fill. Both have
    a SECOND consumer: the `Upload CSV Artifact` step interpolates each raw
    input into its `path:`, which is a newline-delimited list of glob patterns
    (`@actions/glob`), not a filename. A default supplied only in the body would
    write the CSVs to the substituted paths while the upload still looked at the
    blank ones — an `if-no-files-found: error` two steps later naming neither
    cause. `test_the_upload_step_still_consumes_both_inputs_raw` pins that
    premise, so the reasoning in the workflow's comments cannot outlive the
    shape it describes.

THE L214 ARGUMENT-SHIFT CLAIM DOES NOT HOLD HERE, AND IS PINNED AS NOT HOLDING
    202 binds both paths INLINE (`-ProductsOutputFile $productsOut`) rather than
    through a `$cliArgs` array, so the argument-shift hazard this workflow's own
    `api_url` comment describes looks like it should apply to them too. It does
    not: measured with the blank guard stripped and the input omitted, the body
    dies at `Split-Path -Parent` with `Cannot bind argument to parameter 'Path'
    because it is null`, two lines above the call, and never reaches it. The
    first draft of this lane wrote the L214 rationale into the workflow before
    measuring it — which is exactly what #1172 is about — so
    `test_without_the_blank_guard_an_unset_value_dies_before_the_call` exists to
    keep the corrected claim honest rather than to test the guard.

    What the blank guard is load-bearing for is the case that does NOT die
    there: an all-whitespace value passes `Split-Path` and binds
    `Products=[   ]`, so the export writes a whitespace-named CSV before the
    `Test-Path` assertion turns the step red — after the write, and with the
    upload pointed at the same blank name.
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

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-workflow-input-interpolation.py"
)
_spec = importlib.util.spec_from_file_location("interp_guard_202", _GUARD_PATH)
# An unguarded `_spec.loader.exec_module(...)` can raise AttributeError at
# IMPORT time, before a single case reports — the worst version of the roster
# abort this module's docstring is about: not a truncated roster but an empty
# one, naming `NoneType` rather than the path it could not import.
#
# Raised by Copilot on #1328, whose OBSERVATION is right and whose stated
# trigger is not — worth recording, because the wrong trigger is the one a
# reader would test against. Measured on this interpreter:
#
#   path missing, still `.py`   spec and loader both exist; `exec_module` raises
#                               FileNotFoundError NAMING the path. Already
#                               diagnosable; this assertion does not fire.
#   suffix changed / directory  `spec_from_file_location` returns None outright,
#                               so `_spec.loader` is the AttributeError.
#
# So the case to guard is the checker being MOVED or RENAMED, not deleted.
assert _spec is not None and _spec.loader is not None, (
    f"cannot infer an importer for the #1080 checker at {_GUARD_PATH} "
    f"(exists={_GUARD_PATH.exists()}) — every assertion in this module is "
    f"stated against that checker, so name the path rather than failing on a "
    f"None attribute"
)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "202-whmcs-export-products.yml"
JOB = "export_products"
STEP = "Export products + client products (read-only)"
ARTIFACT_STEP = "Upload CSV Artifact"
CALLEE = "whmcs-products-export.ps1"

MAPPINGS = {
    "IN_PRODUCTS_OUTPUT_FILE": "${{ inputs.products_output_file }}",
    "IN_CLIENT_PRODUCTS_OUTPUT_FILE": "${{ inputs.client_products_output_file }}",
}

# (dispatch input name, env var, declared default) for each guarded value. Every
# behavioural case iterates this, which is what makes "both inputs are covered"
# a property of the test roster rather than of a reviewer's attention.
INPUTS = (
    (
        "products_output_file",
        "IN_PRODUCTS_OUTPUT_FILE",
        "artifacts/whmcs/whmcs_products.csv",
    ),
    (
        "client_products_output_file",
        "IN_CLIENT_PRODUCTS_OUTPUT_FILE",
        "artifacts/whmcs/whmcs_client_products.csv",
    ),
)

CONTROLLED_VARS = tuple(var for _, var, _ in INPUTS)
DEFAULTS = {var: default for _, var, default in INPUTS}

# Decoys standing in for what `whmcs-secrets-from-kv` exports to GITHUB_ENV.
# Always set: they are what a payload is supposed to be able to reach, so
# clearing them would make the pre-fix control look harmless while the payload
# had in fact executed perfectly. The assertions read the sentinel's CONTENTS
# for the same reason — its mere existence would score the same either way.
DECOY_CREDENTIALS = {
    "WHMCS_API_SECRET": "whmcs-secret-placeholder-not-a-real-credential",
    "WHMCS_APIM_SUBSCRIPTION_KEY": "apim-key-placeholder-not-a-real-credential",
}

SENTINEL = "STOLEN-202.txt"
MARKER = "INJECTED-202"


def _payload(legal_path: str) -> str:
    """A subexpression payload that leaves the path legal and the run ordinary.

    `$null =` swallows Set-Content's (absent) output and the leading text keeps
    the assignment holding a real path, so the callee binds what a clean
    dispatch would bind.
    """
    return (
        legal_path
        + "$(Write-Host '"
        + MARKER
        + "'; $null = Set-Content -Path "
        + SENTINEL
        + ' -Value "secret=$env:WHMCS_API_SECRET")'
    )


# The shipped spellings the pre-fix control rewrites, and what each becomes when
# GitHub pastes the value in as raw text instead.
PRE_FIX_SITES = {
    "IN_PRODUCTS_OUTPUT_FILE": (
        "$productsOut = $env:IN_PRODUCTS_OUTPUT_FILE",
        '$productsOut = "{}"',
    ),
    "IN_CLIENT_PRODUCTS_OUTPUT_FILE": (
        "$clientProductsOut = $env:IN_CLIENT_PRODUCTS_OUTPUT_FILE",
        '$clientProductsOut = "{}"',
    ),
}

GUARD_LOOP_ANCHOR = "foreach ($spec in @("
GUARD_LOOP_OPEN = ")) {"

# Each guard's message fragment, in the order the loop applies them. The order
# is itself under test: the newline check must precede the two ANCHORED checks,
# because `^` anchors at the start of the string rather than of each line, so a
# multi-line value would only ever have its first line examined by them.
GUARD_MESSAGES = (
    "is blank.",
    "carriage return or newline",
    "glob metacharacter",
    "workspace-relative",
    "must not begin with '~'",
    "'..' segment",
)
BLANK_GUARD_MESSAGE = "is blank."
NEWLINE_GUARD_MESSAGE = "carriage return or newline"
ANCHORED_GUARD_MESSAGES = ("workspace-relative", "must not begin with '~'")

# A stand-in for the real exporter that records what it was BOUND, and resolves
# each path WITHOUT writing outside the temp workspace.
#
# `-LiteralPath` is deliberate and deliberately permissive: the real exporter's
# wildcard behaviour is unknown, and a stub that refused a glob itself would
# make the guard look load-bearing when the stub was doing the work. A stub that
# accepts more than the real callee can only understate the guard's value, which
# is the safe direction for a security test.
STUB = """param(
    [Parameter()]
    [string]$ApiUrl,
    [Parameter()]
    [string]$ProductsOutputFile,
    [Parameter()]
    [string]$ClientProductsOutputFile
)
[Console]::Error.WriteLine("CALLED Products=[$ProductsOutputFile] ClientProducts=[$ClientProductsOutputFile]")
$cwd = (Get-Location).ProviderPath
foreach ($p in @($ProductsOutputFile, $ClientProductsOutputFile)) {
    if ($null -eq $p) { continue }
    try {
        $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($p)
    } catch {
        [Console]::Error.WriteLine("RESOLVE-ERROR=[$p]")
        continue
    }
    [Console]::Error.WriteLine("RESOLVED=[$resolved]")
    if (-not $resolved.StartsWith($cwd)) {
        [Console]::Error.WriteLine("OUTSIDE-WORKSPACE=[$resolved]")
        continue
    }
    $dir = Split-Path -Parent $p
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Set-Content -LiteralPath $p -Value 'id,name'
}
exit 0
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that
# omits it pins the wrong exit code and vouches for it (#1080 lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _step() -> dict:
    return find_step(_workflow(), JOB, STEP)


def _body() -> str:
    return _step()["run"]


def _declared_inputs() -> dict:
    """`on.workflow_dispatch.inputs`, with the guard's own `on:` handling.

    `on:` parses to the YAML 1.1 boolean `True` (the Norway problem), so reading
    `workflow["on"]` raises KeyError — and a KeyError is not an AssertionError,
    so the module runner would not catch it: the roster would abort and every
    case sorted after this one would report no outcome at all, which a reviewer
    counting FAIL lines scores as passing (ledger L194). This module hit exactly
    that on its first run.
    """
    on = _workflow().get(True, _workflow().get("on"))
    dispatch = on.get("workflow_dispatch", {}) if isinstance(on, dict) else {}
    inputs = dispatch.get("inputs", {}) if isinstance(dispatch, dict) else {}
    return inputs if isinstance(inputs, dict) else {}


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    The CHECKER's own patterns rather than a substring test of this module's
    devising, so a spelling the checker recognises cannot slip past the
    step-level assertion and the two cannot drift apart.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _strip_guard_loop(body: str) -> str:
    """Remove the whole `foreach ($spec …)` guard loop, asserting it was there.

    Brace-counted from the loop's OWN opening brace rather than from the
    `foreach` keyword: the spec list is built from `@{ … }` hashtables, so a
    counter started at the keyword returns to depth 0 at the end of the first
    hashtable and would truncate the strip in the middle of the list — leaving a
    body that does not parse and a control that fails for the wrong reason.

    Counts are asserted BEFORE the cut (ledger L47), and the result is checked
    for every guard message and for the callee afterwards: a strip that silently
    did nothing would otherwise score as a pre-fix control.
    """
    assert body.count(GUARD_LOOP_ANCHOR) == 1, (
        f"expected exactly one {GUARD_LOOP_ANCHOR!r}, found "
        f"{body.count(GUARD_LOOP_ANCHOR)} — this control would otherwise "
        f"measure a body it did not modify. Body: {body!r}"
    )
    start = body.index(GUARD_LOOP_ANCHOR)
    assert GUARD_LOOP_OPEN in body[start:], (
        f"the guard loop no longer opens with {GUARD_LOOP_OPEN!r}, so this "
        f"control cannot locate its body. Body: {body!r}"
    )
    open_brace = body.index(GUARD_LOOP_OPEN, start) + len(GUARD_LOOP_OPEN) - 1
    depth, end = 0, None
    for i in range(open_brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, (
        f"the guard loop's braces do not balance, so this control cannot find "
        f"its end. Body: {body!r}"
    )
    stripped = body[:start] + body[end:]
    for message in GUARD_MESSAGES:
        assert message not in stripped, (
            f"the guard message {message!r} survived the strip, so the control "
            f"is measuring a guarded body. Stripped: {stripped!r}"
        )
    assert CALLEE in stripped, (
        f"the strip removed the invocation itself, so the control proves "
        f"nothing. Stripped: {stripped!r}"
    )
    return stripped


def _strip_only_blank_guard(body: str) -> str:
    """Remove the blank check alone, leaving the other five in place.

    Used by the L214 case, which is about what happens BELOW the guards rather
    than about whether they fire — stripping the whole loop would also remove
    the checks that are not under test and make the result ambiguous.
    """
    anchor = "if ([string]::IsNullOrWhiteSpace($value)) {"
    assert body.count(anchor) == 1, (
        f"expected exactly one {anchor!r}, found {body.count(anchor)} — a "
        f"refactor moved the blank guard, so this control would measure "
        f"nothing. Body: {body!r}"
    )
    start = body.index(anchor)
    assert BLANK_GUARD_MESSAGE in body[start:], (
        f"the blank guard no longer contains {BLANK_GUARD_MESSAGE!r}. "
        f"Body: {body!r}"
    )
    message_at = body.index(BLANK_GUARD_MESSAGE, start)
    assert "}" in body[message_at:], (
        f"the blank guard's block does not close after its message, so this "
        f"strip cannot find its end. Body: {body!r}"
    )
    end = body.index("}", message_at) + 1
    stripped = body[:start] + body[end:]
    assert anchor not in stripped, "the blank guard survived its own strip"
    assert NEWLINE_GUARD_MESSAGE in stripped, (
        "the strip took more than the blank guard — the newline check is gone "
        "too, so the result would not isolate the case under test"
    )
    return stripped


def _pre_fix(values: dict) -> str:
    """The body as it shipped BEFORE this lane, with GitHub's substitution done.

    DERIVED from the current body rather than kept as a second copy, so the
    control cannot drift from what ships: only the two spellings the burn-down
    changed are rewritten, and the guard loop the burn-down added is stripped —
    it is part of the remedy, and leaving it in would make the control refuse
    the payload for the reason under test rather than executing it.
    """
    body = _strip_guard_loop(_body())
    for var, (shipped, template) in PRE_FIX_SITES.items():
        assert body.count(shipped) == 1, (
            f"expected exactly one {shipped!r} to rewrite, found "
            f"{body.count(shipped)} — the pre-fix control would otherwise "
            f"measure a body that is not the shipped one. Body: {body!r}"
        )
        body = body.replace(shipped, template.format(values[var]))
    return body


def _run(body: str, **env_overrides):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test
    is which credential the payload reached.

    A variable is REMOVED when its override is None rather than set to "": the
    two blank forms differ and conflating them is what L291 is about. Every
    controlled variable is cleared unless a caller supplies it, so an inherited
    one cannot satisfy an assertion the workflow is supposed to (ledger L199).

    `stdin=DEVNULL` is load-bearing, not hygiene: an unsatisfied parameter makes
    PowerShell PROMPT, and on an interactive stdin the call blocks forever.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8", newline="\n"
        )
        # The runner-provided variables the step's summary block reads. Without
        # GITHUB_STEP_SUMMARY the body's `Out-File -FilePath $env:…` fails on an
        # empty path and every case returns rc 1 — a refusal that looks exactly
        # like a guard firing, including on the POSITIVE controls, which is how
        # it announced itself the first time this module ran.
        runner_vars = {
            "GITHUB_STEP_SUMMARY": str(tmp / "step_summary.md"),
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "FreeForCharity/FFC-Cloudflare-Automation",
            "GITHUB_RUN_ID": "1",
        }
        concrete = {k: v for k, v in env_overrides.items() if v is not None}
        env = child_env(**DECOY_CREDENTIALS, **runner_vars, **concrete)
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


def _with_defaults(**overrides):
    """Both controlled variables at their declared defaults, then overridden.

    Every case must supply BOTH, or the body refuses at the OTHER input's guard
    and the run reports a refusal that has nothing to do with the case. The
    assertions on the message text are what catch that, but supplying both is
    what stops it happening.
    """
    env = dict(DEFAULTS)
    env.update(overrides)
    return env


def _bound(output: str) -> str:
    """The stub's `CALLED …` line, or "" when the callee never ran."""
    for line in output.splitlines():
        if line.startswith("CALLED "):
            return line
    return ""


def _executed(output: str) -> bool:
    """Did the payload RUN, as opposed to merely appearing in the output?

    `MARKER in output` is not the question, and getting that wrong is why this
    helper exists (ledger L297). When the remedy works the payload arrives at
    the callee as an ordinary argument VALUE, so the stub's `CALLED …` line
    quotes it verbatim — and so does any pwsh error that echoes an offending
    argument back. A substring predicate therefore reports "the payload
    executed" for the run in which it provably did not, turning the fixed body's
    proof into the failure and pushing the next reader to weaken the assertion
    rather than the predicate.

    `Write-Host` emits the marker as a line of its OWN, which nothing that
    merely quotes the payload can produce. The sentinel file is the independent
    second reading beside it.
    """
    return any(line.strip() == MARKER for line in output.splitlines())


# --------------------------------------------------------------------------
# Wiring — pure YAML/AST, no tool required
# --------------------------------------------------------------------------


def test_both_inputs_travel_in_env_and_are_not_interpolated():
    """The mapping exists, the body reads it, and no site was reintroduced.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` mapping and the step still sees them from the harness, so
    every behavioural case below would keep passing over plumbing that no longer
    exists.
    """
    step = _step()
    env = step.get("env") or {}
    body = step["run"]
    for var, expression in MAPPINGS.items():
        assert env.get(var) == expression, (
            f"step {STEP!r} in job {JOB!r} must map {var} to {expression} — its "
            f"env: mapping is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"step {STEP!r} maps {var} but never reads $env:{var} — the env: "
            f"mapping would be dead plumbing. Body: {body!r}"
        )
    leftover = _interpolated_inputs(body) & {name for name, _, _ in INPUTS}
    assert not leftover, (
        f"{sorted(leftover)} are still interpolated into the body of step "
        f"{STEP!r} — the whole point of this lane is that they are not"
    )


def test_the_checker_agrees_this_step_interpolates_nothing():
    """#1080's own guard, not this module's reading of the YAML.

    A module that only checked its own patterns could pass while the shipped
    checker still reported a finding, which is the disagreement that would keep
    202 frozen after a lane that believed it had burned it down.
    """
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    named = {pathlib.Path(f.workflow).name for f in findings}
    assert WORKFLOW not in named, (
        f"{WORKFLOW} still reports an interpolated free-text input to the "
        f"checker: {[f for f in findings if pathlib.Path(f.workflow).name == WORKFLOW]}"
    )


def test_202_is_off_the_freeze():
    """The allowlist entry must go in the same PR, or the guard exits 1 stale."""
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} was burned down but is still in KNOWN_UNGUARDED — the "
        f"checker's stale-entry detection (#1080 criterion 5) fails on this"
    )


def test_the_guard_loop_covers_both_inputs_and_only_them():
    """The spec list names every guarded input, and reads each one's variable.

    This is the structural half of "one guard body, two values": a third path
    input added to this step without a spec entry is a failure here rather than
    an unguarded path in production, and a spec entry whose Value reads the
    WRONG variable — the copy-paste this loop exists to prevent — fails too,
    because each pairing is asserted rather than each name being merely present.
    """
    body = _body()
    assert GUARD_LOOP_ANCHOR in body, (
        f"the guard loop is gone from the body, so NOTHING is validated — a "
        f"missing anchor must fail here, not raise ValueError and abort the "
        f"roster (L194). Body: {body!r}"
    )
    start = body.index(GUARD_LOOP_ANCHOR)
    assert GUARD_LOOP_OPEN in body[start:], (
        f"the guard loop no longer opens with {GUARD_LOOP_OPEN!r}, so its spec "
        f"list cannot be read. Body: {body!r}"
    )
    spec_list = body[start : body.index(GUARD_LOOP_OPEN, start)]
    for name, var, _default in INPUTS:
        pairing = f"Input = '{name}'"
        assert pairing in spec_list, (
            f"the guard loop's spec list does not carry {pairing!r}, so "
            f"{name} is not guarded. Spec list: {spec_list!r}"
        )
        entry = spec_list[spec_list.index(pairing) :]
        entry = entry[: entry.index("}") if "}" in entry else len(entry)]
        assert f"$env:{var}" in entry, (
            f"the spec entry for {name} does not read $env:{var} — it reads "
            f"{entry!r}. A spec that names one input and reads another "
            f"validates one value twice and leaves the other unguarded."
        )
    assert spec_list.count("Input =") == len(INPUTS), (
        f"the guard loop carries {spec_list.count('Input =')} spec entries but "
        f"{len(INPUTS)} inputs are declared to this module. A new path input "
        f"must be added to both. Spec list: {spec_list!r}"
    )


def test_every_guarded_input_is_a_free_text_dispatch_input():
    """The inputs this module claims to cover are the ones GitHub cannot constrain.

    A `choice` or `boolean` input carries no payload, so guarding one would be
    decoration and — more to the point — a module listing one would overstate
    what this lane did.
    """
    inputs = _declared_inputs()
    for name, _var, default in INPUTS:
        assert name in inputs, f"{name} is not a dispatch input of {WORKFLOW}"
        # A YAML input whose body is empty parses to None, not to a mapping, so
        # `.get` would raise AttributeError and abort the roster — the same
        # failure this module's docstring says it does not permit, reached
        # through the workflow rather than through the test. Raised by Copilot
        # on #1328; the guard's own `dispatch_inputs` already takes this care,
        # which is what makes it the right shape to copy.
        spec = inputs[name]
        assert isinstance(spec, dict), (
            f"{name}'s dispatch-input definition is {spec!r}, not a mapping — "
            f"the workflow's `inputs:` block is malformed, and this must be a "
            f"named failure rather than an AttributeError"
        )
        declared = spec.get("type", "string")
        assert declared == "string", (
            f"{name} is declared {declared!r}, not free text — this module's "
            f"premise is wrong for it"
        )
        assert spec.get("default") == default, (
            f"{name}'s declared default is {spec.get('default')!r}, not "
            f"{default!r} — the fixtures below use the stale value"
        )


def test_the_upload_step_still_consumes_both_inputs_raw():
    """The two-consumer premise every fail-closed comment in the body rests on.

    If a later edit routes the upload through the guarded body instead, the
    reasoning for failing closed rather than default-filling stops applying and
    the comments become wrong. That should be a red test, not a stale paragraph.
    """
    step = find_step(_workflow(), JOB, ARTIFACT_STEP)
    # Same class as the two Copilot raised on #1328, fixed here rather than left
    # as the one unguarded lookup in a module that says it has none: a `with:`
    # block or a `path:` key that has gone away must name itself, not raise
    # KeyError and abort the roster.
    with_block = step.get("with")
    assert isinstance(with_block, dict) and "path" in with_block, (
        f"the {ARTIFACT_STEP!r} step has no `with.path` ({with_block!r}) — the "
        f"second consumer this PR's fail-closed reasoning rests on is gone, "
        f"which is a finding rather than a crash"
    )
    path = with_block["path"]
    assert isinstance(path, str), (
        f"the {ARTIFACT_STEP!r} step's path: is {path!r}, not a string — the "
        f"substring checks below would be meaningless against it"
    )
    for name, _var, _default in INPUTS:
        assert "${{ inputs.%s }}" % name in path, (
            f"the {ARTIFACT_STEP!r} step no longer interpolates {name} into "
            f"its path: — its path is {path!r}. The body's fail-closed guards "
            f"are justified by that second consumer."
        )
    assert path.count("\n") >= 1, (
        f"the {ARTIFACT_STEP!r} step's path: is no longer a multi-line pattern "
        f"list ({path!r}), which is what makes the newline guard's reasoning "
        f"about smuggled patterns concrete"
    )


def test_the_newline_guard_precedes_the_anchored_guards():
    """Ordering no behavioural case can observe, because every payload is refused.

    `^` anchors at the start of the STRING, not of each line, so the rooted and
    tilde checks see only the first line of a multi-line value. With the newline
    check first, a multi-line payload never reaches them; move it last and they
    silently validate a prefix. Both orders are green behaviourally, which is
    exactly why the ordering needs its own assertion.
    """
    body = _body()
    assert NEWLINE_GUARD_MESSAGE in body, (
        f"the newline guard is gone from the body — a multi-line value would "
        f"reach the anchored checks below, which see only its first line. "
        f"Body: {body!r}"
    )
    newline_at = body.index(NEWLINE_GUARD_MESSAGE)
    for message in ANCHORED_GUARD_MESSAGES:
        assert message in body, (
            f"the {message!r} guard is gone from the body, so the ordering this "
            f"case pins no longer has two things to order. Body: {body!r}"
        )
        assert newline_at < body.index(message), (
            f"the newline guard must come before the {message!r} guard, which "
            f"anchors on '^' and would otherwise examine only the first line "
            f"of a multi-line value"
        )


# --------------------------------------------------------------------------
# Behaviour — the pre-fix control
# --------------------------------------------------------------------------


def test_the_pre_fix_body_ran_the_payload_at_each_site_and_exited_zero():
    """Both interpolation sites were live, and neither failed loudly.

    The control that gives every assertion below its meaning: without it, a
    fixed body that refuses a payload proves only that the payload was bad.
    """
    for _name, var, default in INPUTS:
        values = dict(DEFAULTS)
        values[var] = _payload(default)
        out, stolen, rc = _run(_pre_fix(values))
        assert _executed(out), (
            f"the pre-fix body did not run the payload at {var} — this control "
            f"proves nothing about the remedy. Output: {out!r}"
        )
        assert stolen is not None and DECOY_CREDENTIALS["WHMCS_API_SECRET"] in stolen, (
            f"the payload at {var} ran but did not reach the credential, so "
            f"the control understates what was possible. Sentinel: {stolen!r}"
        )
        assert rc == 0, (
            f"the injected run at {var} must be indistinguishable from an "
            f"ordinary export; rc={rc}. Output: {out!r}"
        )
        assert _bound(out).startswith("CALLED Products=["), (
            f"the callee did not run at {var}, so the legitimate export did "
            f"not happen and the run WOULD have looked wrong. "
            f"Bound: {_bound(out)!r}"
        )


def test_without_the_guard_a_whitespace_value_binds_whitespace(
):
    """The half of the blank case that was SILENT, for each input.

    An empty value was already loud at `Split-Path`; an all-whitespace one was
    not. This is what makes the blank guard load-bearing rather than
    attribution-only (ledger L260), and pinning it stops a later lane copying
    this shape and dropping the guard on "the empty case was already loud".
    """
    for _name, var, _default in INPUTS:
        stripped = _strip_guard_loop(_body())
        out, _stolen, _rc = _run(stripped, **_with_defaults(**{var: "   "}))
        assert "=[   ]" in _bound(out), (
            f"without the guard, a whitespace {var} must reach the callee as "
            f"whitespace — that silent write is the case the guard closes. "
            f"Bound: {_bound(out)!r}"
        )


def test_without_the_blank_guard_an_unset_value_dies_before_the_call():
    """The corrected L214 claim, pinned as NOT holding (#1172).

    202 binds both paths inline, so an argument that vanishes would shift the
    ones after it. It cannot: `Split-Path -Parent $null` fails two lines above
    the call. The workflow says so in a comment; this keeps the comment honest.
    """
    for _name, var, _default in INPUTS:
        stripped = _strip_only_blank_guard(_body())
        env = _with_defaults()
        env[var] = None  # omitted, not blank
        out, _stolen, rc = _run(stripped, **env)
        assert rc != 0, (
            f"an omitted {var} with no blank guard must still fail; rc={rc}. "
            f"Output: {out!r}"
        )
        assert "Split-Path" in out and "null" in out, (
            f"an omitted {var} is expected to die at `Split-Path -Parent` with "
            f"a null binding error, ABOVE the call — if it now dies at the "
            f"call instead, the L214 argument-shift claim this workflow's "
            f"comment denies has become true and the comment must change. "
            f"Output: {out!r}"
        )
        assert not _bound(out), (
            f"the callee ran with an omitted {var}, so the failure is at the "
            f"call rather than above it. Bound: {_bound(out)!r}"
        )


# --------------------------------------------------------------------------
# Behaviour — the shipped body
# --------------------------------------------------------------------------


def test_the_shipped_body_binds_each_payload_as_data():
    """The remedy, measured at both sites: the payload arrives, inert.

    Note what is asserted and what is not. The payload text DOES appear in the
    output — the callee echoes the argument it was bound — so the discriminator
    is the exact-line marker and the absent sentinel, never a substring search.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    for _name, var, default in INPUTS:
        out, stolen, _rc = _run(_body(), **_with_defaults(**{var: _payload(default)}))
        assert not _executed(out), (
            f"the payload at {var} EXECUTED against the shipped body — the "
            f"burn-down did not take. Output: {out!r}"
        )
        assert stolen is None, (
            f"the payload at {var} wrote the sentinel against the shipped "
            f"body. Sentinel: {stolen!r}"
        )
        assert _payload(default) in _bound(out), (
            f"the payload at {var} should reach the callee verbatim, as DATA. "
            f"Bound: {_bound(out)!r}"
        )


def test_the_shipped_body_passes_ordinary_values_through():
    """The positive control: the guards refuse payloads, not the job."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), **_with_defaults())
    assert rc == 0, f"an ordinary dispatch must succeed; rc={rc}. Output: {out!r}"
    assert _bound(out) == (
        f"CALLED Products=[{DEFAULTS['IN_PRODUCTS_OUTPUT_FILE']}] "
        f"ClientProducts=[{DEFAULTS['IN_CLIENT_PRODUCTS_OUTPUT_FILE']}]"
    ), f"both paths must bind unchanged. Bound: {_bound(out)!r}"


def test_the_declared_defaults_survive_their_own_guards():
    """A guard that refuses the workflow's own default is a broken workflow.

    Read from the YAML rather than from this module's fixtures, so a changed
    default that the guards would reject fails here instead of on dispatch.
    """
    inputs = _declared_inputs()
    # The SECOND site of the class Copilot raised on #1328, and the one its
    # review did not name. Found by mutation-testing the fix to the first: a
    # null input body made this comprehension raise TypeError ('NoneType' is not
    # subscriptable), the roster truncated to 14 of 24, and the eight cases
    # after it reported nothing — so fixing only the site the review named would
    # have left the abort intact and looked like it had been closed.
    env = {}
    for name, var, _default in INPUTS:
        spec = inputs.get(name)
        assert isinstance(spec, dict) and "default" in spec, (
            f"{name} has no declared default ({spec!r}), so there is nothing "
            f"for this case to send through the guards"
        )
        env[var] = spec["default"]
    out, _stolen, rc = _run(_body(), **env)
    assert rc == 0, (
        f"the declared defaults must pass every guard; rc={rc}. Output: {out!r}"
    )


def _assert_refused(var: str, name: str, value: str, fragment: str):
    """Run one payload and assert WHICH guard refused it and for WHICH input."""
    out, _stolen, rc = _run(_body(), **_with_defaults(**{var: value}))
    assert rc != 0, (
        f"{name}={value!r} must be refused; rc={rc}. Output: {out!r}"
    )
    assert fragment in out, (
        f"{name}={value!r} was refused, but not by the {fragment!r} guard — "
        f"another check fired first and this case is not measuring what it "
        f"claims. Output: {out!r}"
    )
    assert name in out, (
        f"the refusal of {name}={value!r} does not name the input the "
        f"dispatcher typed, so the guard loop is not carrying the name with "
        f"the value. Output: {out!r}"
    )
    assert not _bound(out), (
        f"{name}={value!r} was refused only AFTER the callee ran, so the "
        f"export already happened. Bound: {_bound(out)!r}"
    )


def test_a_blank_value_is_refused_for_each_input():
    for name, var, _default in INPUTS:
        _assert_refused(var, name, "", BLANK_GUARD_MESSAGE)


def test_an_unset_value_is_refused_for_each_input():
    """Unset is not the same string as blank after the move to `env:` (L291).

    An omitted input reaches the interpolated body as the empty string between
    two quotes and reaches this one as `$null`; `IsNullOrWhiteSpace` is true for
    both, which is the whole reason for that spelling.
    """
    for name, var, _default in INPUTS:
        env = _with_defaults()
        env[var] = None
        out, _stolen, rc = _run(_body(), **env)
        assert rc != 0 and BLANK_GUARD_MESSAGE in out and name in out, (
            f"an unset {name} must be refused by its blank guard; rc={rc}. "
            f"Output: {out!r}"
        )


def test_a_whitespace_value_is_refused_for_each_input():
    for name, var, _default in INPUTS:
        _assert_refused(var, name, "   ", BLANK_GUARD_MESSAGE)


def test_a_newline_is_refused_for_each_input():
    """The smuggled-pattern case: `path:` is a newline-delimited pattern list."""
    for name, var, default in INPUTS:
        _assert_refused(
            var,
            name,
            default + "\nC:/Users/runneradmin/.azure/msal_token_cache.json",
            NEWLINE_GUARD_MESSAGE,
        )


def test_a_glob_metacharacter_is_refused_for_each_input():
    """`[` and `]` specifically — the pair with no self-block (ledger L298)."""
    for name, var, _default in INPUTS:
        _assert_refused(
            var, name, "artifacts/whmcs/[m]sal_token_cache.json", "glob metacharacter"
        )


def test_an_absolute_or_drive_rooted_value_is_refused_for_each_input():
    for name, var, _default in INPUTS:
        for value in ("C:/Users/runneradmin/.azure/x.json", "/etc/passwd", "\\\\srv\\s"):
            _assert_refused(var, name, value, "workspace-relative")


def test_a_psdrive_qualified_value_is_refused_for_each_input():
    """`[A-Za-z]+:`, not `[A-Za-z]:` — a qualifier is not one letter.

    `Temp:/x.csv` passes the single-letter spelling and is still resolved by the
    provider, so the narrow regex admits a workspace escape that looks nothing
    like `C:\\`.
    """
    for name, var, _default in INPUTS:
        for value in ("Temp:/x.csv", "env:/x"):
            _assert_refused(var, name, value, "workspace-relative")


def test_a_tilde_prefixed_value_is_refused_for_each_input():
    """`~` starts with neither a separator nor a qualifier, so it needs its own."""
    for name, var, _default in INPUTS:
        _assert_refused(var, name, "~/.azure/x.csv", "must not begin with '~'")


def test_a_dotdot_segment_is_refused_for_each_input():
    for name, var, _default in INPUTS:
        for value in ("../../etc/x.csv", "artifacts/../../x.csv", "artifacts\\..\\x"):
            _assert_refused(var, name, value, "'..' segment")


def test_the_path_guards_do_not_refuse_legitimate_relative_paths():
    """The negative control for the five path guards.

    `.csv` and a dot-containing directory are the shapes a naive `..` test
    catches, and `sub[1]` is NOT included here on purpose — it is a legal
    filename that the glob guard refuses by design.
    """
    for name, var, _default in INPUTS:
        for value in (
            "artifacts/whmcs/products.2026-09.csv",
            "artifacts/whmcs/sub.dir/out.csv",
            "out.csv",
        ):
            out, _stolen, rc = _run(_body(), **_with_defaults(**{var: value}))
            assert rc == 0, (
                f"{name}={value!r} is a legitimate relative path and must not "
                f"be refused; rc={rc}. Output: {out!r}"
            )


def test_a_payload_in_one_input_does_not_disarm_the_other_guard():
    """Both values are refused independently, not just the first one examined.

    A loop that `break`s on its first finding, or that validated only
    `$spec[0]`, would pass every single-input case above and fail this one.
    """
    name_b, var_b, _default_b = INPUTS[1]
    env = _with_defaults(**{INPUTS[0][1]: "~/.azure/a.csv", var_b: "~/.azure/b.csv"})
    out, _stolen, rc = _run(_body(), **env)
    assert rc != 0, f"both payloads must be refused; rc={rc}. Output: {out!r}"
    env_second_only = _with_defaults(**{var_b: "~/.azure/b.csv"})
    out2, _stolen2, rc2 = _run(_body(), **env_second_only)
    assert rc2 != 0 and name_b in out2, (
        f"a payload in the SECOND input alone must be refused and named; "
        f"rc={rc2}. Output: {out2!r}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and
# checker-agreement cases are pure YAML/AST and must run on a host without pwsh
# installed (#1182 — a whole-module `shutil.which` gate turns "could not run"
# into "everything passed"). This set is scoped to exactly the cases that call
# `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_ran_the_payload_at_each_site_and_exited_zero",
    "test_without_the_guard_a_whitespace_value_binds_whitespace",
    "test_without_the_blank_guard_an_unset_value_dies_before_the_call",
    "test_the_shipped_body_binds_each_payload_as_data",
    "test_the_shipped_body_passes_ordinary_values_through",
    "test_the_declared_defaults_survive_their_own_guards",
    "test_a_blank_value_is_refused_for_each_input",
    "test_an_unset_value_is_refused_for_each_input",
    "test_a_whitespace_value_is_refused_for_each_input",
    "test_a_newline_is_refused_for_each_input",
    "test_a_glob_metacharacter_is_refused_for_each_input",
    "test_an_absolute_or_drive_rooted_value_is_refused_for_each_input",
    "test_a_psdrive_qualified_value_is_refused_for_each_input",
    "test_a_tilde_prefixed_value_is_refused_for_each_input",
    "test_a_dotdot_segment_is_refused_for_each_input",
    "test_the_path_guards_do_not_refuse_legitimate_relative_paths",
    "test_a_payload_in_one_input_does_not_disarm_the_other_guard",
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
