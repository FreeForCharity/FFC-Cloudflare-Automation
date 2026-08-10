"""The #1080 remedy's second half: an empty `$env:` reference is a MISSING ARGUMENT.

WHAT THIS MODULE IS ABOUT
    Moving a free-text dispatch input out of a `run:` body into step-level
    `env:` is checked by `scripts/check-workflow-input-interpolation.py`.
    Nothing checks the second half — that the body then fails closed when the
    mapping is empty. Ledger **L214** is why the second half matters, and it is
    a statement about ARITY, not content: in a pwsh body a bare `$env:X` renders
    an empty value as *no argument at all*, so `-Ein $env:INPUT_EIN -OutputFile
    $out` becomes `-Ein -OutputFile $out` and every later argument binds one
    position to the left. The failure is reported as a call-signature problem
    for what is really a missing value — or, worse, a real value lands in the
    slot the author meant for a different parameter.

    #1146 measured 16 such sites on `main` (`954b95d`). This module pins the
    fix at the two ends of the range it covers and carries the positive control
    that makes those assertions mean anything.

THE INVOCATION FORM DECIDES WHETHER THE HAZARD EXISTS — MEASURED, NOT ASSUMED
    #1146's site list was built from a regex over `-Param $env:VAR`, which does
    not distinguish how the callee is invoked. It matters, and it cuts both
    ways. Measured on pwsh 7.4.6 with the variable unset:

        & pwsh -NoProfile -File probe.ps1 -ApiUrl $env:X -OutputFile 'lit.csv'
          -> probe.ps1: Missing an argument for parameter 'ApiUrl'.   HAZARD

        $a = @('-ApiUrl', $env:X, '-OutputFile', 'lit.csv')
        & pwsh -NoProfile -File probe.ps1 @a
          -> probe.ps1: Missing an argument for parameter 'ApiUrl'.   HAZARD

        & ./probe.ps1 -ApiUrl $env:X -OutputFile 'lit.csv'
          -> ApiUrl=[] OutputFile=[lit.csv]                           SAFE

    A NATIVE command (`pwsh -File …`) drops the empty argument from the array
    it builds; PowerShell's own binder, invoked with `& <script.ps1>`, binds it
    as an empty string and shifts nothing. Two consequences for the burn-down:

    * `226-whmcs-application-triage.yml` is on #1146's list and is NOT a hazard
      — it calls `& .\scripts\whmcs-application-triage.ps1` directly, and its
      `orderids` input is legitimately empty in `report` mode. It is
      deliberately left unguarded, and
      `test_226_is_excluded_for_a_measured_reason` pins the property that makes
      that safe, so the exclusion fails loudly if the call is ever rewritten
      into the native form.
    * `213`'s ARRAY-SPLAT sites are hazards the regex could not see — there is
      no `-Param $env:VAR` adjacency to match, the parameter name and the value
      are separate array elements. Two of them were missing from #1146's count
      of 16. `test_the_array_splat_form_defaults_like_the_direct_form` covers
      one, because a checker written to #1146's spelling would freeze these two
      live holes as compliant.

TWO REMEDIES, ONE PROPERTY
    The property that matters is that the reference is never empty at the call
    site. Which remedy achieves it depends on whether the input has a
    meaningful default:

    * `WHMCS_API_URL` (14 sites) is `required: false` with a documented APIM
      default, and nine sibling steps — 209/210/219/221/225/226/228 — already
      DEFAULT-FILL it. Diverging into `exit 1` here would make these lanes
      refuse a dispatch its siblings accept, for a value the workflow itself
      declares a default for.
    * `QUERY` / `INPUT_EIN` / `INPUT_SEARCH_TERMS` are `required: true` with no
      sane default, so they take 303/304's fail-closed shape: a stated
      `::error::` naming the variable, then `exit 1`.

    Both are asserted below against the SAME unset-variable input, because the
    thing under test is the argument binding, not the spelling of the guard.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow

# --------------------------------------------------------------------------
# The whole converted set, driven from one table (#1146).
#
# (workflow, job, step-name substring, env var, expression, remedy)
#
# `remedy` is "default" (fill the documented APIM value) or "fail-closed"
# (state the cause and exit 1). Every row is asserted for wiring; the rows
# named in BEHAVIOURAL below are additionally run.
# --------------------------------------------------------------------------
APIM = "https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php"

SITES = [
    ("201-whmcs-export-domains.yml", "export_domains", "Export domains", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("202-whmcs-export-products.yml", "export_products", "Export products", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("203-whmcs-export-payment-methods.yml", "export_payment_methods", "Export payment method", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("212-whmcs-product-add.yml", "product_add", "Add catalog product", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export clients", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export transactions", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export invoices", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Lookup invoices", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("214-whmcs-clients-metrics.yml", "clients_metrics", "Aggregate client metrics", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("215-whmcs-nonprofit-clients-metrics.yml", "nonprofit_clients_metrics", "Aggregate nonprofit", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("216-whmcs-activity-metrics.yml", "activity_metrics", "Build activity matrix", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("217-whmcs-client-fields-survey.yml", "client_fields_survey", "Survey client classification", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("218-whmcs-siteslist-reconciliation.yml", "siteslist_reconciliation", "Reconcile WHMCS", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("220-whmcs-served-metrics.yml", "served_metrics", "Build served-per-year", "WHMCS_API_URL", "${{ inputs.api_url }}", "default"),
    ("221-whmcs-application-search.yml", "application_search", "Search applications", "QUERY", "${{ inputs.query }}", "fail-closed"),
    ("801-candid-charity-check.yml", "charity_check", "Charity Check lookup", "INPUT_EIN", "${{ inputs.ein }}", "fail-closed"),
    ("802-candid-essentials-search.yml", "essentials_search", "Essentials search", "INPUT_SEARCH_TERMS", "${{ inputs.search_terms }}", "fail-closed"),
]

GUARD_MARKER = "if ([string]::IsNullOrWhiteSpace($env:%s))"

# Permissive stubs: they record what they were BOUND and create the output file
# the body then asserts exists. A marker search over stdout is not a
# discriminator on its own — pwsh echoes the offending source line back in a
# binder error — so every behavioural assertion below is on a CALLED line the
# stub itself emitted.
STUB = """[CmdletBinding()]
param(
    [string]$ApiUrl,
    [string]$Query,
    [string]$Ein,
    [string]$SearchTerms,
    [int]$Size,
    [int]$MaxMatches,
    [string]$MaxRows,
    [string]$StartDate,
    [string]$EndDate,
    [string]$OutputFile
)
Write-Output "CALLED ApiUrl=[$ApiUrl] Query=[$Query] Ein=[$Ein] SearchTerms=[$SearchTerms] OutputFile=[$OutputFile]"
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $d = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    Set-Content -Path $OutputFile -Value '{}'
}
"""

# The real parameter block for the positive control. `Mandatory` is what a
# reader assumes already protects them, so the control has to include it: the
# point is that it does NOT, because the argument never arrives to be judged.
MANDATORY_STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Ein,
    [string]$OutputFile
)
Write-Output "CALLED Ein=[$Ein] OutputFile=[$OutputFile]"
"""

