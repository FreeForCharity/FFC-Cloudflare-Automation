"""Unit tests for 201's and 203's `output_file` call site (#1080 lane 26).

201 exports WHMCS domains and 203 exports WHMCS payment-method identifiers.
Each has ONE free-text dispatch input, `output_file`, which used to be
interpolated into its `whmcs-prod-read` pwsh body as `$out = "<expression>"`.
Both now read it from step-level `env:` (IN_OUTPUT_FILE).

ONE MODULE FOR TWO WORKFLOWS, ON PURPOSE
    The two bodies differ only in the callee script, the step name, the
    artifact name and 203's extra blank line. Lane 25 argued on 202 that one
    guard body over two values beats two copies, because duplicated guards are
    how one value silently keeps a check the other gains. This is that argument
    one level up: every case below runs over BOTH lanes, so "applied to both"
    is structural rather than reviewed. `test_the_two_lanes_are_still_twins`
    is what stops the two drifting apart underneath that claim.

MEASURED HERE, NOT CARRIED FROM LANE 24
    `$out = "<expression>"` is DOUBLE quoted, so `$( )` expands where it stands
    and no quote breakout is needed. Measured on pwsh 7.4.6 against BOTH bodies
    by the pre-fix control below, with a decoy WHMCS credential in the
    environment and a stub callee, for a value ending
    `$(Write-Host <marker>; $null = Set-Content -Path SENTINEL -Value
    "secret=$env:WHMCS_API_SECRET")`:

        pre-fix   the payload RAN, the sentinel held the decoy credential, the
                  callee bound `Out=[artifacts/whmcs/whmcs_domains.csv]` (and
                  `…whmcs_payment_methods.csv`) -- the LEGITIMATE path, because
                  `Set-Content` emits nothing -- and the step exited **0**.
                  Nothing in the run distinguishes it from an ordinary export.
        shipped   the same payload arrives as one literal filename, never runs,
                  writes no sentinel, and the step exits 1.

    `_pre_fix` DERIVES the shipped-before text from what ships now, asserting
    each anchor's count first (ledger L47), so the control cannot drift into
    vouching for a body nobody runs.

    What does NOT carry is the blank case, and this module says so because the
    opposite was written into these workflows first. 208's step ends at
    `$LASTEXITCODE`; 201 and 203 both assert `Test-Path $out` afterwards. So a
    blank `output_file` was NOT silent here the way it was on 208 — it
    surfaces as `Expected output file not found: ` with nothing after the
    colon. The blank guard's value here is that it names the INPUT, at the
    input, rather than leaving two downstream errors that name neither cause.
    Carrying 208's "silent at rc 0" sentence across on the strength of the two
    bodies looking alike is exactly ledger L260 — attribution is not evidence —
    and `test_both_bodies_assert_test_path_which_is_why_the_blank_case_is_loud`
    pins the structural difference so the claim cannot quietly come back.

    The load-bearing half of the guard block here is therefore the PATH guards.
    `output_file` is consumed a second time, raw, by each workflow's
    `Upload CSV Artifact` step, whose `path:` is an `@actions/glob` SELECTOR
    rather than a filename — so the junior consumer can be pointed at files the
    export never wrote, on a runner holding a WHMCS credential and the `az`
    session `whmcs-secrets-from-kv` leaves on disk (#1188/#1208). `[` and `]`
    are legal Windows filename characters AND glob metacharacters, which is the
    shape with no self-block; an absolute path, a `~` prefix or a `..` segment
    needs no glob at all.

THE PWSH GATE, AND WHY IT IS PER-CASE
    A host with no PowerShell SKIPS exactly the cases that spawn `pwsh` and
    runs the rest. Per #1182 the gate is never whole-module: a `shutil.which`
    around everything turns "could not run" into "everything passed".

    This lane was authored on a Linux cloud-worker sandbox, where the standing
    note is that no PowerShell host is on PATH -- true of the image, and NOT a
    limit on what can be measured there. The official `powershell-7.4.6-linux-
    x64.tar.gz` unpacks and runs in that sandbox, which is how every figure
    above was measured rather than remembered. Anything here that reads like a
    platform claim (Windows filename legality, `@actions/glob` behaviour) is
    still reasoned rather than measured; PowerShell's own behaviour is not.
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
_spec = importlib.util.spec_from_file_location("interp_guard_201_203", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


class Lane:
    """One workflow in this lane, and everything that differs between them."""

    def __init__(self, workflow, job, step, artifact_step, callee, legal_out):
        self.workflow = workflow
        self.job = job
        self.step = step
        self.artifact_step = artifact_step
        self.callee = callee
        self.legal_out = legal_out
        self.marker = "INJECTED-" + workflow.split("-", 1)[0]
        self.sentinel = "STOLEN-" + workflow.split("-", 1)[0] + ".txt"

    def __repr__(self):
        return f"<lane {self.workflow}>"

    def yaml(self):
        return load_workflow(self.workflow)

    def step_node(self):
        return find_step(self.yaml(), self.job, self.step)

    def body(self):
        return self.step_node()["run"]


LANES = (
    Lane(
        workflow="201-whmcs-export-domains.yml",
        job="export_domains",
        step="Export domains (read-only)",
        artifact_step="Upload CSV Artifact",
        callee="whmcs-domain-export.ps1",
        legal_out="artifacts/whmcs/whmcs_domains.csv",
    ),
    Lane(
        workflow="203-whmcs-export-payment-methods.yml",
        job="export_payment_methods",
        step="Export payment method identifiers (read-only)",
        artifact_step="Upload CSV Artifact",
        callee="whmcs-payment-methods-export.ps1",
        legal_out="artifacts/whmcs/whmcs_payment_methods.csv",
    ),
)

# 208 is read as a third party here, never edited. Lane 24 is where this guard
# set was designed, and two cases below compare against it: one asserts the six
# CONDITIONS are textually identical, so the three bodies cannot drift into
# subtly different rules, and one asserts the ONE structural difference the
# workflow comments here argue from.
LANE_24 = "208-whmcs-tickets-export.yml"

BURNED_INPUT = "output_file"
ENV_VAR = "IN_OUTPUT_FILE"
MAPPING = "${{ inputs.output_file }}"

# The variable that stands in for the workflow's own `env:` mapping. REMOVED
# unless a case supplies it, so an inherited one cannot satisfy an assertion the
# workflow is supposed to (ledger L199).
CONTROLLED_VARS = (ENV_VAR,)

# Decoys standing in for what `whmcs-secrets-from-kv` exports to GITHUB_ENV.
# Always set: they are what a payload is supposed to be able to reach, so
# clearing them would make the pre-fix control look harmless while the payload
# had in fact executed perfectly. The assertions read the sentinel's CONTENTS
# for that reason — its mere existence would score the same either way.
DECOY_CREDENTIALS = {
    "WHMCS_API_SECRET": "whmcs-secret-placeholder-not-a-real-credential",
    "WHMCS_APIM_SUBSCRIPTION_KEY": "apim-key-placeholder-not-a-real-credential",
}

# The six guards, by their CONDITION line. Each is paired with its own message
# fragment rather than the shared word `throw`: six blocks end in `throw`, so
# that anchor identifies none of them (the lesson lane 24 recorded when its
# path guards landed beside an emptiness guard that already used it).
GUARD_BLOCKS = (
    (
        "if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {",
        "output_file is blank.",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '[\r\n]') {",
        "carriage return or newline",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '[*?\[\]]') {",
        "glob metacharacter",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '^([A-Za-z]+:|[\\/])') {",
        "workspace-relative",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '^~') {",
        "must not begin with '~'",
    ),
    (
        r"if (($env:IN_OUTPUT_FILE -split '[\\/]') -contains '..') {",
        "'..' segment",
    ),
)

# The shipped spelling the pre-fix control rewrites, and what it became when
# GitHub pasted the value in as raw text instead.
SHIPPED_SITE = "$out = $env:IN_OUTPUT_FILE"
PRE_FIX_TEMPLATE = '$out = "{}"'

# A stand-in for the real exporter that records what it was BOUND. That is the
# only discriminator that works: a marker search over stdout is not one, because
# pwsh echoes offending source back in a binder error, so a substring predicate
# matches the payload text on a run that executed nothing.
STUB = """param(
    [Parameter()]
    [string]$ApiUrl,
    [Parameter()]
    [string]$OutputFile = 'UNBOUND'
)
[Console]::Error.WriteLine("CALLED Out=[$OutputFile]")
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -Path $OutputFile -Value 'a,b'
}
exit 0
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that
# omits it pins the wrong exit code and vouches for it (#1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's devising, so a spelling the checker recognises cannot slip
    past the step-level assertion and the two cannot drift apart.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _strip_guards(lane: Lane, body: str) -> str:
    """Remove all six guards, asserting each was there first.

    Counts are asserted BEFORE substituting (ledger L47): an anchor that
    stopped matching must fail loudly rather than silently leave the body
    unchanged and score the control as a pass.

    Every lookup is guarded by an assertion rather than a bare `str.index`. A
    ValueError is not an AssertionError, so the module runner would not catch
    it — it would abort the roster and every case after this one would report
    no outcome at all, which a reviewer counting FAIL lines scores as passing
    (ledger L194).
    """
    for anchor, terminator in GUARD_BLOCKS:
        assert body.count(anchor) == 1, (
            f"{lane.workflow}: expected exactly one {anchor!r} to strip, found "
            f"{body.count(anchor)} — this control would otherwise measure an "
            f"unmodified body. Body: {body!r}"
        )
        start = body.index(anchor)
        assert terminator in body[start:], (
            f"{lane.workflow}: the guard at {anchor!r} no longer contains "
            f"{terminator!r}, so this control cannot locate its end. "
            f"Body: {body!r}"
        )
        end = body.index("}", body.index(terminator, start)) + 1
        body = body[:start] + body[end:]
    for anchor, _ in GUARD_BLOCKS:
        assert anchor not in body, (
            f"{lane.workflow}: a guard survived the strip, so the control is "
            f"measuring the guarded body. Stripped: {body!r}"
        )
    assert lane.callee in body, (
        f"{lane.workflow}: the strip removed the invocation itself, so the "
        f"control proves nothing. Stripped: {body!r}"
    )
    return body


