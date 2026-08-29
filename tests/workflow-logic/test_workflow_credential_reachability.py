"""Tests for the credential-reachability resolver (#1188, extended by #1208).

The subject is the `credentials_in_reach` half of
`scripts/check-workflow-input-interpolation.py`. What it claims: for any step,
the credentials in its process environment and HOW each arrived — the step's own
`env:`, the job's `env:`, a `$GITHUB_ENV` write by an earlier step in the same
job (including one performed by a local composite action the job `uses:`), or
an authenticated CLI session an earlier step logged in and left on disk.

That fourth path is #1208, and it is the one with no variable anywhere. The
index was defined over env-var ARRIVAL, so on 101 it named the two `cloudflare`
steps and omitted both `m365` ones — while the `m365` job is the only reason 101
carried a write environment at all, and its bodies mint a Graph token by running
`az account get-access-token` one line below an `azure/login`. An absent line and
an empty line read identically to an operator and mean very different things.

Why the third path is the whole point. Both sweeps this repo recommends for
judging a #1080 call site read the workflow's declarative surface only — the
injecting step's `env:` (ledger L213) and a `secrets.` grep of the file (the
sweep used on #1141). Neither can see a credential an earlier step exported, and
the #1080 burn-down hit that three times (lanes 7/9/10 — `301`, `103`, `306`).
On `306` both sweeps answer "this workflow holds nothing", because the Graph
bearer token it mints into `GITHUB_ENV` is its ONLY credential.

Two failure modes this module is designed against, both seen in this repo:

* **Vacuous green.** A resolver whose extractor silently stops matching reports
  "no credential in reach" for every step and passes. Every shape gets a
  positive control, `test_the_real_tree_reaches_credentials` pins the tree-level
  result away from zero, and the `306` case is additionally proved by MUTATION —
  delete the export and the token must stop being reported.
* **A reading that flatters.** Over-reporting is the safe direction here, but
  "everything is a credential" would be as useless as "nothing is". So the
  negative controls are explicit: a dynamic name is never invented, a helper that
  does not write `GITHUB_ENV` is not a source, a later step is not in reach, and
  an ordinary `env:` remedy variable (`DOMAIN`) is not reported as a secret.

Run: python3 tests/workflow-logic/test_workflow_credential_reachability.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_cred_reach", CHECKER)
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file (wrong extension, or a directory). If it was renamed, "
        "update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _run_checker(*args: str) -> subprocess.CompletedProcess:
    """Run the checker as CI does, with BOTH ends of the pipe pinned to utf-8.

    Pinning only the parent's `encoding=` turns a mojibake'd line into a
    UnicodeDecodeError raised on subprocess's reader thread: the call still
    returns, `proc.stdout` is None, and the traceback blames this module's
    assertions rather than the console codepage (#962). The report this drives
    prints em-dashes, so that is not hypothetical here.
    """
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _workflow(name: str) -> dict:
    """A real workflow from the tree, parsed. Never a fixture stand-in."""
    path = guard.WORKFLOWS / name
    assert path.exists(), f"{name} is gone from .github/workflows — update this test"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _reach_at(workflow: dict, step_name: str) -> list:
    """Credentials in reach at the step called `step_name`, found by NAME.

    Looking the index up by name rather than hard-coding it means a step
    inserted above the one under test moves the answer instead of silently
    testing a different step.
    """
    for job_id, job in (workflow.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and step.get("name") == step_name:
                return guard.credentials_in_reach(workflow, job_id, index)
    raise AssertionError(f"no step named {step_name!r} in this workflow")


def _names(reaches) -> set[str]:
    return {r.name for r in reaches}


def _var_names(reaches) -> set[str]:
    """Only the credentials that arrive IN A VARIABLE.

    #1208 added a second kind of entry — a CLI session a login step left behind,
    which has no variable name at all. The env-var cases below assert an EXACT
    set, and they mean "these are the variables in reach", so they filter rather
    than widen: a test that accepted any extra entry would stop catching the
    over-report this resolver is most at risk of.
    """
    return {r.name for r in reaches if r.arrival != guard.Reach.ACQUIRED}


def _session_names(reaches) -> set[str]:
    return {r.name for r in reaches if r.arrival == guard.Reach.ACQUIRED}


# --- the export extractor, shape by shape -----------------------------------


def test_the_pwsh_out_file_export_is_seen():
    """`"NAME=$v" | Out-File … $env:GITHUB_ENV` — 302/303/305/306's form."""
    body = (
        "$token = (az account get-access-token --query accessToken -o tsv)\n"
        '"GRAPH_ACCESS_TOKEN=$token" | Out-File -FilePath $env:GITHUB_ENV -Append\n'
    )
    assert guard.exported_env_names(body) == {"GRAPH_ACCESS_TOKEN"}


def test_the_bash_brace_group_export_is_seen():
    """`{ echo "NAME<<$d"; … } >> "$GITHUB_ENV"` — 120/701/702/720's form.

    The `${d}` inside the block is the trap: a non-greedy `\\{(.*?)\\}` that did
    not require the redirect after the closing brace would end the match there
    and read the block as empty.
    """
    body = (
        'd="GHTOKEN_EOF_${RANDOM}"\n'
        "{\n"
        '  echo "GH_TOKEN<<${d}"\n'
        '  echo "$token"\n'
        '  echo "${d}"\n'
        '} >> "$GITHUB_ENV"\n'
    )
    assert guard.exported_env_names(body) == {"GH_TOKEN"}


def test_the_pwsh_heredoc_delimited_export_is_seen():
    """228's `"NAME<<DELIM" | Out-File …` opens GitHub's heredoc form."""
    body = (
        '"FRAUD_REVIEW_JSON<<FRAUD_EOF" | Out-File -FilePath $env:GITHUB_ENV -Append\n'
        "$json | Out-File -FilePath $env:GITHUB_ENV -Append\n"
        '"FRAUD_EOF" | Out-File -FilePath $env:GITHUB_ENV -Append\n'
    )
    found = guard.exported_env_names(body)
    # Exactly the variable, and NOT the delimiter. The line that opens the
    # heredoc is a complete write on its own; if it were also read as opening a
    # shell heredoc the scanner would swallow the rest of the step and invent
    # names from whatever followed. The closing `"FRAUD_EOF" | Out-File …` line
    # is what would leak: it looks like an assignment until you notice the name
    # is followed by a closing quote rather than `=` or `<<`.
    assert found == {"FRAUD_REVIEW_JSON"}, found


def test_the_shell_heredoc_export_is_seen():
    """`cat <<EOF >> "$GITHUB_ENV"` with bare NAME=value lines."""
    body = 'cat <<EOF >> "$GITHUB_ENV"\nAPI_TOKEN=$t\nNOT_A_SECRET=1\nEOF\n'
    assert guard.exported_env_names(body) == {"API_TOKEN", "NOT_A_SECRET"}


def test_the_helper_call_supplies_the_name():
    """The `*-from-kv` actions write through a helper whose `$Name` is a param.

    The literal name exists only at the CALL SITE, so a scanner reading the
    `Add-Content` line alone learns nothing.
    """
    body = (
        "function Add-EnvVar {\n"
        "  param([string]$Name, [string]$Value)\n"
        '  Add-Content -Path $env:GITHUB_ENV -Value "$Name<<$delim"\n'
        "  Add-Content -Path $env:GITHUB_ENV -Value $Value\n"
        "}\n"
        "Add-EnvVar -Name 'WHMCS_API_SECRET' -Value $secret\n"
        "Add-EnvVar -Name 'WHMCS_API_IDENTIFIER' -Value $identifier\n"
    )
    assert guard.exported_env_names(body) == {
        "WHMCS_API_SECRET",
        "WHMCS_API_IDENTIFIER",
    }


def test_a_dynamic_name_is_not_invented():
    """`-Value "$Name<<$delim"` must contribute NO name of its own.

    `$Name` is a parameter. A scanner that stripped the `$` and reported `Name`
    would put a variable called `Name` in reach of every downstream step of every
    workflow using these actions — noise that looks like signal.
    """
    body = (
        "function Add-EnvVar {\n"
        "  param([string]$Name, [string]$Value)\n"
        '  Add-Content -Path $env:GITHUB_ENV -Value "$Name<<$delim"\n'
        "}\n"
    )
    assert guard.exported_env_names(body) == set()


def test_a_helper_that_never_writes_github_env_is_not_a_source():
    """Polarity: resolution is per-function, not a sweep of every `-Name`.

    Without this the `Protect-Secret`/`Write-Log` helpers next to the real one
    would donate their arguments to the export set.
    """
    body = (
        "function Write-Log {\n"
        "  param([string]$Name)\n"
        '  Write-Host "handling $Name"\n'
        "}\n"
        "Write-Log -Name 'NOT_EXPORTED'\n"
        '"REAL_TOKEN=$t" | Out-File -FilePath $env:GITHUB_ENV -Append\n'
    )
    found = guard.exported_env_names(body)
    assert found == {"REAL_TOKEN"}, found


def test_a_body_that_never_mentions_github_env_exports_nothing():
    body = 'echo "TOKEN=$t" > /tmp/somewhere\npwsh -File ./x.ps1 -Zone $env:DOMAIN\n'
    assert guard.exported_env_names(body) == set()


# --- the credential classifier ----------------------------------------------


def test_a_secrets_reference_is_a_credential_whatever_its_name_is():
    """Name-blind on purpose: a secret mapped under an innocuous name counts."""
    assert guard.looks_like_credential("HARMLESS", "${{ secrets.CBM_TOKEN }}")
    assert guard.looks_like_credential("FFC_EXO_CERT_PFX_BASE64")
    assert guard.looks_like_credential("GH_TOKEN")
    assert guard.looks_like_credential("WHMCS_APIM_SUBSCRIPTION_KEY")


def test_an_ordinary_env_remedy_variable_is_not_a_credential():
    """`env:` is the #1080 REMEDY — reporting `DOMAIN` as a secret buries the signal."""
    assert not guard.looks_like_credential("DOMAIN", "${{ inputs.domain }}")
    assert not guard.looks_like_credential("IN_MAILBOXES", "${{ inputs.mailboxes }}")
    assert not guard.looks_like_credential("OUTPUT_FILE", "report.json")


# --- the four workflows #1188 was filed about (criterion 1) ------------------


def test_306_reaches_the_graph_token_through_github_env():
    """The motivating case: the token is the workflow's ONLY credential.

    Both recommended sweeps score `306` as holding nothing — its file contains no
    `secrets.` in a `run:` body and the injecting step's `env:` maps only the two
    dispatch inputs.
    """
    workflow = _workflow("306-discover-uncaptured-comms.yml")
    reached = _reach_at(workflow, "Discover uncaptured comms (PII masked)")
    assert _var_names(reached) == {"GRAPH_ACCESS_TOKEN"}, reached
    assert _session_names(reached) == {"az CLI session"}, (
        "the step that mints the token logs in with azure/login first, so the "
        f"session is in reach too (#1208): {[str(r) for r in reached]}"
    )
    token = reached[0]
    assert token.hidden, "a GITHUB_ENV arrival is the one neither sweep can see"
    assert token.arrival == guard.Reach.GITHUB_ENV
    assert token.source == "Acquire Microsoft Graph token", (
        "the exporting step must be NAMED — 'a credential is in reach somewhere "
        f"earlier' is not actionable, got {token.source!r}"
    )
    assert "GITHUB_ENV from step 'Acquire Microsoft Graph token'" in str(token)


def test_103_and_110_reach_the_cloudflare_tokens_through_a_composite_action():
    """Lane 9's surprise: the tokens are in no `env:` block in either file."""
    for name, step in (
        ("103-enforce-domain-standard.yml", "Enforce Cloudflare standard"),
        ("110-cloudflare-zone-create.yml", "Create zone"),
    ):
        reached = _reach_at(_workflow(name), step)
        assert _var_names(reached) == {
            "CLOUDFLARE_API_TOKEN_CM",
            "CLOUDFLARE_API_TOKEN_FFC",
        }, f"{name}: {reached}"
        assert _session_names(reached) == {"az CLI session"}, (
            f"{name}: the composite action logs into Azure before it reads Key "
            f"Vault, so its az session is in reach of the caller too (#1208) — "
            f"and that session is broader than the two tokens it exports"
        )
        assert all(r.hidden for r in reached), f"{name}: {reached}"
        assert all("cloudflare-tokens-from-kv" in (r.source or "") for r in reached), (
            f"{name}: the exporting step is the composite action, and naming it is "
            f"what makes the finding openable — got {[r.source for r in reached]}"
        )


def test_301_reaches_the_cloudflare_tokens_in_its_second_job():
    """Lane 7. The two jobs differ in ARRIVAL, which is the point of the field."""
    workflow = _workflow("301-m365-domain-preflight.yml")
    cloudflare = _reach_at(workflow, "Run Cloudflare audit portion")
    assert _var_names(cloudflare) == {"CLOUDFLARE_API_TOKEN_CM", "CLOUDFLARE_API_TOKEN_FFC"}
    assert _session_names(cloudflare) == {"az CLI session"}, cloudflare
    assert all(r.hidden for r in cloudflare), cloudflare


# --- criterion 2: a step-env credential is not relabelled -------------------


def test_a_step_env_credential_is_reported_as_step_env():
    """301's Graph token is mapped in the injecting step's OWN `env:`.

    Same credential NAME as 306's, arriving the other way. If the resolver
    reported both as `GITHUB_ENV` the field would be decoration.
    """
    reached = _reach_at(_workflow("301-m365-domain-preflight.yml"), "Run domain preflight")
    assert _var_names(reached) == {"GRAPH_ACCESS_TOKEN"}, reached
    token = next(r for r in reached if r.name == "GRAPH_ACCESS_TOKEN")
    assert token.arrival == guard.Reach.STEP_ENV, token.arrival
    assert not token.hidden
    assert token.source is None
    assert str(token) == "GRAPH_ACCESS_TOKEN (step env:)"
    assert "GITHUB_ENV" not in str(token)


def test_a_synthetic_step_env_credential_is_not_relabelled():
    """The same claim on a tree the burn-down cannot move out from under it."""
    workflow = yaml.safe_load(
        "on: {workflow_dispatch: {inputs: {x: {}}}}\n"
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - name: only\n"
        "        env:\n"
        "          GH_TOKEN: ${{ secrets.CBM_TOKEN }}\n"
        "        run: echo hi\n"
    )
    reached = guard.credentials_in_reach(workflow, "j", 0)
    assert [str(r) for r in reached] == ["GH_TOKEN (step env:)"], reached


# --- ordering and scope ------------------------------------------------------


def test_only_earlier_steps_are_in_reach():
    """A step's environment cannot contain what a LATER step exports.

    Reported as reachable, this would be the flattering direction: it inflates
    the blast radius of an early step and makes the whole reading untrustworthy.
    """
    workflow = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - name: first\n"
        "        run: echo nothing\n"
        "      - name: exporter\n"
        "        run: |\n"
        '          "API_TOKEN=$t" | Out-File -FilePath $env:GITHUB_ENV -Append\n'
        "      - name: last\n"
        "        run: echo hi\n"
    )
    assert guard.credentials_in_reach(workflow, "j", 0) == []
    assert guard.credentials_in_reach(workflow, "j", 1) == [], (
        "a step does not see its OWN export — GITHUB_ENV applies to LATER steps"
    )
    later = guard.credentials_in_reach(workflow, "j", 2)
    assert [str(r) for r in later] == ["API_TOKEN (GITHUB_ENV from step 'exporter')"]


