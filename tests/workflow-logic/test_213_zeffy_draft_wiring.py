"""213's nine free-text inputs travel in `env:`, and the blanks it now refuses.

    #1080 lane 21 — the widest entry the freeze ever held (nine inputs, 21
    references, six script bodies in one job) and the FIRST read-only lane, so it
    is the first burn-down that moves the interpolating baseline without moving
    the write one.

    WHY THE SITES WERE LIVE

    Every one of them sat in DOUBLE quotes (`$out = "${{ inputs.clients_output }}"`),
    where pwsh expands `$( )` — so this is the 116/702 shape and no quote breakout
    is needed. Measured on pwsh 7.4.6 against the body as it shipped, with
    `clients_output` carrying a `$($null = Set-Content …)` tail: the decoy
    credential was written to the sentinel, the exporter was still handed a legal
    `OutputFile=[artifacts/whmcs/whmcs_clients.csv]`, and the step exited **0**.
    `$null =` swallows the subexpression's output, so the variable keeps its legal
    value and the log reads as an ordinary run. The benign control leaked nothing.

    The credential is a WHMCS API secret arriving through GITHUB_ENV from
    `whmcs-secrets-from-kv`, so an L213 read of the injected step's own `env:` and
    a `secrets.` grep of the file (#1141) both score it as holding nothing — the
    arrival path #1188's reachability index exists to surface.

    THE BLANK, MEASURED HERE RATHER THAN CARRIED ACROSS (ledger L260)

    Lane 20 recorded, for 601, that an EMPTY value is preserved as an argument and
    an UNSET one is dropped. Restating that here would have been wrong. 213's path
    values reach a CMDLET binder (`Split-Path -Parent $out`) before they ever reach
    the native `pwsh -File` call, so L214's argument-drop never gets a chance to
    fire. Guard stripped, pwsh 7.4.6, three blank forms:

      EMPTY   ''     `Cannot bind argument to parameter 'Path' because it is an
                     empty string` — rc 1, callee never invoked. LOUD.
      UNSET   $null  same line, `…because it is null` — rc 1. LOUD.
      SPACES  '   '  `Split-Path` ACCEPTS it, `$dir` comes back empty so no
                     directory is made, and the callee IS invoked with
                     `OutputFile=[   ]` — a credentialed call the step should
                     never have made.

    So on this workflow the fail-closed guard buys ATTRIBUTION for the first two
    (it names the missing mapping instead of a binder error pointing at
    `Split-Path`) and closes a real hole only on the third — which is exactly the
    case a `-ne ''` gate passes through (#1213). The tests below pin all three, so
    the bound is a measurement rather than a claim inherited from a sibling lane.

    TWO INPUTS DELIBERATELY DO NOT FAIL CLOSED

    `start_date` and `end_date` document `default: ''`, and `max_rows` /
    `max_rows_per_file` are caps whose omission is meaningful, so a blank is a
    ROUTINE dispatch for all four and refusing it would break the documented call.
    They keep the gated-append remedy (L254: choose the remedy from what the callee
    does with a missing argument). That decision is pinned below, in both
    directions — a later lane that "tidies" them into fail-closed guards fails
    here rather than silently narrowing the workflow's contract.

    `include_zero_invoices` stays interpolated at `:244` and in the generator body
    and is NOT a finding: it is `type: boolean`, which GitHub constrains, and that
    declaration is pinned rather than left as a reading of a comment (the 101
    precedent). The five `actions/upload-artifact` `path:` sites are not script
    bodies; the guard correctly does not judge them and this lane did not widen to
    cover them (#1080 lane 18/20 handoff) — also pinned, so the omission is a
    recorded decision rather than an oversight a reader has to re-derive.
"""

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD_PATH = _REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "213-whmcs-zeffy-payments-import-draft.yml"
JOB = "whmcs_to_zeffy_draft"
ENVIRONMENT = "whmcs-prod-read"

