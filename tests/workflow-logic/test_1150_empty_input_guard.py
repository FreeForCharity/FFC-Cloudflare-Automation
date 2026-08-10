"""Tests for the empty-dispatch-input guard (#1150).

The subject is `scripts/check-workflow-empty-input-guard.py`. What it claims:
every parameter-position `$env:` reference fed by a `workflow_dispatch` input
and reaching a NATIVE PowerShell host is either guarded against emptiness or
frozen in `KNOWN_UNGUARDED` — and that freeze is exact in both directions.

Three failure modes this module is designed against, all of which this repo has
shipped before:

* **Vacuous green.** A sweep whose input set silently empties passes by
  inspecting nothing. `test_the_scan_sees_real_bodies` pins the denominator,
  and `test_every_1148_site_is_seen_and_guarded` pins the specific 17 sites
  whose absence from the freeze would otherwise be indistinguishable from
  never having looked at them (#1150 acceptance criterion 2).

* **A guard that cannot fail.** Reading the checker proves it is wired, never
  that it detects (AGENTS.md). So every criterion that says "X makes it exit 1"
  is exercised by actually doing X, on a synthetic workflow rather than by
  mutating the tree — the repo's own mutation sites work on a copy for the same
  reason (ledger L182).

* **A control that measures the harness.** The positive controls below run real
  PowerShell and assert on a line the STUB emitted, never on the absence of an
  error: pwsh echoes the offending source back in a binder error, so a marker
  search over stdout is not a discriminator on its own. And "empty" is always
  reproduced by REMOVING the variable, never by assigning `''` — on Windows
  those are different scenarios and only the removal reproduces the hazard on
  both hosts (#1150, pitfall 3).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-empty-input-guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_empty_input", CHECKER)
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file (wrong extension, or a directory). If it was renamed, "
        "update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves the class's own module out of
    # `sys.modules`, and without this line the import dies with
    # `AttributeError: 'NoneType' object has no attribute '__dict__'` inside
    # dataclasses — an error naming neither this file nor the checker.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()

HAVE_PWSH = guard.find_powershell() is not None

# The 17 call sites #1148 converted, keyed the way `test_1146`'s own SITES table
# keys them: (workflow, job, step-name substring, variable).
#
# Keyed by STEP and not by (workflow, variable), because `213` converted FOUR
# distinct steps that all map `WHMCS_API_URL`. Collapsing them to one key
# asserts one of the four and silently drops the rest, so a regression in
# "Export invoices" would pass as long as "Export clients" still held — the
# exact vacuous-green shape this list exists to prevent, one level down.
# Caught in review on #1155.
CONVERTED_1148 = [
    ("201-whmcs-export-domains.yml", "export_domains", "Export domains", "WHMCS_API_URL"),
    ("202-whmcs-export-products.yml", "export_products", "Export products", "WHMCS_API_URL"),
    ("203-whmcs-export-payment-methods.yml", "export_payment_methods", "Export payment method", "WHMCS_API_URL"),
    ("212-whmcs-product-add.yml", "product_add", "Add catalog product", "WHMCS_API_URL"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export clients", "WHMCS_API_URL"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export transactions", "WHMCS_API_URL"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Export invoices", "WHMCS_API_URL"),
    ("213-whmcs-zeffy-payments-import-draft.yml", "whmcs_to_zeffy_draft", "Lookup invoices", "WHMCS_API_URL"),
    ("214-whmcs-clients-metrics.yml", "clients_metrics", "Aggregate client metrics", "WHMCS_API_URL"),
    ("215-whmcs-nonprofit-clients-metrics.yml", "nonprofit_clients_metrics", "Aggregate nonprofit", "WHMCS_API_URL"),
    ("216-whmcs-activity-metrics.yml", "activity_metrics", "Build activity matrix", "WHMCS_API_URL"),
    ("217-whmcs-client-fields-survey.yml", "client_fields_survey", "Survey client classification", "WHMCS_API_URL"),
    ("218-whmcs-siteslist-reconciliation.yml", "siteslist_reconciliation", "Reconcile WHMCS", "WHMCS_API_URL"),
    ("220-whmcs-served-metrics.yml", "served_metrics", "Build served-per-year", "WHMCS_API_URL"),
    ("221-whmcs-application-search.yml", "application_search", "Search applications", "QUERY"),
    ("801-candid-charity-check.yml", "charity_check", "Charity Check lookup", "INPUT_EIN"),
    ("802-candid-essentials-search.yml", "essentials_search", "Essentials search", "INPUT_SEARCH_TERMS"),
]

# 221's step also references WHMCS_API_URL, guarded, and is deliberately NOT in
# the list above: #1146's table covers that step under `QUERY`. So the scan sees
# 18 references across these 14 workflows and 17 of them are #1148's — the
# difference is accounted for rather than absorbed by a >= assertion.
EXTRA_GUARDED_IN_THE_SAME_STEPS = 1

# The two sites a form-blind matcher flags and this guard must not (#1150
# criterion 3): both invoke through the PowerShell binder, so an empty value
# binds empty and shifts nothing.
BINDER_FORM_WORKFLOWS = (
    "225-whmcs-domain-order-url-verify.yml",
    "226-whmcs-application-triage.yml",
)

_SAMPLE_HEAD = """name: 999 sample
on:
  workflow_dispatch:
    inputs:
      domain:
        description: apex
        required: true
        type: string