def _pre_fix(lane: Lane, output_file: str) -> str:
    """The body as it shipped BEFORE this lane, with GitHub's substitution done.

    DERIVED from the current body rather than kept as a second copy, so the
    control cannot drift from what ships: only the spelling the burn-down
    changed is rewritten, and the guard the burn-down added is stripped — it is
    part of the remedy, and leaving it in would make the control refuse the
    payload for the reason under test rather than executing it.
    """
    body = _strip_guards(lane, lane.body())
    assert body.count(SHIPPED_SITE) == 1, (
        f"{lane.workflow}: expected exactly one {SHIPPED_SITE!r} to rewrite, "
        f"found {body.count(SHIPPED_SITE)} — the pre-fix control would "
        f"otherwise measure a body that is not the shipped one. Body: {body!r}"
    )
    return body.replace(SHIPPED_SITE, PRE_FIX_TEMPLATE.format(output_file))


def _run(lane: Lane, body: str, **env_overrides):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd with the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test
    is which credential the payload reached.

    `stdin=DEVNULL` is load-bearing, not hygiene. An unsatisfied parameter
    makes PowerShell PROMPT for it, and on an interactive stdin the call blocks
    forever. A runner's stdin is not a terminal, so DEVNULL is the faithful
    shape too.

    A variable is REMOVED when its override is None, rather than set to "": the
    two blank forms differ and conflating them is what L291 is about.

    GITHUB_STEP_SUMMARY is pointed at a real file because these bodies append
    to it — unlike 208's, which is why lane 24's harness did not need this. An
    unset value would make `Out-File -FilePath $null` throw, turning every case
    red for a reason that has nothing to do with the input under test.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / lane.callee).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8", newline="\n"
        )
        summary = tmp / "step_summary.md"
        summary.write_text("", encoding="utf-8", newline="\n")
        concrete = {k: v for k, v in env_overrides.items() if v is not None}
        env = child_env(
            **DECOY_CREDENTIALS,
            GITHUB_STEP_SUMMARY=str(summary),
            **concrete,
        )
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
        stolen = tmp / lane.sentinel
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _bound(output: str) -> str:
    """The stub's `CALLED …` line, or "" when the callee never ran."""
    for line in output.splitlines():
        if line.startswith("CALLED "):
            return line
    return ""