def test_a_job_level_env_credential_is_in_reach_of_every_step():
    """303's shape: the Exchange Online cert is mapped on the JOB."""
    workflow = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    env:\n"
        "      EXO_CERT_PFX_PASSWORD: ${{ secrets.EXO_PW }}\n"
        "    steps:\n"
        "      - name: only\n"
        "        run: echo hi\n"
    )
    reached = guard.credentials_in_reach(workflow, "j", 0)
    assert [str(r) for r in reached] == ["EXO_CERT_PFX_PASSWORD (job env:)"], reached


def test_a_third_party_action_is_not_resolved():
    """`azure/login@v3` has no source in this tree; silence must not read as proof.

    The report says so in as many words. This pins the behaviour so a future
    change that starts guessing at third-party actions is a deliberate one.
    """
    assert guard.action_exported_env_names("azure/login@f5d393ae") == set()
    assert guard.action_exported_env_names("actions/checkout@v7.0.1") == set()


def test_a_local_composite_action_is_resolved_from_the_tree():
    """The WHMCS action's three exports are recovered from its own source."""
    found = guard.action_exported_env_names("./.github/actions/whmcs-secrets-from-kv")
    assert {"WHMCS_API_IDENTIFIER", "WHMCS_API_SECRET"} <= found, found
    assert "WHMCS_APIM_SUBSCRIPTION_KEY" in found, found


