"""Unit tests for 301's two `domain` call sites (#1080 burn-down).

301 preflights a domain against the FFC Microsoft tenant (Graph) and against
Cloudflare. Its one free-text dispatch input, `domain`, used to be interpolated
into TWO pwsh bodies in TWO different jobs. Both now arrive through step-level
`env:`.

WHERE THE CREDENTIAL SAT — AND WHY THE RECOMMENDED SWEEP MISSES IT
    303 and 304 were ranked by ledger L213: read the step's (and the job's)
    `env:` before its `run:`, because that is what an injected payload can reach
    without moving. Both times the answer was a `secrets.*` value — an Exchange
    Online certificate — and #1141's review generalized it into a scope test for
    the remaining lanes: sweep the file for `secrets.` in a `run:` body.

    301 is the case that test does not see. The first injection point maps

        GRAPH_ACCESS_TOKEN: ${{ steps.graph.outputs.access_token }}

    — a live Microsoft Graph bearer token, minted by the preceding step from the
    OIDC login. It is a credential by every operational measure and a step
    OUTPUT by every syntactic one: it is not in the secrets store, it is not in
    Key Vault, no `secrets.` appears anywhere in that step, and an `env:` block
    read by the shape of its right-hand side scores the site as mapping nothing
    sensitive at all. `test_the_graph_token_is_a_step_output_not_a_secret` pins
    that, because it is the whole reason this entry was worth taking out of
    order.

    The second call site holds no `env:` credential either, and for a different
    reason: `.github/actions/cloudflare-tokens-from-kv` exports the Key Vault
    Cloudflare tokens through `GITHUB_ENV`, so they are in the process
    environment of every later step in that job without appearing in any `env:`
    block a reviewer can read. Two sites, two credentials, and neither one is
    visible to a scan for `secrets.`.

    Measured against the bodies as they shipped, with
    `domain = ffcworkingsite1.org$(Set-Content -Path <sentinel> -Value
    $env:<credential>)`:

        * site 1 wrote the Graph bearer token to a file;
        * site 2 wrote the Cloudflare API token to a file;
        * both then invoked the callee with `Domain=[ffcworkingsite1.org]`, a
          legal domain, because the side effect left no residue;
        * both steps exited **0**.

    A pwsh double-quoted string expands `$( )`, so neither payload had to break
    out of the quotes — being INSIDE them was enough (ledger L213's companion).

TWO JOBS, TWO ENVIRONMENTS, ONE DISPATCH
    Unlike 303 and 304, 301's call sites are not in one job. `graph` runs on
    `m365-prod` (gated, and what makes this a WRITE entry in the freeze);
    `cloudflare` runs on `cloudflare-prod-read` and `needs: [graph]`. So a
    single payload in a single dispatch ran in both, under two environments, and
    the `-read` half is not the harmless half: a read-scoped Cloudflare token
    still enumerates every zone in both accounts.

    The file's own title says "(Read-only)". That is true of what the scripts do
    and false of what an injected body could do there — which is the reading
    that would have kept this entry near the bottom of a burn-down ordered by
    how a workflow describes itself.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
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

WORKFLOW = "301-m365-domain-preflight.yml"

# The env var the input must travel in, and the expression it must be mapped from.
ENV_VAR = "IN_DOMAIN"
EXPRESSION = "${{ inputs.domain }}"

# The credential each injection point could reach, and the variable it lives in.
# Neither is a `secrets.*` reference in the step that holds it: the first is a
# step output, the second is exported through GITHUB_ENV by a composite action.
GRAPH_TOKEN_VAR = "GRAPH_ACCESS_TOKEN"
GRAPH_TOKEN_EXPRESSION = "${{ steps.graph.outputs.access_token }}"
CF_TOKEN_VAR = "CLOUDFLARE_API_TOKEN_FFC"

FAKE_GRAPH_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.FAKE-GRAPH-BEARER"
FAKE_CF_TOKEN = "cf-read-token-from-key-vault-FAKE"

# Every place `domain` reaches a script body: the job, the step-name substring,
# the credential in reach, and the extra environment the body needs to run at
# all. Driving both sites from one table is the point — they were two copies of
# the same defect in two jobs, and a module that checked one would be green with
# the other still interpolating.
#
# `extra` is not fixture decoration: site 1 passes TWO env-sourced values in one
# invocation, so leaving GRAPH_ACCESS_TOKEN unset would drop `-AccessToken`'s
# argument and shift the call (ledger L214) — the test would then fail for a
# reason that has nothing to do with `domain`.
CALL_SITES = {
    "graph": {
        "job": "graph",
        "step": "Run domain preflight",
        "environment": "m365-prod",
        "credential": GRAPH_TOKEN_VAR,
        "credential_value": FAKE_GRAPH_TOKEN,
        "sentinel": "STOLEN-301-graph.txt",
        "extra": {GRAPH_TOKEN_VAR: FAKE_GRAPH_TOKEN},
    },
    "cloudflare": {
        "job": "cloudflare",
        "step": "Run Cloudflare audit portion",
        "environment": "cloudflare-prod-read",
        "credential": CF_TOKEN_VAR,
        "credential_value": FAKE_CF_TOKEN,
        "sentinel": "STOLEN-301-cf.txt",
        "extra": {CF_TOKEN_VAR: FAKE_CF_TOKEN},
    },
}

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (ENV_VAR, GRAPH_TOKEN_VAR, CF_TOKEN_VAR, "CLOUDFLARE_API_TOKEN_CM")

# The bodies as they shipped BEFORE the burn-down, kept verbatim as the positive
# controls for the inertness tests below. `DOMAIN_HERE` stands where GitHub
# substituted `${{ inputs.domain }}` — inside double quotes, where pwsh expands
# `$( )` without any quote break-out.
PRE_FIX_BODIES = {
    "graph": (
        "pwsh -NoProfile -File scripts/m365-domain-preflight.ps1 "
        '-Domain "DOMAIN_HERE" -AccessToken $env:GRAPH_ACCESS_TOKEN -SkipCloudflare\n'
    ),
    "cloudflare": (
        "$cfToken = if (-not [string]::IsNullOrWhiteSpace($env:CLOUDFLARE_API_TOKEN_FFC)) {\n"
        "  $env:CLOUDFLARE_API_TOKEN_FFC\n"
        "} else {\n"
        "  $env:CLOUDFLARE_API_TOKEN_CM\n"
        "}\n"
        "pwsh -NoProfile -File scripts/m365-domain-preflight.ps1 "
        '-Domain "DOMAIN_HERE" -SkipGraph -CloudflareToken $cfToken\n'
    ),
}

# A permissive stand-in for the real script: it records what it was BOUND, which
# is the only discriminator that works here. A marker-string search over stdout
# is not one — pwsh echoes the offending source line back in a ParserError, so
# any substring predicate matches the payload text on a run that executed
# nothing.
#
# `-Domain` is deliberately NOT Mandatory here even though the real
# m365-domain-preflight.ps1 declares it so, because this stub is here to measure
# the WORKFLOW's guard and a Mandatory parameter would measure the callee's
# binder instead. MANDATORY_STUB below is the one that reproduces the callee.
STUB = """[CmdletBinding()]
param(
    [string]$Domain,
    [string]$AccessToken,
    [string]$CloudflareToken,
    [switch]$SkipCloudflare,
    [switch]$SkipGraph
)
Write-Output "CALLED Domain=[$Domain]"
"""

# The real script's shape, reproduced only for the silent-success control below.
MANDATORY_STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$AccessToken,
    [string]$CloudflareToken,
    [switch]$SkipCloudflare,
    [switch]$SkipGraph
)
Write-Output "CALLED Domain=[$Domain]"
"""