# Every script name any pinned body invokes. Written as the same permissive
# stub, so a body that calls a different one fails on the missing file rather
# than silently taking a different path.
STUB_SCRIPTS = (
    "whmcs-domain-export.ps1",
    "whmcs-transactions-export.ps1",
    "whmcs-application-search.ps1",
    "candid-charity-check.ps1",
    "candid-essentials-search.ps1",
)

# Which rows are also RUN, and with what the runner would substitute for the
# non-input expressions still in their bodies.
BEHAVIOURAL = {
    "201-whmcs-export-domains.yml/Export domains": {
        "${{ inputs.output_file }}": "artifacts/whmcs/whmcs_domains.csv",
    },
    "213-whmcs-zeffy-payments-import-draft.yml/Export transactions": {
        "${{ inputs.transactions_output }}": "artifacts/whmcs/whmcs_transactions.csv",
        "${{ inputs.max_rows }}": "200000",
        "${{ inputs.start_date }}": "",
        "${{ inputs.end_date }}": "",
    },
    "221-whmcs-application-search.yml/Search applications": {},
    "801-candid-charity-check.yml/Charity Check lookup": {},
    "802-candid-essentials-search.yml/Essentials search": {},
}

# Variables the harness must own outright. An inherited value would satisfy an
# assertion the workflow is supposed to (ledger L199) — and for the unset-input
# tests that is the difference between measuring the guard and measuring the
# developer's shell.
CONTROLLED = (
    "WHMCS_API_URL",
    "QUERY",
    "MAX_MATCHES",
    "ISSUE_NUMBER",
    "INPUT_EIN",
    "INPUT_SEARCH_TERMS",
    "INPUT_SIZE",
    "INPUT_OUTPUT_FILE",
)