# --- the acquired arrival (#1208), in both directions -----------------------
#
# The three cases above index a credential the step was HANDED, in a variable.
# #1208 is the credential a step ACQUIRES: an OIDC login leaves an authenticated
# CLI session on disk, no variable exists anywhere, and every sweep defined over
# variables therefore reported the step as empty — on 101 that was both sites of
# the `m365-prod` job, the only reason the workflow was a `[W]` entry at all.
#
# Over-reporting is the safe direction here, so the negative controls carry the
# weight: an ordinary action is not a login, a login is not in reach of a step
# ABOVE it, and a job with neither a login nor a variable must still print
# nothing — otherwise "nothing in reach" stops meaning anything.


def test_101s_m365_sites_reach_the_session_the_azure_login_left():
    """#1208 criterion 1, on the real tree and at the real call sites.

    Both `m365` steps sit one line above `az account get-access-token` and map
    only `IN_DOMAIN` into their `env:`. Before this landed the index named
    neither.
    """
    workflow = _workflow("101-domain-status.yml")
    for step_name in (
        "M365 domain status (Graph summary)",
        "Run repo M365 preflight (read-only)",
    ):
        reached = _reach_at(workflow, step_name)
        assert [str(r) for r in reached] == [
            "az CLI session (acquired by step 'Azure login (OIDC)'; no variable in reach)"
        ], f"{step_name}: {[str(r) for r in reached]}"
        assert all(r.arrival == guard.Reach.ACQUIRED for r in reached)