def _executed(output: str, marker: str) -> bool:
    """Did the payload RUN, as opposed to merely appearing in the output?

    `marker in output` is not the question, and getting that wrong is the whole
    reason this helper exists. When the remedy works the payload arrives at the
    callee as an ordinary argument VALUE — so the stub's `CALLED …` line quotes
    it verbatim, and so does any pwsh error that echoes the offending argument
    back. A substring predicate therefore reports "the payload executed" for
    the run in which it provably did not, which is the reassuring direction: it
    turns the fixed body's proof into a failure and pushes the next reader to
    weaken the assertion rather than the predicate.

    `Write-Host` emits the marker as a line of its OWN, and nothing that merely
    quotes the payload can produce that. So the discriminator is an exact
    whole-line match, with the sentinel file — written only by code that ran —
    as the independent second reading beside it.
    """
    return any(line.strip() == marker for line in output.splitlines())


def _subexpr_payload(lane: Lane) -> str:
    """No breakout needed: the shipped site sat inside DOUBLE quotes.

    `$null =` swallows Set-Content's (absent) output and the leading string
    leaves `$out` holding a legal path, so the run reads as ordinary.
    """
    return (
        lane.legal_out
        + "$(Write-Host '"
        + lane.marker
        + "'; $null = Set-Content -Path "
        + lane.sentinel
        + ' -Value "secret=$env:WHMCS_API_SECRET")'
    )