def _step(site: str) -> dict:
    spec = CALL_SITES[site]
    return find_step(load_workflow(WORKFLOW), spec["job"], spec["step"])


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
    job = CALL_SITES[site]["job"]
    assert env.get(ENV_VAR) == EXPRESSION, (
        f"step {name!r} in job {job!r} must map {ENV_VAR} to {EXPRESSION} — its "
        f"env: mapping is {env!r}"
    )
    assert f"$env:{ENV_VAR}" in body, (
        f"step {name!r} in job {job!r} maps {ENV_VAR} but never reads "
        f"$env:{ENV_VAR} — the env: block is decoration and the value reaches "
        f"nothing. Body: {body!r}"
    )
    assert "inputs.domain" not in body, (
        f"step {name!r} in job {job!r} interpolates inputs.domain into its "
        f"script body again (#1080): under "
        f"{load_workflow(WORKFLOW)['jobs'][job].get('environment')!r} that is "
        f"dispatcher text executed after the approval. Body: {body!r}"
    )


def _payload(site: str) -> str:
    """A payload valid in the position it lands in, per site.

    It does not need to escape the quotes: pwsh expands `$( )` inside a
    double-quoted string, which is where both call sites put the value. It reads
    that site's credential and evaluates to the empty string, so what reaches
    the callee is a legal domain and the run looks clean.
    """
    spec = CALL_SITES[site]
    return (
        "ffcworkingsite1.org$(Set-Content -Path '"
        + spec["sentinel"]
        + "' -Value $env:"
        + spec["credential"]
        + ")"
    )