def test_the_acquired_arrival_is_hidden_from_both_recommended_sweeps():
    """`hidden` is what the report counts, and an acquired session belongs in it.

    It is the more thorough of the two hidden paths: a GITHUB_ENV arrival at
    least has a NAME somewhere in the tree for a grep to find, and this has no
    textual trace at the call site at all.
    """
    acquired = guard.Reach("az CLI session", guard.Reach.ACQUIRED, "Azure login (OIDC)")
    assert acquired.hidden
    assert not guard.Reach("GH_TOKEN", guard.Reach.STEP_ENV).hidden
    assert not guard.Reach("GH_TOKEN", guard.Reach.JOB_ENV).hidden


def test_a_run_body_that_logs_in_is_a_source():
    """The same acquisition performed by a `run:` rather than an action."""
    workflow = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - name: login\n"
        "        run: |\n"
        "          az login --service-principal -u $APP_ID --federated-token $T\n"
        "          gh auth login --with-token < token.txt\n"
        "      - name: victim\n"
        "        run: echo hi\n"
    )
    assert _names(guard.credentials_in_reach(workflow, "j", 1)) == {
        "az CLI session",
        "gh CLI session",
    }


def test_a_job_with_no_login_and_no_variable_still_reports_nothing():
    """#1208 criterion 3 — the distinction has to stay meaningful.

    If every step grew a line, "nothing in reach" would stop being a reading and
    the report would be noise. This is the negative control that keeps the
    over-report side of the trade bounded.
    """
    workflow = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7.0.1\n"
        "      - name: build\n"
        "        run: npm run build\n"
        "      - name: upload\n"
        "        uses: actions/upload-artifact@v4\n"
    )
    for index in range(3):
        assert guard.credentials_in_reach(workflow, "j", index) == [], index