# --------------------------------------------------------------------------
# Wiring — pure YAML/AST, no tool required
# --------------------------------------------------------------------------


def test_the_input_travels_in_env_and_is_not_interpolated():
    """The mapping exists, the body reads it, and no site was reintroduced.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural case below would keep passing over plumbing that no
    longer exists.
    """
    for lane in LANES:
        env = lane.step_node().get("env") or {}
        body = lane.body()
        assert env.get(ENV_VAR) == MAPPING, (
            f"{lane.workflow}: step {lane.step!r} in job {lane.job!r} must map "
            f"{ENV_VAR} to {MAPPING} — its env: mapping is {env!r}"
        )
        assert f"$env:{ENV_VAR}" in body, (
            f"{lane.workflow}: maps {ENV_VAR} but never reads $env:{ENV_VAR} — "
            f"the env: block is decoration and the value reaches nothing. "
            f"Body: {body!r}"
        )
        reintroduced = _interpolated_inputs(body) & {BURNED_INPUT}
        assert not reintroduced, (
            f"{lane.workflow}: step {lane.step!r} interpolates "
            f"{sorted(reintroduced)} into its script body again (#1080): GitHub "
            f"substitutes that as raw text before the body is parsed, so a "
            f"dispatcher supplies code. Body: {body!r}"
        )


def test_the_variable_reaches_the_CALL_SITE_and_not_merely_the_body():
    """`$env:X in body` is too weak, because the guards read the variable too.

    Ledger L294. The #1080 remedy has two parts — a guard that reads the mapped
    variable, and a call site that passes it on — so a body that validates a
    value and then hands the callee a literal satisfies the wiring assertion
    above while the dispatcher's value reaches nothing.

    These two workflows invoke with NAMED arguments rather than a splat, so the
    call site is the `&` line itself: `-OutputFile $out` there, and `$out`
    assigned from the variable and from nothing else.
    """
    for lane in LANES:
        body = lane.body()
        invocation = [
            ln
            for ln in body.splitlines()
            if lane.callee in ln and ln.lstrip().startswith("&")
        ]
        assert len(invocation) == 1, (
            f"{lane.workflow}: expected exactly one line invoking "
            f"{lane.callee}; found {len(invocation)}. Body: {body!r}"
        )
        assert "-OutputFile $out" in invocation[0], (
            f"{lane.workflow}: the invocation must pass `-OutputFile $out`, or "
            f"the dispatched value reaches nothing however faithfully the env: "
            f"block maps it. Invocation: {invocation[0]!r}"
        )
        assert body.count(SHIPPED_SITE) == 1, (
            f"{lane.workflow}: `$out` must come from {ENV_VAR} exactly once — "
            f"a `$out` assigned from anything else silently ignores the input. "
            f"Body: {body!r}"
        )


