"""Unit tests for 103's free-text call sites (#1080 burn-down).

103 enforces the FFC domain standard: Cloudflare DNS plus Exchange Online DKIM.
Its free-text dispatch inputs used to be interpolated into FIVE script bodies
across FIVE jobs, and both of the values a dispatcher controls travelled that
way.

WHY THIS IS THE WIDEST ENTRY IN THE FREEZE
    One input, `domain`, reached SIX call sites in FIVE jobs under TWO distinct
    gated write environments:

        cloudflare_enforce    cloudflare-prod-write   pwsh
        exo_check             m365-prod               pwsh
        cloudflare_set_dkim   cloudflare-prod-write   pwsh
        exo_enable            m365-prod               pwsh
        post_back             (ungated)               github-script  x2

    The jobs are chained (`needs:`), so ONE payload in ONE dispatch ran four
    times under two production credentials on a SINGLE approval. Every earlier
    lane reached one credential, or two in one job; this one crosses two
    services.

WHERE THE CREDENTIAL SAT — BOTH WAYS, IN ONE FILE
    This is also the first lane where the two exposure shapes the burn-down has
    been learning about appear together, which is why neither sweep finds all of
    it:

    * The Cloudflare jobs hide their tokens. `cloudflare-tokens-from-kv` with
      `scope: write` resolves the `wr-all-*` Key Vault secrets and exports
      CLOUDFLARE_API_TOKEN_FFC / _CM through GITHUB_ENV, so they are in the
      process environment of the injection point while appearing in NO `env:`
      block a reviewer can read. That is the #1161 blindness: a sweep of the
      file for `secrets.` in a `run:` body finds nothing at the site that
      matters.
    * Both m365-prod steps name FFC_EXO_CERT_PFX_BASE64 and
      FFC_EXO_CERT_PASSWORD in the SAME step's `env:` as the injection point
      (ledger L213), so there the Exchange Online certificate is visible — and a
      reviewer who has learned to check `env:` blocks would score the Cloudflare
      half as holding nothing.

    Ranking this workflow by either test alone understates it. Measured at all
    four pwsh sites against the bodies as they shipped: the credential was
    written to a file, the callee was then invoked with a LEGAL domain, and the
    step exited 0.

THE FIRST `github-script` SITE IN THE BURN-DOWN
    `post_back` is a `github-script` step, and a `script:` block is substituted
    exactly like a `run:` body — GitHub renders `${{ }}` into the JS SOURCE
    before node parses it. So

        const domain = '${{ inputs.domain }}';

    is a string literal only for as long as the value contains no apostrophe. It
    is the same defect in a different language, and the runtime is not a lesser
    one: the payload runs inside the `github` client already authenticated with
    a token carrying `issues: write`.

    It is worth being precise about which half of that step was dangerous.
    `post_back` declares no `environment:`, so it is NOT behind the gate — which
    makes it the one site an injected payload reached WITHOUT an approver
    spending anything. The gate is what protects the other four; nothing
    protected this one.

`issue_number` IS FREE TEXT, AND THAT IS THE EASIEST THING HERE TO GET WRONG
    It is declared `type: number`, which reads like a constraint and is not one.
    `CONSTRAINED_TYPES` in the guard is exactly {boolean, choice}, because those
    are the two GitHub actually validates — a `number` input renders as a free
    text box and its value is substituted verbatim. So `issue_number` was as
    injectable as `domain`, in the same step, and is moved for the same reason.
    `test_the_number_typed_input_is_not_treated_as_constrained` pins that, since
    the whole argument for moving it rests on a fact about GitHub that the type
    name actively argues against.

    The four `boolean` inputs (`dry_run`, `github_pages_only`, `skip_m365`,
    `dmarc_mgmt_debug`) stay interpolated deliberately, and
    `test_the_still_interpolated_inputs_are_type_constrained` pins their
    declarations rather than leaving that as a reading of a comment.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
    is honestly green over a step that can no longer receive its input. Only a
    per-step assertion on the wiring notices, which is `_assert_pwsh_wiring`.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow, step_github_script

WORKFLOW = "103-enforce-domain-standard.yml"

# The env var every free-text input must travel in.
ENV_DOMAIN = "IN_DOMAIN"
ENV_ISSUE = "IN_ISSUE_NUMBER"
EXPR_DOMAIN = "${{ inputs.domain }}"
EXPR_ISSUE = "${{ inputs.issue_number }}"

# A legal domain, so that a payload whose side effect leaves no residue reaches
# the callee looking exactly like a clean dispatch.
LEGAL_DOMAIN = "ffcworkingsite1.org"

# Deliberately NOT shaped like the credentials they stand in for. A
# token-shaped literal is treated as a secret by scanners and by the tooling
# that renders CI output, so it comes back REDACTED — and the one place these
# values are ever printed is an assertion message on a failing run, which is the
# moment the reader needs to see whether the sentinel holds the credential or an
# empty string. The tests only ever compare them for equality.
FAKE_CF_TOKEN = "cf-write-placeholder-not-a-real-token"
FAKE_EXO_PASSWORD = "exo-cert-placeholder-not-a-real-password"
FAKE_GH_TOKEN = "gh-issues-write-placeholder-not-a-real-token"

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = (
    ENV_DOMAIN,
    ENV_ISSUE,
    "CLOUDFLARE_API_TOKEN_FFC",
    "CLOUDFLARE_API_TOKEN_CM",
    "FFC_EXO_CERT_PASSWORD",
    "FFC_EXO_CERT_PFX_BASE64",
)

CF_STUB = """[CmdletBinding()]
param(
    [string]$Zone,
    [string]$Name,
    [string]$Type,
    [string]$Content,
    [switch]$EnforceStandard,
    [switch]$DryRun,
    [switch]$GitHubPagesOnly,
    [switch]$Audit
)
Write-Output "CALLED Zone=[$Zone] Name=[$Name] Type=[$Type] Content=[$Content] Enforce=[$EnforceStandard] Audit=[$Audit]"
"""

# SupportsShouldProcess so the stub accepts the real call's `-Confirm:$false`.
# Without it the invocation fails on an unknown parameter, and every payload
# test would report "the callee was never reached" for a harness reason.
DKIM_STUB = """[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Domain,
    [string]$Organization,
    [string]$AppId,
    [string]$TenantId,
    [switch]$CreateIfMissing,
    [switch]$Enable
)
Write-Output "CALLED Domain=[$Domain] Organization=[$Organization] AppId=[$AppId] Enable=[$Enable]"
"""


@dataclasses.dataclass(frozen=True)
class PwshSite:
    """One pwsh call site, and everything needed to run it in isolation."""

    job: str
    step: str
    environment: str
    # Where the stub callee goes, relative to the temp cwd.
    stub_path: str
    stub: str
    # The credential a payload here could reach, and the value the harness gives
    # it. `in_step_env` records WHICH of the two exposure shapes this site has —
    # named in the step's own `env:`, or exported invisibly through GITHUB_ENV.
    credential_var: str
    fake_credential: str
    in_step_env: bool
    # `${{ }}` expressions other than the burned-down inputs, which the runner
    # substitutes exactly as GitHub would. pwsh cannot PARSE `${{` at all.
    render: dict
    # The body as it shipped BEFORE the burn-down, verbatim from origin/main.
    pre_fix_body: str
    # Extra env the body needs, as {var: path-relative-to-the-temp-cwd}. An
    # empty value means the temp dir ITSELF (RUNNER_TEMP is a directory);
    # anything else names a file (GITHUB_OUTPUT is appended to, so pointing it
    # at a directory makes Out-File fail and the step exit 1 for a harness
    # reason that reads exactly like the guard firing).
    extra_env: dict = dataclasses.field(default_factory=dict)
    capture: tuple = ()
    # How many times the fixed body must read the env var: one guard + one per
    # invocation.
    min_env_reads: int = 2

    @property
    def sentinel(self) -> str:
        return f"STOLEN-103-{self.job}.txt"


SECRET_RENDER = {
    "${{ secrets.FFC_AZURE_CLIENT_ID }}": "00000000-0000-0000-0000-000000000000",
    "${{ secrets.FFC_AZURE_TENANT_ID }}": "11111111-1111-1111-1111-111111111111",
}

PWSH_SITES = (
    PwshSite(
        job="cloudflare_enforce",
        step="Enforce Cloudflare standard",
        environment="cloudflare-prod-write",
        stub_path="Update-CloudflareDns.ps1",
        stub=CF_STUB,
        credential_var="CLOUDFLARE_API_TOKEN_FFC",
        fake_credential=FAKE_CF_TOKEN,
        in_step_env=False,
        render={
            "${{ inputs.dry_run }}": "false",
            "${{ inputs.github_pages_only }}": "false",
        },
        pre_fix_body=(
            "$ErrorActionPreference = 'Stop'\n"
            '$domain = "${{ inputs.domain }}"\n'
            '$dryRun = "${{ inputs.dry_run }}"\n'
            '$pagesOnly = "${{ inputs.github_pages_only }}"\n'
            "\n"
            "$outDir = Join-Path $env:RUNNER_TEMP 'domain-enforce-cloudflare'\n"
            "New-Item -ItemType Directory -Force -Path $outDir | Out-Null\n"
            "\n"
            "$enforceArgs = @('-Zone', $domain, '-EnforceStandard')\n"
            "if ($dryRun -eq 'true') { $enforceArgs += '-DryRun' }\n"
            "if ($pagesOnly -eq 'true') { $enforceArgs += '-GitHubPagesOnly' }\n"
            "$out = & pwsh -NoProfile -File .\\Update-CloudflareDns.ps1 @enforceArgs *>&1\n"
            "\n"
            "$outPath = Join-Path $outDir 'cloudflare-enforce.txt'\n"
            "$out | Out-File -FilePath $outPath -Encoding utf8\n"
            "\n"
            "$audit = & pwsh -NoProfile -File .\\Update-CloudflareDns.ps1 -Zone $domain "
            "-Audit -ErrorAction Continue *>&1\n"
            "$auditPath = Join-Path $outDir 'cloudflare-audit-after.txt'\n"
            "$audit | Out-File -FilePath $auditPath -Encoding utf8\n"
            "\n"
            '"out_dir=$outDir" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8\n'
        ),
        extra_env={"RUNNER_TEMP": "", "GITHUB_OUTPUT": "github_output.txt"},
        capture=(
            "domain-enforce-cloudflare/cloudflare-enforce.txt",
            "domain-enforce-cloudflare/cloudflare-audit-after.txt",
        ),
        min_env_reads=2,
    ),
    PwshSite(
        job="exo_check",
        step="Check DKIM selector targets",
        environment="m365-prod",
        stub_path="scripts/m365-dkim.ps1",
        stub=DKIM_STUB,
        credential_var="FFC_EXO_CERT_PASSWORD",
        fake_credential=FAKE_EXO_PASSWORD,
        in_step_env=True,
        render={
            "${{ steps.org.outputs.organization }}": "freeforcharity.onmicrosoft.com",
            **SECRET_RENDER,
        },
        pre_fix_body=(
            "pwsh -NoProfile -File scripts/m365-dkim.ps1 "
            '-Domain "${{ inputs.domain }}" '
            '-Organization "${{ steps.org.outputs.organization }}" '
            '-AppId "${{ secrets.FFC_AZURE_CLIENT_ID }}" '
            '-TenantId "${{ secrets.FFC_AZURE_TENANT_ID }}" '
            "-CreateIfMissing -Confirm:$false\n"
        ),
    ),
    PwshSite(
        job="cloudflare_set_dkim",
        step="Create/Update DKIM selector CNAMEs",
        environment="cloudflare-prod-write",
        stub_path="Update-CloudflareDns.ps1",
        stub=CF_STUB,
        credential_var="CLOUDFLARE_API_TOKEN_FFC",
        fake_credential=FAKE_CF_TOKEN,
        in_step_env=False,
        render={
            "${{ needs.exo_check.outputs.selector1_target }}": (
                "selector1-ffcworkingsite1-org._domainkey.freeforcharity.onmicrosoft.com"
            ),
            "${{ needs.exo_check.outputs.selector2_target }}": (
                "selector2-ffcworkingsite1-org._domainkey.freeforcharity.onmicrosoft.com"
            ),
        },
        pre_fix_body=(
            '$domain = "${{ inputs.domain }}"\n'
            '$s1Target = "${{ needs.exo_check.outputs.selector1_target }}"\n'
            '$s2Target = "${{ needs.exo_check.outputs.selector2_target }}"\n'
            "\n"
            "if ([string]::IsNullOrWhiteSpace($s1Target) -or "
            "[string]::IsNullOrWhiteSpace($s2Target)) {\n"
            "  throw 'Missing DKIM selector targets from Exchange Online check job.'\n"
            "}\n"
            "\n"
            "pwsh -NoProfile -File Update-CloudflareDns.ps1 -Zone $domain "
            '-Name "selector1._domainkey" -Type CNAME -Content $s1Target\n'
            "pwsh -NoProfile -File Update-CloudflareDns.ps1 -Zone $domain "
            '-Name "selector2._domainkey" -Type CNAME -Content $s2Target\n'
        ),
    ),
    PwshSite(
        job="exo_enable",
        step="Enable DKIM signing",
        environment="m365-prod",
        stub_path="scripts/m365-dkim.ps1",
        stub=DKIM_STUB,
        credential_var="FFC_EXO_CERT_PASSWORD",
        fake_credential=FAKE_EXO_PASSWORD,
        in_step_env=True,
        render={
            "${{ needs.exo_check.outputs.organization }}": "freeforcharity.onmicrosoft.com",
            **SECRET_RENDER,
        },
        pre_fix_body=(
            "pwsh -NoProfile -File scripts/m365-dkim.ps1 "
            '-Domain "${{ inputs.domain }}" '
            '-Organization "${{ needs.exo_check.outputs.organization }}" '
            '-AppId "${{ secrets.FFC_AZURE_CLIENT_ID }}" '
            '-TenantId "${{ secrets.FFC_AZURE_TENANT_ID }}" '
            "-CreateIfMissing -Enable -Confirm:$false\n"
        ),
    ),
)

# --- the github-script site -------------------------------------------------

GH_JOB = "post_back"
GH_STEP = "Comment results back to issue"
GH_SENTINEL = "STOLEN-103-post_back.txt"
GH_RENDER = {
    "${{ inputs.dry_run }}": "false",
    "${{ needs.cloudflare_enforce.outputs.issues_count }}": "0",
}

GH_PRE_FIX_BODY = """const fs = require('fs');

