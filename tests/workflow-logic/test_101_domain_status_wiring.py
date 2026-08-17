"""Unit tests for 101's six dispatch-input call sites (#1080 burn-down).

101 reports a domain's status from every source at once: Cloudflare DNS, the FFC
Microsoft 365 tenant through Graph, a rendered job summary, and an optional
comment posted back to a GitHub issue. Its two free-text dispatch inputs,
`domain` and `issue_number`, used to be interpolated into FIVE pwsh bodies and
ONE `github-script` body across FOUR jobs. All six now arrive through
step-level `env:`.

It is the widest entry in the freeze by call-site count that remained after 103,
and the ONE dispatch of one `domain` ran the same payload five times.

WHERE THE CREDENTIAL SAT — AND WHY THE REACHABILITY INDEX REPORTS *NOTHING*
    The lanes before this one were each ranked by what an injected body could
    reach without moving. Three sweeps have accumulated for answering that:
    read the step's own `env:` (ledger L213); grep the file for `secrets.` in a
    `run:` body (#1141); and, since #1188, ask the guard itself —
    `check-workflow-input-interpolation.py --reachability 101-domain-status.yml`.

    On 101 the third one is the interesting failure, because it is the
    mechanical one and it is the one now trusted. Measured on `main` at
    `b7a9f2b`, it named exactly two steps, BOTH in the `cloudflare` job:

        [W] 101-domain-status.yml job 'cloudflare' step 'Cloudflare audit + dry-run preview'
              CLOUDFLARE_API_TOKEN_CM  (GITHUB_ENV from 'uses: ./.github/actions/cloudflare-tokens-from-kv')
              CLOUDFLARE_API_TOKEN_FFC (GITHUB_ENV from 'uses: ./.github/actions/cloudflare-tokens-from-kv')

    The `m365` job's two injection points are absent from that listing. They are
    also absent from an L213 read and from a `secrets.` grep. And the `m365` job
    is `environment: m365-prod` — it is the ONLY reason 101 is a `[W]` entry in
    the freeze at all. So every available sweep scores the two sites that carry
    the write environment as holding nothing.

    They are not empty. The preceding step is

        - name: Azure login (OIDC)
          uses: azure/login@…

    and both injection points then call `az account get-access-token`. The
    credential is not a value in a variable; it is an **authenticated CLI
    session on disk**, and every sweep the burn-down has built — including the
    #1188 index, which is defined over env-var arrival — can only see values in
    variables. An injected body does not need to read anything: it runs `az`
    itself, exactly as the shipped body does one line later.

    `test_the_m365_credential_is_a_cli_session_no_sweep_can_see` pins this, and
    `test_the_injected_payload_mints_a_graph_token_with_no_env_var_in_reach`
    measures it: the payload calls `az account get-access-token` from inside the
    interpolation and writes the result to a file, with `CLOUDFLARE_API_TOKEN_*`
    and every other credential variable explicitly removed from the child
    environment. It steals a Graph bearer token from a step the index reports as
    reaching nothing.

    This is a gap in a tool, not a reason to distrust the tool: #1188 answers
    "which VARIABLES are in reach", correctly and cheaply, and that is what the
    earlier lanes needed. It is filed as its own issue rather than papered over
    here, because the next lane will read the same listing and draw the same
    conclusion.

WHAT THE OTHER THREE SITES REACH
    `summary` and `post_back` declare no environment and hold no cloud
    credential. They are still not decoration: `post_back`'s pwsh body renders
    the markdown that the NEXT step posts to a GitHub issue under a token
    carrying `issues: write`, so a payload there controls what FFC publishes in
    a human-readable thread. And the `github-script` site is the token itself —
    the injected JS runs inside the already-authenticated `github` client.

`issue_number` IS FREE TEXT, AND `type: number` IS THE TRAP
    GitHub validates `boolean` and `choice`. It does NOT validate `number`: the
    dispatch form renders a plain text box and substitutes the value verbatim,
    which is why `number` is not in the guard's `CONSTRAINED_TYPES` and why
    `issue_number` is moved for the same reason as `domain` rather than as a
    courtesy. `dmarc_mgmt_debug` stays interpolated and is NOT a finding: it is
    `type: boolean`. `test_the_input_types_are_what_makes_each_choice_correct`
    pins all three declarations, because those declarations are the whole of
    what makes the remaining interpolation safe.

WHAT THIS MODULE COULD AND COULD NOT RUN
    Every site is driven as a **whole shipped body**, never an excerpt, so the
    tests cannot drift from what ships. Four of the five reach their own exit:
    their external calls are repo scripts and `az`, all stubbed, and the
    `summary` / `post_back` renders are driven with GitHub's other `${{ }}`
    substitutions supplied by `EXPRESSION_FIXTURES` — as GitHub itself supplies
    them. So those four produce the full reading: payload executes, credential
    lands in a file, the callee is still invoked with a legal domain, and the
    step exits **0**.

    One site cannot: `graph` calls live Microsoft Graph thirteen lines past the
    assignment. Its runs are asserted on what is observable before that — the
    sentinel, and that the failure really is `Invoke-RestMethod` rather than
    something earlier. That last half is not bookkeeping: a body that died at a
    parse error would leave the sentinel absent too, and would score as "the
    payload was bound as data" while having proved nothing.

    Two harness facts cost a red run each and are worth stating, because both
    fail in the flattering direction. GitHub substitutes EVERY `${{ }}` before
    pwsh sees the body, so a harness that renders only the input under test
    hands the shell a syntax error 14 lines in and every test then reports "the
    payload did not execute" — `_render` refuses an expression it has no fixture
    for rather than letting that happen. And three of these bodies capture their
    callee with `*>&1` into a variable that becomes an ARTIFACT FILE, so nothing
    the callee prints reaches the step's stdout: a stdout-based assertion
    reports "the script was never called" for a run in which it was called
    correctly. The stubs therefore append to a call log on disk.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
    is honestly green over a step that can no longer receive its input. Only a
    per-step assertion on the wiring notices, which is `_assert_wiring`.
"""

