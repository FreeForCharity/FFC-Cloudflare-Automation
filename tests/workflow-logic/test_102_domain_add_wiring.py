"""Unit tests for 102's dispatch-input call sites (#1080 burn-down).

102 creates a Cloudflare zone, enforces the FFC DNS standard on it, and repoints a
production domain's registrar nameservers in WHMCS. It spans four credential-holding
jobs under three environments — `whmcs-prod` twice, `cloudflare-prod-write` twice,
`cloudflare-prod-read` once — off ONE dispatch and ONE approval.

Its two free-text inputs, `domain` (`type: string`) and `issue_number`
(`type: number`), were interpolated into a pwsh body, two bash bodies and a
`github-script` body. All now arrive through step-level `env:`.

WHY THIS MODULE IS NOT SHAPED LIKE THE EARLIER LANES' MODULES
    Every earlier lane had one shape to prove: the input reaches the body as data.
    Here that is necessary and NOT sufficient, and this module exists mostly to say
    why.

    `inputs.domain` is published as a step output:

        $d = $env:IN_DOMAIN.Trim().ToLowerInvariant().Trim('.')     # remedied
        "domain=$d" | Out-File -FilePath $env:GITHUB_OUTPUT -Append

    and the value is then re-interpolated into SEVEN more script bodies as
    `${{ steps.meta.outputs.domain }}` / `${{ needs.<job>.outputs.domain }}`. None of
    those three string methods removes a `$( )`, and none of those seven sites is
    an `inputs.` reference, so `check-workflow-input-interpolation.py` — which is
    defined over `inputs.X` — cannot see any of them.

    MEASURED (test_the_laundered_output_executes_at_the_downstream_site): with the
    `Metadata` step remedied the house way and every downstream site left as it
    shipped, the payload arrives at `Metadata` as inert data, is written verbatim
    into GITHUB_OUTPUT, and EXECUTES one step later inside
    `$domain = "${{ steps.meta.outputs.domain }}"` — the WHMCS API secret is written
    to a sentinel, `-Domain` still binds a legal `example.org`, and the step exits 0.

    So a lane that fixed only the four sites the guard names would have deleted the
    freeze entry, gone green on every check in the repo, and left a working
    injection under two production credentials. That failure points in the
    flattering direction, which is the reason it is pinned here as an executable
    assertion rather than described in a comment. The class is filed as #1233;
    ledger L255.

WHERE THE CREDENTIALS SIT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    Neither credential family is named in any `env:` block in this file and neither
    matches a `secrets.*` reference in any body. `whmcs-secrets-from-kv` exports
    WHMCS_API_IDENTIFIER / _SECRET / _APIM_SUBSCRIPTION_KEY and
    `cloudflare-tokens-from-kv` exports CLOUDFLARE_API_TOKEN_FFC / _CM, both through
    GITHUB_ENV. A reviewer reading a step's own `env:` (ledger L213) and a
    `secrets.` grep of the file (#1141) each score this workflow as holding nothing,
    across all four jobs.
    `test_the_credentials_reach_the_bodies_only_through_the_kv_actions` pins it.

WHAT STAYS INTERPOLATED, AND WHY THAT IS NOT AN OVERSIGHT
    `zone_type` is `type: choice`; `jump_start`, `enforce_dry_run`,
    `dmarc_mgmt_debug`, `update_whmcs_nameservers` and `require_whmcs_domain` are
    `type: boolean`. GitHub generates all six from the declared options, so none can
    carry a payload. `test_the_input_types_are_what_the_burn_down_claims` asserts the
    DECLARATIONS rather than leaving that as a reading of this docstring — if
    someone retypes `zone_type` to `string`, the claim expires and this module says
    so.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import typing
import tempfile
import uuid

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

WORKFLOW = "102-domain-add-ffc-cloudflare-and-whmcs.yml"

# Every script-bearing step the burn-down touched: (job, step-name-substring).
SCRIPT_STEPS = (
    ("whmcs_preflight", "Metadata"),
    ("whmcs_preflight", "Check domain exists in WHMCS"),
    ("cloudflare_preflight", "Metadata"),
    ("cloudflare_preflight", "Check zone exists in Cloudflare"),
    ("cloudflare_zone_create", "Metadata"),
    ("cloudflare_zone_create", "Create zone (FFC)"),
    ("cloudflare_enforce_standard", "Enforce Cloudflare standard"),
    ("cloudflare_enforce_standard", "Summarize Cloudflare post-audit"),
    ("whmcs_update_nameservers", "Update WHMCS nameservers"),
    ("post_back", "Comment results back to issue"),
)

# The free-text inputs, and the six GitHub constrains. Asserted, not assumed.
FREE_TEXT_INPUTS = {"domain": "string", "issue_number": "number"}
CONSTRAINED_INPUTS = {
    "zone_type": "choice",
    "jump_start": "boolean",
    "enforce_dry_run": "boolean",
    "dmarc_mgmt_debug": "boolean",
    "update_whmcs_nameservers": "boolean",
    "require_whmcs_domain": "boolean",
}

# The derived spellings of the SAME dispatch value. The guard cannot see these;
# this module is the only thing standing between them and a script body.
LAUNDERED_REFS = (
    "steps.meta.outputs.domain",
    "needs.whmcs_preflight.outputs.domain",
    "needs.cloudflare_preflight.outputs.domain",
    "needs.cloudflare_zone_create.outputs.domain",
    "needs.cloudflare_preflight.outputs.ns1",
    "needs.cloudflare_preflight.outputs.ns2",
    "needs.cloudflare_zone_create.outputs.ns1",
    "needs.cloudflare_zone_create.outputs.ns2",
    "steps.meta.outputs.ns1",
    "steps.meta.outputs.ns2",
)

CREDENTIAL_VARS = (
    "WHMCS_API_SECRET",
    "WHMCS_API_IDENTIFIER",
    "WHMCS_APIM_SUBSCRIPTION_KEY",
    "CLOUDFLARE_API_TOKEN_FFC",
    "CLOUDFLARE_API_TOKEN_CM",
)
KV_ACTION_USES = (
    "./.github/actions/whmcs-secrets-from-kv",
    "./.github/actions/cloudflare-tokens-from-kv",
)

TOKEN_VAR = "WHMCS_API_SECRET"
# Deliberately not shaped like a real secret: a value a scanner treats as a
# credential comes back REDACTED, and the one place it is printed is an assertion
# message on a failing run.
FAKE_TOKEN = "whmcs-secret-placeholder-not-a-real-token"
SENTINEL = "stolen-102.txt"
LEGAL_DOMAIN = "example.org"


@contextlib.contextmanager
def _sentinel():
    """A sentinel path that survives the laundering hop's `.ToLowerInvariant()`.

    The hop lowercases the WHOLE value, the payload included, so the file the payload
    writes to is named by the LOWERCASED form of the path this test then reads. A
    sentinel that does not survive that round trip is written somewhere the assertion
    never looks, which reads as "the laundered payload did not execute", i.e. as
    evidence the wider fix was unnecessary — the flattering direction, and it cost a
    debugging round here before it was pinned.

    The leaf is ours and is built lowercase rather than borrowed (`mkdtemp` can and
    does return mixed case). The ANCESTOR is the host's and often is not:
    `tempfile.gettempdir()` is `C:\\Users\\<user>\\AppData\\Local\\Temp` on Windows.
    That is harmless exactly when the filesystem resolves the lowercased path to the
    same directory, so this measures the round trip rather than asserting a spelling.
    An all-lowercase check instead aborts the whole module on every Windows host —
    including the Conductor's, which is where this repo's local suite runs — over a
    path that would have worked there.
    """
    base = pathlib.Path(tempfile.gettempdir()) / ("ffc102-" + uuid.uuid4().hex)
    base.mkdir()
    lowered = str(base).lower()
    assert os.path.exists(lowered) and os.path.samefile(lowered, base), (
        f"sentinel directory {base} does not survive lowercasing on this filesystem: "
        f"{lowered} names a different directory, or none. The laundering hop would "
        f"write to that path while the assertion reads this one."
    )
    try:
        yield base / SENTINEL
    finally:
        shutil.rmtree(base, ignore_errors=True)

# GitHub's `shell: pwsh` wrapper (Runner.Worker/Handlers/ScriptHandlerHelpers.cs).
# The epilogue is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it.
PWSH_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
PWSH_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# Bodies as they shipped BEFORE the burn-down, verbatim from origin/main, with the
# substitution point marked. Positive controls: without them, "the fixed body does
# not execute the payload" is a claim about a body that might never have executed
# anything at all.
PRE_FIX_METADATA_PWSH = (
    "$d = \"DOMAIN_HERE\".Trim().ToLowerInvariant().Trim('.')\n"
    '"domain=$d" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8\n'
)
PRE_FIX_DOWNSTREAM_PWSH = (
    "$ErrorActionPreference = 'Stop'\n"
    '$domain = "DOMAIN_HERE"\n'
    "\n"
    "$json = & pwsh -NoProfile -File .\\scripts\\whmcs-domain-exists.ps1 -Domain $domain\n"
    "if ($LASTEXITCODE -ne 0) {\n"
    '  throw "WHMCS preflight check failed (exit $LASTEXITCODE)."\n'
    "}\n"
)
PRE_FIX_METADATA_BASH = (
    'd="${{ needs.whmcs_preflight.outputs.domain }}"\n'
    "if [ -z \"$d\" ]; then d=$(echo \"DOMAIN_HERE\" | tr '[:upper:]' '[:lower:]' | xargs); fi\n"
    'echo "domain=$d" >> "$GITHUB_OUTPUT"\n'
)

# A stand-in for the real script that records what it was BOUND. A marker-string
# search over stdout is not a discriminator here: pwsh echoes the offending source
# line back in a ParserError, so any substring predicate matches the payload text on
# a run that executed nothing.
STUB_DOMAIN_EXISTS = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain
)
# What it was BOUND, recorded out of band. stdout is not available for this: the
# caller pipes it straight into ConvertFrom-Json, so a marker line there fails the
# body on the harness rather than on the claim under test. A stdout substring
# search would not discriminate anyway — pwsh echoes the offending source line back
# in a ParserError, so any such predicate matches the payload text on a run that
# executed nothing.
Set-Content -Path 'bound.txt' -Value $Domain -NoNewline
Write-Output '{"found":true,"domainId":"1"}'
"""