def _key(row) -> str:
    return f"{row[0]}/{row[2]}"


# The parameters any pinned body binds its input to. Used to locate the first
# real use of a variable so the guard can be asserted to precede it.
PARAMS = ("ApiUrl", "Query", "Ein", "SearchTerms")


def _first_parameter_use(body: str, var: str) -> int | None:
    """Offset of the first `-Param $env:VAR` OUTSIDE a comment, or None.

    Comment lines must be excluded, and not as a nicety: each guard's own
    comment quotes the call it is protecting (`-Query $env:QUERY` and so on),
    so a naive `body.find` matches the explanation rather than the code and
    reports every correctly-ordered site as inverted. That failure is in the
    reassuring direction for the opposite edit — a guard moved BELOW its call
    would still find the comment first and pass.
    """
    offset = 0
    for line in body.split("\n"):
        if not line.strip().startswith("#"):
            for param in PARAMS:
                found = line.find(f"-{param} $env:{var}")
                if found >= 0:
                    return offset + found
        offset += len(line) + 1
    return None


def _step(row) -> dict:
    return find_step(load_workflow(row[0]), row[1], row[2])


def _render(row) -> str:
    """The body with non-input expressions substituted the way GitHub would.

    Every anchor's presence is asserted BEFORE substituting (ledger L47): a
    body that stopped containing one must fail loudly rather than quietly test
    a string nobody rendered.
    """
    body = _step(row)["run"]
    for anchor, value in BEHAVIOURAL[_key(row)].items():
        assert anchor in body, (
            f"{_key(row)}: expected {anchor} in the body and found none — the "
            f"step was rewritten, so this render is testing nothing"
        )
        body = body.replace(anchor, value)
    assert "${{" not in body, (
        f"{_key(row)}: the rendered body still holds an unsubstituted "
        f"expression, so pwsh would see literal text: {body!r}"
    )
    return body