from __future__ import annotations

import importlib.util
import json
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

WORKFLOW = "101-domain-status.yml"

DOMAIN_VAR = "IN_DOMAIN"
DOMAIN_EXPRESSION = "${{ inputs.domain }}"
ISSUE_VAR = "IN_ISSUE_NUMBER"
ISSUE_EXPRESSION = "${{ inputs.issue_number }}"

CF_TOKEN_VAR = "CLOUDFLARE_API_TOKEN_FFC"

# Deliberately NOT shaped like the credentials they stand in for. A JWT-shaped
# literal (`eyJ...`) is treated as a token by secret scanners and by the tooling
# that renders CI output, so it comes back REDACTED — and the one place these
# values are ever printed is an assertion message on a failing run, which is the
# moment the reader needs to see whether the sentinel holds the credential or an
# empty string. A placeholder that cannot be mistaken for a token keeps the
# failure legible; the tests only ever compare it for equality.
FAKE_CF_TOKEN = "cf-read-placeholder-not-a-real-token"
FAKE_GRAPH_TOKEN = "graph-bearer-placeholder-not-a-real-token"

LEGAL_DOMAIN = "ffcworkingsite1.org"

_ARTIFACT_REF = __import__("re").compile(r"artifacts/[\w./-]+\.txt")

# `shell: pwsh` wraps every step body. The epilogue is what makes an unset
# mapping's binder refusal fail the STEP rather than pass silently (#1174).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# A permissive stand-in for each repo script the bodies call: it records what it
# was BOUND, which is the only discriminator that works here. A marker-string
# search over stdout is not one — pwsh echoes the offending source line back in
# a ParserError, so any substring predicate matches the payload text on a run
# that executed nothing.
STUB_CF = """[CmdletBinding()]
param(
    [string]$Zone,
    [switch]$Audit,
    [switch]$EnforceStandard,
    [switch]$DryRun
)
Write-Output "CALLED Zone=[$Zone]"
Add-Content -Path 'CALLS.txt' -Value "CALLED Zone=[$Zone]"
"""

STUB_PREFLIGHT = """[CmdletBinding()]
param(
    [string]$Domain,
    [string]$AccessToken,
    [string]$CloudflareToken,
    [switch]$SkipCloudflare,
    [switch]$SkipGraph,
    [switch]$ShowDnsRecords
)
Write-Output "CALLED Domain=[$Domain]"
Add-Content -Path 'CALLS.txt' -Value "CALLED Domain=[$Domain]"
"""

# The `az` the m365 bodies call. It is a PATH shim rather than an env var on
# purpose: that is the whole claim of this module — the credential at those two
# sites is a CLI session, so an injected body reaches it by running the same
# command the shipped body runs, with nothing sensitive in its environment.
AZ_SHIM = f"""#!/bin/sh
# Stands in for the session `azure/login@v3` leaves behind. Prints a token for
# `account get-access-token` and nothing for anything else.
case "$1 $2" in
  "account get-access-token") echo "{FAKE_GRAPH_TOKEN}" ;;
  *) echo "" ;;
esac
"""

# Every place a free-text input reaches a script body.
#
# `complete` says whether the shipped body can be driven to its own exit in this
# harness. It is False for exactly one site — `graph`, which calls live Microsoft
# Graph 13 lines past the point of interest — and that run is asserted only on
# what is observable before it. See the module docstring.
CALL_SITES = {
    "cloudflare": {
        "job": "cloudflare",
        "step": "Cloudflare audit + dry-run preview",
        "environment": "cloudflare-prod-read",
        "sentinel": "STOLEN-101-cloudflare.txt",
        "complete": True,
        "credential_var": CF_TOKEN_VAR,
        "credential_value": FAKE_CF_TOKEN,
        "refusal": "Refusing to run Update-CloudflareDns.ps1 with an empty -Zone.",
        "called": f"CALLED Zone=[{LEGAL_DOMAIN}]",
        "extra": {CF_TOKEN_VAR: FAKE_CF_TOKEN},
    },
    "graph": {
        "job": "m365",
        "step": "M365 domain status (Graph summary)",
        "environment": "m365-prod",
        "sentinel": "STOLEN-101-graph.txt",
        "complete": False,
        "credential_var": None,
        "credential_value": FAKE_GRAPH_TOKEN,
        "refusal": "Refusing to query Microsoft Graph with an empty domain.",
        "called": None,
        "extra": {},
    },
    "preflight": {
        "job": "m365",
        "step": "Run repo M365 preflight (read-only)",
        "environment": "m365-prod",
        "sentinel": "STOLEN-101-preflight.txt",
        "complete": True,
        "credential_var": None,
        "credential_value": FAKE_GRAPH_TOKEN,
        "refusal": "Refusing to call m365-domain-preflight.ps1 with an empty -Domain.",
        "called": f"CALLED Domain=[{LEGAL_DOMAIN}]",
        "extra": {},
    },
    "summary": {
        "job": "summary",
        "step": "Build deterministic summary",
        "environment": None,
        "sentinel": "STOLEN-101-summary.txt",
        "complete": True,
        "credential_var": None,
        "credential_value": None,
        "refusal": "Refusing to publish a status summary naming no domain.",
        "called": None,
        "extra": {},
    },
    "comment": {
        "job": "post_back",
        "step": "Build comment markdown (includes checks table)",
        "environment": None,
        "sentinel": "STOLEN-101-comment.txt",
        "complete": True,
        "credential_var": None,
        "credential_value": None,
        "refusal": "Refusing to build an issue comment naming no domain.",
        "called": None,
        "extra": {},
    },
}