def test_the_checker_agrees_both_workflows_are_burned_down():
    """The guard's freeze and this module must not be able to disagree.

    Both halves matter: an entry left in `KNOWN_UNGUARDED` after the fix makes
    the guard's stale-entry detection fail, and a site the guard still finds
    means the burn-down is incomplete however green this module is.
    """
    for lane in LANES:
        assert lane.workflow not in guard.KNOWN_UNGUARDED, (
            f"{lane.workflow} is burned down but is still listed in "
            f"KNOWN_UNGUARDED — the guard's stale-entry detection should be "
            f"failing on this tree"
        )
        findings = _interpolated_inputs(lane.body())
        assert not findings, (
            f"the checker still finds {sorted(findings)} interpolated in "
            f"{lane.workflow}'s step body"
        )


def test_output_file_fails_closed_because_the_artifact_step_shares_the_input():
    """The second consumer is what rules out default-fill — pin that it exists.

    If an upload step ever stops interpolating the raw input into its `path:`,
    the reason for failing closed rather than default-filling is gone and that
    body should be revisited. That is a reason to fail here, loudly, rather
    than to leave a comment in the workflow claiming a coupling that no longer
    holds.
    """
    for lane in LANES:
        upload = find_step(lane.yaml(), lane.job, lane.artifact_step)
        path = str((upload.get("with") or {}).get("path", ""))
        assert "inputs.output_file" in path, (
            f"{lane.workflow}: step {lane.artifact_step!r} no longer consumes "
            f"the output_file input in its path: ({path!r}) — the two-consumer "
            f"argument for failing closed rather than default-filling no longer "
            f"applies, so re-decide it rather than leaving the body's comment "
            f"asserting a coupling that is gone"
        )
        body = lane.body()
        for anchor, _ in GUARD_BLOCKS:
            assert anchor in body, (
                f"{lane.workflow}: the guard {anchor!r} is gone from the body "
                f"while the second consumer remains. All six exist because this "
                f"input reaches a glob-capable `path:` as well as this script. "
                f"Body: {body!r}"
            )


def test_the_jobs_still_enter_only_a_read_environment():
    """#1080's write half is finished; a lane must not reopen it.

    The whole blast-radius argument for these two — a payload runs under a
    read-scoped WHMCS credential — depends on this environment, so it is
    asserted rather than assumed.
    """
    for lane in LANES:
        job = lane.yaml()["jobs"][lane.job]
        env_name = job.get("environment")
        assert isinstance(env_name, str), (
            f"{lane.workflow}: job {lane.job!r} declares environment "
            f"{env_name!r}; this assertion reads only the bare-string shape"
        )
        assert not guard.is_write_environment(env_name), (
            f"{lane.workflow}: job {lane.job!r} now enters {env_name!r}, a "
            f"write environment. #1080's write half is closed and this "
            f"workflow was a read lane"
        )


def test_the_two_lanes_are_still_twins():
    """One module covers two workflows only while the two really are alike.

    The claim this module makes about itself — that running every case over
    both lanes makes "applied to both" structural — is empty if the two bodies
    drift into needing different treatment. Each of the six guard CONDITIONS
    must appear exactly once in BOTH bodies, and the shipped call site must be
    spelled identically in both.
    """
    for anchor, _ in GUARD_BLOCKS:
        counts = {lane.workflow: lane.body().count(anchor) for lane in LANES}
        assert set(counts.values()) == {1}, (
            f"the guard {anchor!r} does not appear exactly once in both lanes: "
            f"{counts}. Either the two bodies have diverged, in which case they "
            f"need separate treatment, or a guard was dropped from one of them"
        )
    counts = {lane.workflow: lane.body().count(SHIPPED_SITE) for lane in LANES}
    assert set(counts.values()) == {1}, (
        f"the call site {SHIPPED_SITE!r} is not spelled identically in both "
        f"lanes: {counts}"
    )