# --------------------------------------------------------------------------
# extraction helpers
# --------------------------------------------------------------------------


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _step(job: str, name: str) -> dict:
    return find_step(_workflow(), job, name)


def _body(step: dict) -> str:
    """The script text of a step, whether it is a `run:` or a `github-script`."""
    if "run" in step:
        return step["run"]
    return ((step.get("with") or {}).get("script")) or ""


def _expressions(body: str) -> list[str]:
    """Every `${{ … }}` expression in a script body, using the CHECKER's own regex.

    Deliberately the guard's pattern rather than a substring test of this module's
    own devising, so the two cannot drift apart the way a restated rule does.
    """
    return [m.group(1).strip() for m in guard._EXPRESSION.finditer(body)]


def _interpolated_inputs(body: str) -> set[str]:
    found: set[str] = set()
    for expr in _expressions(body):
        found.update(guard._INPUT_REF.findall(expr))
    return found


def _render(body: str, substitutions: dict[str, str]) -> str:
    """Perform GitHub's substitution, asserting every expression is accounted for.

    Running a body with an unsubstituted `${{ }}` left in it is a silent trap: the
    literal text `'${{ inputs.require_whmcs_domain }}'` is not `'true'`, so a branch
    the real run takes is skipped and a test asserting the ordinary path fails
    against a correct workflow — a false red pointing at the fix. An unexpected
    expression means an input was re-interpolated or a constrained one renamed, and
    failing loudly here is much better than reading the result of executing it.
    """
    remaining = set(_expressions(body))
    unknown = remaining - set(substitutions)
    assert not unknown, (
        f"body carries expression(s) this module does not know how to render: "
        f"{sorted(unknown)}. Either a value was re-interpolated into a script body "
        f"(#1080) or a constrained input was renamed; either way the body about to "
        f"be executed is not the body that ships."
    )
    for expr, value in substitutions.items():
        body = body.replace("${{ " + expr + " }}", value)
    return body