# Every step that consumes a free-text input, with the mappings it must carry.
# Read as a table so a step that loses a mapping fails by name rather than by a
# whole-file grep that cannot say which body regressed.
SITES = {
    "Export clients": {
        "IN_CLIENTS_OUTPUT": "${{ inputs.clients_output }}",
    },
    "Export transactions": {
        "IN_TRANSACTIONS_OUTPUT": "${{ inputs.transactions_output }}",
        "IN_MAX_ROWS": "${{ inputs.max_rows }}",
        "IN_START_DATE": "${{ inputs.start_date }}",
        "IN_END_DATE": "${{ inputs.end_date }}",
    },
    "Export invoices": {
        "IN_INVOICES_OUTPUT": "${{ inputs.invoices_output }}",
        "IN_START_DATE": "${{ inputs.start_date }}",
        "IN_END_DATE": "${{ inputs.end_date }}",
    },
    "Lookup invoices": {
        "IN_TRANSACTIONS_OUTPUT": "${{ inputs.transactions_output }}",
    },
    "Generate Zeffy payments import draft": {
        "IN_CLIENTS_OUTPUT": "${{ inputs.clients_output }}",
        "IN_TRANSACTIONS_OUTPUT": "${{ inputs.transactions_output }}",
        "IN_INVOICES_OUTPUT": "${{ inputs.invoices_output }}",
        "IN_ZEFFY_OUTPUT": "${{ inputs.zeffy_output }}",
        "IN_ZEFFY_OUTPUT_XLSX": "${{ inputs.zeffy_output_xlsx }}",
        "IN_MAX_ROWS_PER_FILE": "${{ inputs.max_rows_per_file }}",
    },
    "Validate Zeffy CSV headers": {
        "IN_ZEFFY_OUTPUT_XLSX": "${{ inputs.zeffy_output_xlsx }}",
    },
}

# The nine free-text inputs, split by the remedy each one takes. The split is the
# lane's substantive decision, so it is data here rather than prose.
FAIL_CLOSED_INPUTS = {
    "clients_output": "IN_CLIENTS_OUTPUT",
    "transactions_output": "IN_TRANSACTIONS_OUTPUT",
    "invoices_output": "IN_INVOICES_OUTPUT",
    "zeffy_output": "IN_ZEFFY_OUTPUT",
    "zeffy_output_xlsx": "IN_ZEFFY_OUTPUT_XLSX",
}
GATED_APPEND_INPUTS = {
    "max_rows": "IN_MAX_ROWS",
    "start_date": "IN_START_DATE",
    "end_date": "IN_END_DATE",
    "max_rows_per_file": "IN_MAX_ROWS_PER_FILE",
}

# The boolean that is allowed to stay interpolated, and the reason it is allowed.
CONSTRAINED_INPUT = "include_zero_invoices"

# The artifact-upload sites this lane deliberately did not touch: not script
# bodies, so out of the guard's scope and out of the lane's.
UPLOAD_PATH_INPUTS = (
    "clients_output",
    "transactions_output",
    "invoices_output",
    "zeffy_output",
    "zeffy_output_xlsx",
)

CALLEES = (
    "whmcs-clients-export.ps1",
    "whmcs-transactions-export.ps1",
    "whmcs-invoices-export.ps1",
    "whmcs-invoices-lookup.ps1",
    "zeffy-payments-import-draft.ps1",
)

APIM = "https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php"

# The credential in reach, and the variable an injected payload would read. Named
# by construction rather than by hand: it is what `whmcs-secrets-from-kv` exports.
CREDENTIAL_VAR = "WHMCS_API_" + "SECRET"

# Deliberately not shaped like a real secret — a scanner-detected value comes back
# REDACTED, and the one place it is printed is an assertion message on a failing
# run, which is exactly when the reader needs to see whether the sentinel holds
# the credential or an empty string.
DECOY = "whmcs-secret-placeholder-not-a-real-credential"
SENTINEL = "STOLEN-213.txt"

LEGAL_CLIENTS = "artifacts/whmcs/whmcs_clients.csv"