def _run(body: str, stub: str = STUB, **overrides: str) -> tuple[str, int]:
    """Run a rendered body in a temp cwd holding the stubs. Returns (output, rc)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        for name in STUB_SCRIPTS:
            (tmp / "scripts" / name).write_text(stub, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        # Inherited, then layered — never a fresh minimal dict (ledger L35).
        env = dict(__import__("os").environ)
        for var in CONTROLLED:
            env.pop(var, None)
        env.update(overrides)
        # The bodies append to the step summary; without a real path the
        # Out-File calls terminate under $ErrorActionPreference = 'Stop' and
        # the test would be measuring the harness.
        env["GITHUB_STEP_SUMMARY"] = str(tmp / "summary.md")
        env["GITHUB_SERVER_URL"] = "https://github.com"
        env["GITHUB_REPOSITORY"] = "FreeForCharity/FFC-Cloudflare-Automation"
        env["GITHUB_RUN_ID"] = "0"
        env["RUNNER_TEMP"] = str(tmp)
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
        return proc.stdout + proc.stderr, proc.returncode


def _strip_guard(body: str, var: str) -> str:
    """Remove the emptiness guard for `var`, asserting it was there first.

    Asserted rather than assumed: a mutation that silently does not apply
    produces a green run, and green reads as 'the guard has a hole' — the
    technique's own false negative, pointing the wrong way (AGENTS.md).
    """
    marker = GUARD_MARKER % var
    assert body.count(marker) == 1, (
        f"expected exactly one {var} guard to strip, found "
        f"{body.count(marker)}: {body!r}"
    )
    start = body.index(marker)
    end = body.index("}\n", start) + 2
    stripped = body[:start] + body[end:]
    assert marker not in stripped, (
        f"the {var} guard survived the strip, so this control is measuring the "
        f"GUARDED body: {stripped!r}"
    )
    return stripped


# --------------------------------------------------------------------------
# Wiring — asserted for all 17 converted sites, no pwsh required
# --------------------------------------------------------------------------


def test_every_converted_site_maps_reads_and_guards_its_variable():
    for row in SITES:
        wf, job, sub, var, expr, remedy = row
        step = _step(row)
        env = step.get("env") or {}
        body = step.get("run", "")
        assert env.get(var) == expr, (
            f"{_key(row)}: must map {var} to {expr} — its env: mapping is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"{_key(row)}: maps {var} but never reads $env:{var} — the env: "
            f"block is decoration and the value reaches nothing"
        )
        assert GUARD_MARKER % var in body, (
            f"{_key(row)}: has no emptiness guard on {var}, so an empty value "
            f"vanishes as an ARGUMENT and shifts the binding (#1146 / L214)"
        )
        guard_at = body.index(GUARD_MARKER % var)
        first_use = _first_parameter_use(body, var)
        if first_use is not None:
            assert guard_at < first_use, (
                f"{_key(row)}: the {var} guard sits AFTER its first "
                f"parameter-position use, so the shift happens first"
            )


def test_the_remedy_matches_the_inputs_declared_requiredness():
    """A default-fill is only correct where the workflow declares a default.

    Without this, a later edit could swap either remedy for the other and every
    behavioural test below would still pass — they assert the binding, not the
    policy. `required: true` with a silent default would invent a value the
    dispatcher never supplied; `required: false` with `exit 1` would refuse a
    dispatch its nine siblings accept.
    """
    for row in SITES:
        wf, job, sub, var, expr, remedy = row
        doc = load_workflow(wf)
        trigger = doc.get(True, doc.get("on"))
        name = expr.split("inputs.")[1].split()[0].rstrip("}").strip()
        spec = trigger["workflow_dispatch"]["inputs"][name]
        body = _step(row).get("run", "")
        if remedy == "default":
            assert spec.get("required") is False, (
                f"{_key(row)}: {name} is required={spec.get('required')!r} but "
                f"the body silently defaults it — a required input must fail "
                f"closed, not invent a value"
            )
            assert spec.get("default") == APIM, (
                f"{_key(row)}: the body defaults to the APIM gateway but the "
                f"input declares default={spec.get('default')!r}; the two must "
                f"agree or a dispatch and a fallback reach different endpoints"
            )
            assert APIM in body, f"{_key(row)}: the body does not fill the APIM default"
        else:
            assert spec.get("required") is True, (
                f"{_key(row)}: {name} is required={spec.get('required')!r}; a "
                f"non-required input should default rather than exit 1"
            )
            assert f"::error::{var} is empty" in body, (
                f"{_key(row)}: fail-closed sites must NAME the variable, or the "
                f"failure is indistinguishable from a broken harness"
            )
            assert "exit 1" in body, f"{_key(row)}: fail-closed site does not exit 1"


def test_226_is_excluded_for_a_measured_reason():
    """226 is on #1146's list and is deliberately NOT guarded.

    It invokes the callee through PowerShell's own binder (`& <script.ps1>`),
    where an empty value binds as an empty string and shifts nothing — measured
    in this module's docstring — and its `orderids` input is legitimately empty
    in `report` mode, which is the DEFAULT mode. Guarding it would break the
    common path to fix a hazard it does not have.

    What makes the exclusion safe is the invocation form, so that is what is
    pinned: rewrite the call as `pwsh -File` and this test fails, which is when
    the guard becomes necessary.
    """
    step = find_step(
        load_workflow("226-whmcs-application-triage.yml"),
        "application_triage",
        "Rank (and optionally accept)",
    )
    body = step["run"]
    assert "-OrderIds $env:TRIAGE_ORDERIDS" in body, (
        f"226's OrderIds call site moved; re-measure before trusting this "
        f"exclusion: {body!r}"
    )
    assert ".\\scripts\\whmcs-application-triage.ps1" in body.replace("\r", ""), (
        f"226 no longer invokes the script directly. If it now goes through "
        f"`pwsh -File`, an empty TRIAGE_ORDERIDS vanishes as an argument and "
        f"this site needs the #1146 guard: {body!r}"
    )
    assert "pwsh -NoProfile -File .\\scripts\\whmcs-application-triage.ps1" not in body, (
        "226 now invokes its callee as a NATIVE command, which is the form "
        "that drops an empty argument — add the guard (#1146 / L214)"
    )


# --------------------------------------------------------------------------
# Behaviour — requires pwsh
# --------------------------------------------------------------------------


def test_values_reach_the_callee_at_every_behavioural_site():
    """The happy path still works. A guard that broke it would be caught here."""
    cases = [
        ("201-whmcs-export-domains.yml", "Export domains", {"WHMCS_API_URL": "https://example.invalid/api.php"}, "ApiUrl=[https://example.invalid/api.php]"),
        ("213-whmcs-zeffy-payments-import-draft.yml", "Export transactions", {"WHMCS_API_URL": "https://example.invalid/api.php"}, "ApiUrl=[https://example.invalid/api.php]"),
        ("221-whmcs-application-search.yml", "Search applications", {"QUERY": "restoredradiance"}, "Query=[restoredradiance]"),
        ("801-candid-charity-check.yml", "Charity Check lookup", {"INPUT_EIN": "46-2471893", "INPUT_OUTPUT_FILE": "artifacts/candid/charity_check.json"}, "Ein=[46-2471893]"),
        ("802-candid-essentials-search.yml", "Essentials search", {"INPUT_SEARCH_TERMS": "free for charity", "INPUT_SIZE": "10", "INPUT_OUTPUT_FILE": "artifacts/candid/essentials_search.json"}, "SearchTerms=[free for charity]"),
    ]
    for wf, sub, overrides, expected in cases:
        row = next(r for r in SITES if r[0] == wf and r[2] == sub)
        out, rc = _run(_render(row), **overrides)
        assert rc == 0, f"{wf}/{sub} exited {rc}: {out}"
        assert expected in out, f"{wf}/{sub}: expected {expected} in output: {out}"


def test_fail_closed_sites_state_the_cause_and_do_not_call_the_script():
    """An unmapped variable stops the step, naming itself.

    Asserting on the OUTPUT as well as the code is the point: a harness that
    cannot start also exits non-zero, so an exit-code-only assertion cannot
    tell the system under test from its harness (CLAUDE.md).
    """
    cases = [
        ("221-whmcs-application-search.yml", "Search applications", "QUERY", {}),
        ("801-candid-charity-check.yml", "Charity Check lookup", "INPUT_EIN", {"INPUT_OUTPUT_FILE": "artifacts/candid/charity_check.json"}),
        ("802-candid-essentials-search.yml", "Essentials search", "INPUT_SEARCH_TERMS", {"INPUT_SIZE": "10", "INPUT_OUTPUT_FILE": "artifacts/candid/essentials_search.json"}),
    ]
    for wf, sub, var, overrides in cases:
        row = next(r for r in SITES if r[0] == wf and r[2] == sub)
        out, rc = _run(_render(row), **overrides)  # `var` deliberately unset
        assert rc == 1, f"{wf}/{sub} exited {rc} with {var} unset, expected 1: {out}"
        assert f"::error::{var} is empty" in out, (
            f"{wf}/{sub} exited 1 without naming {var} as the cause, so the "
            f"failure is indistinguishable from a broken harness: {out}"
        )
        assert "CALLED " not in out, (
            f"{wf}/{sub} called the script anyway after reporting the empty "
            f"mapping: {out}"
        )


def test_default_filled_sites_reach_the_apim_gateway_with_the_mapping_gone():
    """The other remedy, measured against the same input.

    An unset WHMCS_API_URL must arrive at the callee as the APIM gateway — not
    vanish, and not reach the IP-restricted origin.
    """
    for wf, sub in (
        ("201-whmcs-export-domains.yml", "Export domains"),
        ("213-whmcs-zeffy-payments-import-draft.yml", "Export transactions"),
    ):
        row = next(r for r in SITES if r[0] == wf and r[2] == sub)
        out, rc = _run(_render(row))  # WHMCS_API_URL deliberately unset
        assert rc == 0, f"{wf}/{sub} exited {rc} with WHMCS_API_URL unset: {out}"
        assert f"ApiUrl=[{APIM}]" in out, (
            f"{wf}/{sub}: an unset WHMCS_API_URL did not arrive as the APIM "
            f"default: {out}"
        )


def test_the_array_splat_form_defaults_like_the_direct_form():
    """213's array-splat sites are the two #1146's matcher could not see.

    `@('-ApiUrl', $env:WHMCS_API_URL, …)` has no `-Param $env:VAR` adjacency to
    match, so a checker written to #1146's spelling would report these two
    sites as compliant while they were live. The hazard is identical — measured
    in the docstring — so the remedy is asserted identically, and the array
    construction itself is pinned so a rewrite to the direct form is visible.
    """
    row = next(
        r for r in SITES
        if r[0] == "213-whmcs-zeffy-payments-import-draft.yml" and r[2] == "Export transactions"
    )
    body = _step(row)["run"]
    assert "'-ApiUrl', $env:WHMCS_API_URL" in body, (
        f"213's transactions step no longer builds its arguments as an array; "
        f"re-read this module's docstring before trusting it: {body!r}"
    )
    assert "-ApiUrl $env:WHMCS_API_URL" not in body, (
        "213's transactions step now uses the direct form as well as (or "
        "instead of) the array — the site table must be re-derived"
    )
    out, rc = _run(_render(row))
    assert rc == 0 and f"ApiUrl=[{APIM}]" in out, (
        f"the array-splat site did not default (rc={rc}): {out}"
    )


def test_without_the_guard_a_missing_mapping_is_a_silent_success():
    """Positive control. Without it the tests above prove only that 1 == 1.

    Runs 801's body with the guard stripped and the callee's real `Mandatory`
    attribute in place — the configuration a reader would assume is already
    safe — and measures what actually happens:

      * `-Ein` receives nothing, so the ARGUMENT vanishes rather than arriving
        empty (ledger L214);
      * pwsh reports `Missing an argument for parameter 'Ein'`, a
        call-signature error for a value problem;
      * the step CONTINUES, because `$ErrorActionPreference = 'Stop'` does not
        propagate a native command's failure.

    801's body happens to catch it one line later via `$LASTEXITCODE`, so the
    assertion here is on the DIAGNOSIS and on the script never having run —
    those are what the guard changes. The exit-0-having-run-nothing shape is
    the reason the guard is load-bearing rather than tidy, and it is what a
    body without a `$LASTEXITCODE` check would do.
    """
    row = next(r for r in SITES if r[0] == "801-candid-charity-check.yml")
    stripped = _strip_guard(_render(row), "INPUT_EIN")
    out, rc = _run(
        stripped,
        stub=MANDATORY_STUB,
        INPUT_OUTPUT_FILE="artifacts/candid/charity_check.json",
    )
    assert "Missing an argument for parameter 'Ein'" in out, (
        f"expected the vanished-argument diagnosis from the callee's binder, "
        f"so this control is measuring the case it claims to. If the argument "
        f"now arrives empty, L214 does not apply here as stated: {out}"
    )
    assert "CALLED Ein=" not in out, (
        f"the script ran despite the missing mapping, so the empty value did "
        f"not vanish and this control measures nothing: {out}"
    )
    assert "::error::INPUT_EIN is empty" not in out, (
        f"the stripped body still emitted the guard's error, so the strip did "
        f"not take effect: {out}"
    )


def test_without_the_guard_the_default_filled_form_loses_the_argument_too():
    """The same control for the other remedy, on the array-splat site.

    A default-fill reads as a convenience rather than a guard, which is exactly
    why it needs the control: strip it and the array element is empty, so the
    native command drops it and `-ApiUrl` binds `-OutputFile`.
    """
    row = next(
        r for r in SITES
        if r[0] == "213-whmcs-zeffy-payments-import-draft.yml" and r[2] == "Export transactions"
    )
    stripped = _strip_guard(_render(row), "WHMCS_API_URL")
    out, rc = _run(stripped)
    assert "Missing an argument for parameter 'ApiUrl'" in out, (
        f"expected the array-splat site to lose its empty argument without the "
        f"default-fill; if it now arrives empty, the remedy is cosmetic and "
        f"this module's docstring is wrong: {out}"
    )
    assert "CALLED ApiUrl=" not in out, (
        f"the export script ran despite the empty API URL: {out}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
# Wiring tests need no pwsh; behaviour does. Split so a host without pwsh still
# runs the half it can, rather than reporting a blanket SKIP that reads as a pass.
NEEDS_PWSH = {
    "test_values_reach_the_callee_at_every_behavioural_site",
    "test_fail_closed_sites_state_the_cause_and_do_not_call_the_script",
    "test_default_filled_sites_reach_the_apim_gateway_with_the_mapping_gone",
    "test_the_array_splat_form_defaults_like_the_direct_form",
    "test_without_the_guard_a_missing_mapping_is_a_silent_success",
    "test_without_the_guard_the_default_filled_form_loses_the_argument_too",
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
