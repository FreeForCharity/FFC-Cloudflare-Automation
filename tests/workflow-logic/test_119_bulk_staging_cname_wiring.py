"""Unit tests for 119's bulk staging-CNAME step (#1080 burn-down).

119 deletes every DNS record at `staging.<domain>` and creates a CNAME in its
place, for a whole list of domains, across BOTH Cloudflare accounts, on the
`cloudflare-prod-write` lane. Its two free-text dispatch inputs used to be
interpolated straight into the pwsh body:

    $domainsInput = '${{ inputs.domains }}'
    ...
      Target  = '${{ inputs.target }}'

which are not arguments — they are the dispatcher's text pasted into the program
GitHub then runs, holding the write token the APPROVER spent. Both now arrive
through step-level `env:`.

THE LANE WHERE THE OTHER LANES' REMEDY WOULD HAVE BEEN WRONG
    Every earlier #1080 lane ends with a body that fails closed on an empty
    value, because every earlier lane's inputs were `required: true` — so empty
    could only mean "the `env:` mapping was deleted or misspelled". Here it
    cannot: `domains` is `required: false` with an empty default and a
    documented fallback to a 13-domain FFC-EX list, so a blank box is the
    COMMON path and a fail-closed guard would break it. The remedy is the
    "default fill" shape `scripts/check-workflow-empty-input-guard.py` already
    recognises, and `target` is omitted from the splat rather than passed empty
    (the callee resolves the canonical Pages host itself, #778, so a default
    spelled out in the workflow would be a second copy free to drift).

    The cost of that is stated rather than hidden: a deleted `env:` mapping
    looks exactly like a blank dispatch box, so nothing at RUNTIME can notice
    it. `_assert_wiring` is the only thing that can, which is why it is
    re-asserted before every behavioural case below.

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    `cloudflare-tokens-from-kv` with `scope: write` resolves the `wr-all-*` Key
    Vault secrets and exports `CLOUDFLARE_API_TOKEN_FFC` / `_CM` through
    `GITHUB_ENV`. So the tokens are in the process environment of the injection
    point while appearing in no `env:` block in the file (ledger L213) and
    matching no `secrets.` grep of the body (#1141) — the GITHUB_ENV blindness
    recorded on lanes 7, 9 and 10 and filed as #1188.
    `test_the_write_tokens_reach_the_step_only_through_github_env` pins it.

THE PAYLOAD SHAPE: SINGLE QUOTES, SO `$( )` IS INERT (ledger L243)
    Both injection points sat in SINGLE-quoted strings, where pwsh expands
    nothing. The `$( )` payload that exploited the double-quoted lanes reaches
    the callee here as literal text, so a control built that way reports *the
    pre-fix body did not execute the payload* — which reads as evidence the
    interpolation was harmless, i.e. as a reason not to fix a live injection
    under `cloudflare-prod-write`. That inert result is kept as an assertion
    (`test_the_subexpression_payload_is_inert_in_single_quotes`) rather than a
    footnote, because the next lane will reach for the same payload.

    The two positions then need DIFFERENT break-outs, which is the same
    per-position lesson lane 9 hit in JavaScript:

      * `domains` lands at statement level (`$x = '<HERE>'`), where `;` chains
        statements, so the payload closes the quote and chains.
      * `target` lands inside a `@{ }` hash literal, where a bare `;` separates
        ENTRIES and a statement there is a ParserError — so that payload closes
        the quote and concatenates a `$( )` whose last statement supplies the
        value, the shape `test_112_bulk_replace_wiring.py` documents.

    A payload built for the wrong position writes no sentinel and reports the
    defect as absent, so both are measured.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete the `env:` block and nothing is interpolated, so the checker
    is honestly green while the step wires 13 production zones from a default
    list nobody dispatched.
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
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "119-bulk-staging-cname-github-pages.yml"
JOB = "bulk-staging-cname"
STEP = "Run bulk staging CNAME wiring"
ENVIRONMENT = "cloudflare-prod-write"
TOKENS_ACTION = "./.github/actions/cloudflare-tokens-from-kv"

# The env var each input must travel in, and the expression it must map from.
MAPPINGS = {
    "IN_DOMAINS": "${{ inputs.domains }}",
    "IN_TARGET": "${{ inputs.target }}",
}
INPUT_NAMES = ("domains", "target")

# The credentials in reach of the injection point. Neither is a `secrets.*`
# reference in this file and neither appears in any `env:` block — the composite
# action exports both through GITHUB_ENV.
TOKEN_VARS = ("CLOUDFLARE_API_TOKEN_FFC", "CLOUDFLARE_API_TOKEN_CM")

# Deliberately NOT shaped like a real Cloudflare token: a token-shaped literal is
# redacted by secret scanners and by the tooling that renders CI output, and the
# one place this value is ever printed is an assertion message on a failing run —
# the moment a reader needs to see whether the sentinel holds the credential or
# an empty string.
FAKE_TOKEN = "cf-write-token-placeholder-not-a-real-token"
SENTINEL = "STOLEN-119.txt"

LEGAL_DOMAINS = "aprilhansen.com,nj4israel.org"
LEGAL_TARGET = "freeforcharity.github.io"

# The 13-domain fallback is behaviour this lane must PRESERVE, so one member is
# pinned: `Domains=[]` and `Domains=[the default list]` are the two outcomes an
# empty mapping can produce, and only one of them is correct here.
A_DEFAULT_DOMAIN = "southamptonfriends.org"

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = ("IN_DOMAINS", "IN_TARGET") + TOKEN_VARS

# The body as it shipped BEFORE the burn-down, verbatim from origin/main with the
# two substitution points marked, trimmed to the statements that matter. It is
# the positive control: without it, "the fixed body does not execute the payload"
# is a claim about a body that might never have executed anything.
PRE_FIX_BODY = """$defaultDomains = @(
  'aprilhansen.com',
  'southamptonfriends.org'
) -join ','