def test_an_ordinary_action_is_not_read_as_a_login():
    """The `uses:` classifier must discriminate, not match everything."""
    assert guard.uses_acquired_sessions("actions/checkout@v7.0.1") == set()
    assert guard.uses_acquired_sessions("actions/upload-artifact@v4") == set()
    assert guard.uses_acquired_sessions("./.github/actions/candid-keys-from-kv") == set(), (
        "a LOCAL action is resolved from its source by action_acquired_sessions, "
        "not matched by name — this classifier is the third-party half"
    )
    assert guard.uses_acquired_sessions("azure/login@f5d393ae") == {"az CLI session"}
    assert guard.uses_acquired_sessions("docker/login-action@v3") == {
        "docker registry session"
    }


def test_a_login_below_a_step_is_not_in_its_reach():
    """Same ordering rule as GITHUB_ENV, and the same flattering failure.

    Reporting a later login would inflate the blast radius of an early step —
    exactly the direction that makes a reading untrustworthy.
    """
    workflow = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - name: early\n"
        "        run: echo hi\n"
        "      - name: login\n"
        "        uses: azure/login@f5d393ae\n"
        "      - name: late\n"
        "        run: az account get-access-token\n"
    )
    assert guard.credentials_in_reach(workflow, "j", 0) == []
    assert guard.credentials_in_reach(workflow, "j", 1) == [], (
        "a step does not see its own login — the session exists after it runs"
    )
    assert _names(guard.credentials_in_reach(workflow, "j", 2)) == {"az CLI session"}


