"""Unit tests for 303's two `domain` call sites (#1080 burn-down).

303 reports a domain's M365 status and checks/enables its DKIM configuration.
Its one free-text dispatch input, `domain`, used to be interpolated into TWO
pwsh bodies in a single `m365-prod` job. Both now arrive through step-level
`env:`.

WHERE THE CREDENTIAL SAT — AND WHY IT IS WORSE HERE THAN IN 304
    304 was taken ahead of its siblings because its two m365-prod steps carried
    the Exchange Online certificate in the SAME step's `env:` as the injection
    point. 303 is that shape widened: the certificate and its password are on
    the JOB (`EXO_CERT_PFX_BASE64` / `EXO_CERT_PFX_PASSWORD`), so they are in
    the process environment of EVERY step — including the `status` step, which
    reads as the harmless half of the workflow and takes no `-Enable`. The DKIM
    step then adds `EXO_APP_ID` / `EXO_TENANT` / `EXO_ORGANIZATION` in its own
    `env:` on top.

    A job-level mapping is the easier one to miss precisely because it is not
    next to the `run:` you are reading, which is the practical correction to
    L213's "read the step's env: before its run:" — read the JOB's too, and
    treat every step in that job as holding what the job holds.
    `test_the_certificate_is_on_the_job_so_both_steps_hold_it` pins it, and
    `test_prefix_body_is_the_positive_control` measures it: the payload steals
    the certificate password from the STATUS step, which maps nothing itself.

    Measured against the bodies as they shipped, with
    `domain = ffcworkingsite1.org$(Set-Content -Path STOLEN-303.txt -Value
    $env:EXO_CERT_PFX_PASSWORD)`:

        * the certificate password was written to a file;
        * the callee was then invoked with `Domain=[ffcworkingsite1.org]`, a
          legal domain, because the side effect left no residue;
        * the step exited **0**.

    A pwsh double-quoted string expands `$( )`, so the payload never had to
    break out of the quotes — being INSIDE them was enough. The expansion
    happens while the ARGUMENT is being built, before the callee runs, so
    nothing it validates can reach it: `m365-domain-status.ps1` declares
    `-Domain` `Mandatory` and that is no defence at all.

WHAT IS STILL INTERPOLATED, AND WHY IT IS NOT A FINDING
    `action` (`choice`) and `create_if_missing` (`boolean`) remain in the DKIM
    body. GitHub constrains both to a value it generated itself, so neither can
    carry a payload. `test_the_remaining_interpolations_are_constrained_types`
    asserts that reason rather than leaving it as a comment — if either input
    is ever widened to free text, the body becomes a call site again and the
    check that would notice is this one, not the workflow diff.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
    is honestly green over a step that can no longer receive its input at all.
    Measured, not argued: mutations M1 and M2 of this PR's review delete the
    mapping from each step in turn, and
    `scripts/check-workflow-input-interpolation.py` exits **0** for both while
    this module fails. The emptiness check downgrades the consequence from a
    silent success to a stated failure; it does not make the regression
    visible. Only a per-step assertion on the wiring does, which is
    `_assert_wiring`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

WORKFLOW = "303-m365-domain-and-dkim.yml"
JOB = "m365"

# The env var the input must travel in, and the expression it must be mapped from.
ENV_VAR = "IN_DOMAIN"
EXPRESSION = "${{ inputs.domain }}"

# Every place `domain` reaches a script body: the step-name substring, and the
# non-input expressions that body still contains with what the runner would put
# there. Driving both sites from one table is the point — they were two copies
# of the same defect, and a module that checked one would be green with the
# other still interpolating.
CALL_SITES = {
    "status": ("Domain status (Graph)", {}),
    "dkim": (
        "DKIM check / enable",
        {
            "${{ inputs.create_if_missing }}": "false",
            "${{ inputs.action }}": "dkim-check",
        },
    ),
}

# The certificate pair lives on the JOB, not on either step — which is the whole
# claim of this module. Asserted as wiring so that an edit moving it changes a
# test rather than quietly falsifying the docstring.
JOB_SECRET_VARS = ("EXO_CERT_PFX_BASE64", "EXO_CERT_PFX_PASSWORD")

# The DKIM step's own additions, on top of what the job supplies.
DKIM_STEP_SECRET_VARS = ("EXO_APP_ID", "EXO_TENANT", "EXO_ORGANIZATION")

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (ENV_VAR,) + JOB_SECRET_VARS + DKIM_STEP_SECRET_VARS

SENTINEL = "STOLEN-303.txt"
# A payload valid in the position it lands in. It does not need to escape the
# quotes: pwsh expands `$( )` inside a double-quoted string, which is where both
# call sites put the value. It reads the certificate password out of the JOB's
# env: block — reachable from either step — and evaluates to the empty string,
# so what reaches the callee is a legal domain and the run looks clean.
PAYLOAD = (
    "ffcworkingsite1.org$(Set-Content -Path '"
    + SENTINEL
    + "' -Value $env:EXO_CERT_PFX_PASSWORD)"
)
FAKE_CERT_PASSWORD = "p@ssw0rd-from-key-vault"

# The bodies as they shipped BEFORE the burn-down, kept verbatim as the positive
# controls for the inertness tests below.
PRE_FIX_BODIES = {
    "status": (
        "$ErrorActionPreference = 'Stop'\n"
        'pwsh -File scripts/m365-domain-status.ps1 -Domain "DOMAIN_HERE" -ShowDnsRecords\n'
    ),
    "dkim": (
        "$ErrorActionPreference = 'Stop'\n"
        '$domain = "DOMAIN_HERE"\n'
        "pwsh -File scripts/m365-dkim.ps1 -Domain $domain -CreateIfMissing\n"
    ),
}

# Permissive stand-ins for the real scripts: they record what they were BOUND,
# which is the only discriminator that works here. A marker-string search over
# stdout is not one — pwsh echoes the offending source line back in a
# ParserError, so any substring predicate matches the payload text on a run that
# executed nothing.
#
# `-Domain` is deliberately NOT Mandatory in these stubs even though the real
# m365-domain-status.ps1 declares it so, because the stub is here to measure the
# WORKFLOW's guard and a Mandatory parameter would measure the callee's binder
# instead. What the real Mandatory actually does with an empty value is worth
# stating, because the intuitive answer is wrong in the reassuring direction and
# it is the reason the emptiness guard is load-bearing rather than tidy —
# measured, with stdin non-interactive as it is on a runner:
#
#     $ErrorActionPreference = 'Stop'
#     pwsh -File scripts/m365-domain-status.ps1 -Domain $env:IN_DOMAIN -ShowDnsRecords
#     # -> m365-domain-status.ps1: Missing an argument for parameter 'Domain'.
#     # -> the step CONTINUES and exits 0
#
# It does not prompt and it does not stop. An empty `$env:X` vanishes as an
# argument entirely (ledger L214), the inner pwsh reports a *call-signature*
# problem for a *value* problem, and `$ErrorActionPreference = 'Stop'` does not
# propagate a native command's exit code — so the step goes GREEN having never
# run the script. `test_without_the_guard_a_missing_mapping_is_a_silent_success`
# is the positive control for that, and it is what makes the fail-closed test
# below a statement about the guard rather than about the stub.
STATUS_STUB = """[CmdletBinding()]
param(
    [string]$Domain,
    [string]$Auth,
    [string]$AccessToken,
    [string]$TenantId,
    [switch]$ShowDnsRecords
)
Write-Output "CALLED Domain=[$Domain] ShowDnsRecords=[$ShowDnsRecords]"
"""

# The real script's parameter block, reproduced only for the positive control
# below. Kept separate from STATUS_STUB so the discriminating tests are never
# measuring pwsh's binder by accident.
MANDATORY_STATUS_STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [switch]$ShowDnsRecords
)
Write-Output "CALLED Domain=[$Domain]"
"""