$domainsInput = 'DOMAINS_HERE'
if ([string]::IsNullOrWhiteSpace($domainsInput)) {
  $domainsInput = $defaultDomains
  Write-Host "No 'domains' input provided. Using default 13-domain FFC-EX staging list."
}

$params = @{
  Domains = $domainsInput
  Target  = 'TARGET_HERE'
}
if ('true' -eq 'true') { $params.DryRun = $true }

./scripts/bulk-staging-cname-github-pages.ps1 @params
"""

# A permissive stand-in for the real script: it records what it was BOUND, which
# is the only discriminator that works here. A marker-string search over stdout
# is not one — pwsh echoes the offending source line back in a ParserError, so
# any substring predicate matches the payload text on a run that executed
# nothing.
# It reports whether -Target was BOUND, not only what it holds. Those are two
# different states that render identically: a `Target = ''` passed in the splat
# and a `Target` omitted altogether both arrive as an empty string, so a stub
# printing the value alone cannot tell the gated append from the unconditional
# assignment it replaced — and the mutation that removes the gate would pass.
STUB = """[CmdletBinding()]
param(
    [string]$Domains,
    [string]$Target,
    [switch]$DryRun
)
$bound = $PSBoundParameters.ContainsKey('Target')
Write-Output "CALLED Domains=[$Domains] Target=[$Target] TargetBound=[$bound] DryRun=[$DryRun]"
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that
# omits it pins the wrong exit code and vouches for it (#1080 lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _statement_payload() -> str:
    """A break-out for the `domains` position: `$x = '<HERE>'`, statement level.

    Closes the quote, steals the write token, and re-opens a quote so the
    assignment still terminates — the exploited run then continues into the
    legitimate call and exits 0 with a normal-looking log.
    """
    return (
        LEGAL_DOMAINS + "'; "
        "Set-Content -Path '" + SENTINEL + "' -Value $env:" + TOKEN_VARS[0] + "; "
        "$domainsInput = '" + LEGAL_DOMAINS
    )


def _hashtable_payload() -> str:
    """A break-out for the `target` position: a value inside a `@{ }` literal.

    A bare `;` there separates hashtable ENTRIES, so the statement-level payload
    above is a ParserError in this position — which would report the defect as
    absent. `$( )` in an expression position is valid and its last statement
    supplies the value (the shape `test_112_bulk_replace_wiring.py` records).
    """
    return (
        LEGAL_TARGET
        + "' + $(Set-Content -Path '"
        + SENTINEL
        + "' -Value $env:"
        + TOKEN_VARS[0]
        + "; '') + '"
    )