jobs:
  sample:
    runs-on: windows-latest
    environment: sample-prod-write
    steps:
      - name: Call
        shell: pwsh
        env:
          INPUT_DOMAIN: ${{ inputs.domain }}
        run: |
"""


def _sample(body: str, head: str = _SAMPLE_HEAD) -> str:
    indented = "\n".join(f"          {line}" for line in body.strip("\n").split("\n"))
    return head + indented + "\n"


def _scan_text(text: str, name: str = "999-sample.yml"):
    """Scan one synthetic workflow. Returns (sites, problems)."""
    with tempfile.TemporaryDirectory() as tmp:
        sample = pathlib.Path(tmp) / name
        sample.write_text(text, encoding="utf-8")
        sites, problems, _scanned, _bodies, _unjudged = guard.scan([sample])
    return sites, problems


# --------------------------------------------------------------------------
# The denominator. Everything below is worthless if the scan sees nothing.
# --------------------------------------------------------------------------


def test_the_scan_sees_real_bodies():
    sites, problems, scanned, bodies, _unjudged = guard.scan()
    assert not problems, f"the tree scan reported problems: {problems}"
    assert scanned > 50, f"only {scanned} workflow files scanned — the glob is wrong"
    assert bodies > 20, (
        f"only {bodies} PowerShell bodies map a dispatch input; this guard would "
        f"be passing by inspecting almost nothing"
    )
    assert len(sites) > 30, (
        f"only {len(sites)} parameter-position references found — a checker that "
        f"finds nothing cannot fail on anything"
    )


def test_the_tree_matches_the_freeze():
    """The whole contract, end to end: `main()` exits 0 on `main`."""
    assert guard.main() == 0, (
        "the checker exits non-zero on the committed tree — either a call site "
        "changed or KNOWN_UNGUARDED is stale; run it and read the report"
    )


def test_the_freeze_is_empty_because_the_burn_down_landed():
    """The 24 frozen sites are fixed, so the freeze describes nothing.

    An empty freeze makes `compare()` vacuous in the stale direction, which is
    exactly the shape that lets a sweep pass by inspecting nothing. So this
    asserts the three things that would otherwise be indistinguishable from a
    checker that stopped working: the population is non-empty, none of it is
    unguarded, and the write lanes are still recognised as write lanes. If
    `job_environments` silently returned nothing, the middle assertion would
    still pass and the last would not.
    """
    assert guard.KNOWN_UNGUARDED == {}, (
        f"KNOWN_UNGUARDED is not empty: {sorted(guard.KNOWN_UNGUARDED)}. Every "
        f"entry is a live hazard, so a re-populated freeze is a burn-down that "
        f"regressed, not a new baseline"
    )
    sites, *_ = guard.scan()
    unguarded = [site for site in sites if not site.guarded]
    assert not unguarded, (
        f"{len(unguarded)} site(s) are unguarded with an empty freeze, so the "
        f"checker must be exiting 1: {sorted(str(site) for site in unguarded)}"
    )
    write = [site for site in sites if site.is_write]
    assert len(write) > 10, (
        f"only {len(write)} of {len(sites)} sites classify as a write lane — "
        f"#1150 measured 17 unguarded ones alone, so a collapse here means "
        f"`job_environments` stopped resolving, not that the lanes changed"
    )


# --------------------------------------------------------------------------
# Acceptance criteria 2 and 3 — what must NOT be in the freeze, and why the
# absence is evidence rather than an oversight.
# --------------------------------------------------------------------------


def test_every_1148_site_is_seen_and_guarded():
    """All 17, each located by its own step — not 14 keys with 3 sites dropped."""
    assert len(CONVERTED_1148) == 17, (
        f"the table lists {len(CONVERTED_1148)} sites; #1148 converted 17, and a "
        f"short table is a criterion-2 assertion that silently covers less than "
        f"it claims"
    )
    sites, *_ = guard.scan()
    for workflow, job, step_substring, variable in CONVERTED_1148:
        matches = [
            site
            for site in sites
            if site.workflow == workflow
            and site.job == job
            and step_substring in site.step
            and site.variable == variable
        ]
        where = f"{workflow} :: {job} :: …{step_substring}… :: {variable}"
        assert len(matches) == 1, (
            f"{where} — expected exactly one reference, found {len(matches)}. Zero "
            f"means one of the 17 sites #1148 converted is not seen at all, so its "
            f"absence from the freeze proves nothing; more than one means the step "
            f"substring stopped identifying a single call site and this row is no "
            f"longer asserting what it names"
        )
        site = matches[0]
        assert site.guarded, (
            f"{where} is reported UNGUARDED although #1148 guarded it. Per "
            f"criterion 2 the guard-detection is wrong, not the workflow"
        )
        assert site.key not in guard.KNOWN_UNGUARDED.get(workflow, ()), (
            f"{where} is frozen although it is guarded (criterion 2)"
        )


def test_the_1148_workflows_hold_no_references_the_table_does_not_account_for():
    """The other half of criterion 2: nothing in those steps is silently extra.

    Asserted as an exact count rather than a floor. A `>=` here would let a new
    unguarded reference appear inside an already-converted workflow and still
    read as "all 17 present and guarded", which is the reassuring direction.
    """
    sites, *_ = guard.scan()
    prefixes = {workflow[:3] for workflow, _job, _step, _var in CONVERTED_1148}
    found = [site for site in sites if site.workflow[:3] in prefixes]
    expected = len(CONVERTED_1148) + EXTRA_GUARDED_IN_THE_SAME_STEPS
    assert len(found) == expected, (
        f"expected {expected} references across the #1148 workflows "
        f"({len(CONVERTED_1148)} converted + {EXTRA_GUARDED_IN_THE_SAME_STEPS} "
        f"accounted-for extra), found {len(found)}: "
        f"{sorted((s.workflow, s.step, s.variable) for s in found)}"
    )
    unguarded = [site for site in found if not site.guarded]
    assert not unguarded, (
        f"a reference inside an already-converted workflow is unguarded: {unguarded}"
    )


def test_the_binder_form_produces_no_site_at_all():
    """225 / 226 are correct code — not findings, and not freeze entries."""
    sites, *_ = guard.scan()
    for workflow in BINDER_FORM_WORKFLOWS:
        offenders = [site for site in sites if site.workflow == workflow]
        assert not offenders, (
            f"{workflow} produced {len(offenders)} site(s): {offenders}. It calls "
            f"through the PowerShell binder, which binds an empty value and shifts "
            f"nothing — a freeze entry here would record a defect that does not exist"
        )
        assert workflow not in guard.KNOWN_UNGUARDED, (
            f"{workflow} is in KNOWN_UNGUARDED (criterion 3)"
        )


def test_a_binder_invocation_is_not_a_finding():
    sites, problems = _scan_text(_sample(
        "& .\\scripts\\x.ps1 -Domain $env:INPUT_DOMAIN -Out 'lit.csv'"
    ))
    assert not problems, problems
    assert not sites, (
        f"`& .\\x.ps1 -Domain $env:INPUT_DOMAIN` was reported: {sites}. Measured on "
        f"pwsh 7.4.6/7.6.4, that call binds Domain=[] and shifts nothing"
    )


# --------------------------------------------------------------------------
# Acceptance criterion 4 — both call forms detected, each naming its site.
# --------------------------------------------------------------------------


def test_the_direct_form_is_detected():
    sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN -Out 'lit.csv'"
    ))
    assert not problems, problems
    assert len(sites) == 1, f"expected one site, got {sites}"
    site = sites[0]
    assert (site.variable, site.form, site.guarded) == ("INPUT_DOMAIN", guard.DIRECT, False)
    assert site.job == "sample" and "Call" in site.step, (
        f"the finding must name the job and step: {site}"
    )


def test_the_array_splat_form_is_detected():
    """The form #1146's regex could not see: no `-Param $env:VAR` adjacency."""
    sites, problems = _scan_text(_sample(
        "$a = @('-Domain', $env:INPUT_DOMAIN, '-Out', 'lit.csv')\n"
        "& pwsh -NoProfile -File scripts/x.ps1 @a"
    ))
    assert not problems, problems
    assert len(sites) == 1, f"expected one site, got {sites}"
    assert (sites[0].variable, sites[0].form, sites[0].guarded) == (
        "INPUT_DOMAIN", guard.ARRAY_SPLAT, False,
    )