# The github-script site is listed apart because it is a different runtime, not
# because it is a lesser one.
JS_SITE = {"job": "post_back", "step": "Comment results back to issue"}
JS_SENTINEL = "STOLEN-101-js.txt"

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (
    DOMAIN_VAR,
    ISSUE_VAR,
    CF_TOKEN_VAR,
    "CLOUDFLARE_API_TOKEN_CM",
    "FFC_CF_DMARCMGMT_DEBUG",
)

# GitHub substitutes EVERY `${{ }}` before the body reaches pwsh, so a harness
# that substitutes only the input under test hands the shell a syntax error and
# measures a ParserError instead of the workflow. The `summary` and `post_back`
# bodies read six `needs.*` outputs and three `github.*` values; the three sites
# above read none. Values are chosen to exercise the rendering path rather than
# to be realistic — every branch below them is asserted elsewhere.
EXPRESSION_FIXTURES = {
    "${{ needs.cloudflare.outputs.issues_count }}": "0",
    "${{ needs.cloudflare.outputs.severe_issues_count }}": "0",
    "${{ needs.cloudflare.outputs.changes_count }}": "0",
    "${{ needs.m365.outputs.domain_exists }}": "True",
    "${{ needs.m365.outputs.is_verified }}": "True",
    "${{ needs.m365.outputs.supports_email }}": "True",
    "${{ github.server_url }}": "https://github.com",
    "${{ github.repository }}": "FreeForCharity/FFC-Cloudflare-Automation",
    "${{ github.run_id }}": "1",
}

_EXPRESSION = __import__("re").compile(r"\$\{\{[^}]*\}\}")

# The `summary` and `post_back` bodies read the artifacts the two upstream jobs
# uploaded. On the runner they arrive via `download-artifact`; here they are
# written into the temp cwd. Contents are one representative line each — the
# rendering branches over them are not what this module is about, and every
# branch is reached from the same `$auditLines` variable either way.
ARTIFACT_FIXTURES = {
    "artifacts/cloudflare/cloudflare-audit.txt": "[OK] MX freeforcharity-org.mail.protection.outlook.com\n",
    "artifacts/cloudflare/cloudflare-dryrun.txt": "[DRY-RUN] would add TXT _dmarc\n",
    "artifacts/cloudflare/cloudflare-dkim-check.txt": "[OK] selector1 CNAME present\n",
    "artifacts/m365/m365-dns-guidance.txt": "MX  0  example-org.mail.protection.outlook.com\n",
    "artifacts/m365/m365-preflight.txt": "Domain is verified.\n",
}


def _render(body: str) -> str:
    """Perform GitHub's substitution for everything EXCEPT the burned-down input.

    Fails loudly on an expression it has no fixture for. Left to fall through,
    such an expression is a pwsh syntax error 14 lines into the body, and every
    test built on the run then reports "the payload did not execute" — the
    flattering direction, and the one nobody re-checks (ledger L47).
    """
    for expression, value in EXPRESSION_FIXTURES.items():
        body = body.replace(expression, value)
    leftover = sorted(set(_EXPRESSION.findall(body)))
    assert not leftover, (
        f"the body still contains expressions this harness has no fixture for, "
        f"so pwsh would fail to parse it and every assertion below would "
        f"measure a ParserError: {leftover}"
    )
    return body


# The five bodies as they shipped BEFORE the burn-down, reduced to the line that
# carried the defect. `DOMAIN_HERE` stands where GitHub substituted
# `${{ inputs.domain }}` — inside DOUBLE quotes, where pwsh expands `$( )`
# without any quote break-out being needed.
PRE_FIX_ASSIGNMENT = '$domain = "DOMAIN_HERE"'


def _step(site: str) -> dict:
    spec = CALL_SITES[site]
    return find_step(load_workflow(WORKFLOW), spec["job"], spec["step"])


def _js_step() -> dict:
    return find_step(load_workflow(WORKFLOW), JS_SITE["job"], JS_SITE["step"])