const issueNumber = Number('${{ inputs.issue_number }}');
const domain = '${{ inputs.domain }}';
const dryRun = String('${{ inputs.dry_run }}');

const cfIssues = Number('${{ needs.cloudflare_enforce.outputs.issues_count }}');
const cfStatus = (cfIssues === 0) ? 'PASS' : 'PARTIAL';

const runUrl = `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`;

const cfEnforce = fs.readFileSync('artifacts/cloudflare/cloudflare-enforce.txt', 'utf8');
const cfAuditAfter = fs.readFileSync('artifacts/cloudflare/cloudflare-audit-after.txt', 'utf8');

const body = [
  `Enforce Domain Standard for **${domain}**`,
  '',
  `- Mode: **${dryRun === 'true' ? 'DRY RUN' : 'LIVE'}**`,
  `- Cloudflare post-audit: **${cfStatus}** (${cfIssues} issues)`,
  `- Run: ${runUrl}`,
  '',
  '<details><summary>Cloudflare enforce output</summary>\\n\\n```\\n' + cfEnforce.trim() + '\\n```\\n</details>',
  '',
  '<details><summary>Cloudflare audit (after)</summary>\\n\\n```\\n' + cfAuditAfter.trim() + '\\n```\\n</details>',
].join('\\n');

await github.rest.issues.createComment({
  owner: context.repo.owner,
  repo: context.repo.repo,
  issue_number: issueNumber,
  body,
});
"""

# The harness `actions/github-script` stands in for. It supplies exactly the
# four names the action injects and that this body uses — `github`, `context`,
# `core`, and node's own `require` — and records the two things the tests
# discriminate on: what createComment was called with, and whether the step
# failed itself. Errors are captured rather than thrown so that "the body threw"
# and "the body called setFailed" stay distinguishable; collapsing them would
# make a body that crashes score identically to one that refused a payload.
GH_HARNESS = """
const fs = require('fs');
const OUT = process.env.HARNESS_RESULT;
const calls = [];
const github = {
  rest: { issues: { createComment: async (args) => { calls.push(args); return { data: {} }; } } },
};
const context = {
  serverUrl: 'https://github.com',
  repo: { owner: 'FreeForCharity', repo: 'FFC-Cloudflare-Automation' },
  runId: 1234567890,
};
let failed = null;
const core = { setFailed: (m) => { failed = String(m); } };