def test_a_local_from_kv_action_leaves_an_az_session_in_the_callers_job():
    """Not a hypothetical extension of the direct case — it is the common one.

    All five `*-from-kv` actions open with `azure/login@…`, and a composite
    action's steps run in the CALLER's job. So the session is in reach of every
    later step, and it is strictly broader than the two or three names the
    action exports: it can read anything that identity may `az keyvault secret
    show`.
    """
    for action in (
        "./.github/actions/cloudflare-tokens-from-kv",
        "./.github/actions/whmcs-secrets-from-kv",
    ):
        assert guard.action_acquired_sessions(action) == {"az CLI session"}, action


# --- mutation: the resolver must be READING the export, not asserting it ----


def test_deleting_306s_export_stops_the_token_being_reported():
    """#1188's required mutation, on a COPY — never the tracked file (L182).

    A resolver that reported the Graph token either way would pass every test
    above while reading nothing.
    """
    path = guard.WORKFLOWS / "306-discover-uncaptured-comms.yml"
    raw = path.read_text(encoding="utf-8")
    anchor = '"GRAPH_ACCESS_TOKEN=$token" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8'
    # Assert the anchor BEFORE substituting: a mutation that silently fails to
    # apply produces a green run that reads as "the resolver has a hole" (L96).
    assert raw.count(anchor) == 1, (
        f"the export line in {path.name} moved — this mutation would test nothing. "
        "Re-anchor it on the current token-minting step."
    )
    with tempfile.TemporaryDirectory() as tmp:
        mutant_path = pathlib.Path(tmp) / path.name
        mutant_path.write_text(raw.replace(anchor, "Write-Host 'no export'"), encoding="utf-8")
        mutant = yaml.safe_load(mutant_path.read_text(encoding="utf-8"))
        # The mutant must still be a workflow: a parse failure exits every reader
        # non-zero and would score as a clean detection (L203).
        assert isinstance(mutant, dict) and mutant.get("jobs"), "the mutant is not a workflow"
        reached = _reach_at(mutant, "Discover uncaptured comms (PII masked)")
    assert _var_names(reached) == set(), (
        "with the export deleted the Graph token must NOT be reported — the "
        f"resolver is not reading the export, it is asserting it. Got {reached}"
    )
    # The az session survives the mutation and SHOULD: deleting the export does
    # not delete the login above it, and the step can still mint a fresh token
    # by hand. That is #1208's whole point, and it is why this assertion is
    # scoped to the variable rather than to the list being empty — a step with
    # its export removed is not a step holding nothing.
    assert _session_names(reached) == {"az CLI session"}, reached
    # …and the unmutated tree still reports it, so the delta is the mutation and
    # not a resolver that has stopped working altogether.
    assert _var_names(
        _reach_at(_workflow(path.name), "Discover uncaptured comms (PII masked)")
    ) == {"GRAPH_ACCESS_TOKEN"}


def test_deleting_101s_azure_login_stops_the_session_being_reported():
    """The #1208 mutation, on a COPY — never the tracked file (L182).

    A resolver that named the session either way would pass every case above
    while reading nothing: `az account get-access-token` is still in both
    bodies, so a checker that matched on the CONSUMER rather than the LOGIN
    would look identical until the login is removed.
    """
    path = guard.WORKFLOWS / "101-domain-status.yml"
    raw = path.read_text(encoding="utf-8")
    anchor = "uses: azure/login@"
    # Assert the anchor BEFORE substituting: a mutation that silently fails to
    # apply produces a green run that reads as "the resolver has a hole" (L96).
    assert raw.count(anchor) == 1, (
        f"{path.name} no longer performs exactly one direct azure/login — this "
        f"mutation would test something other than what it names ({raw.count(anchor)} found)"
    )
    with tempfile.TemporaryDirectory() as tmp:
        mutant_path = pathlib.Path(tmp) / path.name
        mutant_path.write_text(
            raw.replace(anchor, "uses: actions/checkout@"), encoding="utf-8", newline=""
        )
        mutant = yaml.safe_load(mutant_path.read_text(encoding="utf-8"))
        # The mutant must still be a workflow: a parse failure exits every reader
        # non-zero and would score as a clean detection (L203).
        assert isinstance(mutant, dict) and mutant.get("jobs"), "the mutant is not a workflow"
        reached = _reach_at(mutant, "M365 domain status (Graph summary)")
    assert reached == [], (
        "with the login removed the session must NOT be reported — the resolver "
        f"is matching the CONSUMER, not the acquisition. Got {[str(r) for r in reached]}"
    )
    # …and the unmutated tree still reports it, so the delta is the mutation.
    assert _names(_reach_at(_workflow(path.name), "M365 domain status (Graph summary)")) == {
        "az CLI session"
    }