def _run(
    site: str, body: str, stub: str = STUB, **env_overrides: str
) -> tuple[str, str | None, int]:
    """Run a pwsh body in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test
    is which credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "m365-domain-preflight.ps1").write_text(
            stub, encoding="utf-8"
        )
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
        stolen = tmp / CALL_SITES[site]["sentinel"]
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_both_call_sites_are_wired_through_env():
    for site in CALL_SITES:
        _assert_wiring(site, _step(site))


def test_the_two_sites_are_distinct_steps_in_two_gated_jobs():
    """A guard against the table silently collapsing, and against the split.

    `find_step` matches a NAME SUBSTRING, so two entries that resolved to the
    same step would make the loops above run twice over one site and report full
    coverage of two. The environments are asserted here too: this entry is in
    the #1080 freeze as a WRITE one because of `m365-prod`, and the second job
    `needs:` the first, which is what makes one dispatch run a payload twice.
    """
    workflow = load_workflow(WORKFLOW)
    seen = {site: (spec["job"], _step(site).get("name")) for site, spec in CALL_SITES.items()}
    assert len(set(seen.values())) == 2, (
        f"the two call sites did not resolve to two distinct steps: {seen}"
    )
    for site, spec in CALL_SITES.items():
        declared = workflow["jobs"][spec["job"]].get("environment")
        assert declared == spec["environment"], (
            f"site {site!r} no longer sits in the environment this module is "
            f"about: job {spec['job']!r} declares {declared!r}, expected "
            f"{spec['environment']!r}"
        )
    needs = workflow["jobs"]["cloudflare"].get("needs")
    assert needs == ["graph"] or needs == "graph", (
        f"the cloudflare job no longer needs: [graph], so one dispatch no "
        f"longer runs both call sites: needs is {needs!r}"
    )


def test_the_graph_token_is_a_step_output_not_a_secret():
    """This module's central claim, pinned rather than left as prose.

    The credential in reach of the first injection point is mapped from a step
    OUTPUT. That is why the scope test #1141's review recommended for the
    remaining lanes — sweep the file for `secrets.` in a `run:` body — reports
    nothing for the step that holds a live Graph bearer token. If a later edit
    ever maps this from `secrets.*` instead, the reasoning in the docstring
    stops being true and should be re-read.
    """
    env = _step("graph").get("env") or {}
    assert env.get(GRAPH_TOKEN_VAR) == GRAPH_TOKEN_EXPRESSION, (
        f"the preflight step no longer maps {GRAPH_TOKEN_VAR} from the step "
        f"output — its env: mapping is {env!r}"
    )
    assert "secrets." not in GRAPH_TOKEN_EXPRESSION, (
        "the token expression now names a secret, so the claim that a "
        "`secrets.` sweep misses this site no longer holds"
    )
    # And the step that MINTS it masks it first. A runtime-derived value is not
    # masked the way a `secrets.*` value is — it never passed through the
    # secrets store — so without this the token is one `Write-Output` away from
    # the log. (Runner masking behaviour is GitHub's documented contract, not
    # something this sandbox can execute; what is asserted here is the ordering,
    # which is the part that can be got wrong locally.)
    minting = find_step(load_workflow(WORKFLOW), "graph", "Acquire Graph token")
    body = minting.get("run", "")
    assert "::add-mask::$token" in body, (
        f"the Graph token is written to a step output without being masked "
        f"first, so anything echoing it downstream prints a live bearer token "
        f"into the log. Body: {body!r}"
    )
    assert body.index("::add-mask::$token") < body.index("$GITHUB_OUTPUT"), (
        f"the mask is registered AFTER the token becomes a step output, which "
        f"leaves the window it exists to close. Body: {body!r}"
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


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_values_reach_the_scripts():
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, _, rc = _run(
            site, step["run"], IN_DOMAIN="ffcworkingsite1.org", **spec["extra"]
        )
        assert rc == 0, f"site {site!r} step exited {rc}: {out}"
        assert "CALLED Domain=[ffcworkingsite1.org]" in out, f"site {site!r}: {out}"


def test_injected_payload_arrives_as_data_at_both_call_sites():
    """The whole point: the payload binds verbatim and runs nothing, twice."""
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        payload = _payload(site)
        out, stolen, rc = _run(
            site, step["run"], IN_DOMAIN=payload, **spec["extra"]
        )
        assert stolen is None, (
            f"site {site!r}: the payload EXECUTED — {spec['sentinel']} was "
            f"written from a value that reached the body through env:, "
            f"containing {stolen!r}. Output: {out}"
        )
        assert f"CALLED Domain=[{payload}]" in out, (
            f"site {site!r}: payload did not arrive verbatim, so something "
            f"re-parsed it (rc={rc}). Output: {out}"
        )


def test_prefix_bodies_are_the_positive_control():
    """The same payload against each PRE-FIX body executes and steals a credential.

    Without this, the inertness test above proves nothing: a payload that never
    worked in the first place also writes no sentinel. It pins all three halves
    of the finding — that the code ran, that what it reached was the site's
    credential, and that the run then looked clean.
    """
    for site, spec in CALL_SITES.items():
        template = PRE_FIX_BODIES[site]
        assert template.count("DOMAIN_HERE") == 1, (
            f"site {site!r}'s pre-fix control has "
            f"{template.count('DOMAIN_HERE')} substitution points, expected 1"
        )
        payload = _payload(site)
        out, stolen, rc = _run(
            site, template.replace("DOMAIN_HERE", payload), **spec["extra"]
        )
        assert stolen is not None, (
            f"site {site!r}: the pre-fix body did NOT execute the payload, so "
            f"this control cannot support the inertness test above. Output: {out}"
        )
        # WHAT was stolen, not merely that something was written: a sentinel
        # written from an unset variable would score identically.
        assert spec["credential_value"] in stolen, (
            f"site {site!r}: the payload ran but reached nothing — the sentinel "
            f"holds {stolen!r}, expected the {spec['credential']} value"
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


def test_both_steps_fail_closed_when_the_mapping_is_missing():
    """An unmapped or misnamed env: var must stop the step, not shift the call.

    Ledger L214: swapping a quoted interpolation for a bare `$env:X` is not a
    pure substitution — pwsh renders an empty variable as NO ARGUMENT AT ALL, so
    the next token binds one parameter to the left and the diagnosis arrives as
    a call-signature error. The emptiness guard is what makes the bare form
    safe, and it is only load-bearing in the case production never reaches, so
    it needs its own test: `domain` is `required: true`, which means a real
    dispatch cannot produce this state and a deleted `env:` mapping can.
    """
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, stolen, rc = _run(site, step["run"], **spec["extra"])  # IN_DOMAIN unset
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
        assert stolen is None, f"site {site!r} wrote the sentinel: {out}"


def test_without_the_guard_a_missing_mapping_is_a_silent_success():
    """Positive control for the fail-closed test: prove the guard is load-bearing.

    Without it, the assertions above prove only that a step which exits 1 exits
    1. This runs the SAME body with the emptiness check stripped out and the
    callee's real `Mandatory` attribute in place — the configuration a reader
    would assume is already safe, because "the parameter is Mandatory" reads as
    a defence.

    It is not. An empty `$env:X` vanishes as an argument entirely, the inner
    pwsh reports a call-signature problem for a value problem, and
    `$ErrorActionPreference = 'Stop'` does not propagate a native command's exit
    code — so the step goes GREEN having never run the script.
    """
    for site, spec in CALL_SITES.items():
        body = _step(site)["run"]
        stripped = _strip_guard(site, body)
        out, _, rc = _run(site, stripped, stub=MANDATORY_STUB, **spec["extra"])
        assert rc == 0, (
            f"site {site!r}: expected the UNGUARDED body to exit 0 with "
            f"{ENV_VAR} unset — if it now fails on its own, the emptiness check "
            f"is no longer what stands between a deleted mapping and a silent "
            f"success, and this control is measuring something else. rc={rc}: "
            f"{out}"
        )
        assert "CALLED Domain=[" not in out, (
            f"site {site!r}: the script was invoked despite an empty -Domain, "
            f"so the arity shift this control is about did not happen: {out}"
        )


def _strip_guard(site: str, body: str) -> str:
    """Remove the emptiness check from a step body, asserting it was there.

    Anchored on the smallest span that is unique to the guard, and the
    occurrence count is asserted BEFORE substituting (ledger L47): an anchor
    that stopped matching must fail loudly rather than silently leave the body
    unchanged and score the control as a pass.
    """
    start = body.index("if ([string]::IsNullOrWhiteSpace")
    end = body.index("}", body.index("exit 1", start)) + 1
    assert body.count("if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN))") == 1, (
        f"site {site!r}: expected exactly one emptiness guard to strip, found "
        f"{body.count('if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN))')} — "
        f"this control would otherwise test an unmodified body. Body: {body!r}"
    )
    stripped = body[:start] + body[end:]
    assert "IsNullOrWhiteSpace($env:IN_DOMAIN)" not in stripped, (
        f"site {site!r}: the guard survived the strip, so the control is "
        f"measuring the guarded body. Stripped: {stripped!r}"
    )
    # The call under test must still be there — a strip that removed it would
    # also produce "no CALLED line" and pass for the wrong reason.
    assert "m365-domain-preflight.ps1" in stripped, (
        f"site {site!r}: the strip removed the invocation itself, so the "
        f"control proves nothing. Stripped: {stripped!r}"
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