def _assert_wiring(site: str, step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would render a report about no domain, or
    call a script with an empty -Zone.

    The failure messages name the step, the variable and the whole `env:`
    mapping. Rendering a bare `step.get("env")` prints the literal `None` in
    exactly the case the assertion exists for, which is the least useful thing
    it could say.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    name = step.get("name")
    job = CALL_SITES[site]["job"]
    assert env.get(DOMAIN_VAR) == DOMAIN_EXPRESSION, (
        f"step {name!r} in job {job!r} must map {DOMAIN_VAR} to "
        f"{DOMAIN_EXPRESSION} — its env: mapping is {env!r}"
    )
    assert f"$env:{DOMAIN_VAR}" in body, (
        f"step {name!r} in job {job!r} maps {DOMAIN_VAR} but never reads "
        f"$env:{DOMAIN_VAR} — the env: block is decoration and the value "
        f"reaches nothing. Body: {body[:400]!r}"
    )
    assert "inputs.domain" not in body, (
        f"step {name!r} in job {job!r} interpolates inputs.domain into its "
        f"script body again (#1080): under "
        f"{load_workflow(WORKFLOW)['jobs'][job].get('environment')!r} that is "
        f"dispatcher text executed after the approval. Body: {body[:400]!r}"
    )


def _payload(site: str) -> str:
    """A payload valid in the position it lands in.

    It does not need to escape the quotes: pwsh expands `$( )` inside a
    double-quoted string, which is where all five call sites put the value. It
    evaluates to the empty string, so what reaches the callee is a legal domain
    and the run looks clean.

    The two m365 sites do not READ a variable — they run `az` and steal what it
    prints, which is the point this module exists to make.
    """
    spec = CALL_SITES[site]
    sentinel = spec["sentinel"]
    if spec["credential_var"]:
        theft = "$env:" + spec["credential_var"]
    elif spec["credential_value"]:
        theft = (
            "(az account get-access-token --resource-type ms-graph "
            "--query accessToken -o tsv)"
        )
    else:
        theft = "'INJECTED-PWSH'"
    return (
        LEGAL_DOMAIN + "$(Set-Content -Path '" + sentinel + "' -Value " + theft + ")"
    )


def _run(
    site: str, body: str, wrap: bool = True, **env_overrides: str
) -> tuple[str, str | None, int, str]:
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding stubs.

    Returns (output, sentinel_contents_or_None, rc, call_log).

    The sentinel's CONTENTS, not merely its existence: a file written from an
    unset variable would score the same as one written from the live credential,
    and the claim under test is which credential the payload reached.

    The call log is a FILE the stubs append to, not the step's stdout. Three of
    these bodies capture their callee with `*>&1` into a variable and write it
    to an artifact, so nothing the callee prints ever reaches the step's stdout
    — a stdout-based assertion reports "the script was never called" for a run
    in which it was called correctly.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "Update-CloudflareDns.ps1").write_text(STUB_CF, encoding="utf-8")
        for name in ("m365-domain-preflight.ps1", "m365-domain-status.ps1"):
            (tmp / "scripts" / name).write_text(STUB_PREFLIGHT, encoding="utf-8")

        harness = tmp / "harness"
        harness.mkdir()
        az = harness / "az"
        az.write_text(AZ_SHIM, encoding="utf-8")
        az.chmod(0o755)

        for relative, contents in ARTIFACT_FIXTURES.items():
            target = tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")

        runner_temp = tmp / "runner-temp"
        runner_temp.mkdir()

        rendered = _render(body)
        script = tmp / "step.ps1"
        script.write_text(
            (RUNNER_PREAMBLE + rendered + RUNNER_EPILOGUE) if wrap else rendered,
            encoding="utf-8",
        )
        env = child_env(
            harness,
            RUNNER_TEMP=str(runner_temp),
            GITHUB_OUTPUT=str(tmp / "github-output.txt"),
            GITHUB_STEP_SUMMARY=str(tmp / "step-summary.md"),
            GITHUB_WORKSPACE=str(tmp),
            **env_overrides,
        )
        # Only what the test sets may be visible: an inherited IN_DOMAIN would
        # make the fail-closed tests pass for the wrong reason, and an inherited
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
            timeout=180,
        )
        stolen = tmp / CALL_SITES[site]["sentinel"]
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        call_log = tmp / "CALLS.txt"
        calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
        return proc.stdout + proc.stderr, contents, proc.returncode, calls


def _pre_fix(site: str, domain: str) -> str:
    """The shipped body with the burn-down undone and GitHub's substitution done.

    Asserted rather than assumed (ledger L47): a `.replace` that stopped
    matching would leave the body unmutated and the control would measure the
    FIXED code while reporting that the payload did not run — the technique's
    own false negative, pointing the flattering way.
    """
    body = _step(site)["run"]
    marker = f"$domain = $env:{DOMAIN_VAR}"
    assert body.count(marker) == 1, (
        f"site {site!r}: expected exactly one {marker!r} to swap back to the "
        f"pre-fix assignment, found {body.count(marker)}. The control below "
        f"would otherwise measure the fixed body. Body: {body[:400]!r}"
    )
    # Drop the fail-closed guard too — it did not exist before the burn-down,
    # and leaving it in would refuse the run before the payload was reached.
    pre = _strip_guard(body).replace(
        marker, PRE_FIX_ASSIGNMENT.replace("DOMAIN_HERE", domain)
    )
    assert PRE_FIX_ASSIGNMENT.split("=")[0].strip() + " = \"" in pre, (
        f"site {site!r}: the pre-fix assignment did not land: {pre[:400]!r}"
    )
    return pre


def _strip_guard(body: str) -> str:
    """Remove the `if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN)) { … }` block.

    Line-based rather than regex-based: the refusal message contains brackets
    and colons, and a regex over them is the kind of thing that silently matches
    nothing and leaves the guard in place.
    """
    lines = body.splitlines(keepends=True)
    out, i, removed = [], 0, 0
    while i < len(lines):
        if f"IsNullOrWhiteSpace($env:{DOMAIN_VAR})" in lines[i]:
            depth = 0
            while i < len(lines):
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
                if depth <= 0:
                    break
            removed += 1
            continue
        out.append(lines[i])
        i += 1
    assert removed == 1, (
        f"expected to strip exactly one fail-closed guard, stripped {removed} — "
        f"the tests built on this would measure something other than what they "
        f"claim"
    )
    return "".join(out)