# `SupportsShouldProcess` is required, not decoration: the real m365-dkim.ps1
# declares it and the enable branches pass `-Confirm:$false`, so a stub without
# it fails on the parameter rather than on the value under test.
DKIM_STUB = """[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Domain,
    [switch]$Enable,
    [switch]$CreateIfMissing,
    [string]$Organization,
    [string]$AppId,
    [string]$TenantId
)
Write-Output "CALLED Domain=[$Domain] Enable=[$Enable] CreateIfMissing=[$CreateIfMissing]"
"""


def _step(site: str) -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, CALL_SITES[site][0])


def _assert_wiring(site: str, step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would call the script with an empty -Domain.

    The failure messages name the step, the variable and the whole `env:`
    mapping. Rendering a bare `step.get("env")` prints the literal `None` in
    exactly the case the assertion exists for, which is the least useful thing
    it could say.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    name = step.get("name")
    assert env.get(ENV_VAR) == EXPRESSION, (
        f"step {name!r} must map {ENV_VAR} to {EXPRESSION} — its env: mapping "
        f"is {env!r}"
    )
    assert f"$env:{ENV_VAR}" in body, (
        f"step {name!r} maps {ENV_VAR} but never reads $env:{ENV_VAR} — the "
        f"env: block is decoration and the value reaches nothing. Body: {body!r}"
    )
    assert "inputs.domain" not in body, (
        f"step {name!r} interpolates inputs.domain into its script body again "
        f"(#1080): under "
        f"{load_workflow(WORKFLOW)['jobs'][JOB].get('environment')!r} that is "
        f"dispatcher text executed after the approval. Body: {body!r}"
    )


def _render(site: str, body: str) -> str:
    """Substitute the non-input expressions the way GitHub would.

    Each anchor's occurrence count is asserted BEFORE substituting (ledger L47):
    a body that stopped containing one must fail loudly rather than silently
    test a string nobody rendered.
    """
    for anchor, value in CALL_SITES[site][1].items():
        assert body.count(anchor) >= 1, (
            f"expected {anchor} in site {site!r}'s body and found none — the "
            f"step moved or was rewritten, so this render is testing nothing"
        )
        body = body.replace(anchor, value)
    assert "${{" not in body, (
        f"site {site!r}'s rendered body still holds an unsubstituted "
        f"expression, so pwsh would see literal `${{{{ … }}}}` text: {body!r}"
    )
    return body


def _run(body: str, **env_overrides: str) -> tuple[str, bool, int]:
    """Run a pwsh body in a temp cwd holding the stubs.

    Returns (output, sentinel_written, rc).
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-domain-status.ps1").write_text(
            STATUS_STUB, encoding="utf-8"
        )
        (tmp / "scripts" / "m365-dkim.ps1").write_text(DKIM_STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAIN would
        # make the fail-closed test pass for the wrong reason, and an inherited
        # EXO_CERT_PFX_PASSWORD would let the theft assertion pass without the
        # job supplying anything.
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
        return proc.stdout + proc.stderr, (tmp / SENTINEL).exists(), proc.returncode


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_both_call_sites_are_wired_through_env():
    for site in CALL_SITES:
        _assert_wiring(site, _step(site))


def test_the_certificate_is_on_the_job_so_both_steps_hold_it():
    """Pins this module's central claim rather than leaving it as prose.

    The certificate pair is mapped on the JOB, which is why the `status` step —
    which maps nothing of its own — could still be used to steal it. If a later
    edit moves these onto individual steps, the reason 303 reads as broader
    than 304 stops being true and the docstring should be re-read.
    """
    workflow = load_workflow(WORKFLOW)
    job_env = workflow["jobs"][JOB].get("env") or {}
    for var in JOB_SECRET_VARS:
        assert job_env.get(var) == "${{ secrets.%s }}" % var, (
            f"{var} is no longer mapped at job level — the job's env: is "
            f"{job_env!r}"
        )
    status_env = _step("status").get("env") or {}
    assert not (set(status_env) & set(JOB_SECRET_VARS)), (
        f"the status step now maps the certificate itself, so it is no longer "
        f"the example of a step inheriting a credential it never named: "
        f"{status_env!r}"
    )
    dkim_env = _step("dkim").get("env") or {}
    for var in DKIM_STEP_SECRET_VARS:
        assert var in dkim_env, (
            f"the DKIM step no longer carries {var} in its own env: — its "
            f"mapping is {dkim_env!r}"
        )


def test_the_scan_sees_two_distinct_steps_in_one_gated_job():
    """A guard against the table silently collapsing.

    `find_step` matches a NAME SUBSTRING, so two entries that resolved to the
    same step would make the loops above run twice over one site and report
    full coverage of two.
    """
    seen = {site: _step(site).get("name") for site in CALL_SITES}
    assert len(set(seen.values())) == 2, (
        f"the two call sites did not resolve to two distinct steps: {seen}"
    )
    environment = load_workflow(WORKFLOW)["jobs"][JOB].get("environment")
    assert environment == "m365-prod", (
        f"the call sites no longer sit in the gated job this module is about: "
        f"job {JOB!r} declares environment {environment!r}"
    )


def test_the_remaining_interpolations_are_constrained_types():
    """`action` and `create_if_missing` stay in the body — for a stated reason.

    They are a `choice` and a `boolean`, so GitHub only ever substitutes a
    value it generated. Widening either to free text would make the DKIM body a
    #1080 call site again, and this assertion is what notices — the workflow
    diff would show an input-type change nowhere near the `run:` it re-opens.
    """
    on = load_workflow(WORKFLOW)
    trigger = on.get(True, on.get("on"))
    declared = trigger["workflow_dispatch"]["inputs"]
    body = _step("dkim").get("run", "")
    for name, expected in (("action", "choice"), ("create_if_missing", "boolean")):
        assert f"inputs.{name}" in body, (
            f"inputs.{name} is no longer interpolated into the DKIM body, so "
            f"this test is asserting a constraint on nothing. Body: {body!r}"
        )
        assert declared[name]["type"] == expected, (
            f"inputs.{name} is declared {declared[name].get('type')!r}, not "
            f"{expected!r} — it is interpolated into a pwsh body in a gated "
            f"job, so it must stay a type GitHub constrains (#1080)"
        )


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_values_reach_the_scripts():
    for site in CALL_SITES:
        step = _step(site)
        _assert_wiring(site, step)
        out, _, rc = _run(_render(site, step["run"]), IN_DOMAIN="ffcworkingsite1.org")
        assert rc == 0, f"site {site!r} step exited {rc}: {out}"
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, f"site {site!r}: {out}"


def test_injected_payload_arrives_as_data_at_both_call_sites():
    """The whole point: the payload binds verbatim and runs nothing, twice."""
    for site in CALL_SITES:
        step = _step(site)
        _assert_wiring(site, step)
        out, injected, rc = _run(
            _render(site, step["run"]),
            IN_DOMAIN=PAYLOAD,
            EXO_CERT_PFX_PASSWORD=FAKE_CERT_PASSWORD,
        )
        assert not injected, (
            f"site {site!r}: the payload EXECUTED — {SENTINEL} was written from "
            f"a value that reached the body through env:. Output: {out}"
        )
        assert f"CALLED Domain=[{PAYLOAD}]" in out, (
            f"site {site!r}: payload did not arrive verbatim, so something "
            f"re-parsed it (rc={rc}). Output: {out}"
        )


def test_prefix_body_is_the_positive_control():
    """The same payload against each PRE-FIX body executes and steals the cert.

    Without this, the inertness test above proves nothing: a payload that never
    worked in the first place also writes no sentinel. It pins both halves of
    the finding — that the code ran, and that what it could reach was the
    Exchange Online certificate password the JOB puts in every step's
    environment, including the `status` step that maps nothing itself.
    """
    for site, template in PRE_FIX_BODIES.items():
        assert template.count("DOMAIN_HERE") == 1, (
            f"site {site!r}'s pre-fix control has "
            f"{template.count('DOMAIN_HERE')} substitution points, expected 1"
        )
        out, injected, rc = _run(
            template.replace("DOMAIN_HERE", PAYLOAD),
            EXO_CERT_PFX_PASSWORD=FAKE_CERT_PASSWORD,
        )
        assert injected, (
            f"site {site!r}: the pre-fix body did NOT execute the payload, so "
            f"this control cannot support the inertness test above. Output: {out}"
        )
        # The residue is a LEGAL domain and the step succeeds — which is why no
        # triage heuristic that looks for a red step would ever have found this.
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, (
            f"site {site!r}: expected the payload to leave a legal-looking "
            f"value behind. Output: {out}"
        )
        assert rc == 0, (
            f"site {site!r}: expected the exploited pre-fix run to exit 0 — the "
            f"whole point is that it looks clean. rc={rc}. Output: {out}"
        )


def test_the_stolen_credential_is_really_the_jobs_certificate_password():
    """Separates 'the payload ran' from 'the payload reached the certificate'.

    The control above asserts a file was written; this asserts WHAT was in it,
    from the `status` step specifically — the one that maps no secret of its
    own. Without it, a sentinel written from an unset variable would score the
    same as one written from the live credential, and the module's claim about
    a job-level mapping reaching every step would be untested.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-domain-status.ps1").write_text(
            STATUS_STUB, encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(
            PRE_FIX_BODIES["status"].replace("DOMAIN_HERE", PAYLOAD), encoding="utf-8"
        )
        env = child_env(EXO_CERT_PFX_PASSWORD=FAKE_CERT_PASSWORD)
        env.pop(ENV_VAR, None)
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
        assert stolen.exists(), (
            f"the pre-fix status body did not write the sentinel, so there is "
            f"nothing to read: {proc.stdout + proc.stderr}"
        )
        assert FAKE_CERT_PASSWORD in stolen.read_text(encoding="utf-8"), (
            f"the sentinel does not contain the certificate password, so the "
            f"payload ran but reached nothing: {stolen.read_text(encoding='utf-8')!r}"
        )


def test_both_steps_fail_closed_when_the_mapping_is_missing():
    """An unmapped or misnamed env: var must stop the step, not shift the call.

    Ledger L214: swapping a quoted interpolation for a bare `$env:X` is not a
    pure substitution — pwsh renders an empty variable as NO ARGUMENT AT ALL,
    so the next token binds one parameter to the left and the diagnosis arrives
    as a call-signature error. The emptiness guard is what makes the bare form
    safe, and it is only load-bearing in the case production never reaches, so
    it needs its own test: `domain` is `required: true`, which means a real
    dispatch cannot produce this state and a deleted `env:` mapping can.
    """
    for site in CALL_SITES:
        step = _step(site)
        _assert_wiring(site, step)
        out, injected, rc = _run(_render(site, step["run"]))  # IN_DOMAIN unset
        assert rc == 1, (
            f"site {site!r} exited {rc} with {ENV_VAR} unset, expected 1: {out}"
        )
        # Asserting on the output, not only the code: a harness that cannot
        # start also exits non-zero, and this must be distinguishable from the
        # step refusing to run (CLAUDE.md's exit-code rule).
        assert f"::error::{ENV_VAR} is empty" in out, (
            f"site {site!r} exited 1 without naming {ENV_VAR} as the cause, so "
            f"the failure is indistinguishable from a broken harness: {out}"
        )
        assert "CALLED Domain=" not in out, (
            f"site {site!r} called the script anyway after reporting the empty "
            f"mapping: {out}"
        )
        assert not injected, f"site {site!r} wrote the sentinel: {out}"


def test_without_the_guard_a_missing_mapping_is_a_silent_success():
    """Positive control for the fail-closed test: prove the guard is load-bearing.

    Without it, the assertions above prove only that a step which exits 1 exits
    1. This runs the SAME body with the emptiness check stripped out and the
    callee's real `Mandatory` attribute in place — the configuration a reader
    would assume is already safe — and measures what actually happens:

        * `-Domain` receives nothing, so the argument vanishes rather than
          arriving empty (ledger L214);
        * pwsh reports `Missing an argument for parameter 'Domain'`, a
          call-signature error for a value problem;
        * the step CONTINUES and exits **0**, because
          `$ErrorActionPreference = 'Stop'` does not propagate a native
          command's exit code.

    A green step that never ran the script is the silent-success shape, and it
    is exactly what the guard converts into a stated cause.
    """
    step = _step("status")
    body = step["run"]
    # YAML strips the block scalar's common indentation, so the guard is not
    # found by its indented spelling — asserted rather than assumed (ledger L47).
    marker = "if ([string]::IsNullOrWhiteSpace"
    assert body.count(marker) == 1, (
        f"expected exactly one emptiness guard in the status body, found "
        f"{body.count(marker)} — this control would strip the wrong thing: {body!r}"
    )
    guard_start = body.index(marker)
    guard_end = body.index("}\n", guard_start) + 2
    stripped = body[:guard_start] + body[guard_end:]
    assert "IsNullOrWhiteSpace" not in stripped, (
        f"the emptiness guard was not removed, so this control is measuring "
        f"the guarded body: {stripped!r}"
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-domain-status.ps1").write_text(
            MANDATORY_STATUS_STUB, encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(stripped, encoding="utf-8")
        env = child_env()
        env.pop(ENV_VAR, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"the unguarded body exited {proc.returncode}; if the callee's own "
        f"Mandatory now stops the step, the fail-closed guard is no longer the "
        f"only thing holding this and the docstring should be re-read: {out}"
    )
    assert "Missing an argument for parameter 'Domain'" in out, (
        f"expected the vanished-argument diagnosis from the callee's binder, "
        f"so this control is measuring the case it claims to: {out}"
    )
    assert "CALLED Domain=" not in out, (
        f"the script ran despite the missing mapping, so the empty value did "
        f"not vanish and L214 does not apply here as stated: {out}"
    )


def test_no_step_interpolates_a_secret_into_its_body():
    """A `run:` body is a FILE on the runner before it is a program.

    So an interpolated secret is a credential written into the workspace — the
    same workspace a payload injected through `domain` would have been running
    in. Found on #1141's review round; swept here rather than checked at the
    three call sites I happened to touch, because the worst instance there was
    a step nobody had flagged.
    """
    jobs = load_workflow(WORKFLOW)["jobs"]
    offenders = [
        (job, step.get("name"))
        for job, spec in jobs.items()
        for step in spec.get("steps", [])
        if "secrets." in str(step.get("run", ""))
    ]
    assert not offenders, (
        f"these steps interpolate a secret into their script body, which "
        f"writes it to a file in the runner workspace: {offenders}"
    )
    # Positive control: the sweep must be looking at real bodies. Without it a
    # change that made `run` unreadable would report zero offenders and pass.
    bodies = [
        step.get("run", "")
        for spec in jobs.values()
        for step in spec.get("steps", [])
    ]
    assert sum(1 for b in bodies if b.strip()) >= 4, (
        f"the sweep found almost no run: bodies to check, so its empty result "
        f"says nothing: {bodies!r}"
    )


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