# GitHub's `shell: pwsh` wrapper (Runner.Worker/Handlers/ScriptHandlerHelpers.cs).
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (#1080 lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# Permissive stub: records what it was BOUND and creates the file the body then
# asserts exists. Every behavioural assertion is on a CALLED line the stub itself
# emitted — a marker search over stdout is not a discriminator on its own, since
# pwsh echoes the offending source line back in a binder error.
STUB = """[CmdletBinding()]
param(
    [string]$ApiUrl,
    [string]$OutputFile,
    [string]$MaxRows,
    [string]$StartDate,
    [string]$EndDate
)
Write-Output "CALLED ApiUrl=[$ApiUrl] OutputFile=[$OutputFile] MaxRows=[$MaxRows] StartDate=[$StartDate] EndDate=[$EndDate]"
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $d = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    Set-Content -Path $OutputFile -Value 'id'
}
"""

GUARD_HEAD = "if ([string]::IsNullOrWhiteSpace($env:%s)) {"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _wf() -> dict:
    return load_workflow(WORKFLOW)


def _step(name_substring: str) -> dict:
    return find_step(_wf(), JOB, name_substring)


def _body(name_substring: str) -> str:
    return _step(name_substring)["run"]


def _path() -> pathlib.Path:
    return _REPO_ROOT / ".github" / "workflows" / WORKFLOW


def _free_text_inputs() -> set:
    """The workflow's free-text dispatch inputs, from the guard, not by hand."""
    return set(guard.free_text_inputs(_wf()))


def _findings_by_step() -> dict:
    """The guard's own findings for this workflow, keyed by step label.

    Deliberately the guard's scan rather than a grep of my own: a spelling the
    guard understands (`github.event.inputs.X`, a nested expression) and a hand
    pattern does not is exactly the site that would survive this lane.
    """
    out: dict[str, list] = {}
    for finding in guard.scan_workflow(_path()):
        out.setdefault(finding.step, []).append(finding.input_name)
    return out


def _payload(legal: str) -> str:
    """A dispatched value that keeps a legal residue and steals on the way past."""
    return (
        legal
        + "$($null = Set-Content -Path "
        + SENTINEL
        + " -Value $env:"
        + CREDENTIAL_VAR
        + ")"
    )


def _pre_fix(body: str, env_var: str, value: str) -> str:
    """Put the defect back: the value pasted into the body, guard removed.

    Reviewing a guard by reading it proves it is WIRED, not that it DETECTS
    (AGENTS.md). Both substitutions assert their anchor first (ledger L47), so a
    rewrite of the step fails here loudly rather than quietly measuring nothing.
    """
    body = _strip_guard(body, env_var)
    anchor = f"$env:{env_var}"
    assert anchor in body, f"{anchor} absent — this reconstruction tests nothing"
    return body.replace(anchor, f'"{value}"')


def _strip_guard(body: str, env_var: str) -> str:
    marker = GUARD_HEAD % env_var
    assert body.count(marker) == 1, (
        f"expected exactly one {env_var} guard to strip, found {body.count(marker)}"
    )
    start = body.index(marker)
    end = body.index("}\n", start) + 2
    stripped = body[:start] + body[end:]
    assert marker not in stripped, "the mutation did not apply"
    return stripped


def _run(body: str, **env_overrides: str):
    """Run a body the way the runner does, in a temp cwd holding the stubs.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS, not
    its existence: a file written from an unset variable scores the same as one
    written from the live credential, and which one the payload reached is the
    claim.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        for name in CALLEES:
            (tmp / "scripts" / name).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        env = child_env(**env_overrides)
        env["GITHUB_STEP_SUMMARY"] = str(tmp / "summary.md")
        env["RUNNER_TEMP"] = str(tmp)
        # Only what the test sets may be visible. An inherited IN_* would make a
        # blank case pass for the wrong reason, and an inherited credential would
        # let a theft assertion pass without the workflow supplying anything
        # (ledger L199).
        controlled = set(FAIL_CLOSED_INPUTS.values()) | set(GATED_APPEND_INPUTS.values())
        for var in controlled | {CREDENTIAL_VAR}:
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
            timeout=180,
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _bound(output: str, param: str = "OutputFile") -> str | None:
    """What the callee was bound, or None if it never ran."""
    called = next((ln.strip() for ln in output.splitlines() if "CALLED" in ln), "")
    if not called:
        return None
    marker = f"{param}=["
    if marker not in called:
        return None
    start = called.index(marker) + len(marker)
    return called[start : called.index("]", start)]


# --------------------------------------------------------------------------
# Wiring — pure YAML, must run on a host with no pwsh (#1182)
# --------------------------------------------------------------------------


def test_no_script_body_interpolates_a_free_text_input():
    """The lane's whole claim, asserted over every body rather than the one fixed.

    Driven from the guard's own notion of a free-text input and its own
    interpolation patterns, so a body that grows a new site — or a spelling the
    guard understands and a hand grep does not — fails here.
    """
    leftover = _findings_by_step()
    assert not leftover, (
        f"{WORKFLOW} still interpolates free-text inputs into script bodies: "
        + "; ".join(f"{step}: {sorted(set(names))}" for step, names in leftover.items())
    )
    # Positive control: the scan must have had something to look at. A guard that
    # stopped recognising this workflow's inputs would report the same empty
    # result as a burned-down one (the Norway problem the sibling module pins).
    assert len(_free_text_inputs()) >= 9, (
        f"expected 213's nine free-text inputs, the guard sees "
        f"{sorted(_free_text_inputs())} — the emptiness above proves nothing"
    )


def test_every_consuming_step_maps_every_input_it_reads():
    """Per step, per variable — a table, so a regression names the body."""
    for step_name, mappings in SITES.items():
        step = _step(step_name)
        env = step.get("env") or {}
        body = step.get("run", "")
        for var, expression in mappings.items():
            assert env.get(var) == expression, (
                f"step {step_name!r} must map {var} to {expression}; its env: is "
                f"{env!r}"
            )
            assert f"$env:{var}" in body, (
                f"step {step_name!r} maps {var} but never reads it — the mapping "
                f"is decoration and the value the body uses comes from elsewhere"
            )


def test_the_table_covers_every_step_that_reads_a_mapped_variable():
    """Positive control on SITES itself.

    A table of expectations cannot report a step it does not list, so the
    assertion that matters is that the table is CURRENT. Derived from the tree:
    every step reading an `IN_*` variable must appear above.
    """
    known = set(FAIL_CLOSED_INPUTS.values()) | set(GATED_APPEND_INPUTS.values())
    listed = {name for name in SITES}
    for job in _wf()["jobs"].values():
        for step in job["steps"]:
            body = step.get("run") or ""
            reads = {var for var in known if f"$env:{var}" in body}
            if not reads:
                continue
            name = step.get("name", "")
            assert any(sub in name for sub in listed), (
                f"step {name!r} reads {sorted(reads)} but is absent from SITES — "
                f"the table is stale, so this module is not testing that step"
            )


def test_every_path_input_fails_closed_before_its_first_use():
    """The guard must PRECEDE the use, or it is decoration.

    Comment lines are excluded from the search for the first use: each guard's
    own message names the callee and the parameter, so a naive `find` matches the
    explanation and reports a correctly-ordered site as inverted — and, worse,
    scores a guard moved BELOW its call as fine.
    """
    for step_name, mappings in SITES.items():
        body = _body(step_name)
        for var in mappings:
            if var not in FAIL_CLOSED_INPUTS.values():
                continue
            marker = GUARD_HEAD % var
            assert marker in body or _guarded_in_loop(body, var), (
                f"step {step_name!r} reads {var} with no IsNullOrWhiteSpace guard"
            )
            guard_at = _first_code_offset(body, marker)
            if guard_at is None:
                continue  # covered by the loop form, asserted separately below
            use_at = _first_code_offset(body, f"= $env:{var}")
            assert use_at is not None and guard_at < use_at, (
                f"step {step_name!r}: the {var} guard does not precede its first "
                f"use, so a blank is spent before it is refused"
            )


def _guarded_in_loop(body: str, var: str) -> bool:
    """The generator step checks its five paths in one `foreach`, by NAME."""
    return f"'{var}'" in body and "GetEnvironmentVariable($name)" in body


def _first_code_offset(body: str, needle: str) -> int | None:
    offset = 0
    for line in body.split("\n"):
        if not line.strip().startswith("#"):
            found = line.find(needle)
            if found >= 0:
                return offset + found
        offset += len(line) + 1
    return None


def test_the_optional_filters_keep_the_gated_append_and_do_not_fail_closed():
    """Pin the L254 decision in BOTH directions.

    A later lane tidying these into fail-closed guards would look like
    consistency and would break the documented call: `start_date` / `end_date`
    ship `default: ''`, and the caps mean "no flag, use the callee's default".
    """
    inputs = _wf()[True]["workflow_dispatch"]["inputs"]
    for name, var in GATED_APPEND_INPUTS.items():
        for step_name, mappings in SITES.items():
            if var not in mappings:
                continue
            body = _body(step_name)
            assert (GUARD_HEAD % var) not in body, (
                f"step {step_name!r} fails closed on {var}, but a blank {name} is "
                f"a routine dispatch — this narrows the workflow's contract"
            )
            assert f"([string]$env:{var}).Trim()" in body, (
                f"step {step_name!r} must read {var} through a [string] cast "
                f"before .Trim(); an empty env: mapping reads back as $null on "
                f"the Windows runner and $null.Trim() is terminating under 'Stop'"
            )
        assert name in inputs, f"{name} is no longer a dispatch input"


def test_the_boolean_is_declared_boolean_which_is_what_makes_it_safe():
    """`include_zero_invoices` stays interpolated; pin the premise, not a comment.

    If a later edit retypes it to `string`, it becomes a live interpolation site
    and the freeze would have to grow again — this fails first.
    """
    inputs = _wf()[True]["workflow_dispatch"]["inputs"]
    assert inputs[CONSTRAINED_INPUT]["type"] == "boolean", (
        f"{CONSTRAINED_INPUT} is interpolated into a script body and is only safe "
        f"because GitHub constrains a boolean; it is now "
        f"{inputs[CONSTRAINED_INPUT].get('type')!r}"
    )
    assert CONSTRAINED_INPUT not in _free_text_inputs(), (
        "the guard now counts it as free text, so leaving it interpolated is a "
        "finding rather than a documented exclusion"
    )


def test_the_artifact_uploads_still_take_the_input_directly():
    """A recorded decision, not an oversight.

    `actions/upload-artifact`'s `path:` is not a script body: the value is not
    parsed as code, the guard does not judge it, and the lane did not widen to
    cover it (#1080 lane 18/20 handoff). Pinned so the next reader does not have
    to re-derive whether these were missed.
    """
    doc = _wf()
    seen = set()
    for job in doc["jobs"].values():
        for step in job["steps"]:
            if not str(step.get("uses", "")).startswith("actions/upload-artifact"):
                continue
            path = str((step.get("with") or {}).get("path", ""))
            for name in UPLOAD_PATH_INPUTS:
                if f"inputs.{name}" in path:
                    seen.add(name)
    assert seen == set(UPLOAD_PATH_INPUTS), (
        f"the upload steps no longer take {sorted(set(UPLOAD_PATH_INPUTS) - seen)} "
        f"directly. If a step now runs a SCRIPT over that value it is in scope for "
        f"#1080 and needs the env: remedy; re-derive rather than deleting this."
    )


def test_the_environment_is_read_only_which_is_what_makes_this_lane_the_first():
    """213 is the first burn-down that does not decrement the write baseline.

    If this job ever enters a write environment, the injection measured above
    runs under a production write credential and the lane's whole risk statement
    changes — so the fact is asserted from the tree, not assumed.
    """
    envs = sorted(guard.environments(_wf()))
    assert envs == [ENVIRONMENT], f"expected [{ENVIRONMENT}], got {envs}"
    assert not any(guard.is_write_environment(e) for e in envs), (
        f"{WORKFLOW} now enters a write environment — re-measure the lane"
    )


def test_the_checker_agrees_this_workflow_is_burned_down():
    """The guard's own answer, so the module cannot disagree with CI."""
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    current = guard.current_map(findings)
    assert WORKFLOW not in current, (
        f"{WORKFLOW} still interpolates {current.get(WORKFLOW)}"
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} is burned down but still listed in KNOWN_UNGUARDED — the "
        f"stale-entry check will fail CI"
    )


# --------------------------------------------------------------------------
# Behaviour — requires pwsh
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_credential_and_exited_zero():
    """Reintroduce the defect and watch it work (AGENTS.md).

    Without this the module proves the remedy is present, never that it was
    needed — and a reader has only the prose above to tell them the difference.
    """
    body = _pre_fix(_body("Export clients"), "IN_CLIENTS_OUTPUT", _payload(LEGAL_CLIENTS))
    out, stolen, rc = _run(body, WHMCS_API_URL=APIM, **{CREDENTIAL_VAR: DECOY})
    assert stolen is not None and stolen.strip() == DECOY, (
        f"the pre-fix body did not execute the payload, so this module is not "
        f"measuring the defect it claims: sentinel={stolen!r} out={out[:400]}"
    )
    assert _bound(out) == LEGAL_CLIENTS, (
        f"the exporter should still have been handed the legal residue; it got "
        f"{_bound(out)!r}"
    )
    assert rc == 0, f"the exploited run must look ordinary; it exited {rc}: {out[:400]}"


def test_the_shipped_body_binds_the_payload_as_inert_data():
    """Same payload, same step, through `env:`: nothing runs."""
    body = _body("Export clients")
    out, stolen, _ = _run(
        body,
        WHMCS_API_URL=APIM,
        IN_CLIENTS_OUTPUT=_payload(LEGAL_CLIENTS),
        **{CREDENTIAL_VAR: DECOY},
    )
    assert stolen is None, (
        f"the payload executed through the fixed body: sentinel={stolen!r}"
    )
    bound = _bound(out)
    assert bound is not None and bound.startswith(LEGAL_CLIENTS + "$("), (
        f"the payload should have reached the callee verbatim as one argument; "
        f"it bound {bound!r}"
    )


def test_an_ordinary_value_still_reaches_the_callee():
    """The happy path. A guard that broke it would be caught here."""
    out, _, rc = _run(
        _body("Export clients"), WHMCS_API_URL=APIM, IN_CLIENTS_OUTPUT=LEGAL_CLIENTS
    )
    assert rc == 0, f"the ordinary path exited {rc}: {out[:400]}"
    assert _bound(out) == LEGAL_CLIENTS, f"bound {_bound(out)!r}"


def test_every_blank_form_is_refused_before_the_callee_runs():
    """Empty, unset and all-spaces, on both fail-closed shapes in the job."""
    cases = (
        ("Export clients", "IN_CLIENTS_OUTPUT"),
        ("Export transactions", "IN_TRANSACTIONS_OUTPUT"),
    )
    for step_name, var in cases:
        for label, overrides in (
            ("empty", {var: ""}),
            ("unset", {}),
            ("spaces", {var: "   "}),
        ):
            out, _, rc = _run(_body(step_name), WHMCS_API_URL=APIM, **overrides)
            assert rc == 1, (
                f"{step_name}/{label}: expected exit 1, got {rc}: {out[:400]}"
            )
            assert f"::error::{var} is empty" in out, (
                f"{step_name}/{label}: exited 1 without naming {var}, so the "
                f"refusal is indistinguishable from a broken harness: {out[:400]}"
            )
            assert "CALLED " not in out, (
                f"{step_name}/{label}: the callee ran anyway: {out[:400]}"
            )


def test_without_the_guard_the_empty_and_unset_blanks_are_already_loud():
    """The bound this lane measured, and it is NOT 601's (ledger L260).

    These two blanks reach `Split-Path`'s binder before the native call, so they
    fail there — the guard buys ATTRIBUTION on them, not safety. Recorded as a
    test so the next lane reads a measurement rather than a sibling's claim.
    """
    for label, overrides in (("empty", {"IN_CLIENTS_OUTPUT": ""}), ("unset", {})):
        body = _strip_guard(_body("Export clients"), "IN_CLIENTS_OUTPUT")
        out, _, rc = _run(body, WHMCS_API_URL=APIM, **overrides)
        assert rc != 0, f"unguarded {label} exited 0 — re-derive the bound: {out[:400]}"
        assert "Split-Path" in out, (
            f"unguarded {label} no longer fails at Split-Path, so the shape this "
            f"lane measured has changed: {out[:400]}"
        )
        assert "CALLED " not in out, (
            f"unguarded {label} reached the callee — that is the 601 shape, not "
            f"this one, and the guard's value here is larger than recorded: "
            f"{out[:400]}"
        )


def test_without_the_guard_the_whitespace_blank_reaches_the_callee():
    """The one blank the guard actually stops, and the one `-ne ''` would pass.

    This is the positive control for the test above: if all three blanks were
    already loud, the fail-closed guard would buy nothing but attribution and
    `-ne ''` would be as good as `IsNullOrWhiteSpace`. It is not.
    """
    body = _strip_guard(_body("Export clients"), "IN_CLIENTS_OUTPUT")
    out, _, _ = _run(body, WHMCS_API_URL=APIM, IN_CLIENTS_OUTPUT="   ")
    assert _bound(out) == "   ", (
        f"an all-spaces path no longer reaches the callee unguarded, so #1213's "
        f"distinction between `-ne ''` and IsNullOrWhiteSpace is not what makes "
        f"this guard necessary here — re-derive: {out[:400]}"
    )


def test_a_blank_optional_filter_is_accepted_and_omits_its_flag():
    """The other half of the L254 decision, measured rather than declared."""
    out, _, rc = _run(
        _body("Export transactions"),
        WHMCS_API_URL=APIM,
        IN_TRANSACTIONS_OUTPUT="artifacts/whmcs/whmcs_transactions.csv",
        IN_START_DATE="",
        IN_END_DATE="",
        IN_MAX_ROWS="",
    )
    assert rc == 0, f"a blank optional filter must not fail the step: {out[:400]}"
    assert _bound(out, "StartDate") == "", f"bound {_bound(out, 'StartDate')!r}"
    assert _bound(out, "MaxRows") == "", f"bound {_bound(out, 'MaxRows')!r}"
    assert _bound(out) == "artifacts/whmcs/whmcs_transactions.csv"


def test_a_supplied_optional_filter_still_reaches_the_callee():
    """…and the gated append still appends when there is something to append."""
    out, _, rc = _run(
        _body("Export transactions"),
        WHMCS_API_URL=APIM,
        IN_TRANSACTIONS_OUTPUT="artifacts/whmcs/whmcs_transactions.csv",
        IN_START_DATE="2026-01-01",
        IN_END_DATE="2026-06-30",
        IN_MAX_ROWS="1000",
    )
    assert rc == 0, f"exited {rc}: {out[:400]}"
    assert _bound(out, "StartDate") == "2026-01-01", f"bound {_bound(out, 'StartDate')!r}"
    assert _bound(out, "EndDate") == "2026-06-30", f"bound {_bound(out, 'EndDate')!r}"
    assert _bound(out, "MaxRows") == "1000", f"bound {_bound(out, 'MaxRows')!r}"


def test_a_payload_in_an_optional_filter_is_inert_too():
    """The gated-append inputs took the same remedy and get the same proof.

    They are the ones a lane is most likely to wave through, because the body
    already looked defensive about them — `.Trim()` and an emptiness test read as
    validation, and neither stops code that has already run.
    """
    out, stolen, _ = _run(
        _body("Export transactions"),
        WHMCS_API_URL=APIM,
        IN_TRANSACTIONS_OUTPUT="artifacts/whmcs/whmcs_transactions.csv",
        IN_START_DATE=_payload("2026-01-01"),
        **{CREDENTIAL_VAR: DECOY},
    )
    assert stolen is None, f"the payload executed from start_date: {stolen!r}"
    bound = _bound(out, "StartDate")
    assert bound is not None and bound.startswith("2026-01-01$("), (
        f"the payload should have reached the callee verbatim; bound {bound!r}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn pwsh. The wiring cases are pure YAML and must
# run on a host without it — a whole-module `shutil.which` gate turns "could not
# run" into "everything passed" (#1182).
NEEDS_PWSH = {
    "test_the_pre_fix_body_stole_the_credential_and_exited_zero",
    "test_the_shipped_body_binds_the_payload_as_inert_data",
    "test_an_ordinary_value_still_reaches_the_callee",
    "test_every_blank_form_is_refused_before_the_callee_runs",
    "test_without_the_guard_the_empty_and_unset_blanks_are_already_loud",
    "test_without_the_guard_the_whitespace_blank_reaches_the_callee",
    "test_a_blank_optional_filter_is_accepted_and_omits_its_flag",
    "test_a_supplied_optional_filter_still_reaches_the_callee",
    "test_a_payload_in_an_optional_filter_is_inert_too",
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