# --------------------------------------------------------------------------
# The github-script site runs under node, so it gets its own harness.
# --------------------------------------------------------------------------

JS_HARNESS = """
const path = require('path');
const outcome = { setFailed: null, commented: null };
const core = {
  setFailed: (m) => { outcome.setFailed = String(m); },
  warning: (m) => {},
};
const github = {
  rest: { issues: { createComment: async (a) => { outcome.commented = a; } } },
};
const context = {
  repo: { owner: 'FreeForCharity', repo: 'FFC-Cloudflare-Automation' },
  serverUrl: 'https://github.com',
  runId: 1,
};
(async () => {
  try {
    await (async () => {
SCRIPT_BODY_HERE
    })();
  } catch (e) {
    outcome.threw = String(e && e.message ? e.message : e);
  }
  process.stdout.write(JSON.stringify(outcome));
})();
"""


def _run_js(script_body: str, comment_path: str, **env_overrides: str) -> dict:
    """Run a github-script body under node with `core`/`github`/`context` stubs.

    Returns the recorded outcome. `createComment` is stubbed rather than
    stopped: the question is not whether the network call happens, it is what
    the body decided to send and under whose issue number.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        pathlib.Path(comment_path).write_text  # noqa: B018  (documented below)
        body_path = tmp / "comment.md"
        body_path.write_text("rendered comment body", encoding="utf-8")
        source = JS_HARNESS.replace(
            "SCRIPT_BODY_HERE",
            script_body.replace(
                "${{ steps.comment.outputs.comment_path }}", str(body_path)
            ),
        )
        script = tmp / "step.js"
        script.write_text(source, encoding="utf-8")
        env = child_env(**env_overrides)
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["node", str(script)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        stolen = tmp / JS_SENTINEL
        out = {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "rc": proc.returncode,
            "sentinel": stolen.read_text(encoding="utf-8") if stolen.exists() else None,
        }
        try:
            out["outcome"] = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            out["outcome"] = {}
        return out


JS_PAYLOAD = (
    "1'); require('fs').writeFileSync('"
    + JS_SENTINEL
    + "', 'INJECTED-JS'); const stolen = Number('1"
)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_all_five_pwsh_call_sites_are_wired_through_env():
    for site in CALL_SITES:
        _assert_wiring(site, _step(site))


def test_the_github_script_site_is_wired_through_env():
    step = _js_step()
    env = step.get("env") or {}
    body = step.get("with", {}).get("script", "")
    assert env.get(ISSUE_VAR) == ISSUE_EXPRESSION, (
        f"the post-back step must map {ISSUE_VAR} to {ISSUE_EXPRESSION} — its "
        f"env: mapping is {env!r}"
    )
    assert f"process.env.{ISSUE_VAR}" in body, (
        f"the post-back step maps {ISSUE_VAR} but never reads "
        f"process.env.{ISSUE_VAR}, so the env: block is decoration. "
        f"Script: {body[:400]!r}"
    )
    assert "inputs.issue_number" not in body, (
        f"the post-back step interpolates inputs.issue_number into its JS "
        f"source again (#1080): it would run inside the authenticated github "
        f"client. Script: {body[:400]!r}"
    )


def test_the_six_sites_are_distinct_steps_across_four_jobs():
    """A guard against the table silently collapsing.

    `find_step` matches a NAME SUBSTRING, so two entries that resolved to the
    same step would make the loops above run twice over one site and report full
    coverage of two. The environments are asserted here too: this entry is in
    the #1080 freeze as a WRITE one solely because the `m365` job is
    `m365-prod`, and every job runs off the same dispatch.
    """
    workflow = load_workflow(WORKFLOW)
    seen = {
        site: (spec["job"], _step(site).get("name"))
        for site, spec in CALL_SITES.items()
    }
    seen["_js"] = (JS_SITE["job"], _js_step().get("name"))
    assert len(set(seen.values())) == 6, (
        f"the six call sites did not resolve to six distinct steps: {seen}"
    )
    assert len({job for job, _ in seen.values()}) == 4, (
        f"the six call sites no longer span four jobs: {seen}"
    )
    for site, spec in CALL_SITES.items():
        declared = workflow["jobs"][spec["job"]].get("environment")
        assert declared == spec["environment"], (
            f"site {site!r} no longer sits in the environment this module is "
            f"about: job {spec['job']!r} declares {declared!r}, expected "
            f"{spec['environment']!r}"
        )
    assert workflow["jobs"]["m365"].get("environment") == "m365-prod", (
        "the m365 job no longer declares m365-prod, which is the only reason "
        "101 is a WRITE entry in the #1080 freeze"
    )


def test_the_input_types_are_what_makes_each_choice_correct():
    """`domain` and `issue_number` are free text; `dmarc_mgmt_debug` is not.

    Pinned rather than left as prose in the docstring, because these three
    declarations are the entire justification for moving two inputs and leaving
    the third interpolated. A later edit that changed `dmarc_mgmt_debug` to
    `string` would make the surviving interpolation a live finding, and nothing
    else in the tree would say so.
    """
    workflow = load_workflow(WORKFLOW)
    declared = guard.dispatch_inputs(workflow)
    assert declared.get("domain") == "string", (
        f"`domain` is no longer declared `type: string`: {declared!r}"
    )
    assert declared.get("issue_number") == "number", (
        f"`issue_number` is no longer declared `type: number`: {declared!r}"
    )
    assert declared.get("dmarc_mgmt_debug") == "boolean", (
        f"`dmarc_mgmt_debug` is no longer declared `type: boolean`, so leaving "
        f"it interpolated is no longer safe: {declared!r}"
    )
    free = set(guard.free_text_inputs(workflow))
    assert {"domain", "issue_number"} <= free, (
        f"the guard no longer treats both moved inputs as free text, so this "
        f"module's premise is gone: {free!r}"
    )
    assert "dmarc_mgmt_debug" not in free, (
        f"`dmarc_mgmt_debug` is now free text, and it is still interpolated "
        f"into the Cloudflare step's env: expression: {free!r}"
    )
    assert "number" not in guard.CONSTRAINED_TYPES, (
        f"`number` is now treated as a constrained type, which would make the "
        f"guard blind to the `issue_number` class: {guard.CONSTRAINED_TYPES!r}"
    )


def test_the_m365_credential_is_a_cli_session_no_sweep_can_see():
    """This module's central claim, pinned rather than left as prose.

    The two `m365-prod` injection points hold no credential in any `env:` block,
    match no `secrets.` reference, and are absent from the #1188 reachability
    index — while sitting one line above `az account get-access-token`. If a
    later edit ever maps a token into either step's `env:`, the reasoning in the
    docstring stops being true and should be re-read.
    """
    workflow = load_workflow(WORKFLOW)
    m365_steps = workflow["jobs"]["m365"]["steps"]
    assert any(
        str(step.get("uses", "")).startswith("azure/login@") for step in m365_steps
    ), (
        "the m365 job no longer performs an OIDC azure/login, so the CLI "
        "session this module is about is gone"
    )
    for site in ("graph", "preflight"):
        step = _step(site)
        env = step.get("env") or {}
        assert set(env) == {DOMAIN_VAR}, (
            f"site {site!r} now maps something besides {DOMAIN_VAR} into its "
            f"env:, so 'no credential is visible here' is no longer the "
            f"reading: {env!r}"
        )
        assert "az account get-access-token" in step.get("run", ""), (
            f"site {site!r} no longer mints its token from the CLI session, so "
            f"the claim that no sweep can see the credential does not hold"
        )
    # And the reachability index really does report nothing for them — asserted
    # against the guard rather than quoted from a run, so it tracks the tool.
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"the guard could not read: {unreadable}"
    reach = guard.reachability_by_site(findings)
    m365_reach = [key for key in reach if key[0] == WORKFLOW and key[1] == "m365"]
    assert not m365_reach, (
        f"the reachability index now reports a credential in reach at the m365 "
        f"sites. That is an improvement to the tool, and it means this "
        f"module's docstring is out of date: {m365_reach!r}"
    )


def test_the_artifact_fixtures_cover_every_path_the_bodies_read():
    """Keeps the harness honest about a file it silently does not supply.

    A missing artifact does not raise where it is read — `Get-Content
    -ErrorAction SilentlyContinue` yields nothing — it raises 100 lines later
    inside `Get-CheckStatus`, as a null-argument error naming a DNS record. The
    obvious reading of that is "the render is broken", not "the harness did not
    lay down a file", which is why this is asserted up front rather than
    diagnosed again next time.
    """
    referenced = set()
    for site in CALL_SITES:
        referenced.update(_ARTIFACT_REF.findall(_step(site).get("run", "")))
    missing = sorted(referenced - set(ARTIFACT_FIXTURES))
    assert not missing, (
        f"these artifact paths are read by a body and not supplied by "
        f"ARTIFACT_FIXTURES, so the render dies far from the cause: {missing}"
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
        and "is not set" not in str(step.get("run", ""))
    ]
    assert not offenders, (
        f"these steps interpolate a secret into their script body, which "
        f"writes it to a file in the runner workspace: {offenders}"
    )
    # Positive control: the sweep must be looking at real bodies. Without it a
    # change that made `run` unreadable would report zero offenders and pass.
    bodies = [
        step.get("run", "") for spec in jobs.values() for step in spec.get("steps", [])
    ]
    assert sum(1 for b in bodies if b.strip()) >= 6, (
        f"the sweep found almost no run: bodies to check, so its empty result "
        f"says nothing: {len(bodies)} steps"
    )


# --------------------------------------------------------------------------
# Behaviour — the pre-fix controls
# --------------------------------------------------------------------------


def test_the_pre_fix_cloudflare_body_stole_the_write_token_and_exited_zero():
    """The positive control for the whole lane, on the site with a real token.

    Without this, every "the payload binds as data" test below is satisfied by a
    body that never ran the payload for some unrelated reason.
    """
    site = "cloudflare"
    body = _pre_fix(site, _payload(site))
    out, stolen, rc, calls = _run(site, body, **CALL_SITES[site]["extra"])
    assert stolen is not None, (
        f"the pre-fix body did not execute the payload, so this control proves "
        f"nothing about the burn-down: {out[:600]}"
    )
    assert stolen.strip() == FAKE_CF_TOKEN, (
        f"the payload wrote a sentinel but not the Cloudflare token — it may "
        f"have read an unset variable, which would score the same as a theft: "
        f"{stolen!r}"
    )
    assert rc == 0, (
        f"the pre-fix body was expected to exit 0 — a run that goes red is a "
        f"run someone investigates. Got rc={rc}: {out[:600]}"
    )
    assert CALL_SITES[site]["called"] in calls, (
        f"the payload left residue: the callee should still have been invoked "
        f"with a legal domain, which is what makes the run look clean. "
        f"Calls: {calls!r}"
    )


def test_the_pre_fix_m365_body_minted_a_graph_token_with_no_env_var_in_reach():
    """The claim the reachability index cannot make.

    Every credential VARIABLE is removed from this child environment. The
    payload still walks away with a Graph bearer token, because it runs `az`
    itself — the credential is the login session, not a value in the process
    environment.
    """
    site = "preflight"
    body = _pre_fix(site, _payload(site))
    out, stolen, rc, calls = _run(site, body)
    assert stolen is not None, (
        f"the pre-fix body did not execute the payload: {out[:600]}"
    )
    assert stolen.strip() == FAKE_GRAPH_TOKEN, (
        f"the payload wrote a sentinel but not a Graph token: {stolen!r}"
    )
    assert rc == 0, (
        f"the pre-fix body was expected to exit 0. Got rc={rc}: {out[:600]}"
    )
    assert CALL_SITES[site]["called"] in calls, (
        f"the callee should still have been invoked with a legal domain. "
        f"Calls: {calls!r}"
    )


def test_every_pre_fix_body_executed_the_payload():
    """All five, including the three that cannot be driven to completion here.

    For those three the assertion is only that the payload RAN — which is the
    fact the burn-down is about, and it is observable at the assignment on the
    first line, long before the Graph call or the render that stops this
    harness.
    """
    for site in CALL_SITES:
        body = _pre_fix(site, _payload(site))
        out, stolen, _, _ = _run(site, body, **CALL_SITES[site]["extra"])
        assert stolen is not None, (
            f"site {site!r}: the pre-fix body did not execute the payload, so "
            f"the corresponding data-binding test below proves nothing: "
            f"{out[:600]}"
        )


# --------------------------------------------------------------------------
# Behaviour — the shipped bodies
# --------------------------------------------------------------------------


def test_the_shipped_bodies_bind_the_payload_as_data():
    """The whole point: the payload binds verbatim and runs nothing, five times."""
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, stolen, rc, calls = _run(
            site, step["run"], **{DOMAIN_VAR: _payload(site)}, **spec["extra"]
        )
        assert stolen is None, (
            f"site {site!r}: the payload EXECUTED through the env: mapping — "
            f"the sentinel holds {stolen!r}. Output: {out[:600]}"
        )
        if spec["complete"]:
            assert rc == 0, f"site {site!r} step exited {rc}: {out[:600]}"
        else:
            # `graph` dies at its live Graph call, 13 lines past the assignment.
            # Asserted rather than skipped: a body that failed EARLIER — at the
            # guard, or at a parse error — would also leave the sentinel absent,
            # and would score as "the payload was bound as data" while proving
            # nothing at all.
            assert "Invoke-RestMethod" in out, (
                f"site {site!r} was expected to get past the input handling and "
                f"fail at its live Graph call. It failed somewhere else, so the "
                f"absent sentinel says nothing: {out[:600]}"
            )
        if spec["called"]:
            assert _payload(site) in calls, (
                f"site {site!r}: the payload should have reached the callee as "
                f"a literal argument. Calls: {calls!r}"
            )


def test_the_shipped_bodies_still_pass_ordinary_domains_through():
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, _, rc, calls = _run(
            site, step["run"], **{DOMAIN_VAR: LEGAL_DOMAIN}, **spec["extra"]
        )
        if spec["complete"]:
            assert rc == 0, f"site {site!r} step exited {rc}: {out[:600]}"
        else:
            assert "Invoke-RestMethod" in out, (
                f"site {site!r} was expected to reach its live Graph call with "
                f"an ordinary domain: {out[:600]}"
            )
        if spec["called"]:
            assert spec["called"] in calls, (
                f"site {site!r}: expected {spec['called']!r} in the call log: "
                f"{calls!r}"
            )


def test_an_empty_mapping_fails_closed_and_says_which_one():
    """The silent case. EMPTY binds, so nothing downstream would refuse it."""
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, _, rc, _ = _run(site, step["run"], **{DOMAIN_VAR: ""}, **spec["extra"])
        assert rc == 1, (
            f"site {site!r}: an EMPTY {DOMAIN_VAR} was expected to refuse with "
            f"rc 1. Got rc={rc}: {out[:600]}"
        )
        assert DOMAIN_VAR in out and spec["refusal"] in out, (
            f"site {site!r}: the refusal must name the variable and what it is "
            f"refusing to do, or an operator cannot act on it: {out[:600]}"
        )


def test_an_unset_mapping_fails_closed_and_says_which_one():
    for site, spec in CALL_SITES.items():
        step = _step(site)
        _assert_wiring(site, step)
        out, _, rc, _ = _run(site, step["run"], **spec["extra"])
        assert rc == 1, (
            f"site {site!r}: an UNSET {DOMAIN_VAR} was expected to refuse with "
            f"rc 1. Got rc={rc}: {out[:600]}"
        )
        assert DOMAIN_VAR in out and spec["refusal"] in out, (
            f"site {site!r}: the refusal must name the variable: {out[:600]}"
        )


def test_without_the_guard_an_empty_domain_is_silent():
    """Why the fail-closed block is not decoration, measured at both shapes.

    On the Cloudflare site an empty domain reaches `-Zone` and the callee is
    invoked against a name that is not a zone, at rc 0. On the summary site
    there is no callee at all — the body simply renders a report headline naming
    no domain, which is the case nothing else in the tree would catch.
    """
    site = "cloudflare"
    stripped = _strip_guard(_step(site)["run"])
    out, _, rc, calls = _run(
        site, stripped, **{DOMAIN_VAR: ""}, **CALL_SITES[site]["extra"]
    )
    assert rc == 0 and "CALLED Zone=[]" in calls, (
        f"with the guard removed, an EMPTY mapping was expected to bind and "
        f"reach the callee silently (rc 0) — that is what the guard exists to "
        f"stop. Got rc={rc}: {out[:600]} / calls={calls!r}"
    )


# --------------------------------------------------------------------------
# Behaviour — the github-script site, under node
# --------------------------------------------------------------------------


def test_the_pre_fix_script_executed_injected_javascript():
    """The positive control for the JS half.

    GitHub renders `${{ }}` into the JS SOURCE before node parses it, so
    `Number('${{ inputs.issue_number }}')` is a string literal only for as long
    as the value contains no apostrophe.
    """
    body = _js_step()["with"]["script"]
    marker = f"Number(process.env.{ISSUE_VAR})"
    assert body.count(marker) == 1, (
        f"expected exactly one {marker!r} to swap back to the pre-fix form, "
        f"found {body.count(marker)}: {body[:400]!r}"
    )
    pre = body.replace(marker, "Number('" + JS_PAYLOAD + "')")
    assert "require('fs').writeFileSync" in pre, (
        f"the pre-fix substitution did not land: {pre[:400]!r}"
    )
    result = _run_js(pre, JS_SENTINEL)
    assert result["sentinel"] == "INJECTED-JS", (
        f"the pre-fix script did not execute the injected JS, so this control "
        f"proves nothing: {result['stderr'][:600]} / rc={result['rc']}"
    )


def test_the_shipped_script_treats_the_issue_number_as_data():
    body = _js_step()["with"]["script"]
    result = _run_js(body, JS_SENTINEL, **{ISSUE_VAR: JS_PAYLOAD})
    assert result["sentinel"] is None, (
        f"the payload EXECUTED through the env: mapping: "
        f"{result['sentinel']!r} / {result['stderr'][:600]}"
    )
    outcome = result["outcome"]
    assert outcome.get("commented") is None, (
        f"a non-numeric issue number reached createComment: {outcome!r}"
    )
    assert outcome.get("setFailed"), (
        f"the script neither commented nor failed, so it passed silently: "
        f"{outcome!r}"
    )
    assert ISSUE_VAR in outcome["setFailed"], (
        f"the refusal does not name the variable, so an operator cannot act on "
        f"it: {outcome['setFailed']!r}"
    )


def test_the_shipped_script_still_comments_on_an_ordinary_issue_number():
    body = _js_step()["with"]["script"]
    result = _run_js(body, JS_SENTINEL, **{ISSUE_VAR: "719"})
    outcome = result["outcome"]
    assert outcome.get("setFailed") is None, (
        f"a legitimate issue number was refused: {outcome!r}"
    )
    assert outcome.get("commented", {}).get("issue_number") == 719, (
        f"the comment was not posted to issue 719: {outcome!r}"
    )
    assert outcome["commented"].get("body"), (
        f"the comment was posted with an empty body: {outcome!r}"
    )


def test_the_shipped_script_refuses_an_unset_mapping():
    """JS refuses nothing on its own — this is the quieter of the two runtimes.

    An unmapped env var is `undefined`, `Number(undefined)` is NaN, and
    `createComment` would be called with a nonsense issue_number under a token
    carrying `issues: write`.
    """
    body = _js_step()["with"]["script"]
    result = _run_js(body, JS_SENTINEL)
    outcome = result["outcome"]
    assert outcome.get("commented") is None, (
        f"an unset {ISSUE_VAR} still reached createComment: {outcome!r}"
    )
    assert outcome.get("setFailed") and ISSUE_VAR in outcome["setFailed"], (
        f"the unset case did not refuse by name: {outcome!r}"
    )


# --------------------------------------------------------------------------
# The checker agrees
# --------------------------------------------------------------------------


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


# Split by what each test actually needs, rather than gating the whole module on
# one tool (#1182): the static half asserts things about the YAML and runs
# anywhere, the pwsh half needs pwsh, the JS half needs node. A blanket
# `sys.exit(0)` would report a module that asserted NOTHING as green.
PWSH_TESTS = (
    "test_the_pre_fix_cloudflare_body_stole_the_write_token_and_exited_zero",
    "test_the_pre_fix_m365_body_minted_a_graph_token_with_no_env_var_in_reach",
    "test_every_pre_fix_body_executed_the_payload",
    "test_the_shipped_bodies_bind_the_payload_as_data",
    "test_the_shipped_bodies_still_pass_ordinary_domains_through",
    "test_an_empty_mapping_fails_closed_and_says_which_one",
    "test_an_unset_mapping_fails_closed_and_says_which_one",
    "test_without_the_guard_an_empty_domain_is_silent",
)
NODE_TESTS = (
    "test_the_pre_fix_script_executed_injected_javascript",
    "test_the_shipped_script_treats_the_issue_number_as_data",
    "test_the_shipped_script_still_comments_on_an_ordinary_issue_number",
    "test_the_shipped_script_refuses_an_unset_mapping",
)

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    have_node = shutil.which("node") is not None
    failures = skipped = 0
    for t in TESTS:
        if t.__name__ in PWSH_TESTS and not have_pwsh:
            skipped += 1
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        if t.__name__ in NODE_TESTS and not have_node:
            skipped += 1
            print(f"  SKIP {t.__name__} (node not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    if skipped:
        print(f"  ({skipped} skipped for a missing interpreter)")
    sys.exit(1 if failures else 0)