def test_all_three_bodies_carry_the_same_guard_conditions():
    """The three bodies must carry the SAME six rules, not six similar ones.

    This lane re-measured the injection rather than citing 208's (see the
    module docstring), so this case is not propping up a citation — it is
    stopping three copies of one guard set from drifting apart. A rule that
    holds on 208 and has quietly been relaxed on 201 is exactly the shape lane
    25 warned about when it replaced two copies with one loop; three
    workflows cannot share a loop, so they share an assertion instead.

    The message STRINGS deliberately are not compared — 208 names its artifact
    step `Upload CSV artifact` and these two name theirs `Upload CSV Artifact`,
    so the messages differ by design. The conditions are what decide behaviour.
    """
    lane_24_body = find_step(
        load_workflow(LANE_24), "export_tickets", "Export tickets (read-only)"
    )["run"]
    for anchor, _ in GUARD_BLOCKS:
        assert lane_24_body.count(anchor) == 1, (
            f"{LANE_24} no longer carries the guard condition {anchor!r} "
            f"exactly once (found {lane_24_body.count(anchor)}). Three "
            f"workflows share this guard set; if 208's rules changed "
            f"deliberately, change these two with it rather than relaxing "
            f"this check"
        )


def test_both_bodies_assert_test_path_which_is_why_the_blank_case_is_loud():
    """Pin the ONE structural difference from 208, because a comment claims it.

    208's step ends at `$LASTEXITCODE`; these two also assert
    `Test-Path $out`. That difference is the whole reason the workflow comments
    here say the blank case was already loud rather than repeating 208's
    "silent at rc 0" — ledger L260, attribution is not evidence. If a body ever
    loses that assertion the comment becomes false, so fail here instead of
    letting it rot.
    """
    for lane in LANES:
        body = lane.body()
        assert "Test-Path $out" in body, (
            f"{lane.workflow}: the body no longer asserts `Test-Path $out`. The "
            f"comment above its blank guard argues from that assertion — it "
            f"says the blank case surfaces downstream rather than silently, "
            f"which is what distinguishes this lane from 208's. Re-derive the "
            f"comment rather than deleting this check. Body: {body!r}"
        )
    lane_24_body = find_step(
        load_workflow(LANE_24), "export_tickets", "Export tickets (read-only)"
    )["run"]
    assert "Test-Path $out" not in lane_24_body, (
        f"{LANE_24} now asserts `Test-Path $out` too, so the difference this "
        f"lane's comments are written around no longer exists. The comments "
        f"say what 208 did NOT do; re-derive them"
    )


def test_the_guard_anchors_this_module_strips_are_really_in_the_body():
    """Ledger L47: prove the pre-fix control can still find what it removes.

    Without this, a refactor that renamed a guard would make `_strip_guards`
    fail — but only inside the pwsh-gated cases, which SKIP on a host with no
    pwsh. The control would then be silently unexercised on exactly the hosts
    that cannot notice.
    """
    for lane in LANES:
        body = lane.body()
        for anchor, _ in GUARD_BLOCKS:
            assert body.count(anchor) == 1, (
                f"{lane.workflow}: the pre-fix control's guard anchor {anchor!r} "
                f"appears {body.count(anchor)} times. Body: {body!r}"
            )
        stripped = _strip_guards(lane, body)
        # Each guard's own message, not the shared word `throw`, and checked
        # against the CODE before the invocation only — the comments above each
        # guard quote these fragments too (#1019).
        code = stripped.split(lane.callee)[0]
        for _, message in GUARD_BLOCKS:
            surviving = [
                line
                for line in code.splitlines()
                if message in line and not line.lstrip().startswith("#")
            ]
            assert not surviving, (
                f"{lane.workflow}: the strip left {message!r} behind in "
                f"executable code: {surviving!r}"
            )


# --------------------------------------------------------------------------
# The pre-fix control — the defect this lane removes, reproduced
# --------------------------------------------------------------------------