def test_the_flag_and_its_value_are_matched_across_appends():
    """`$a = @('-Domain'); $a += $env:X` is one argument list, not two."""
    sites, problems = _scan_text(_sample(
        "$a = @('-Domain')\n"
        "$a += $env:INPUT_DOMAIN\n"
        "& pwsh -NoProfile -File scripts/x.ps1 @a"
    ))
    assert not problems, problems
    assert len(sites) == 1 and sites[0].form == guard.ARRAY_SPLAT, (
        f"adjacency across two assignments was missed: {sites}"
    )


def test_an_unfrozen_instance_fails_the_run():
    current = {"999-sample.yml": ("sample::INPUT_DOMAIN",)}
    new, stale = guard.compare(current, known={})
    assert new and not stale, f"expected a new instance, got new={new} stale={stale}"
    assert "999-sample.yml" in new[0] and "INPUT_DOMAIN" in new[0]


def test_a_new_variable_in_an_already_frozen_workflow_fails():
    new, stale = guard.compare(
        {"999-sample.yml": ("sample::A", "sample::B")},
        known={"999-sample.yml": ("sample::A",)},
    )
    assert new and "sample::B" in new[0], f"a widened site was not reported: {new}"


# --------------------------------------------------------------------------
# Acceptance criteria 5 and 6 — local-copy guards, and the freeze not lying.
# --------------------------------------------------------------------------