# --- the report is additive (criterion 4) -----------------------------------


def test_the_real_tree_reaches_credentials():
    """Vacuity guard: an extractor that stops matching must not read as clean."""
    findings, unreadable, scanned = guard.scan_all()
    assert not unreadable and scanned > 50, (scanned, unreadable)
    sites = guard.reachability_by_site(findings)
    assert len(sites) > 10, f"only {len(sites)} sites reach a credential — check the extractor"
    hidden = [k for k, v in sites.items() if any(r.hidden for r in v)]
    assert len(hidden) > 10, (
        f"only {len(hidden)} sites reach a credential through GITHUB_ENV, which is "
        "the path this whole resolver exists to surface"
    )


def test_the_report_neither_creates_nor_clears_a_finding():
    """Criterion 4: reachability is information ABOUT findings, never a verdict."""
    findings, _unreadable, _scanned = guard.scan_all()
    before = guard.current_map(findings)
    paragraph = guard.reachability_paragraph(findings, ["101-domain-status.yml"])
    after = guard.current_map(guard.scan_all()[0])
    assert before == after, "building the report changed the finding set"
    assert guard.compare(after) == ([], []), (
        "this tree must be exactly the frozen set — if it is not, the failure is "
        "#1080's, not this module's"
    )
    assert "credential reachability (#1188)" in paragraph


def test_the_guard_still_exits_zero_and_prints_the_frozen_counts():
    """End to end, as CI runs it."""
    proc = _run_checker()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "workflow input interpolation OK:" in proc.stdout
    assert "credential reachability (#1188):" in proc.stdout
    assert "GITHUB_ENV from step" in proc.stdout, (
        "the arrival path must reach the operator, not just the library"
    )


def test_the_reachability_mode_answers_for_a_burned_down_workflow():
    """The report can only speak about FROZEN sites, and 306 is no longer one.

    Every workflow #1188 names as motivation had already been burned down when it
    was filed, so the mode that answers for an arbitrary workflow is what makes
    the mechanism usable by the next lane's reviewer.
    """
    proc = _run_checker("--reachability", "306-discover-uncaptured-comms.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "GRAPH_ACCESS_TOKEN (GITHUB_ENV from step 'Acquire Microsoft Graph token')" in proc.stdout
    assert "306-discover-uncaptured-comms.yml" not in guard.current_map(guard.scan_all()[0]), (
        "306 is back in the freeze — this test's premise (it is burned down) is gone"
    )


def test_the_reachability_mode_names_101s_gated_sites_to_an_operator():
    """#1208 criterion 1 as the operator meets it, not as the library reports it.

    101 is burned down, so it has no frozen finding and the report paragraph
    cannot speak about it at all — `--reachability <file>` is the ONLY surface
    that answers for it, and it is the surface the next lane will read.
    """
    proc = _run_checker("--reachability", "101-domain-status.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "job 'm365' step 'M365 domain status (Graph summary)'" in proc.stdout
    assert "job 'm365' step 'Run repo M365 preflight (read-only)'" in proc.stdout
    assert (
        "az CLI session (acquired by step 'Azure login (OIDC)'; no variable in reach)"
        in proc.stdout
    ), proc.stdout
    assert "101-domain-status.yml" not in guard.current_map(guard.scan_all()[0]), (
        "101 is back in the freeze — this test's premise (only --reachability can "
        "answer for it) is gone"
    )


def test_an_unknown_argument_is_a_usage_error_not_a_silent_pass():
    """A typo'd flag must not look like a clean run (exit 0 is a verdict here)."""
    proc = _run_checker("--reachabilty")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stdout

    missing = _run_checker("--reachability", "999-not-a-workflow.yml")
    assert missing.returncode == 1, missing.stdout + missing.stderr


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