def test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero():
    """The double-quoted site: `$( )` expands, so no quote breakout is needed.

    This is the case that makes the lane worth doing, and it is reproduced on
    each body rather than cited from 208.
    """
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        out, stolen, rc = _run(lane, _pre_fix(lane, _subexpr_payload(lane)))
        assert _executed(out, lane.marker), (
            f"{lane.workflow}: the pre-fix control did not execute the payload, "
            f"so it does not reproduce the defect this lane removes. "
            f"Output: {out!r}"
        )
        assert (
            stolen is not None and DECOY_CREDENTIALS["WHMCS_API_SECRET"] in stolen
        ), (
            f"{lane.workflow}: the payload ran but did not reach the WHMCS "
            f"credential, so the control understates the finding. "
            f"Sentinel: {stolen!r}"
        )
        assert f"Out=[{lane.legal_out}]" in _bound(out), (
            f"{lane.workflow}: the payload ran but the callee did not bind the "
            f"legitimate path, so the run would NOT have looked ordinary — the "
            f"finding is that nothing distinguishes it. Bound: {_bound(out)!r}"
        )
        assert rc == 0, (
            f"{lane.workflow}: the pre-fix control exited {rc}; the finding is "
            f"that the injected run is indistinguishable from a clean one, and "
            f"a non-zero exit is a distinction. Output: {out!r}"
        )


def test_the_shipped_body_binds_the_subexpression_payload_as_data():
    """The remedy: the same payload arrives as a filename and runs nothing."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        payload = _subexpr_payload(lane)
        out, stolen, rc = _run(lane, lane.body(), IN_OUTPUT_FILE=payload)
        assert not _executed(out, lane.marker), (
            f"{lane.workflow}: the payload executed against the shipped body. "
            f"Output: {out!r}"
        )
        assert stolen is None, (
            f"{lane.workflow}: the shipped body let the payload reach a "
            f"credential. Sentinel: {stolen!r}"
        )
        # It is refused before it can be bound: the payload contains `(` `)` and
        # `$`, none of which this guard set cares about, but it also contains
        # `[` — no. It does not. The refusal here is not guaranteed, so assert
        # the property that IS: whatever happens, the payload never RAN, and if
        # the callee was reached at all it was reached with the payload as one
        # literal argument rather than as code.
        bound = _bound(out)
        if bound:
            assert payload in bound, (
                f"{lane.workflow}: the callee was called with something other "
                f"than the payload as a literal path. Bound: {bound!r}"
            )
        assert rc != 0, (
            f"{lane.workflow}: the step exited 0 with a payload as its output "
            f"path. The export cannot have written a file at that name, so a "
            f"clean exit means an assertion in the body stopped working. "
            f"Output: {out!r}"
        )


def test_the_shipped_body_passes_an_ordinary_value_through():
    """The other polarity: a legitimate path still works end to end.

    Without this every refusal below is satisfied by a body that refuses
    everything.
    """
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        out, stolen, rc = _run(lane, lane.body(), IN_OUTPUT_FILE=lane.legal_out)
        assert rc == 0, f"{lane.workflow}: a legitimate path failed. Output: {out!r}"
        assert f"Out=[{lane.legal_out}]" in _bound(out), (
            f"{lane.workflow}: the callee did not receive the legitimate path. "
            f"Bound: {_bound(out)!r}"
        )
        assert stolen is None, f"{lane.workflow}: unexpected sentinel {stolen!r}"


def test_the_declared_default_survives_its_own_guards():
    """The value a plain dispatch actually sends must not be refused.

    The guards are written against a path shape; the workflow declares a
    default of that shape. A guard that refuses its own declared default would
    break every ordinary dispatch, and nothing else here would catch it.
    """
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        declared = lane.yaml()[True]["workflow_dispatch"]["inputs"][BURNED_INPUT][
            "default"
        ]
        assert declared == lane.legal_out, (
            f"{lane.workflow}: declared default {declared!r} is not the value "
            f"this module exercises ({lane.legal_out!r}); update the module "
            f"rather than leaving the default untested"
        )
        out, _, rc = _run(lane, lane.body(), IN_OUTPUT_FILE=declared)
        assert rc == 0, (
            f"{lane.workflow}: the declared default {declared!r} is refused by "
            f"this step's own guards. Output: {out!r}"
        )


# --------------------------------------------------------------------------
# The guards, one case per refusal
# --------------------------------------------------------------------------


def _assert_refused(lane: Lane, value, fragment: str):
    out, stolen, rc = _run(lane, lane.body(), IN_OUTPUT_FILE=value)
    assert rc != 0, (
        f"{lane.workflow}: {value!r} was accepted. Output: {out!r}"
    )
    assert fragment in out, (
        f"{lane.workflow}: {value!r} failed, but not with the guard's message "
        f"({fragment!r}) — so it failed somewhere else and this case is not "
        f"measuring the guard. Output: {out!r}"
    )
    assert not _bound(out), (
        f"{lane.workflow}: the callee ran before the refusal, so the export had "
        f"already started. Bound: {_bound(out)!r}"
    )
    assert stolen is None, f"{lane.workflow}: unexpected sentinel {stolen!r}"


def test_a_blank_output_file_fails_closed_and_never_calls_the_script():
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, "", "output_file is blank.")


def test_an_unset_output_file_fails_closed_too():
    """`$null`, not `""` — the domain the move to env: widened (L291)."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, None, "output_file is blank.")


