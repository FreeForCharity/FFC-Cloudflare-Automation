"""Unit tests for 304's three DKIM call sites (#1080 burn-down).

304 enables DKIM in FFC's Exchange Online and writes the selector CNAMEs to
Cloudflare. Its one free-text dispatch input, `domain`, used to be interpolated
into THREE pwsh bodies across three jobs and two gated environments —
`m365-prod` twice and `cloudflare-prod-write` once — so one dispatcher's text
ran three times, under two credentials, on one approval. All three now arrive
through step-level `env:`.

WHAT MADE THIS ENTRY DIFFERENT FROM THE OTHER BURN-DOWNS
    Not the number of call sites — WHERE the credential sat. In 112 and 704 the
    write credential was loaded in a NEIGHBOURING step, so the injected body had
    to reach for it. Both m365-prod steps here carry
    `FFC_EXO_CERT_PFX_BASE64` / `FFC_EXO_CERT_PASSWORD` in the SAME step's `env:`
    as the injection point: the Exchange Online certificate is already in the
    process environment of the body being injected into.
    `test_prefix_body_is_the_positive_control` measures exactly that, and it is
    what keeps the inertness tests from passing vacuously.

    Measured against the body as it shipped, with
    `domain = ffcworkingsite1.org$(Set-Content -Path STOLEN.txt -Value
    $env:FFC_EXO_CERT_PASSWORD)`:

        * the cert password was written to a file;
        * `m365-dkim.ps1` was then called with `Domain=[ffcworkingsite1.org]`,
          a legal domain, because the side effect left no residue;
        * the step exited **0**.

    A pwsh double-quoted string expands `$( )`, so the payload never had to
    break out of the quotes — being INSIDE them was enough. And the expansion
    happens while the ARGUMENT is being built, before `m365-dkim.ps1` is
    invoked, so nothing the callee validates can reach it (the same ordering
    that makes 112's `[ValidatePattern]` no defence).

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
    is honestly green while the step runs with an empty `-Domain`. Only a
    per-step assertion on the wiring can see that, which is `_assert_wiring`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

WORKFLOW = "304-m365-dkim-enable.yml"

# The env var the input must travel in, and the expression it must be mapped from.
ENV_VAR = "IN_DOMAIN"
EXPRESSION = "${{ inputs.domain }}"

# Every place `domain` reaches a script body: (job, step-name substring, the
# non-input expressions that body still contains and what the runner would put
# there). Driving all three sites from one table is the point — the defect was
# that they were three copies of the same line, and a module that checked one of
# them would have been green with two still interpolating.
CALL_SITES = {
    "exo_check": (
        "Check DKIM status",
        {"${{ steps.org.outputs.organization }}": "ffc.onmicrosoft.com"},
    ),
    "cloudflare_set": (
        "Create/Update DKIM selector CNAMEs",
        {
            "${{ needs.exo_check.outputs.selector1_target }}": "selector1-x._domainkey.ffc.onmicrosoft.com",
            "${{ needs.exo_check.outputs.selector2_target }}": "selector2-x._domainkey.ffc.onmicrosoft.com",
        },
    ),
    "exo_enable": (
        "Enable DKIM signing",
        {"${{ needs.exo_check.outputs.organization }}": "ffc.onmicrosoft.com"},
    ),
}

# The two m365-prod steps carry the Exchange Online certificate in their own
# `env:`. Asserted as wiring, because it is the reason this file's injection
# points were worth taking before the other 16 — if a future edit moves the
# credential out, the module's central claim stops being true and should be
# re-read rather than silently kept.
EXO_SECRET_VARS = ("FFC_EXO_CERT_PFX_BASE64", "FFC_EXO_CERT_PASSWORD")

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (
    ENV_VAR,
    "FFC_AZURE_CLIENT_ID",
    "FFC_AZURE_TENANT_ID",
    "FFC_EXO_CERT_PFX_BASE64",
    "FFC_EXO_CERT_PASSWORD",
)

SENTINEL = "STOLEN-304.txt"
# A payload valid in the position it lands in. It does not need to escape the
# quotes: pwsh expands `$( )` inside a double-quoted string, which is where every
# one of these three call sites put the value. It reads the credential out of the
# step's OWN env: block, and it evaluates to the empty string, so what reaches
# the callee is a legal domain and the run looks clean.
PAYLOAD = (
    "ffcworkingsite1.org$(Set-Content -Path '"
    + SENTINEL
    + "' -Value $env:FFC_EXO_CERT_PASSWORD)"
)
FAKE_CERT_PASSWORD = "p@ssw0rd-from-key-vault"

# The bodies as they shipped BEFORE the burn-down, kept verbatim as the positive
# controls for the inertness tests below.
PRE_FIX_BODIES = {
    "exo_check": (
        'pwsh -NoProfile -File scripts/m365-dkim.ps1 -Domain "DOMAIN_HERE" '
        '-Organization "ffc.onmicrosoft.com" -AppId "APP-ID" -TenantId "TENANT-ID" '
        "-CreateIfMissing -Confirm:$false\n"
    ),
    "cloudflare_set": (
        '$domain = "DOMAIN_HERE"\n'
        '$s1Name = "selector1._domainkey"\n'
        '$s1Target = "selector1-x._domainkey.ffc.onmicrosoft.com"\n'
        "pwsh -NoProfile -File Update-CloudflareDns.ps1 -Zone $domain "
        "-Name $s1Name -Type CNAME -Content $s1Target\n"
    ),
    "exo_enable": (
        'pwsh -NoProfile -File scripts/m365-dkim.ps1 -Domain "DOMAIN_HERE" '
        '-Organization "ffc.onmicrosoft.com" -AppId "APP-ID" -TenantId "TENANT-ID" '
        "-CreateIfMissing -Enable -Confirm:$false\n"
    ),
}

# Permissive stand-ins for the real scripts: they record what they were BOUND,
# which is the only discriminator that works here. A marker-string search over
# stdout is not one — pwsh echoes the offending source line back in a
# ParserError, so any substring predicate matches the payload text on a run that
# executed nothing. `SupportsShouldProcess` is required, not decoration: the real
# m365-dkim.ps1 declares it and the bodies pass `-Confirm:$false`, so a stub
# without it fails on the parameter rather than on the value under test.
DKIM_STUB = """[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Domain,
    [string]$Organization,
    [string]$AppId,
    [string]$TenantId,
    [switch]$CreateIfMissing,
    [switch]$Enable
)
Write-Output "CALLED Domain=[$Domain] Organization=[$Organization] AppId=[$AppId] TenantId=[$TenantId] Enable=[$Enable]"
"""

CF_STUB = """[CmdletBinding()]
param(
    [string]$Zone,
    [string]$Name,
    [string]$Type,
    [string]$Content
)
Write-Output "CALLED Zone=[$Zone] Name=[$Name] Type=[$Type] Content=[$Content]"
"""


def _step(job: str) -> dict:
    return find_step(load_workflow(WORKFLOW), job, CALL_SITES[job][0])


def _assert_wiring(job: str, step: dict) -> None:
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
        f"step {name!r} in job {job!r} must map {ENV_VAR} to {EXPRESSION} — "
        f"its env: mapping is {env!r}"
    )
    assert f"$env:{ENV_VAR}" in body, (
        f"step {name!r} in job {job!r} maps {ENV_VAR} but never reads "
        f"$env:{ENV_VAR} — the env: block is decoration and the value reaches "
        f"nothing. Body: {body!r}"
    )
    assert "inputs.domain" not in body, (
        f"step {name!r} in job {job!r} interpolates inputs.domain into its "
        f"script body again (#1080): under {load_workflow(WORKFLOW)['jobs'][job].get('environment')!r} "
        f"that is dispatcher text executed after the approval. Body: {body!r}"
    )


def _render(job: str, body: str) -> str:
    """Substitute the non-input expressions the way GitHub would.

    Each anchor's occurrence count is asserted BEFORE substituting (ledger L47):
    a body that stopped containing one must fail loudly rather than silently
    test a string nobody rendered.
    """
    for anchor, value in CALL_SITES[job][1].items():
        assert body.count(anchor) >= 1, (
            f"expected {anchor} in job {job!r}'s body and found none — the "
            f"step moved or was rewritten, so this render is testing nothing"
        )
        body = body.replace(anchor, value)
    assert "${{" not in body, (
        f"job {job!r}'s rendered body still holds an unsubstituted expression, "
        f"so pwsh would see literal `${{{{ … }}}}` text: {body!r}"
    )
    return body


def _run(body: str, **env_overrides: str) -> tuple[str, bool, int]:
    """Run a pwsh body in a temp cwd holding the stubs.

    Returns (output, sentinel_written, rc).
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-dkim.ps1").write_text(DKIM_STUB, encoding="utf-8")
        # 304 invokes this one by a bare relative name, so it lives at the root
        # of the working directory, not under scripts/.
        (tmp / "Update-CloudflareDns.ps1").write_text(CF_STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAIN would make
        # the missing-mapping test pass for the wrong reason, and an inherited
        # FFC_AZURE_* would let the credential assertions pass without the workflow
        # supplying anything.
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        return proc.stdout + proc.stderr, (tmp / SENTINEL).exists(), proc.returncode


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_all_three_call_sites_are_wired_through_env():
    for job in CALL_SITES:
        _assert_wiring(job, _step(job))


def test_the_two_exo_steps_still_hold_the_certificate_in_their_own_env():
    """Pins this module's central claim rather than leaving it as prose.

    If a later edit moves the Exchange Online certificate out of these steps,
    the reason 304 was taken ahead of the other frozen entries stops being
    true, and the docstring above should be re-read rather than quietly kept.
    """
    for job in ("exo_check", "exo_enable"):
        env = _step(job).get("env") or {}
        for var in EXO_SECRET_VARS:
            assert var in env, (
                f"job {job!r}'s DKIM step no longer carries {var} in its own "
                f"env: — its mapping is {env!r}"
            )


def test_the_scan_sees_three_distinct_jobs():
    """A guard against the table silently collapsing.

    `find_step` matches a NAME SUBSTRING, so two entries that resolved to the
    same step would make the loops above run twice over one site and report
    full coverage of three.
    """
    seen = {job: _step(job).get("name") for job in CALL_SITES}
    assert len(set(seen.values())) == 3, (
        f"the three call sites did not resolve to three distinct steps: {seen}"
    )
    jobs = load_workflow(WORKFLOW)["jobs"]
    environments = {job: jobs[job].get("environment") for job in CALL_SITES}
    assert set(environments.values()) == {"m365-prod", "cloudflare-prod-write"}, (
        f"the call sites no longer span the two gated environments this module "
        f"is about: {environments}"
    )


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_values_reach_the_scripts():
    for job in ("exo_check", "exo_enable"):
        step = _step(job)
        _assert_wiring(job, step)
        out, _, rc = _run(
            _render(job, step["run"]),
            IN_DOMAIN="ffcworkingsite1.org",
            FFC_AZURE_CLIENT_ID="APP-ID",
            FFC_AZURE_TENANT_ID="TENANT-ID",
        )
        assert rc == 0, f"job {job!r} step exited {rc}: {out}"
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, f"job {job!r}: {out}"
        # The credential arguments now come from env: too — measured, not assumed,
        # because the harness pops these vars unless a test supplies them.
        assert "AppId=[APP-ID] TenantId=[TENANT-ID]" in out, (
            f"job {job!r}: the AppId/TenantId did not reach the script through "
            f"env:, so the invocation is reading something else: {out}"
        )
    step = _step("cloudflare_set")
    _assert_wiring("cloudflare_set", step)
    out, _, rc = _run(
        _render("cloudflare_set", step["run"]), IN_DOMAIN="ffcworkingsite1.org"
    )
    assert rc == 0, f"cloudflare_set step exited {rc}: {out}"
    assert "CALLED Zone=[ffcworkingsite1.org] Name=[selector1._domainkey]" in out, out
    assert "Name=[selector2._domainkey]" in out, out


def test_injected_payload_arrives_as_data_at_every_call_site():
    """The whole point: the payload binds verbatim and runs nothing, three times."""
    for job in CALL_SITES:
        step = _step(job)
        _assert_wiring(job, step)
        out, injected, rc = _run(
            _render(job, step["run"]),
            IN_DOMAIN=PAYLOAD,
            FFC_EXO_CERT_PASSWORD=FAKE_CERT_PASSWORD,
        )
        assert not injected, (
            f"job {job!r}: the payload EXECUTED — {SENTINEL} was written from a "
            f"value that reached the body through env:. Output: {out}"
        )
        bound = "Zone" if job == "cloudflare_set" else "Domain"
        assert f"CALLED {bound}=[{PAYLOAD}]" in out, (
            f"job {job!r}: payload did not arrive verbatim, so something "
            f"re-parsed it (rc={rc}). Output: {out}"
        )


def test_prefix_body_is_the_positive_control():
    """The same payload against each PRE-FIX body executes and steals the cert.

    Without this, the inertness test above proves nothing: a payload that never
    worked in the first place also writes no sentinel. It pins both halves of
    the finding — that the code ran, and that what it could reach was the
    Exchange Online certificate password sitting in the step's own `env:`.
    """
    for job, template in PRE_FIX_BODIES.items():
        assert template.count("DOMAIN_HERE") == 1, (
            f"job {job!r}'s pre-fix control has "
            f"{template.count('DOMAIN_HERE')} substitution points, expected 1"
        )
        out, injected, rc = _run(
            template.replace("DOMAIN_HERE", PAYLOAD),
            FFC_EXO_CERT_PASSWORD=FAKE_CERT_PASSWORD,
        )
        assert injected, (
            f"job {job!r}: the pre-fix body did NOT execute the payload, so "
            f"this control cannot support the inertness test above. Output: {out}"
        )
        # The residue is a LEGAL domain and the step succeeds — which is why no
        # triage heuristic that looks for a red step would ever have found this.
        bound = "Zone" if job == "cloudflare_set" else "Domain"
        assert f"CALLED {bound}=[ffcworkingsite1.org]" in out, (
            f"job {job!r}: expected the payload to leave a legal-looking value "
            f"behind. Output: {out}"
        )
        assert rc == 0, (
            f"job {job!r}: expected the exploited pre-fix run to exit 0 — the "
            f"whole point is that it looks clean. rc={rc}. Output: {out}"
        )


def test_the_stolen_credential_is_really_the_steps_own_env():
    """Separates 'the payload ran' from 'the payload reached the certificate'.

    The control above asserts a file was written; this asserts WHAT was in it.
    Without it, a sentinel written from an unset variable would score the same
    as one written from the live credential, and the module's claim about where
    the credential sits would be untested.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-dkim.ps1").write_text(DKIM_STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            PRE_FIX_BODIES["exo_check"].replace("DOMAIN_HERE", PAYLOAD),
            encoding="utf-8",
        )
        env = child_env(FFC_EXO_CERT_PASSWORD=FAKE_CERT_PASSWORD)
        env.pop(ENV_VAR, None)
        subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        stolen = tmp / SENTINEL
        assert stolen.exists(), "the pre-fix control did not write the sentinel"
        assert FAKE_CERT_PASSWORD in stolen.read_text(encoding="utf-8"), (
            f"the sentinel was written but does not contain the certificate "
            f"password, so this control does not show the credential was "
            f"reachable: {stolen.read_text(encoding='utf-8')!r}"
        )


def test_missing_env_mapping_aborts_before_the_script_runs():
    """A deleted or misspelled `env:` mapping must be loud, not silently empty.

    The checker cannot see this (nothing is interpolated either way, ledger
    L202), and the callee would report it as a domain that is not in the tenant
    or a zone that does not exist — sending the next reader to the dispatch form
    rather than to the workflow.
    """
    for job in CALL_SITES:
        step = _step(job)
        out, _, rc = _run(_render(job, step["run"]))
        assert rc != 0, (
            f"job {job!r}: an empty env: mapping did not fail the step. "
            f"Output: {out}"
        )
        # Matched against the `::error::` ANNOTATION, not against a throw's text.
        # A thrown message reaches stdout only through pwsh's error renderer, and
        # an assertion on a render is an assertion about the host that produced
        # it — that is how an earlier burn-down passed locally and failed on the
        # runner (ledger L207). `Write-Output` text is passed through unmodified.
        assert f"::error::{ENV_VAR} is empty" in out, (
            f"job {job!r}: step failed but did not emit the annotation naming "
            f"the cause: {out}"
        )
        assert "the step-level env: mapping is missing or misnamed" in out, (
            f"job {job!r}: the annotation was emitted but not with the full "
            f"diagnosis: {out}"
        )
        assert "CALLED" not in out, (
            f"job {job!r}: the script ran anyway, with an empty value: {out}"
        )


def test_whitespace_only_domain_is_also_rejected():
    """`required: true` stops an OMITTED value, not a blank one.

    A dispatcher can type a space into the form. It is the same empty -Domain
    the missing-mapping check exists for, so it must take the same path — an
    `-eq ''` test would let it through.
    """
    for job in CALL_SITES:
        step = _step(job)
        out, _, rc = _run(_render(job, step["run"]), IN_DOMAIN="   ")
        assert rc != 0, f"job {job!r}: a whitespace-only domain was accepted: {out}"
        assert "CALLED" not in out, (
            f"job {job!r}: the script ran with a whitespace-only value: {out}"
        )


def test_an_empty_credential_does_not_shift_the_argument_binding():
    """An unset `$env:` reference must pass an empty string, not vanish.

    Found while addressing the #1141 review. Replacing `"${{ secrets.X }}"` with a
    bare `$env:X` reads as a pure substitution and is not one: pwsh renders an
    empty variable as NO argument, so the next token binds to the parameter and
    every argument after it shifts. The unquoted form failed here with
    `Missing an argument for parameter 'AppId'` — a value problem reported as a
    call-signature problem, one parameter to the left of the real cause.

    The presence check in `exo_check` makes an empty credential unreachable in
    production today. That is the reason to pin it rather than the reason not to:
    the property this test defends is invisible on every run where the guard
    upstream is doing its job, so nothing else would report its loss.
    """
    for job in ("exo_check", "exo_enable"):
        step = _step(job)
        out, _, rc = _run(
            _render(job, step["run"]),
            IN_DOMAIN="ffcworkingsite1.org",
        )
        assert rc == 0, f"job {job!r}: an empty credential broke the call: {out}"
        assert "Missing an argument for parameter" not in out, (
            f"job {job!r}: the arguments shifted — an empty $env: reference is "
            f"being rendered as no argument at all. Quote it. Output: {out}"
        )
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, (
            f"job {job!r}: the domain no longer binds correctly: {out}"
        )
        assert "AppId=[] TenantId=[]" in out, (
            f"job {job!r}: expected the empty credentials to bind as empty "
            f"strings: {out}"
        )


def test_no_secret_is_interpolated_into_any_run_body():
    """No `${{ secrets.* }}` reaches a `run:` body anywhere in this workflow.

    Raised in review on #1141 as a duplication/drift risk — the DKIM steps mapped
    the credentials in `env:` and then re-interpolated the same values into the
    invocation. It is more than tidiness: the runner writes a `run:` body to a
    SCRIPT FILE in the workspace before executing it, so the interpolated form put
    the plaintext credential on disk — in the very workspace a payload injected
    through `domain` would have been running in. The two findings compound, which
    is why this is asserted here rather than left to a later cleanup.

    Swept over every job and step rather than the three call sites, because the
    worst instance was neither of them: `Validate required secrets` named all four
    secrets, the certificate and its password included.

    `uses:`/`with:` and `env:` are untouched by design — an action input and a
    step-level mapping are not script text, and `env:` is the remedy. A guard that
    flagged them would have to be switched off to land the fix (ledger L27).
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


def test_the_secret_presence_check_reads_them_from_env():
    """The step that names every secret must map them, not interpolate them.

    Pinned separately from the sweep above because this step is the reason the
    sweep exists, and because its check stays correct under a deleted mapping
    (an absent mapping and an absent secret both arrive empty, and both are what
    it exits 1 on) — so nothing else would notice if it regressed.
    """
    step = find_step(load_workflow(WORKFLOW), "exo_check", "Validate required secrets")
    env = step.get("env") or {}
    for var in ("FFC_AZURE_CLIENT_ID", "FFC_AZURE_TENANT_ID") + EXO_SECRET_VARS:
        assert env.get(var) == "${{ secrets.%s }}" % var, (
            f"the secret presence check must map {var} at step level — its "
            f"env: mapping is {env!r}"
        )
        assert var in step.get("run", ""), (
            f"{var} is mapped but the check never reads it, so the presence "
            f"check silently stopped covering it"
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