def _subexpression_payload(sentinel: pathlib.Path) -> str:
    """`$( )` inside a DOUBLE-quoted pwsh string: expands, so it runs.

    Lowercase throughout on purpose. The laundering hop applies
    `.ToLowerInvariant()` to the value, and the payload has to survive that AS DATA
    to reach the downstream site — which is the whole point of the laundering test.
    The credential is read as `$env:whmcs_api_secret` for the same reason; the jobs
    holding it are `runs-on: windows-latest`, where `$env:` lookup is
    case-insensitive, so lowercasing costs the attacker nothing there.
    `_run_pwsh` sets both spellings so the probe is faithful on this Linux host.
    """
    return "%s$(set-content -path '%s' -value $env:whmcs_api_secret)" % (
        LEGAL_DOMAIN,
        sentinel,
    )


# --------------------------------------------------------------------------
# runners
# --------------------------------------------------------------------------


class PwshRun(typing.NamedTuple):
    out: str
    rc: int
    gho: str
    bound: str | None


def _run_pwsh(body: str, **env_overrides: str) -> PwshRun:
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns a PwshRun: stdout+stderr, the exit code, what was published to
    GITHUB_OUTPUT, and `bound` — what the stub callee recorded as its `-Domain`.
    The sentinel is checked by the CALLER, which owns its (all-lowercase) path; its
    CONTENTS matter, not merely its existence, because a file written from an unset
    variable scores the same as one written from the live credential and the claim
    under test is which credential the payload reached.

    `stdin=DEVNULL` is load-bearing. An unsatisfied mandatory parameter makes
    PowerShell PROMPT, and on an interactive stdin the call blocks forever — a
    whole-suite hang rather than a failure. A runner's stdin is not a terminal, so
    DEVNULL is also the faithful shape.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "whmcs-domain-exists.ps1").write_text(
            STUB_DOMAIN_EXISTS, encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(PWSH_PREAMBLE + body + PWSH_EPILOGUE, encoding="utf-8")
        gho = tmp / "github-output.txt"
        gho.write_text("", encoding="utf-8")
        env = child_env(
            GITHUB_OUTPUT=str(gho),
            GITHUB_STEP_SUMMARY=str(tmp / "summary.md"),
            # Both spellings: this host's pwsh is case-SENSITIVE on `$env:`, the
            # runner the credential actually sits on is windows-latest, which is
            # not. Setting only one would make the theft assertion pass or fail on
            # a property of the test host rather than of the body.
            WHMCS_API_SECRET=FAKE_TOKEN,
            whmcs_api_secret=FAKE_TOKEN,
            **env_overrides,
        )
        # Only what the test sets may be visible: an inherited IN_DOMAIN would make
        # the fail-closed cases pass for the wrong reason.
        for var in ("IN_DOMAIN", "IN_DOMAIN_RESOLVED"):
            if var not in env_overrides:
                env.pop(var, None)
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
        bound_path = tmp / "bound.txt"
        bound = (
            bound_path.read_text(encoding="utf-8") if bound_path.exists() else None
        )
        return PwshRun(
            out=proc.stdout + proc.stderr,
            rc=proc.returncode,
            gho=gho.read_text(encoding="utf-8"),
            bound=bound,
        )


def _run_bash(body: str, **env_overrides: str):
    """Run a bash body as `shell: bash` does, in an isolated temp cwd."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        script = tmp / "step.sh"
        script.write_text(body, encoding="utf-8")
        gho = tmp / "github-output.txt"
        gho.write_text("", encoding="utf-8")
        env = child_env(GITHUB_OUTPUT=str(gho), **env_overrides)
        for var in ("IN_DOMAIN", "IN_DOMAIN_UPSTREAM", "IN_NS1", "IN_NS2"):
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return (
            proc.stdout + proc.stderr,
            contents,
            proc.returncode,
            gho.read_text(encoding="utf-8"),
        )


# `actions/github-script` evaluates the `script:` value as the body of an async
# function with `github`, `context`, `core`, `require` and friends in scope. This
# harness is that shape and nothing more: enough to let the real body run, with
# every effect recorded rather than performed.
NODE_HARNESS = """
const fsReal = require('fs');
const path = require('path');
const calls = [];
const core = {
  setFailed: (m) => { calls.push({ fn: 'setFailed', message: String(m) }); },
  info: () => {},
  warning: () => {},
};
const context = {
  serverUrl: 'https://github.com',
  repo: { owner: 'FreeForCharity', repo: 'FFC-Cloudflare-Automation' },
  runId: 1,
};
const github = {
  rest: { issues: { createComment: async (args) => { calls.push({ fn: 'createComment', args }); } } },
};
(async () => {
  try {
    __BODY__
  } catch (e) {
    calls.push({ fn: 'threw', message: String(e && e.message || e) });
  } finally {
    // `finally`, not a trailing statement: the real body `return`s after
    // core.setFailed, and a return inside the try returns from THIS arrow — which
    // silently skipped the recorder and made every refusal read as "no calls made"
    // rather than "refused correctly".
    fsReal.writeFileSync(process.env.CALLS_PATH, JSON.stringify(calls));
  }
})();
"""


def _run_github_script(body: str, **env_overrides: str):
    """Run a `github-script` body under a recording harness.

    Returns (calls, sentinel_contents_or_None, output). `calls` is what the body
    asked the harness to do — every `core.setFailed` and `github.rest.*` call, in
    order; the sentinel is how a payload that ESCAPED the body reports itself, and
    is checked by the caller, which owns its path.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        art = tmp / "artifacts" / "domain-add" / "cloudflare"
        art.mkdir(parents=True)
        (art / "cloudflare-enforce.txt").write_text("enforce output", encoding="utf-8")
        (art / "cloudflare-audit-after.txt").write_text("audit output", encoding="utf-8")
        calls_path = tmp / "calls.json"
        indented = "\n".join("    " + ln for ln in body.splitlines())
        script = tmp / "step.js"
        script.write_text(NODE_HARNESS.replace("__BODY__", indented), encoding="utf-8")
        env = child_env(CALLS_PATH=str(calls_path), **env_overrides)
        for var in ("IN_ISSUE_NUMBER", "IN_DOMAIN_RESOLVED"):
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["node", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        calls = (
            json.loads(calls_path.read_text(encoding="utf-8"))
            if calls_path.exists()
            else []
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return calls, contents, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# structural tests — no pwsh/node needed
# --------------------------------------------------------------------------


def test_no_free_text_input_reaches_any_script_body():
    """The half of the fix the guard can see, asserted per step rather than in bulk."""
    offenders = {}
    for job, name in SCRIPT_STEPS:
        body = _body(_step(job, name))
        hit = _interpolated_inputs(body) & set(FREE_TEXT_INPUTS)
        if hit:
            offenders[f"{job}/{name}"] = sorted(hit)
    assert not offenders, (
        f"free-text dispatch input(s) interpolated back into a script body: "
        f"{offenders}. Under whmcs-prod / cloudflare-prod-write that is dispatcher "
        f"text executed after the approval (#1080)."
    )


def test_no_derived_domain_expression_reaches_any_script_body():
    """The half the guard CANNOT see — the reason this lane is wider than its entry.

    `steps.meta.outputs.domain` and the `needs.*.outputs.*` spellings carry the same
    dispatch value one or more hops later. `check-workflow-input-interpolation.py`
    is defined over `inputs.X` and scores every one of them clean, so nothing in the
    repo fails if they come back. This test is that nothing (#1233, ledger L255).
    """
    offenders = {}
    for job, name in SCRIPT_STEPS:
        body = _body(_step(job, name))
        hit = sorted(
            {ref for ref in LAUNDERED_REFS for e in _expressions(body) if ref in e}
        )
        if hit:
            offenders[f"{job}/{name}"] = hit
    assert not offenders, (
        f"a value DERIVED from the dispatch input is interpolated into a script "
        f"body: {offenders}. Moving `inputs.domain` into `env:` does not sanitize "
        f"it — the output carries the payload verbatim and executes at the next "
        f"double-quoted site. See test_the_laundered_output_executes_at_the_"
        f"downstream_site for the measurement."
    )


def test_the_input_types_are_what_the_burn_down_claims():
    """Six inputs stay interpolated. That is only safe while their TYPES hold."""
    declared = guard.dispatch_inputs(_workflow())
    for name, expected in {**FREE_TEXT_INPUTS, **CONSTRAINED_INPUTS}.items():
        assert declared.get(name) == expected, (
            f"input {name!r} is declared {declared.get(name)!r}, this module "
            f"expects {expected!r}. If a constrained input became free text, the "
            f"six left interpolated are no longer safe to leave."
        )
    free = guard.free_text_inputs(_workflow())
    assert free == set(FREE_TEXT_INPUTS), (
        f"the guard considers {sorted(free)} free text; this module is written "
        f"against {sorted(FREE_TEXT_INPUTS)}."
    )


def test_every_moved_value_is_read_by_the_body_that_maps_it():
    """A mapping nothing reads is the L202 shape: green wiring, dead value.

    `env:` present + body never dereferences it means the step silently runs on an
    empty value, which is exactly what the fail-closed guards exist to catch — but
    only if something actually reads the variable in the first place.
    """
    missing = {}
    for job, name in SCRIPT_STEPS:
        step = _step(job, name)
        body = _body(step)
        for var in (step.get("env") or {}):
            if not var.startswith("IN_"):
                continue
            if var not in body:
                missing.setdefault(f"{job}/{name}", []).append(var)
    assert not missing, (
        f"step-level env: maps a value the body never reads: {missing}. The "
        f"mapping looks like the remedy and the step runs on nothing (ledger L202)."
    )


def test_the_credentials_reach_the_bodies_only_through_the_kv_actions():
    """Neither sweep a reviewer is told to run can see the credential here."""
    wf = _workflow()
    for job, name in SCRIPT_STEPS:
        step = _step(job, name)
        body = _body(step)
        assert "secrets." not in body, (
            f"{job}/{name} names a secret in its body; this module's premise is "
            f"that a #1141-style `secrets.` grep finds nothing here."
        )
        for var in CREDENTIAL_VARS:
            assert var not in (step.get("env") or {}), (
                f"{job}/{name} names {var} in its own env:; this module's premise "
                f"is that an L213 `env:` read finds nothing here."
            )
    uses = [
        s.get("uses")
        for j in wf["jobs"].values()
        for s in (j.get("steps") or [])
        if s.get("uses")
    ]
    for action in KV_ACTION_USES:
        assert action in uses, (
            f"{action} is gone from the workflow — the credential arrival path this "
            f"module documents no longer exists, so its reachability claims are stale."
        )


def test_the_guard_no_longer_reports_this_workflow():
    """The freeze entry must be gone AND the tree must agree it is gone."""
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    assert WORKFLOW not in current, (
        f"{WORKFLOW} still interpolates {current.get(WORKFLOW)} — the burn-down is "
        f"incomplete and the freeze entry must not have been removed."
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} is still in KNOWN_UNGUARDED; a stale entry fails the guard."
    )


# --------------------------------------------------------------------------
# behavioural — pwsh
# --------------------------------------------------------------------------


def test_the_pre_fix_metadata_body_stole_the_secret_and_exited_zero():
    """Positive control: the shipped-on-main body really did execute dispatcher text."""
    with _sentinel() as sentinel:
        payload = _subexpression_payload(sentinel)
        assert PRE_FIX_METADATA_PWSH.count("DOMAIN_HERE") == 1
        r = _run_pwsh(PRE_FIX_METADATA_PWSH.replace("DOMAIN_HERE", payload))
        stolen = sentinel.read_text(encoding="utf-8") if sentinel.exists() else None
    assert stolen is not None, f"payload did not execute; body output: {r.out!r}"
    assert FAKE_TOKEN in stolen, (
        f"sentinel exists but does not hold the credential ({stolen!r}) — the "
        f"payload ran but reached nothing, a weaker claim than this control makes."
    )
    assert r.rc == 0, f"expected the exploited run to look ordinary; rc={r.rc} {r.out!r}"
    assert "domain=example.org" in r.gho, (
        f"the exploited run also publishes a perfectly normal output; got {r.gho!r}. "
        f"That it looks clean is the point."
    )


def test_the_shipped_metadata_body_binds_the_payload_as_data():
    body = _render(_body(_step("whmcs_preflight", "Metadata")), {})
    with _sentinel() as sentinel:
        payload = _subexpression_payload(sentinel)
        r = _run_pwsh(body, IN_DOMAIN=payload)
        stolen = sentinel.exists()
    assert not stolen, f"the payload EXECUTED through env: — output {r.out!r}"
    assert r.rc == 0, f"ordinary run should succeed; rc={r.rc} out={r.out!r}"
    assert "set-content" in r.gho, (
        f"the payload should survive as literal DATA in the published output; "
        f"got {r.gho!r}"
    )


def test_the_laundered_output_executes_at_the_downstream_site():
    """THE case this lane exists for: the remedy at one hop, the defect at the next.

    Runs the REMEDIED `Metadata` step, takes what it published, and feeds that into
    the downstream body AS IT SHIPPED ON MAIN. If the burn-down had stopped at the
    sites the guard names, this is what a dispatch would have done.
    """
    remedied = _render(_body(_step("whmcs_preflight", "Metadata")), {})
    with _sentinel() as sentinel:
        payload = _subexpression_payload(sentinel)

        meta = _run_pwsh(remedied, IN_DOMAIN=payload)
        assert meta.rc == 0 and not sentinel.exists(), (
            "the remedied Metadata step must itself be clean, or this test is "
            "measuring the wrong hop."
        )
        laundered = meta.gho.split("domain=", 1)[1].strip()
        assert "set-content" in laundered, (
            f"the payload did not survive to the step output ({laundered!r}); "
            f"without that there is no laundering to demonstrate."
        )

        assert PRE_FIX_DOWNSTREAM_PWSH.count("DOMAIN_HERE") == 1
        r = _run_pwsh(PRE_FIX_DOWNSTREAM_PWSH.replace("DOMAIN_HERE", laundered))
        stolen = sentinel.read_text(encoding="utf-8") if sentinel.exists() else None

    assert stolen is not None, (
        f"expected the laundered value to EXECUTE at the pre-fix downstream site. "
        f"If it no longer does, the premise for widening this lane past the guard's "
        f"four call sites has changed and this module's docstring is stale. "
        f"rc={r.rc} out={r.out!r}"
    )
    assert FAKE_TOKEN in stolen, f"payload ran but stole nothing: {stolen!r}"
    assert r.rc == 0, f"the laundered exploit should also exit 0; rc={r.rc} {r.out!r}"
    assert r.bound == LEGAL_DOMAIN, (
        f"the legitimate call should still happen with a legal domain — that is what "
        f"keeps the run unremarkable in the log. Callee bound -Domain {r.bound!r}"
    )


def test_the_shipped_downstream_body_binds_the_laundered_payload_as_data():
    """The same laundered value against the body as it ships now: inert."""
    body = _render(
        _body(_step("whmcs_preflight", "Check domain exists in WHMCS")),
        {"inputs.require_whmcs_domain": "false"},
    )
    with _sentinel() as sentinel:
        payload = _subexpression_payload(sentinel)
        r = _run_pwsh(body, IN_DOMAIN_RESOLVED=payload)
        stolen = sentinel.exists()
    assert not stolen, f"the laundered payload EXECUTED; output {r.out!r}"
    assert r.rc == 0, f"rc={r.rc} out={r.out!r}"
    assert r.bound == payload, (
        f"the payload should reach the callee as ONE literal -Domain argument; "
        f"it bound {r.bound!r}"
    )


def test_an_empty_domain_fails_closed_and_says_so():
    body = _render(_body(_step("whmcs_preflight", "Metadata")), {})
    r = _run_pwsh(body, IN_DOMAIN="   ")
    assert r.rc != 0, f"a blank domain must not publish an output; gho={r.gho!r}"
    assert "IN_DOMAIN is empty" in r.out, (
        f"a non-zero exit alone cannot distinguish the guard refusing from the "
        f"harness failing to start — the message is the discriminator. Got {r.out!r}"
    )


def test_an_unset_domain_fails_closed_and_says_so():
    body = _render(_body(_step("whmcs_preflight", "Metadata")), {})
    r = _run_pwsh(body)
    assert r.rc != 0, f"an unset mapping must fail closed; rc={r.rc} out={r.out!r}"
    assert "IN_DOMAIN is empty" in r.out, (
        f"an unset mapping must produce the guard's own message, not a $null "
        f"method-call error naming neither the input nor the cause. Got {r.out!r}"
    )


# --------------------------------------------------------------------------
# behavioural — bash
# --------------------------------------------------------------------------


def test_the_pre_fix_bash_body_executed_the_payload():
    """`$( )` expands inside double quotes in bash exactly as it does in pwsh."""
    with _sentinel() as sentinel:
        payload = "%s$(printf %%s \"$SECRET_UNDER_TEST\" > '%s')" % (
            LEGAL_DOMAIN,
            sentinel,
        )
        assert PRE_FIX_METADATA_BASH.count("DOMAIN_HERE") == 1
        body = PRE_FIX_METADATA_BASH.replace("DOMAIN_HERE", payload)
        # The upstream expression renders to the empty string when the upstream job
        # published nothing, which is the branch that reaches the dispatch input.
        body = body.replace('d="${{ needs.whmcs_preflight.outputs.domain }}"', 'd=""')
        out, _, rc, _ = _run_bash(body, SECRET_UNDER_TEST=FAKE_TOKEN)
        stolen = sentinel.read_text(encoding="utf-8") if sentinel.exists() else None
    assert stolen is not None, f"payload did not execute in bash; output {out!r}"
    assert FAKE_TOKEN in stolen, f"payload ran but stole nothing: {stolen!r}"
    assert rc == 0, f"the exploited bash run should look ordinary; rc={rc}"


def test_the_shipped_bash_body_binds_the_payload_as_data():
    for job in ("cloudflare_preflight", "cloudflare_zone_create"):
        body = _render(_body(_step(job, "Metadata")), {})
        with _sentinel() as sentinel:
            payload = "%s$(printf %%s stolen > '%s')" % (LEGAL_DOMAIN, sentinel)
            out, _, rc, gho = _run_bash(
                body,
                IN_DOMAIN=payload,
                IN_DOMAIN_UPSTREAM="",
                IN_NS1="ns1.example.org",
                IN_NS2="ns2.example.org",
            )
            stolen = sentinel.exists()
        assert not stolen, f"{job}: the payload EXECUTED through env:; output {out!r}"
        assert rc == 0, f"{job}: rc={rc} out={out!r}"
        assert "printf" in gho, (
            f"{job}: the payload should survive as literal data in the output; "
            f"got {gho!r}"
        )


def test_the_bash_body_fails_closed_when_neither_source_supplies_a_domain():
    for job in ("cloudflare_preflight", "cloudflare_zone_create"):
        body = _render(_body(_step(job, "Metadata")), {})
        out, _, rc, gho = _run_bash(
            body,
            IN_DOMAIN="",
            IN_DOMAIN_UPSTREAM="",
            IN_NS1="ns1.example.org",
            IN_NS2="ns2.example.org",
        )
        assert rc != 0, f"{job}: must not publish `domain=`; rc={rc} gho={gho!r}"
        assert "carries a domain" in out, (
            f"{job}: a non-zero exit alone cannot distinguish the guard refusing "
            f"from bash failing to start. Got {out!r}"
        )


# --------------------------------------------------------------------------
# behavioural — output injection through GITHUB_OUTPUT
#
# The laundering hop again, from the other end. `env:` stops the value being
# CODE; it does nothing to stop it being extra STRUCTURE. `"domain=$d"` is the
# single-line `name=value` form, and `.Trim()` strips leading and trailing
# whitespace only — so an INTERIOR line break turns one write into several
# `key=value` lines that later jobs read through `steps.*` / `needs.*` as
# trusted. Raised by Copilot on #1234 and confirmed reachable.
# --------------------------------------------------------------------------


def test_a_line_break_in_the_domain_is_refused_before_publishing():
    body = _render(_body(_step("whmcs_preflight", "Metadata")), {})
    for label, payload in (
        ("LF", "example.org\nns1=evil.ns.example\nfound=true"),
        ("bare CR", "example.org\rns1=evil.ns.example"),
    ):
        r = _run_pwsh(body, IN_DOMAIN=payload)
        assert r.rc != 0, f"{label}: published anyway; gho={r.gho!r}"
        assert "contains a line break" in r.out, (
            f"{label}: refused for the wrong reason — a non-zero exit alone cannot "
            f"tell this guard from the emptiness guard next to it. Got {r.out!r}"
        )
        assert r.gho.strip() == "", f"{label}: wrote to GITHUB_OUTPUT anyway: {r.gho!r}"

    ok = _run_pwsh(body, IN_DOMAIN="  ExAmple.ORG.  ")
    assert ok.rc == 0 and ok.gho.strip() == "domain=example.org", (
        f"the guard must leave an ordinary domain alone, normalisation included; "
        f"rc={ok.rc} gho={ok.gho!r}"
    )


def test_the_bash_upstream_branch_refuses_a_line_break():
    """`d="$IN_DOMAIN_UPSTREAM"` is raw — no `tr`, no `xargs` — so this is the
    branch where the injection is actually reachable in the bash steps."""
    for job in ("cloudflare_preflight", "cloudflare_zone_create"):
        body = _render(_body(_step(job, "Metadata")), {})
        out, _, rc, gho = _run_bash(
            body,
            IN_DOMAIN="",
            IN_DOMAIN_UPSTREAM="example.org\nns1=evil.ns.example",
            IN_NS1="ns1.example.org",
            IN_NS2="ns2.example.org",
        )
        assert rc != 0, f"{job}: published anyway; gho={gho!r}"
        assert "contains a line break" in out, f"{job}: wrong reason: {out!r}"
        assert gho.strip() == "", f"{job}: wrote anyway: {gho!r}"


def test_the_bash_input_branch_is_already_flattened_by_xargs():
    """The other half of the finding, which did NOT hold — pinned so it stays true.

    `echo "$IN_DOMAIN" | tr … | xargs` collapses a newline to a SPACE, so the value
    is single-line before it is ever published and no extra key can appear. The
    CR/LF guard correctly does not fire here. Asserted rather than assumed in either
    direction: if someone drops the `xargs`, this branch silently becomes injectable
    and only this case says so.
    """
    for job in ("cloudflare_preflight", "cloudflare_zone_create"):
        body = _render(_body(_step(job, "Metadata")), {})
        out, _, rc, gho = _run_bash(
            body,
            IN_DOMAIN="example.org\nns1=evil.ns.example\nfound=true",
            IN_DOMAIN_UPSTREAM="",
            IN_NS1="ns1.example.org",
            IN_NS2="ns2.example.org",
        )
        assert rc == 0, f"{job}: rc={rc} out={out!r}"
        lines = gho.strip().splitlines()
        assert len(lines) == 3, (
            f"{job}: expected exactly domain/ns1/ns2 — a fourth line is an injected "
            f"output key. Got {gho!r}"
        )
        assert not [ln for ln in lines if ln.startswith("ns1=evil")], (
            f"{job}: an injected `ns1=` key reached GITHUB_OUTPUT: {gho!r}"
        )


# --------------------------------------------------------------------------
# behavioural — github-script
# --------------------------------------------------------------------------


def test_the_pre_fix_github_script_body_executed_the_payload():
    """`Number('${{ inputs.issue_number }}')` — a `number` input is a free text box.

    The payload closes the string literal, runs, and re-opens one so the body's own
    trailing `')` still parses. The comma expression then yields 1, so `issueNumber`
    is a perfectly ordinary value and the run continues normally.
    """
    with tempfile.TemporaryDirectory() as td:
        sentinel = pathlib.Path(td) / SENTINEL
        payload = (
            "1'); require('fs').writeFileSync('%s', 'STOLEN:' + "
            "process.env.SECRET_UNDER_TEST); const _x = Number('1" % sentinel
        )
        body = "const issueNumber = Number('%s');\ncalls.push({fn:'ok', issueNumber});\n" % payload
        calls, _, out = _run_github_script(body, SECRET_UNDER_TEST=FAKE_TOKEN)
        stolen = sentinel.read_text(encoding="utf-8") if sentinel.exists() else None
    assert stolen is not None, f"payload did not execute in node; output {out!r}"
    assert FAKE_TOKEN in stolen, f"payload ran but stole nothing: {stolen!r}"
    assert any(c["fn"] == "ok" and c["issueNumber"] == 1 for c in calls), (
        f"the exploited script should carry on with an ordinary issueNumber — that "
        f"is what makes the run unremarkable. Calls: {calls!r}"
    )


def test_the_shipped_github_script_body_binds_the_payload_as_data():
    step = _step("post_back", "Comment results back to issue")
    body = _render(
        _body(step),
        {
            "inputs.enforce_dry_run": "false",
            "needs.cloudflare_enforce_standard.outputs.issues_count": "0",
        },
    )
    with tempfile.TemporaryDirectory() as td:
        sentinel = pathlib.Path(td) / SENTINEL
        payload = (
            "1'); require('fs').writeFileSync('%s', 'STOLEN'); const _x = Number('1"
            % sentinel
        )
        calls, _, out = _run_github_script(
            body, IN_ISSUE_NUMBER=payload, IN_DOMAIN_RESOLVED=LEGAL_DOMAIN
        )
        stolen = sentinel.exists()
    assert not stolen, f"the payload EXECUTED inside github-script; output {out!r}"
    failed = [c for c in calls if c["fn"] == "setFailed"]
    assert failed, (
        f"a non-numeric issue number must be refused, not passed to createComment. "
        f"Calls: {calls!r}"
    )
    assert "IN_ISSUE_NUMBER is not a positive integer" in failed[0]["message"]
    assert not [c for c in calls if c["fn"] == "createComment"], (
        f"nothing may be posted on the refusing path. Calls: {calls!r}"
    )


def test_the_shipped_github_script_body_posts_on_the_ordinary_path():
    """The refusal cases above are only meaningful if the happy path still works."""
    step = _step("post_back", "Comment results back to issue")
    body = _render(
        _body(step),
        {
            "inputs.enforce_dry_run": "true",
            "needs.cloudflare_enforce_standard.outputs.issues_count": "0",
        },
    )
    calls, _, out = _run_github_script(
        body, IN_ISSUE_NUMBER="1203", IN_DOMAIN_RESOLVED=LEGAL_DOMAIN
    )
    posted = [c for c in calls if c["fn"] == "createComment"]
    assert posted, f"expected a comment on the ordinary path. Calls: {calls!r} {out!r}"
    assert posted[0]["args"]["issue_number"] == 1203
    assert LEGAL_DOMAIN in posted[0]["args"]["body"]
    assert "DRY RUN" in posted[0]["args"]["body"]


def test_the_github_script_body_fails_closed_on_an_empty_domain():
    step = _step("post_back", "Comment results back to issue")
    body = _render(
        _body(step),
        {
            "inputs.enforce_dry_run": "false",
            "needs.cloudflare_enforce_standard.outputs.issues_count": "0",
        },
    )
    calls, _, _ = _run_github_script(body, IN_ISSUE_NUMBER="1203")
    failed = [c for c in calls if c["fn"] == "setFailed"]
    assert failed, (
        f"JS refuses nothing on its own: an unmapped env var is `undefined` and the "
        f"body would post a report naming no domain. Calls: {calls!r}"
    )
    assert "IN_DOMAIN_RESOLVED is empty" in failed[0]["message"]


def test_the_sentinel_path_survives_the_laundering_hops_lowercasing():
    """The property the pwsh cases depend on, asserted on the host that will run them.

    Deliberately not a spelling check: whether the temp ancestor is lowercase is the
    host's business, and only the round trip distinguishes a mixed-case ancestor on a
    case-insensitive filesystem (fine — every Windows host) from one on a
    case-sensitive filesystem (fatal — the sentinel is written where nothing reads).
    Needs neither pwsh nor node, so the constraint is checked even where the cases it
    protects skip.
    """
    with _sentinel() as sentinel:
        assert sentinel.name == sentinel.name.lower(), (
            f"the sentinel FILENAME is ours to choose and must be lowercase; "
            f"{sentinel.name!r} is not."
        )
        sentinel.write_text("written by the payload", encoding="utf-8")
        lowered = pathlib.Path(str(sentinel).lower())
        assert lowered.is_file() and os.path.samefile(lowered, sentinel), (
            f"the payload writes to {lowered} and this module reads {sentinel}; on "
            f"this host those are different files, so a successful injection would "
            f"read as a clean run."
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the pwsh cases need an interpreter the CI image has and this sandbox may not.
# Scoped to exactly the cases that call `_run_pwsh` — a whole-module `shutil.which`
# gate turns "could not run" into "everything passed" (#1182).
NEEDS_PWSH = {
    "test_the_pre_fix_metadata_body_stole_the_secret_and_exited_zero",
    "test_the_shipped_metadata_body_binds_the_payload_as_data",
    "test_the_laundered_output_executes_at_the_downstream_site",
    "test_the_shipped_downstream_body_binds_the_laundered_payload_as_data",
    "test_an_empty_domain_fails_closed_and_says_so",
    "test_an_unset_domain_fails_closed_and_says_so",
    "test_a_line_break_in_the_domain_is_refused_before_publishing",
}
NEEDS_NODE = {
    "test_the_pre_fix_github_script_body_executed_the_payload",
    "test_the_shipped_github_script_body_binds_the_payload_as_data",
    "test_the_shipped_github_script_body_posts_on_the_ordinary_path",
    "test_the_github_script_body_fails_closed_on_an_empty_domain",
}

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    have_node = shutil.which("node") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        if t.__name__ in NEEDS_NODE and not have_node:
            print(f"  SKIP {t.__name__} (node not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