def test_a_whitespace_output_file_fails_closed_too():
    """The one blank form `-ne ''` would let through."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, "   ", "output_file is blank.")


def test_a_newline_in_output_file_is_refused():
    """And it must be refused FIRST, or the anchored guards see one line."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(
            lane,
            lane.legal_out + "\nC:/Users/runneradmin/.azure/msal_token_cache.json",
            "carriage return or newline",
        )


def test_a_glob_metacharacter_in_output_file_is_refused():
    """`[` and `]` are the shape with no self-block: legal filename, real glob."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, "artifacts/whmcs/[m]sal_token_cache.json", "glob metacharacter")
        _assert_refused(lane, "artifacts/whmcs/*.json", "glob metacharacter")


def test_an_absolute_or_drive_rooted_output_file_is_refused():
    """Including a multi-letter PSDrive, which a `[A-Za-z]:` test would admit."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        for value in (
            "/etc/passwd.csv",
            "C:/Users/runneradmin/x.csv",
            "Temp:/x.csv",
            "env:/x",
        ):
            _assert_refused(lane, value, "workspace-relative")


def test_a_tilde_prefixed_output_file_is_refused():
    """PowerShell expands `~`; `@actions/glob` does not (ledger L298)."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, "~/.azure/x.csv", "must not begin with '~'")


def test_a_dotdot_segment_in_output_file_is_refused():
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        _assert_refused(lane, "artifacts/../../x.csv", "'..' segment")


def test_the_path_guards_do_not_refuse_a_legitimate_relative_path():
    """The polarity that stops the guard set collapsing into "refuse all"."""
    test_the_input_travels_in_env_and_is_not_interpolated()
    for lane in LANES:
        for value in (
            "artifacts/whmcs/report.csv",
            "artifacts\\whmcs\\report.csv",
            "artifacts/whmcs/report-2026-09-23.csv",
        ):
            out, _, rc = _run(lane, lane.body(), IN_OUTPUT_FILE=value)
            assert rc == 0, (
                f"{lane.workflow}: the legitimate path {value!r} was refused. "
                f"Output: {out!r}"
            )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and
# checker-agreement cases are pure YAML/AST and must run on a host without pwsh
# installed (#1182 — a whole-module `shutil.which` gate turns "could not run"
# into "everything passed"). This set is scoped to exactly the cases that reach
# `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero",
    "test_the_shipped_body_binds_the_subexpression_payload_as_data",
    "test_the_shipped_body_passes_an_ordinary_value_through",
    "test_the_declared_default_survives_its_own_guards",
    "test_a_blank_output_file_fails_closed_and_never_calls_the_script",
    "test_an_unset_output_file_fails_closed_too",
    "test_a_whitespace_output_file_fails_closed_too",
    "test_a_newline_in_output_file_is_refused",
    "test_a_glob_metacharacter_in_output_file_is_refused",
    "test_an_absolute_or_drive_rooted_output_file_is_refused",
    "test_a_tilde_prefixed_output_file_is_refused",
    "test_a_dotdot_segment_in_output_file_is_refused",
    "test_the_path_guards_do_not_refuse_a_legitimate_relative_path",
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