def test_a_guard_on_a_local_copy_is_recognised():
    """The false-positive class #1150 names explicitly."""
    sites, problems = _scan_text(_sample(
        "$domain = $env:INPUT_DOMAIN\n"
        "if ([string]::IsNullOrWhiteSpace($domain)) {\n"
        "  Write-Output '::error::domain is empty'\n"
        "  exit 1\n"
        "}\n"
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN"
    ))
    assert not problems, problems
    assert len(sites) == 1, f"expected the site to be seen, got {sites}"
    assert sites[0].guarded, (
        "a guard written against a local copy was not recognised, so correct code "
        "is reported as a defect — the direction that gets a guard switched off"
    )
    assert "local copy" in sites[0].guard_reason, sites[0].guard_reason


def test_a_gated_append_is_recognised_on_the_same_line():
    """505 and 503 spell 11 of their references this way, all on one line."""
    sites, problems = _scan_text(_sample(
        "$a = @('-Key', 'k')\n"
        "if ($env:INPUT_DOMAIN) { $a += @('-Domain', $env:INPUT_DOMAIN) }\n"
        "& pwsh -NoProfile -File scripts/x.ps1 @a"
    ))
    assert not problems, problems
    assert len(sites) == 1 and sites[0].guarded, (
        f"a same-line gated append read as unguarded: {sites}. Comparing by LINE "
        f"instead of by source offset is what does this"
    )