def _subexpression_payload() -> str:
    """The payload the DOUBLE-quoted lanes used, which single quotes make inert.

    Its inner quoting is DOUBLE, deliberately. Spelled with single quotes — the
    way the earlier lanes spell it — the payload's own `'` terminates the string
    it was pasted into and pwsh reports a ParserError, which is a break-out by
    accident rather than the `$( )` expansion this case is about. That version
    also cannot be asserted on: the only place the text then appears is pwsh's
    error renderer, which truncates the source line with an ellipsis and does
    not render identically on every host (the trap `test_112` records).
    """
    return (
        LEGAL_DOMAINS
        + '$(Set-Content -Path "'
        + SENTINEL
        + '" -Value $env:'
        + TOKEN_VARS[0]
        + ")"
    )


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising: a `"inputs.domains" in body` predicate cannot
    see `${{ inputs . domains }}`, which the expression language allows and the
    checker matches on purpose. Reusing `_INPUT_REF` means a spelling the
    checker recognises cannot slip past this step-level assertion, and the two
    cannot drift apart the way a restated rule does (#1187's review).
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _assert_wiring(step: dict) -> None:
    """Both inputs travel in env, the body reads them, and neither is interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` block and the step still sees them from the harness, so
    every behavioural case below keeps passing over plumbing that no longer
    exists. On this lane that matters more than on the others — an empty
    `IN_DOMAINS` is a legitimate state, so the runtime cannot tell a deleted
    mapping from a blank dispatch box and this assertion is the ONLY thing that
    notices.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    for var, expression in MAPPINGS.items():
        assert env.get(var) == expression, (
            f"step {step.get('name')!r} in job {JOB!r} must map {var} to "
            f"{expression} — its env: block is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"step {step.get('name')!r} maps {var} but never reads $env:{var} — "
            f"the env: block is decoration and the value reaches nothing. "
            f"Body: {body!r}"
        )
    reintroduced = _interpolated_inputs(body) & set(INPUT_NAMES)
    assert not reintroduced, (
        f"step {step.get('name')!r} interpolates {sorted(reintroduced)} into its "
        f"script body again (#1080): under {ENVIRONMENT} that is dispatcher text "
        f"executed after the approval. Body: {body!r}"
    )


def _render_dry_run(body: str, value: str) -> str:
    """Substitute only the `dry_run` expression, the way GitHub would.

    `dry_run` is a `boolean`, which GitHub generates from the declared options,
    so it stays interpolated on purpose and the guard does not count it. It must
    still be substituted here: the expression survives in the body, and a
    `${{ }}` left in place is NOT the loud ParserError lane 8 measured — it sits
    in single quotes, where pwsh parses it happily as literal text and the
    comparison silently goes false (ledger L243). The anchor's occurrence count
    is asserted BEFORE substituting (ledger L47), so a body that stopped
    containing it fails loudly instead of testing nothing.
    """
    anchor = "'${{ inputs.dry_run }}'"
    assert body.count(anchor) == 1, (
        f"expected exactly one {anchor} in the step body, found "
        f"{body.count(anchor)} — the dry_run branch moved"
    )
    rendered = body.replace(anchor, f"'{value}'")
    assert "${{" not in rendered, (
        f"an unsubstituted expression survived into the harness body, which pwsh "
        f"would run as literal text rather than reject: {rendered!r}"
    )
    return rendered


def _run(body: str, wrap: bool = True, **env_overrides: str):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test is
    WHICH credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "bulk-staging-cname-github-pages.ps1").write_text(
            STUB, encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(
            (RUNNER_PREAMBLE + body + RUNNER_EPILOGUE) if wrap else body,
            encoding="utf-8",
        )
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAINS would
        # make the fallback case pass for the wrong reason, and an inherited
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


def _pre_fix(domains: str, target: str = LEGAL_TARGET) -> str:
    """The pre-fix body with GitHub's substitution performed, as GitHub does it.

    Asserted rather than assumed: a `.replace` that stopped matching would leave
    the marker in place, and the control would measure an unexploited body while
    reporting that the payload did not run (ledger L47).
    """
    assert PRE_FIX_BODY.count("DOMAINS_HERE") == 1
    assert PRE_FIX_BODY.count("TARGET_HERE") == 1
    rendered = PRE_FIX_BODY.replace("DOMAINS_HERE", domains).replace(
        "TARGET_HERE", target
    )
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


# --------------------------------------------------------------------------
# Wiring — no tool needed, so these must run on every host (#1182)
# --------------------------------------------------------------------------


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_the_step_sits_in_the_gated_cloudflare_write_job():
    """This entry is a WRITE lane in the #1080 freeze because of this environment.

    If the job ever loses it, an injected payload no longer runs after an
    approval and this module's stated reason for existing stops holding — worth
    failing over rather than silently keeping.
    """
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} declares environment {job.get('environment')!r}, expected "
        f"{ENVIRONMENT!r}"
    )


def test_both_inputs_are_free_text():
    """A `default:` is not a constraint, and this is what makes them findings.

    Both inputs arrive pre-filled, which is what makes this entry read as safe
    on a skim. GitHub constrains `boolean` and `choice` and nothing else, so the
    dispatcher may replace either box with any text at all. Asserted against the
    guard's OWN constrained set rather than restating it, so the two cannot
    quietly disagree if GitHub ever constrains another type.
    """
    workflow = load_workflow(WORKFLOW)
    declared = guard.dispatch_inputs(workflow)
    for name in INPUT_NAMES:
        assert name in declared, f"input {name!r} is gone from the dispatch form: {declared}"
        assert declared[name] not in guard.CONSTRAINED_TYPES, (
            f"input {name!r} now declares type {declared[name]!r}, which GitHub "
            f"constrains — the freeze entry's premise has changed"
        )
    assert set(guard.free_text_inputs(workflow)) >= set(INPUT_NAMES)
    assert declared.get("dry_run") in guard.CONSTRAINED_TYPES, (
        "dry_run stays interpolated on the strength of being a constrained "
        f"type, and it now declares {declared.get('dry_run')!r}"
    )


def test_an_empty_domains_input_is_a_legitimate_state():
    """Why this lane does NOT fail closed, pinned at the declaration.

    `required: false` with an empty default and a documented fallback is the
    whole reason the remedy here differs from every other #1080 lane. If the
    declaration ever changes, the fallback stops being the common path and the
    body should be revisited — so this fails rather than letting the reasoning
    rot in a comment.
    """
    # `on:` is the YAML 1.1 boolean True after safe_load (the Norway problem),
    # so the string key alone reads {} and every assertion below would pass by
    # inspecting nothing. Both spellings, and the lookup is asserted.
    workflow = load_workflow(WORKFLOW)
    on = workflow.get(True, workflow.get("on"))
    assert isinstance(on, dict), f"could not read the trigger block: {on!r}"
    inputs = on["workflow_dispatch"]["inputs"]
    domains = inputs["domains"]
    assert domains.get("required") is False, (
        f"`domains` now declares required={domains.get('required')!r}; an empty "
        f"value may no longer be a legitimate dispatch, in which case this step "
        f"should fail closed like the other #1080 lanes"
    )
    assert domains.get("default") == "", (
        f"`domains` now defaults to {domains.get('default')!r} rather than blank"
    )
    assert "$defaultDomains" in _step()["run"], (
        "the fallback list is gone from the body, so an empty `domains` input no "
        "longer has a documented meaning"
    )


def test_the_target_default_is_not_duplicated_in_the_workflow():
    """The callee owns the canonical Pages host (#778); the workflow must not.

    `Target` is omitted from the splat when blank rather than filled with a
    literal here, so there is one copy of the value and it cannot drift. A
    future edit that "helpfully" hardcodes it would pass every behavioural test
    in this module.
    """
    body = _step()["run"]
    # The whole conditional, not just the assignment inside it. Anchored on the
    # assignment alone this assertion survives the mutation it exists to catch:
    # dropping the gate leaves `$params.Target = $target` in the body verbatim,
    # so the predicate still matched while the behaviour had changed. Measured —
    # mutation M5 flipped one test instead of the two predicted, which is what
    # exposed it.
    gated = (
        "if (-not [string]::IsNullOrWhiteSpace($target)) { $params.Target = $target }"
    )
    assert gated in body, (
        f"the gated append is gone: `Target` is no longer CONDITIONALLY added, "
        f"so an empty input may now reach the callee as an empty argument. "
        f"Body: {body!r}"
    )
    assert LEGAL_TARGET not in body, (
        f"the body now spells out {LEGAL_TARGET!r}, which is a second copy of a "
        f"value the callee already resolves through Get-GhPagesWwwTarget (#778)"
    )


def test_the_write_tokens_reach_the_step_only_through_github_env():
    """This module's central claim about the blast radius, pinned not narrated.

    The credentials in reach of the injection point are exported by the
    PRECEDING composite action through GITHUB_ENV. So a reviewer reading `env:`
    blocks (ledger L213) and a sweep for `secrets.` in a `run:` body both score
    this file as holding nothing — while a payload here runs beside write-scoped
    Cloudflare tokens for both accounts (#1188).
    """
    workflow = load_workflow(WORKFLOW)
    steps = workflow["jobs"][JOB]["steps"]
    action_steps = [s for s in steps if s.get("uses") == TOKENS_ACTION]
    assert len(action_steps) == 1, (
        f"expected exactly one {TOKENS_ACTION} step, found {len(action_steps)} — "
        f"this module's credential claim is about that step"
    )
    assert (action_steps[0].get("with") or {}).get("scope") == "write", (
        f"the tokens step no longer requests scope: write: "
        f"{action_steps[0].get('with')!r}"
    )
    assert steps.index(action_steps[0]) < steps.index(_step()), (
        "the token export no longer precedes the injection point, so the "
        "credentials are not in that step's environment — re-read this "
        "module's docstring before trusting it"
    )
    for token in TOKEN_VARS:
        for step in steps:
            assert token not in (step.get("env") or {}), (
                f"{token} now appears in step {step.get('name')!r}'s env: block. "
                f"That is a better state, not a worse one — but this module's "
                f"claim is that it did NOT, so the docstring needs re-reading"
            )
        assert token not in (workflow["jobs"][JOB].get("env") or {})
        assert token not in _step()["run"], (
            f"{token} is now named in the body, so the GITHUB_ENV blindness this "
            f"module documents no longer describes this step"
        )


def test_the_guard_no_longer_reports_this_workflow():
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"the guard could not read: {unreadable}"
    assert WORKFLOW not in guard.current_map(findings), (
        f"{WORKFLOW} still interpolates a free-text dispatch input into a "
        f"script body"
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} was burned down but is still listed in KNOWN_UNGUARDED — "
        f"a stale entry, which the guard itself exits 1 on"
    )


# --------------------------------------------------------------------------
# Behaviour: the defect, and its absence
# --------------------------------------------------------------------------


def test_the_pre_fix_domains_position_stole_a_write_token_and_exited_zero():
    """The positive control for the statement-level position.

    Without it, every inertness assertion below is a claim about a body that
    might never have executed anything at all.
    """
    out, stolen, rc = _run(
        _pre_fix(_statement_payload()), **{TOKEN_VARS[0]: FAKE_TOKEN}
    )
    assert stolen is not None, (
        f"the pre-fix body did not execute the `domains` payload, so this "
        f"control proves nothing about what the fix prevents. Output: {out}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel holds {stolen.strip()!r}, not the write token — the "
        f"payload ran but reached something else, and the claim under test is "
        f"WHICH credential it reached"
    )
    assert f"CALLED Domains=[{LEGAL_DOMAINS}]" in out, (
        f"the exploited run did not go on to call the script with a legal domain "
        f"list, so it would not have looked like a normal run: {out}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — the point of this control is "
        f"that the theft is invisible in the run's outcome: {out}"
    )


def test_the_pre_fix_target_position_stole_a_write_token_and_exited_zero():
    """The same, for the hash-literal position, with a payload built FOR it.

    The statement-level payload is a ParserError here, and a module that reused
    it would report this position as unexploitable — the flattering direction.
    """
    out, stolen, rc = _run(
        _pre_fix(LEGAL_DOMAINS, _hashtable_payload()),
        **{TOKEN_VARS[0]: FAKE_TOKEN},
    )
    assert stolen is not None, (
        f"the pre-fix body did not execute the `target` payload, so the second "
        f"injection point has no control. Output: {out}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel holds {stolen.strip()!r}, not the write token: {out}"
    )
    assert f"CALLED Domains=[{LEGAL_DOMAINS}] Target=[{LEGAL_TARGET}]" in out, (
        f"the exploited run did not go on to call the script with a legal "
        f"target, so it would not have looked like a normal run: {out}"
    )
    assert rc == 0, f"the exploited run exited {rc}, not 0: {out}"


def test_the_statement_payload_is_a_parse_error_in_the_hashtable_position():
    """The wrong payload for the position, kept as an assertion (lane 9's lesson).

    Chaining with `;` inside a `@{ }` literal is not a statement — it is a
    malformed hashtable entry, so pwsh rejects the file before executing
    anything and no sentinel is written. That result reads as "this position was
    never injectable", which is why the correct payload above exists and why
    this is pinned rather than left in a comment.
    """
    out, stolen, rc = _run(
        _pre_fix(LEGAL_DOMAINS, _statement_payload()),
        **{TOKEN_VARS[0]: FAKE_TOKEN},
    )
    assert stolen is None, (
        f"the statement-level payload DID execute in the hashtable position, "
        f"which contradicts this module's stated reason for using a different "
        f"payload there — re-read the docstring. Output: {out}"
    )
    assert rc != 0, (
        f"expected pwsh to reject the malformed hashtable (rc != 0), got {rc}: "
        f"{out}"
    )
    assert "CALLED" not in out, f"the script ran despite the parse error: {out}"


def test_the_subexpression_payload_is_inert_in_single_quotes():
    """The control that would have "proved" there was no defect here (L243).

    119 interpolated into SINGLE quotes, so the `$( )` payload that exploited
    every double-quoted lane does not expand. A module that reached for the
    familiar payload would report the pre-fix body as harmless.
    """
    out, stolen, rc = _run(
        _pre_fix(_subexpression_payload()), **{TOKEN_VARS[0]: FAKE_TOKEN}
    )
    assert stolen is None, (
        "the `$( )` payload executed inside single quotes, which contradicts "
        "this module's stated reason for using a quote break-out instead — "
        f"re-read the docstring. Output: {out}"
    )
    assert f"CALLED Domains=[{_subexpression_payload()}]" in out, (
        f"the subexpression did not reach the callee as literal text either, so "
        f"this test is no longer measuring the inertness it claims — asserted on "
        f"the callee's BINDING rather than on stdout containing the payload, "
        f"because pwsh echoes the offending source line back in a ParserError "
        f"and a substring predicate matches that too: {out}"
    )
    assert rc == 0, f"expected the inert run to exit 0, got {rc}: {out}"


def test_the_shipped_body_binds_both_payloads_as_data():
    """The whole point: through `env:`, each payload arrives as one argument."""
    step = _step()
    _assert_wiring(step)
    body = _render_dry_run(step["run"], "true")

    for var, payload, other in (
        ("IN_DOMAINS", _statement_payload(), {"IN_TARGET": LEGAL_TARGET}),
        ("IN_TARGET", _hashtable_payload(), {"IN_DOMAINS": LEGAL_DOMAINS}),
    ):
        overrides = {var: payload, TOKEN_VARS[0]: FAKE_TOKEN}
        overrides.update(other)
        out, stolen, rc = _run(body, **overrides)
        assert stolen is None, (
            f"the {var} payload still executed: the sentinel holds {stolen!r}. "
            f"The env: mapping is not doing what #1080 requires of it. "
            f"Output: {out}"
        )
        field = "Domains" if var == "IN_DOMAINS" else "Target"
        assert f"{field}=[{payload}]" in out, (
            f"the {var} payload did not arrive at the callee as one literal "
            f"argument, so it was neither executed nor bound as data — "
            f"something else happened and this test cannot tell what: {out}"
        )
        assert rc == 0, f"the shipped body exited {rc} on a legal-length input: {out}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    """The fix must not have made the step inert (ledger L202)."""
    out, _, rc = _run(
        _render_dry_run(_step()["run"], "true"),
        IN_DOMAINS=LEGAL_DOMAINS,
        IN_TARGET=LEGAL_TARGET,
        **{TOKEN_VARS[0]: FAKE_TOKEN},
    )
    assert f"CALLED Domains=[{LEGAL_DOMAINS}] Target=[{LEGAL_TARGET}] TargetBound=[True]" in out, (
        f"the ordinary path no longer reaches the script with both inputs bound: {out}"
    )
    assert rc == 0, f"the ordinary path exited {rc}: {out}"


def test_an_empty_domains_mapping_falls_back_to_the_default_list():
    """The behaviour a fail-closed guard would have broken.

    This is the case that makes 119 different from every other lane: an empty
    value must reach the default list, NOT abort and NOT bind empty. Pinned on
    the CONTENTS of the argument, because `Domains=[]` and `Domains=[<list>]`
    are the two things an empty mapping can produce and only one is correct.
    """
    for value in ("", "   "):
        out, _, rc = _run(
            _render_dry_run(_step()["run"], "true"),
            IN_DOMAINS=value,
            IN_TARGET=LEGAL_TARGET,
            **{TOKEN_VARS[0]: FAKE_TOKEN},
        )
        assert rc == 0, f"an empty `domains` input exited {rc}: {out}"
        assert A_DEFAULT_DOMAIN in out, (
            f"the default 13-domain list did not reach the script for "
            f"IN_DOMAINS={value!r} — the documented fallback is broken: {out}"
        )
        assert "Domains=[]" not in out, (
            f"an empty `domains` bound as an empty argument instead of falling "
            f"back: {out}"
        )
        assert "No 'domains' input provided" in out, (
            f"the fallback ran without saying so, so an operator reading the log "
            f"cannot tell which list was used: {out}"
        )


def test_an_empty_target_mapping_omits_the_argument_entirely():
    """An empty `target` must not bind as an empty argument.

    The callee falls back to the canonical Pages host when `Target` is blank, so
    binding empty happens to work today — but it makes the workflow depend on a
    branch inside the callee for correctness rather than on not passing an
    argument it does not have. Asserted on the ABSENCE of the parameter.
    """
    out, _, rc = _run(
        _render_dry_run(_step()["run"], "true"),
        IN_DOMAINS=LEGAL_DOMAINS,
        IN_TARGET="   ",
        **{TOKEN_VARS[0]: FAKE_TOKEN},
    )
    assert rc == 0, f"an empty `target` input exited {rc}: {out}"
    assert "TargetBound=[False]" in out, (
        f"-Target was BOUND despite an empty input, so the callee is relying on "
        f"its own IsNullOrWhiteSpace branch to repair an argument the workflow "
        f"should not have passed. Asserted on boundness rather than on the value, "
        f"because an omitted -Target and a `Target = ''` render identically: {out}"
    )
    assert f"CALLED Domains=[{LEGAL_DOMAINS}]" in out, (
        f"the domain list did not reach the callee on the empty-target path: {out}"
    )


def test_the_dry_run_switch_still_tracks_the_boolean():
    """`dry_run` stays interpolated, so its two branches are still pinned here."""
    step = _step()
    for value, expected in (("true", "DryRun=[True]"), ("false", "DryRun=[False]")):
        out, _, rc = _run(
            _render_dry_run(step["run"], value),
            IN_DOMAINS=LEGAL_DOMAINS,
            IN_TARGET=LEGAL_TARGET,
            **{TOKEN_VARS[0]: FAKE_TOKEN},
        )
        assert rc == 0, f"dry_run={value} exited {rc}: {out}"
        assert expected in out, f"dry_run={value} did not produce {expected}: {out}"


# The cases that spawn pwsh, and only those. A whole-module gate would report
# "everything passed" on a host with no PowerShell while every static assertion
# above went unmeasured (#1182); the roster is checked against the declared
# tests below so a rename cannot silently change what runs.
NEEDS_PWSH = {
    "test_an_empty_domains_mapping_falls_back_to_the_default_list",
    "test_an_empty_target_mapping_omits_the_argument_entirely",
    "test_the_dry_run_switch_still_tracks_the_boolean",
    "test_the_pre_fix_domains_position_stole_a_write_token_and_exited_zero",
    "test_the_pre_fix_target_position_stole_a_write_token_and_exited_zero",
    "test_the_shipped_body_binds_both_payloads_as_data",
    "test_the_shipped_body_still_passes_ordinary_inputs_through",
    "test_the_statement_payload_is_a_parse_error_in_the_hashtable_position",
    "test_the_subexpression_payload_is_inert_in_single_quotes",
}
TOOL_CASES = {"pwsh": NEEDS_PWSH}


def test_the_tool_case_list_names_tests_that_exist():
    """A stale name in NEEDS_PWSH silently changes what runs.

    Rename a case and its entry stops matching: it then runs on a host with no
    pwsh and reports a confusing FAIL, or — the direction that costs something —
    a case that DOES need pwsh is no longer listed and fails for want of it
    rather than being skipped.
    """
    declared = {k for k in globals() if k.startswith("test_")}
    for tool, names in sorted(TOOL_CASES.items()):
        unknown = sorted(names - declared)
        assert not unknown, (
            f"{tool} case list names tests that do not exist: {unknown}. "
            f"Update the set beside the rename."
        )


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