(async () => {
__BODY__
})().then(
  () => fs.writeFileSync(OUT, JSON.stringify({ calls, failed, threw: null })),
  (e) => fs.writeFileSync(OUT, JSON.stringify({ calls, failed, threw: String(e) })),
);
"""


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def _step(site: PwshSite) -> dict:
    return find_step(load_workflow(WORKFLOW), site.job, site.step)


def _dispatch_inputs() -> dict:
    """The workflow's declared dispatch inputs.

    `on` is read through `doc.get(True, doc.get("on"))` because YAML 1.1 parses
    a bare `on:` key as the BOOLEAN True, so `doc["on"]` raises KeyError on a
    perfectly well-formed workflow.
    """
    doc = load_workflow(WORKFLOW)
    trigger = doc.get(True, doc.get("on"))
    return trigger["workflow_dispatch"]["inputs"]


def _render(body: str, mapping: dict, where: str) -> str:
    """Substitute every `${{ }}` the runner would, so the body can be executed.

    Not tidiness: `${{` is not parseable pwsh at all (`${` opens a braced
    variable name), so a body carrying one dies with a ParserError before a
    single statement runs — and it exits NON-ZERO, which a test asserting only
    a failure code cannot tell from the step correctly refusing a payload. The
    same body in node is a SyntaxError, with the same problem.

    Every anchor's occurrence count is asserted BEFORE substituting (ledger
    L47), and the result is asserted to hold no expression at all afterwards:
    an anchor that stopped matching must fail loudly rather than leave an
    unrunnable body that fails for a reason nobody reads.
    """
    rendered = body
    for anchor, value in mapping.items():
        found = rendered.count(anchor)
        assert found == 1, (
            f"{where}: expected exactly one {anchor} to substitute, found "
            f"{found} — the body's shape has changed and this module is no "
            f"longer rendering what ships. Body: {rendered!r}"
        )
        rendered = rendered.replace(anchor, value)
    assert "${{" not in rendered, (
        f"{where}: the rendered body still carries a workflow expression, which "
        f"the interpreter cannot parse — every run against it would die before "
        f"executing a statement, with a non-zero exit that reads like a guard "
        f"firing. Rendered: {rendered!r}"
    )
    return rendered


def _payload(site: PwshSite) -> str:
    """A pwsh payload valid in the position it lands in.

    It does not need to escape the quotes: pwsh expands `$( )` inside a
    double-quoted string, which is where the pre-fix body put the value. It
    reads the credential in reach of this site and evaluates to the empty
    string, so what reaches the callee is a legal domain and the run looks
    clean.
    """
    return (
        f"{LEGAL_DOMAIN}$(Set-Content -Path '{site.sentinel}' "
        f"-Value $env:{site.credential_var})"
    )


def _run_pwsh(site: PwshSite, body: str, **env_overrides: str):
    """Run a pwsh body in a temp cwd holding the stub callee.

    Returns (observed_output, sentinel_contents_or_None, rc). The sentinel's
    CONTENTS, not merely its existence: a file written from an unset variable
    would score the same as one written from the live credential, and the claim
    under test is which credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        stub = tmp / site.stub_path
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(site.stub, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")

        overrides = dict(env_overrides)
        for name, rel in site.extra_env.items():
            overrides.setdefault(name, str(tmp / rel) if rel else str(tmp))
        env = child_env(**overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAIN would
        # make a fail-closed test pass for the wrong reason, and an inherited
        # credential would let a theft assertion pass without the workflow
        # supplying anything.
        for var in CONTROLLED_VARS:
            if var not in overrides:
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
        observed = proc.stdout + proc.stderr
        # This workflow captures a callee's output into a FILE rather than
        # letting it reach stdout, so a module reading only stdout would see no
        # CALLED line and score every payload test as "never reached".
        for rel in site.capture:
            path = tmp / rel
            if path.exists():
                observed += path.read_text(encoding="utf-8")
        sentinel = tmp / site.sentinel
        stolen = sentinel.read_text(encoding="utf-8").strip() if sentinel.exists() else None
        return observed, stolen, proc.returncode


def _run_github_script(body: str, **env_overrides: str):
    """Run a github-script body under a stub of the action's injected globals.

    Returns (result_dict, sentinel_contents_or_None, rc).
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        artifacts = tmp / "artifacts" / "cloudflare"
        artifacts.mkdir(parents=True)
        (artifacts / "cloudflare-enforce.txt").write_text("enforce output\n", encoding="utf-8")
        (artifacts / "cloudflare-audit-after.txt").write_text("audit output\n", encoding="utf-8")

        indented = "\n".join("  " + line if line else line for line in body.splitlines())
        assert "__BODY__" in GH_HARNESS
        script = tmp / "step.js"
        script.write_text(GH_HARNESS.replace("__BODY__", indented), encoding="utf-8")
        result_path = tmp / "result.json"

        overrides = dict(env_overrides)
        overrides["HARNESS_RESULT"] = str(result_path)
        overrides.setdefault("GH_FAKE_TOKEN", FAKE_GH_TOKEN)
        overrides.setdefault("JS_SENTINEL", GH_SENTINEL)
        env = child_env(**overrides)
        for var in CONTROLLED_VARS:
            if var not in overrides:
                env.pop(var, None)

        proc = subprocess.run(
            ["node", str(script)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        result = (
            json.loads(result_path.read_text(encoding="utf-8"))
            if result_path.exists()
            else {"calls": [], "failed": None, "threw": proc.stdout + proc.stderr}
        )
        sentinel = tmp / GH_SENTINEL
        stolen = sentinel.read_text(encoding="utf-8").strip() if sentinel.exists() else None
        return result, stolen, proc.returncode


_JS_STEAL = (
    "require('fs').writeFileSync(process.env.JS_SENTINEL, process.env.GH_FAKE_TOKEN);"
)


def _js_payload(residue: str, *, wrapped_in_call: bool) -> str:
    """A JS payload that closes the literal it lands in, runs, and reopens it.

    The two interpolated positions in this body are NOT the same position, and a
    single payload shape cannot serve both — which is the JS analogue of pwsh's
    quoting rules and just as easy to get wrong:

        const domain     = '<HERE>';           -> close the QUOTE only
        const issueNumber = Number('<HERE>');  -> close the quote AND the CALL

    A payload built for the first shape lands in the second as
    `Number('1'; ...)` — a SyntaxError, which the control would read as "the
    pre-fix body did not execute the payload", i.e. as evidence the defect was
    never there. The residue is a legal value in both cases, so the exploited
    run looks clean.
    """
    opener = "');" if wrapped_in_call else "';"
    reopen = "Number('" if wrapped_in_call else "'"
    tag = "".join(c if c.isalnum() else "_" for c in residue)
    return f"{residue}{opener} {_JS_STEAL} const _stolen_{tag} = {reopen}"


def _assert_pwsh_wiring(site: PwshSite) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would call the script with an empty -Domain.
    """
    step = _step(site)
    env = step.get("env") or {}
    body = step.get("run", "")
    assert env.get(ENV_DOMAIN) == EXPR_DOMAIN, (
        f"{site.job}: step must map {ENV_DOMAIN} to {EXPR_DOMAIN} — its env: "
        f"mapping is {env!r}"
    )
    reads = body.count(f"$env:{ENV_DOMAIN}")
    assert reads >= site.min_env_reads, (
        f"{site.job}: body reads $env:{ENV_DOMAIN} {reads} times, expected at "
        f"least {site.min_env_reads} (the emptiness guard plus the invocation) "
        f"— a call that does not read it is a call the mapping never reaches. "
        f"Body: {body!r}"
    )
    assert "inputs.domain" not in body, (
        f"{site.job}: body interpolates inputs.domain again (#1080): under "
        f"{site.environment!r} that is dispatcher text executed after the "
        f"approval. Body: {body!r}"
    )
    assert f"IsNullOrWhiteSpace($env:{ENV_DOMAIN})" in body, (
        f"{site.job}: body has no fail-closed emptiness check on "
        f"$env:{ENV_DOMAIN} — an unset mapping is dropped by the native call "
        f"and shifts every later argument one position left (ledger L214). "
        f"Body: {body!r}"
    )


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_every_pwsh_call_site_is_wired_through_env():
    for site in PWSH_SITES:
        _assert_pwsh_wiring(site)


def test_the_github_script_site_is_wired_through_env():
    """Both inputs, not just `domain`.

    `issue_number` is the one a reader is most likely to leave behind, because
    `type: number` reads like a constraint.
    """
    step = find_step(load_workflow(WORKFLOW), GH_JOB, GH_STEP)
    env = step.get("env") or {}
    body = step_github_script(WORKFLOW, GH_JOB, GH_STEP)
    assert env.get(ENV_DOMAIN) == EXPR_DOMAIN, (
        f"post_back must map {ENV_DOMAIN} to {EXPR_DOMAIN} — env: is {env!r}"
    )
    assert env.get(ENV_ISSUE) == EXPR_ISSUE, (
        f"post_back must map {ENV_ISSUE} to {EXPR_ISSUE} — env: is {env!r}"
    )
    for expr in ("inputs.domain", "inputs.issue_number"):
        assert expr not in body, (
            f"post_back interpolates {expr} into its github-script body again "
            f"(#1080). A `script:` block is substituted into the JS SOURCE "
            f"before node parses it, and this step is NOT behind an "
            f"environment gate. Body: {body!r}"
        )
    for var in (ENV_DOMAIN, ENV_ISSUE):
        assert f"process.env.{var}" in body, (
            f"post_back never reads process.env.{var}, so the mapping reaches "
            f"nothing. Body: {body!r}"
        )


def test_the_jobs_still_sit_in_the_environments_this_module_is_about():
    """The blast-radius claim is an environment claim; pin it rather than say it.

    If a job moves off its gate — or onto a different one — the docstring's
    account of what an injected body could reach stops being true, and nothing
    else in this module would notice.
    """
    jobs = load_workflow(WORKFLOW)["jobs"]
    for site in PWSH_SITES:
        actual = jobs[site.job].get("environment")
        assert actual == site.environment, (
            f"job {site.job!r} declares environment {actual!r}, expected "
            f"{site.environment!r} — re-read this module's docstring before "
            f"trusting it"
        )
    # post_back is deliberately ungated, and that is load-bearing: it is the one
    # site an injected payload reached WITHOUT an approver spending anything.
    assert "environment" not in jobs[GH_JOB], (
        f"job {GH_JOB!r} now declares an environment "
        f"({jobs[GH_JOB].get('environment')!r}). That is not necessarily wrong, "
        f"but this module's docstring says it is the ungated site, so the "
        f"reasoning about it needs re-reading."
    )


def test_the_cloudflare_jobs_load_write_scoped_tokens_before_the_call_site():
    """Where the Cloudflare half's credential comes from, and that it precedes the call.

    The exposure claim is an ORDERING claim, not merely a co-presence one, and
    the tokens appear in no `env:` block — so nothing else in the file records
    that they are in the injection point's process environment.
    """
    jobs = load_workflow(WORKFLOW)["jobs"]
    for site in PWSH_SITES:
        if site.credential_var != "CLOUDFLARE_API_TOKEN_FFC":
            continue
        steps = jobs[site.job].get("steps", [])
        loaders = [
            i
            for i, s in enumerate(steps)
            if "cloudflare-tokens-from-kv" in str(s.get("uses", ""))
        ]
        assert len(loaders) == 1, (
            f"{site.job}: expected exactly one cloudflare-tokens-from-kv step, "
            f"found {len(loaders)}"
        )
        scope = (steps[loaders[0]].get("with") or {}).get("scope")
        assert scope == "write", (
            f"{site.job}: the token loader requests scope {scope!r}, not "
            f"'write' — the credential in reach of the call site has changed"
        )
        call_at = next(
            i
            for i, s in enumerate(steps)
            if site.step.lower() in str(s.get("name", "")).lower()
        )
        assert loaders[0] < call_at, (
            f"{site.job}: the token loader runs after the call site "
            f"({loaders[0]} vs {call_at}), so the tokens are not in that step's "
            f"environment"
        )


def test_the_m365_call_sites_carry_the_exo_certificate_in_their_own_env():
    """The other exposure shape, in the same file.

    These two sites are the reason a reviewer cannot pick one sweep. Here the
    credential IS in the step's `env:` — so a reviewer who checks `env:` blocks
    finds these and scores the Cloudflare half as holding nothing, while the
    #1161 sweep does the reverse.
    """
    for site in PWSH_SITES:
        if not site.in_step_env:
            continue
        env = _step(site).get("env") or {}
        assert "FFC_EXO_CERT_PFX_BASE64" in env and "FFC_EXO_CERT_PASSWORD" in env, (
            f"{site.job}: the Exchange Online certificate is no longer in this "
            f"step's env: block ({sorted(env)}) — this module's account of the "
            f"exposure needs re-reading"
        )


# --------------------------------------------------------------------------
# Inputs: what stays interpolated, and why
# --------------------------------------------------------------------------


def test_the_still_interpolated_inputs_are_type_constrained():
    """Their safety rests ENTIRELY on the declared type, so pin the declaration.

    Flip any of these to `type: string` and the bodies are injectable again
    through a parameter nobody re-reads.
    """
    declared = _dispatch_inputs()
    for name in ("dry_run", "github_pages_only", "skip_m365", "dmarc_mgmt_debug"):
        actual = declared[name].get("type")
        assert actual == "boolean", (
            f"inputs.{name} is declared type {actual!r}, not 'boolean'. It is "
            f"still interpolated into a script body, and only the type "
            f"constraint made that safe (#1080)."
        )


def test_the_number_typed_input_is_not_treated_as_constrained():
    """`type: number` reads like a constraint and is not one.

    This is the single easiest thing in this lane to get wrong, and getting it
    wrong leaves an injectable input in an UNGATED github-script step. GitHub
    validates `boolean` and `choice` only; the guard's CONSTRAINED_TYPES says so
    directly, and this asserts against that set rather than restating it — so if
    the guard ever did start treating `number` as constrained, this fails and
    sends the reader here instead of leaving the two quietly disagreeing.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "interp_guard",
        pathlib.Path(__file__).resolve().parents[2]
        / "scripts"
        / "check-workflow-input-interpolation.py",
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    assert "number" not in guard.CONSTRAINED_TYPES, (
        f"the guard now treats 'number' as a constrained type "
        f"({sorted(guard.CONSTRAINED_TYPES)}). GitHub does not validate a "
        f"`number` dispatch input — it renders a free text box — so this would "
        f"un-see every issue_number-shaped injection in the tree."
    )
    declared = _dispatch_inputs()
    assert declared["issue_number"].get("type") == "number", (
        f"inputs.issue_number is declared "
        f"{declared['issue_number'].get('type')!r}; this test exists to pin "
        f"that a `number` input was moved to env: anyway"
    )


# --------------------------------------------------------------------------
# Behaviour: the payload binds as data
# --------------------------------------------------------------------------


def test_the_shipped_pwsh_bodies_bind_a_payload_as_data():
    for site in PWSH_SITES:
        _assert_pwsh_wiring(site)
        body = _render(_step(site)["run"], site.render, site.job)
        payload = _payload(site)
        observed, stolen, rc = _run_pwsh(
            site,
            body,
            **{ENV_DOMAIN: payload, site.credential_var: site.fake_credential},
        )
        assert stolen is None, (
            f"{site.job}: the payload EXECUTED against the shipped body — the "
            f"sentinel holds {stolen!r}. Output: {observed!r}"
        )
        assert rc == 0, f"{site.job}: expected rc 0, got {rc}. Output: {observed!r}"
        assert payload in observed, (
            f"{site.job}: the callee was not handed the payload verbatim, so "
            f"this run does not show it binding as data. Output: {observed!r}"
        )


def test_the_pre_fix_pwsh_bodies_executed_the_payload():
    """The positive control: the same payload, against the body as it shipped.

    Without this, every assertion above is satisfied by a harness that runs
    nothing. It is also the evidence for the claim in the docstring, measured
    rather than asserted — at all four sites, under both credentials.
    """
    for site in PWSH_SITES:
        payload = _payload(site)
        body = _render(
            site.pre_fix_body,
            {**site.render, EXPR_DOMAIN: payload},
            f"{site.job} (pre-fix)",
        )
        observed, stolen, rc = _run_pwsh(
            site, body, **{site.credential_var: site.fake_credential}
        )
        assert stolen == site.fake_credential, (
            f"{site.job}: the pre-fix body did NOT leak {site.credential_var} "
            f"(sentinel: {stolen!r}) — this control proves nothing, so the "
            f"post-fix result above proves nothing either. Output: {observed!r}"
        )
        # The residue is a LEGAL domain and the step exits 0: the run looks
        # clean, which is what made this worth fixing rather than merely worth
        # noting.
        assert f"[{LEGAL_DOMAIN}]" in observed, (
            f"{site.job}: the callee was not handed the legal residue "
            f"{LEGAL_DOMAIN!r}, so the 'looks clean' half of the claim is "
            f"unproven. Output: {observed!r}"
        )
        assert rc == 0, (
            f"{site.job}: the exploited pre-fix run exited {rc}, not 0 — the "
            f"claim is that it looked successful. Output: {observed!r}"
        )


def test_the_shipped_github_script_binds_both_payloads_as_data():
    """The domain payload becomes text in the comment; the issue payload is refused.

    Two different post-fix outcomes from one fix, and both are worth pinning:
    `domain` is a string the body legitimately uses, so it binds and is posted
    verbatim as data; `issue_number` goes through `Number()`, so the same
    payload arrives as NaN and the fail-closed check rejects it before any API
    call.
    """
    body = _render(step_github_script(WORKFLOW, GH_JOB, GH_STEP), GH_RENDER, GH_JOB)
    domain_payload = _js_payload(LEGAL_DOMAIN, wrapped_in_call=False)

    result, stolen, rc = _run_github_script(
        body, **{ENV_DOMAIN: domain_payload, ENV_ISSUE: "42"}
    )
    assert stolen is None, (
        f"the domain payload EXECUTED against the shipped script — the sentinel "
        f"holds {stolen!r}. Result: {result!r}"
    )
    assert result["threw"] is None, f"the shipped script threw: {result!r}"
    assert result["failed"] is None, f"the shipped script failed itself: {result!r}"
    assert len(result["calls"]) == 1, f"expected one createComment call: {result!r}"
    call = result["calls"][0]
    assert call["issue_number"] == 42, f"wrong issue number: {call!r}"
    assert domain_payload in call["body"], (
        f"the payload did not reach the comment body verbatim, so this run does "
        f"not show it binding as data: {call!r}"
    )

    # Same payload in the number position: refused, and refused BEFORE the call.
    issue_payload = _js_payload("1", wrapped_in_call=True)
    result, stolen, rc = _run_github_script(
        body, **{ENV_DOMAIN: LEGAL_DOMAIN, ENV_ISSUE: issue_payload}
    )
    assert stolen is None, (
        f"the issue_number payload EXECUTED against the shipped script — the "
        f"sentinel holds {stolen!r}. Result: {result!r}"
    )
    assert result["calls"] == [], (
        f"createComment was called with a non-numeric issue number: {result!r}"
    )
    assert result["failed"] and ENV_ISSUE in result["failed"], (
        f"the script did not fail closed naming {ENV_ISSUE}: {result!r}"
    )


def test_the_pre_fix_github_script_executed_both_payloads():
    """The positive control for the JS half, in both interpolated positions."""
    for label, mapping in (
        (
            "domain",
            {EXPR_DOMAIN: _js_payload(LEGAL_DOMAIN, wrapped_in_call=False), EXPR_ISSUE: "42"},
        ),
        (
            "issue_number",
            {EXPR_DOMAIN: LEGAL_DOMAIN, EXPR_ISSUE: _js_payload("1", wrapped_in_call=True)},
        ),
    ):
        body = _render(
            GH_PRE_FIX_BODY, {**GH_RENDER, **mapping}, f"{GH_JOB} (pre-fix, {label})"
        )
        result, stolen, rc = _run_github_script(body)
        assert stolen == FAKE_GH_TOKEN, (
            f"the pre-fix github-script did NOT execute the {label} payload "
            f"(sentinel: {stolen!r}) — this control proves nothing, so the "
            f"post-fix result proves nothing either. Result: {result!r}"
        )
        # And it still posted its comment: the exploited run looked successful.
        assert len(result["calls"]) == 1, (
            f"the exploited pre-fix run did not post its comment, so the "
            f"'looks clean' half of the claim is unproven: {result!r}"
        )


# --------------------------------------------------------------------------
# Behaviour: fail closed
# --------------------------------------------------------------------------


def test_an_empty_mapping_fails_closed_at_every_pwsh_site():
    """Empty and UNSET are different failures, and both must be refused.

    An unset variable is dropped by a native call and shifts every later
    argument one position left (ledger L214); an empty one binds and runs the
    real action against an empty target. `IsNullOrWhiteSpace` is the only check
    covering both, which is why both are exercised here rather than one standing
    in for the other.

    Asserting on the MESSAGE as well as the code, because rc != 0 is also what a
    harness that cannot start produces (CLAUDE.md) — and on this workflow a
    ParserError from an unrendered `${{` would land in exactly this position.
    """
    for site in PWSH_SITES:
        body = _render(_step(site)["run"], site.render, site.job)
        for label, overrides in (
            ("empty", {ENV_DOMAIN: ""}),
            ("whitespace", {ENV_DOMAIN: "   "}),
            ("unset", {}),
        ):
            observed, _, rc = _run_pwsh(
                site, body, **{site.credential_var: site.fake_credential, **overrides}
            )
            assert rc != 0, (
                f"{site.job} ({label}): the step exited 0 with no domain. "
                f"Output: {observed!r}"
            )
            assert ENV_DOMAIN in observed, (
                f"{site.job} ({label}): exited {rc} without naming "
                f"{ENV_DOMAIN} — that is indistinguishable from the harness "
                f"failing to start. Output: {observed!r}"
            )
            assert "CALLED" not in observed, (
                f"{site.job} ({label}): the callee ran anyway. "
                f"Output: {observed!r}"
            )


def test_an_empty_mapping_fails_closed_in_the_github_script():
    body = _render(step_github_script(WORKFLOW, GH_JOB, GH_STEP), GH_RENDER, GH_JOB)
    for label, overrides in (
        ("empty domain", {ENV_DOMAIN: "", ENV_ISSUE: "42"}),
        ("whitespace domain", {ENV_DOMAIN: "   ", ENV_ISSUE: "42"}),
        ("unset domain", {ENV_ISSUE: "42"}),
        ("empty issue", {ENV_DOMAIN: LEGAL_DOMAIN, ENV_ISSUE: ""}),
        ("unset issue", {ENV_DOMAIN: LEGAL_DOMAIN}),
        ("zero issue", {ENV_DOMAIN: LEGAL_DOMAIN, ENV_ISSUE: "0"}),
    ):
        result, _, _ = _run_github_script(body, **overrides)
        assert result["calls"] == [], (
            f"{label}: createComment was called anyway: {result!r}"
        )
        assert result["failed"], f"{label}: the step did not fail closed: {result!r}"
        assert result["threw"] is None, (
            f"{label}: the step THREW rather than failing closed, so the "
            f"diagnosis arrives as a stack trace: {result!r}"
        )


def test_the_unguarded_pwsh_bodies_would_have_run_with_no_domain():
    """The silent-success control: strip the guard and the call goes through.

    Without this, the fail-closed tests above are satisfied by a body that
    cannot run at all. Stripping the guard must leave a body that DOES reach the
    callee — that is what makes the guard's refusal a measurement rather than a
    coincidence.
    """
    for site in PWSH_SITES:
        body = _render(_step(site)["run"], site.render, site.job)
        anchor = f"if ([string]::IsNullOrWhiteSpace($env:{ENV_DOMAIN})) {{"
        count = body.count(anchor)
        assert count == 1, (
            f"{site.job}: expected exactly one emptiness guard to strip, found "
            f"{count} — this control would otherwise test an unmodified body. "
            f"Body: {body!r}"
        )
        start = body.index(anchor)
        end = body.index("}", body.index("exit 1", start)) + 1
        stripped = body[:start] + body[end:]
        assert f"IsNullOrWhiteSpace($env:{ENV_DOMAIN})" not in stripped, (
            f"{site.job}: the guard survived the strip, so this control is "
            f"measuring the guarded body. Stripped: {stripped!r}"
        )
        observed, _, _ = _run_pwsh(
            site, stripped, **{ENV_DOMAIN: "", site.credential_var: site.fake_credential}
        )
        assert "CALLED" in observed, (
            f"{site.job}: with the guard stripped the callee was still never "
            f"reached, so the fail-closed test proves nothing. "
            f"Output: {observed!r}"
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    missing = [t for t in ("pwsh", "node") if shutil.which(t) is None]
    if missing:
        print(f"  SKIP all ({', '.join(missing)} not installed in this environment; runs in CI)")
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