def test_a_guard_below_the_call_does_not_count():
    """Position is the whole rule — a test after the call guards nothing."""
    sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN\n"
        "if ([string]::IsNullOrWhiteSpace($env:INPUT_DOMAIN)) { exit 1 }"
    ))
    assert not problems, problems
    assert len(sites) == 1 and not sites[0].guarded, (
        f"a guard BELOW the call was accepted: {sites}. Then any body mentioning "
        f"the variable in an `if` anywhere would read as guarded"
    )


def test_a_stale_freeze_entry_fails_the_run():
    new, stale = guard.compare({}, known={"999-sample.yml": ("sample::INPUT_DOMAIN",)})
    assert stale and not new, f"expected a stale entry, got new={new} stale={stale}"
    assert "delete the entry" in stale[0]


def test_a_narrowed_freeze_entry_fails_the_run():
    new, stale = guard.compare(
        {"999-sample.yml": ("sample::A",)},
        known={"999-sample.yml": ("sample::A", "sample::B")},
    )
    assert stale and "sample::B" in stale[0], f"a fixed site left its entry: {stale}"


# --------------------------------------------------------------------------
# Acceptance criterion 7 — fail closed, in every direction.
# --------------------------------------------------------------------------


def test_unparseable_yaml_is_a_finding():
    _sites, problems = _scan_text("name: broken\non: [\n  unclosed\n")
    assert problems and "YAML does not parse" in problems[0], (
        f"a workflow whose YAML is broken was skipped silently: {problems}"
    )


def test_unparseable_powershell_is_a_finding():
    _sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN\n"
        "if ($true) {"
    ))
    assert problems and "PowerShell does not parse" in problems[0], (
        f"a body PowerShell cannot parse was skipped: {problems}"
    )


def test_a_splat_that_is_never_assigned_is_a_finding():
    _sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 @neverAssigned"
    ))
    assert problems and "never assigned" in problems[0], (
        f"an unreadable splat was treated as clean: {problems}. A splat this guard "
        f"cannot read is exactly the blind spot it exists to close"
    )


def test_no_powershell_host_refuses_to_report_a_pass():
    original = guard.find_powershell
    guard.find_powershell = lambda: None
    try:
        assert guard.main() == 1, (
            "with no PowerShell host the checker reported a pass it did not measure"
        )
    finally:
        guard.find_powershell = original
    assert guard.find_powershell is original, "the patch was not restored"


# --------------------------------------------------------------------------
# The measured pitfalls #1150 enumerates, each as its own case.
# --------------------------------------------------------------------------


def test_the_hyphen_must_start_the_token():
    """`Test-Path $env:X` must not read as `-Path $env:X` (#1150, pitfall 4).

    This inflated #1146's first measurement from 16 to 18. Written through a
    splat because that is the path where a literal element is tested for
    flag-shape at all.
    """
    sites, problems = _scan_text(_sample(
        "$a = @('Test-Path', $env:INPUT_DOMAIN)\n"
        "& pwsh -NoProfile -File scripts/x.ps1 @a"
    ))
    assert not problems, problems
    assert not sites, (
        f"a hyphenated word was read as a parameter flag: {sites}"
    )


def test_a_mapping_with_a_literal_fallback_is_not_a_hazard():
    head = _SAMPLE_HEAD.replace(
        "INPUT_DOMAIN: ${{ inputs.domain }}",
        "INPUT_DOMAIN: ${{ inputs.domain || 'ffcworkingsite1.org' }}",
    )
    sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN", head=head
    ))
    assert not problems, problems
    assert not sites, (
        f"a mapping the expression engine already defaults was reported: {sites}"
    )


def test_an_empty_string_fallback_is_still_a_hazard():
    """`|| ''` defaults to empty, which is no guard at all."""
    head = _SAMPLE_HEAD.replace(
        "INPUT_DOMAIN: ${{ inputs.domain }}",
        "INPUT_DOMAIN: ${{ inputs.domain || '' }}",
    )
    sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN", head=head
    ))
    assert not problems, problems
    assert len(sites) == 1 and not sites[0].guarded, (
        f"`|| ''` was read as a default: {sites}"
    )


def test_a_variable_that_is_not_a_dispatch_input_is_not_a_site():
    sites, problems = _scan_text(_sample(
        "& pwsh -NoProfile -File scripts/x.ps1 -Temp $env:RUNNER_TEMP"
    ))
    assert not problems, problems
    assert not sites, f"a runner-provided variable was reported: {sites}"


# --------------------------------------------------------------------------
# Acceptance criterion 8 — the positive control, in real PowerShell.
#
# Without these the assertions above prove only that the checker agrees with
# itself about a hazard nobody demonstrated.
# --------------------------------------------------------------------------

# `Mandatory` is what a reader assumes already protects them, so the positive
# control has to include it — the point is that it does NOT, because the
# argument never arrives to be judged.
MANDATORY_STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$Out
)
Write-Output "CALLED Domain=[$Domain] Out=[$Out]"
"""

# The binder measurement needs the permissive block, and the difference is
# itself worth stating: through the binder an empty value ARRIVES, so a
# `Mandatory` parameter rejects it with `Cannot bind argument … it is an empty
# string`. That is a loud, correct refusal — the opposite of the silent
# argument-shift this guard exists for, and measuring it against the mandatory
# stub would conflate the two.
PERMISSIVE_STUB = """[CmdletBinding()]
param(
    [string]$Domain,
    [string]$Out
)
Write-Output "CALLED Domain=[$Domain] Out=[$Out]"
"""


def _run(body: str, stub: str = MANDATORY_STUB) -> tuple[str, int]:
    """Run a PowerShell body with INPUT_DOMAIN REMOVED from the environment.

    Removed, never set to `''`: on Windows an assigned empty string leaves the
    variable present and the hazard does NOT reproduce, so a control written
    that way measures every site as safe on the platform these workflows
    actually run on (#1150, pitfall 3). `env.pop` is unambiguous on both.

    The environment is inherited and then layered — never a fresh minimal dict
    (ledger L35).
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "x.ps1").write_text(stub, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = dict(os.environ)
        env.pop("INPUT_DOMAIN", None)
        proc = subprocess.run(
            [guard.find_powershell(), "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        return proc.stdout + proc.stderr, proc.returncode


def test_the_unguarded_direct_form_exits_zero_having_run_nothing():
    """The whole reason this guard is load-bearing rather than tidy.

    `Mandatory` is what a reader assumes already protects them. It does not:
    the argument never arrives to be judged, pwsh reports a call-SIGNATURE
    problem for a value problem, and the step CONTINUES — `$ErrorActionPreference
    = 'Stop'` does not propagate a native command's failure.
    """
    out, rc = _run(
        "$ErrorActionPreference = 'Stop'\n"
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN -Out 'lit.csv'\n"
        "Write-Output 'STEP-CONTINUED'\n"
    )
    assert "Missing an argument for parameter 'Domain'" in out, (
        f"expected the vanished-argument diagnosis; if the argument now arrives "
        f"empty, L214 does not apply as stated and this guard's premise is wrong: {out}"
    )
    assert "CALLED Domain=" not in out, (
        f"the callee ran despite the missing mapping, so nothing vanished and this "
        f"control measures nothing: {out}"
    )
    assert "STEP-CONTINUED" in out and rc == 0, (
        f"the step did not continue to exit 0 (rc={rc}); the silent-success half of "
        f"the hazard is what makes the guard load-bearing: {out}"
    )


def test_the_unguarded_array_splat_form_loses_it_the_same_way():
    out, rc = _run(
        "$ErrorActionPreference = 'Stop'\n"
        "$a = @('-Domain', $env:INPUT_DOMAIN, '-Out', 'lit.csv')\n"
        "& pwsh -NoProfile -File scripts/x.ps1 @a\n"
    )
    assert "Missing an argument for parameter 'Domain'" in out, (
        f"the array-splat form did not lose its empty element, so the checker's "
        f"reason for matching it would be wrong: {out}"
    )
    assert "CALLED Domain=" not in out, out


def test_the_binder_form_binds_empty_and_shifts_nothing():
    """The measured basis for excluding 225 / 226 rather than freezing them."""
    out, rc = _run(
        "$ErrorActionPreference = 'Stop'\n"
        "& ./scripts/x.ps1 -Domain $env:INPUT_DOMAIN -Out 'lit.csv'\n",
        stub=PERMISSIVE_STUB,
    )
    assert "CALLED Domain=[] Out=[lit.csv]" in out, (
        f"the PowerShell binder did NOT bind the empty value and keep the later "
        f"argument in place; if that ever changes, 225/226 become real findings "
        f"and this guard must start reporting them (rc={rc}): {out}"
    )


def test_the_guarded_form_states_the_cause_and_does_not_call(  # noqa: D401
):
    """The remedy the checker recommends, exercised end to end."""
    out, rc = _run(
        "$ErrorActionPreference = 'Stop'\n"
        "if ([string]::IsNullOrWhiteSpace($env:INPUT_DOMAIN)) {\n"
        "  Write-Output '::error::INPUT_DOMAIN is empty; pass domain to the dispatch.'\n"
        "  exit 1\n"
        "}\n"
        "& pwsh -NoProfile -File scripts/x.ps1 -Domain $env:INPUT_DOMAIN -Out 'lit.csv'\n"
    )
    assert rc == 1, f"the guarded body did not fail closed (rc={rc}): {out}"
    assert "::error::INPUT_DOMAIN is empty" in out, out
    assert "CALLED Domain=" not in out, (
        f"the callee ran anyway, so the guard is decorative: {out}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Everything that reaches the checker's `scan()` needs a PowerShell host — it
# reads PowerShell with the PowerShell parser and refuses to report a pass
# without one. Only the pure `compare()` cases and the no-host refusal run
# without pwsh, and they are the ones NOT listed here.
NEEDS_PWSH = {
    "test_the_scan_sees_real_bodies",
    "test_the_tree_matches_the_freeze",
    "test_the_freeze_is_not_empty_and_names_the_write_lanes",
    "test_every_1148_site_is_seen_and_guarded",
    "test_the_1148_workflows_hold_no_references_the_table_does_not_account_for",
    "test_the_binder_form_produces_no_site_at_all",
    "test_a_binder_invocation_is_not_a_finding",
    "test_the_direct_form_is_detected",
    "test_the_array_splat_form_is_detected",
    "test_the_flag_and_its_value_are_matched_across_appends",
    "test_a_guard_on_a_local_copy_is_recognised",
    "test_a_gated_append_is_recognised_on_the_same_line",
    "test_a_guard_below_the_call_does_not_count",
    "test_unparseable_yaml_is_a_finding",
    "test_unparseable_powershell_is_a_finding",
    "test_a_splat_that_is_never_assigned_is_a_finding",
    "test_the_hyphen_must_start_the_token",
    "test_a_mapping_with_a_literal_fallback_is_not_a_hazard",
    "test_an_empty_string_fallback_is_still_a_hazard",
    "test_a_variable_that_is_not_a_dispatch_input_is_not_a_site",
    "test_the_unguarded_direct_form_exits_zero_having_run_nothing",
    "test_the_unguarded_array_splat_form_loses_it_the_same_way",
    "test_the_binder_form_binds_empty_and_shifts_nothing",
    "test_the_guarded_form_states_the_cause_and_does_not_call",
}

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not HAVE_PWSH:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
