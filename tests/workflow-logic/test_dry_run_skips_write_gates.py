"""Guard: a dry run must not mint a write credential or park on a write gate (#983).

`dry_run` was enforced at the **step** level in 701/702/720 and never at the
job level, so a `dry_run=true` dispatch still started the job, still parked the
run on a reviewer-gated write environment, and still pulled a write-scoped
credential out of Key Vault — and only *then* did the individual steps decide
to do nothing. Three costs, in the order they bite:

1. **The dry path is unverifiable unattended.** A rehearsal that needs the same
   human approval as the live run is not a rehearsal. Run 30708948816 (701,
   `dry_run=true`) got as far as `dns` waiting on `cloudflare-prod-write` and
   had to be cancelled — which is why this check had been owed since #858 and
   deferred for four Conductor runs.
2. **Dry runs pollute the gate queue** with rows indistinguishable at a glance
   from a real pending write.
3. **A write credential is materialised for a run that writes nothing.**

## The two legitimate remedies — skipping is only one of them

`111-dns-create-redirect-rule.yml` is the reference: an **ungated `preview`
job** does the rehearsal and the gated `apply` job carries
`if: inputs.dry_run == false`. The invariant this module enforces is the
credential one — *no write-scoped credential on a dry run* — and a workflow can
satisfy it two ways:

- **Skip the gated job**, when some *other, ungated* job already performs the
  rehearsal (701's `zone_check`, 720's `preflight`).
- **Move the rehearsal out** of the gated job into an ungated one, then skip
  the gated remainder (111's shape).

The distinction matters, and getting it backwards is destructive rather than
merely useless. Two workflows in this repo prove it:

- **702's `clone-deploy`** *is* its own rehearsal — a dry run clones the live
  site with httrack, integrates it into the Next.js app and runs a real
  `next build` plus the self-containment gate, withholding only the commit and
  PR. Its `dry_run` defaults to **true**, so blindly adding a skip term would
  make the workflow's default dispatch a green no-op. It needs the 111 split
  (ungated clone/build, gated publish), which is a restructure, not a one-line
  `if:`.
- **736's `archive`** is worse: its dry run *is the required safety evidence*.
  `736`'s preflight refuses a live archive unless a successful `dry_run=true`
  run of 736 for the same repo exists within 48h (2026-07-18 incident). Skipping
  the gated job on a dry run would still leave a green run for the preflight to
  find while producing none of the preview an operator is told to review —
  converting a two-step safety control into a rubber stamp.

So this module does NOT assert "every write-credentialed job is skipped on a
dry run". It asserts that the set which is, and the set which is not, are both
**exactly enumerated** — the repo's `DISPATCH_ONLY_GATED_READS` convention, for
the same reason: a count is not evidence, the list is.

## Scope discovered while fixing #983

#983's table named five jobs, derived from the three workflows a Conductor run
happened to dispatch. Parsing every workflow instead — which is what acceptance
criterion 4 asks for, so "a seventh copy is caught" — the real population is
**27 credential-bearing jobs across 23 workflows**. The four fixed here are the
ones #983 scoped; the rest are enumerated in `KNOWN_UNGUARDED` below and tracked
as follow-up work, so the number is visible instead of implied.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

# Environments configured with required reviewers — mirrors GATED_ENVS in
# test_gated_env_hygiene.py and scripts/check-workflow-doc-consistency.py.
# test_gated_envs_match_the_hygiene_module below fails if they drift.
GATED_ENVS = {
    "cloudflare-prod",
    "cloudflare-prod-write",
    "github-prod",
    "google-prod-write",
    "m365-prod",
    "whmcs-prod",
    "wpmudev-prod",
}

# Key Vault names on the WRITER identity. The repo's naming split is
# `wr-all-*` (writer) vs `read-all-*` (reader) — see AGENTS.md §Safety model.
WRITE_KV_SECRET = re.compile(r"wr-all-[a-z0-9-]+")

# Composite actions that fetch a credential from Key Vault. Every one takes a
# `scope:` input; the ones that default to `write` are the trap, because a step
# that simply omits `scope:` gets a writer credential silently.
KV_CREDENTIAL_ACTIONS = (
    "cloudflare-tokens-from-kv",
    "whmcs-secrets-from-kv",
    "zeffy-secrets-from-kv",
    "google-secrets-from-kv",
    "candid-keys-from-kv",
)

# Jobs fixed by #983: a dry dispatch must not reach them.
#
# Each is paired with the ungated job that keeps doing the rehearsal, because
# "what still rehearses?" is the question that decides whether a skip is safe —
# and it is the question the 702/736 entries in KNOWN_UNGUARDED answer with
# "nothing, the rehearsal is inside this job".
MUST_SKIP_ON_DRY_RUN = {
    ("701-website-provision.yml", "dns"): "zone_check (cloudflare-prod-read)",
    ("701-website-provision.yml", "repo"): "zone_check (cloudflare-prod-read)",
    ("701-website-provision.yml", "content"): "zone_check (cloudflare-prod-read)",
    ("720-create-repo.yml", "create-repo"): "preflight (ungated)",
}

# Already correct before #983 — the pattern was established, just not applied
# consistently. Listed so that un-guarding one of them fails this module.
ALREADY_GUARDED = {
    ("103-enforce-domain-standard.yml", "cloudflare_set_dkim"),
    ("111-dns-create-redirect-rule.yml", "apply"),
    ("120-bulk-cutover-to-github-pages.yml", "post-cutover-smoke"),
    ("122-cloudflare-zone-member-add.yml", "apply"),
    ("742-fleet-security-audit-backfill.yml", "apply"),
}

# Credential-bearing jobs in dry-run-capable workflows that a dry run STILL
# reaches. Enumerated debt, not an approval: every line is a workflow whose
# `dry_run=true` dispatch parks on a gate and mints a writer credential.
#
# The two marked REHEARSAL-INSIDE must not be "fixed" by adding a skip term —
# see the module docstring. The rest are mechanical, and are follow-up work
# rather than #983's scope only because #983's table was written from three
# workflows and the real population is 23.
KNOWN_UNGUARDED = {
    ("103-enforce-domain-standard.yml", "cloudflare_enforce"),
    ("105-manage-record.yml", "add-test-record"),
    ("106-enforce-standard.yml", "enforce_standard"),
    ("112-dns-bulk-replace-a-ip.yml", "bulk-replace"),
    ("118-whmcs-domain-lock.yml", "lock"),
    ("119-bulk-staging-cname-github-pages.yml", "bulk-staging-cname"),
    ("120-bulk-cutover-to-github-pages.yml", "cname-flip"),
    ("120-bulk-cutover-to-github-pages.yml", "dns-flip"),
    ("204-whmcs-charity-onboard.yml", "onboard"),
    ("205-whmcs-ticket-open.yml", "open_ticket"),
    ("207-whmcs-ticket-respond.yml", "ticket_respond"),
    ("211-whmcs-order-update.yml", "order_update"),
    ("212-whmcs-product-add.yml", "product_add"),
    ("222-whmcs-product-alignment.yml", "align"),
    ("223-whmcs-import-cloudflare-domains.yml", "import"),
    ("224-whmcs-github-pages-product-alignment.yml", "align"),
    ("230-whmcs-record-field-set.yml", "record_field_set"),
    ("503-google-gtm-provision.yml", "provision"),
    ("505-google-ga-property-provision.yml", "provision"),
    # REHEARSAL-INSIDE: the dry run IS the clone + build. Needs the 111 split.
    ("702-ffc-ex-clone-deploy.yml", "clone-deploy"),
    ("704-website-analytics-wire.yml", "wire"),
    ("729-repo-add-collaborator.yml", "add-collaborator"),
    # REHEARSAL-INSIDE: the dry run is the preview that the LIVE run's preflight
    # requires as evidence within 48h. Skipping it defeats the control.
    ("736-repo-archive.yml", "archive"),
}

# Jobs where skipping on a dry run would be actively wrong, with the reason.
# Pinned separately from KNOWN_UNGUARDED so that a future sweep converting the
# mechanical entries cannot quietly take these two with it.
REHEARSAL_INSIDE_THE_GATED_JOB = {
    ("702-ffc-ex-clone-deploy.yml", "clone-deploy"): (
        "the dry run performs the httrack clone, the Next.js integration and a "
        "real build; dry_run defaults to true, so a skip term makes the default "
        "dispatch a no-op. Needs 111's preview/apply split."
    ),
    ("736-repo-archive.yml", "archive"): (
        "736's preflight requires a successful dry_run=true run within 48h as "
        "evidence before a live archive. A skipped job still leaves a green run "
        "to find, but produces none of the preview the operator must review."
    ),
}


def _workflows():
    """(name, parsed-yaml) for every workflow file, parsed once."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if isinstance(wf, dict):
            out.append((path.name, wf))
    return out


def _triggers(wf: dict) -> dict:
    # PyYAML resolves a bare `on:` key to the boolean True (YAML 1.1).
    on = wf.get("on", wf.get(True))
    if isinstance(on, dict):
        return on
    if isinstance(on, list):
        return {k: None for k in on}
    return {str(on): None} if on else {}


def _dry_run_input(wf: dict) -> str | None:
    """The workflow's dry-run input, spelled as its author spelled it.

    701/702 use `dry_run`; **720 uses `DryRun`**. Matching case-insensitively
    (and tolerating a hyphen) is what stops this guard from silently passing a
    workflow whose input it simply failed to recognise.
    """
    for trigger in ("workflow_dispatch", "workflow_call"):
        spec = _triggers(wf).get(trigger)
        if not isinstance(spec, dict):
            continue
        for name in spec.get("inputs") or {}:
            if str(name).lower().replace("-", "_") in ("dry_run", "dryrun"):
                return str(name)
    return None


def _job_environments(job: dict) -> set[str]:
    env = job.get("environment")
    if env is None:
        return set()
    if isinstance(env, dict):
        name = env.get("name")
        return {name} if isinstance(name, str) else set()
    return {str(env)}


def _write_credentials(job: dict) -> list[str]:
    """Every write-scoped credential this job materialises, as evidence strings."""
    found = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue

        uses = str(step.get("uses") or "")
        for action in KV_CREDENTIAL_ACTIONS:
            if action in uses:
                # `scope:` omitted means write for these actions — that default
                # is why a step can hold a writer credential without saying so.
                scope = str((step.get("with") or {}).get("scope") or "write").strip().lower()
                if scope != "read":
                    found.append(f"{action} (scope={scope})")

        for secret in sorted(set(WRITE_KV_SECRET.findall(str(step.get("run") or "")))):
            found.append(f"Key Vault {secret}")
    return found


def _has_dry_run_term(job: dict, input_name: str) -> bool:
    """Does this job's `if:` consult the dry-run input at JOB level?

    Both spellings in the repo count: `== false` (111, 122, 742) and `!= true`
    (120, and the #983 fixes). What does NOT count is the input appearing only
    inside a step's `if:` — that is precisely the defect.
    """
    condition = str(job.get("if") or "")
    return bool(re.search(rf"inputs\.{re.escape(input_name)}\b", condition, re.IGNORECASE))


def _credential_jobs():
    """(workflow, job_id, dry-input, guarded?, credentials) for the whole repo.

    Derived by parsing every workflow — never a hard-coded list of five — so a
    seventh copy of the pattern is caught the day it lands (#983 AC 4).
    """
    rows = []
    for name, wf in _workflows():
        dry_input = _dry_run_input(wf)
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            creds = _write_credentials(job)
            if not creds:
                continue
            rows.append(
                (
                    name,
                    job_id,
                    dry_input,
                    bool(dry_input) and _has_dry_run_term(job, dry_input),
                    creds,
                )
            )
    return rows


# --- the detector must actually detect --------------------------------------


def test_detector_finds_the_known_credential_jobs():
    """A guard whose detector matches nothing passes vacuously.

    Pin both halves: the jobs #983 measured by hand are found, and a job with
    no credential is not flagged. Without this, a rename of the composite
    action (or of the `wr-all-` prefix) would silently empty every assertion
    below and this module would go green while enforcing nothing.
    """
    found = {(name, job_id) for name, job_id, _, _, _ in _credential_jobs()}

    for expected in [
        ("701-website-provision.yml", "dns"),
        ("701-website-provision.yml", "repo"),
        ("701-website-provision.yml", "content"),
        ("702-ffc-ex-clone-deploy.yml", "clone-deploy"),
        ("720-create-repo.yml", "create-repo"),
        ("111-dns-create-redirect-rule.yml", "apply"),
    ]:
        assert expected in found, (
            f"{expected} loads a write credential but the detector missed it — "
            "KV_CREDENTIAL_ACTIONS / WRITE_KV_SECRET no longer match how this "
            "repo fetches credentials, so every assertion here is vacuous."
        )

    # The read lane must NOT be flagged: 701's zone_check passes `scope: read`,
    # which is the whole distinction this module is built on.
    assert ("701-website-provision.yml", "zone_check") not in found, (
        "701's zone_check uses scope: read and must not count as a write "
        "credential — otherwise the guard would demand skipping the very job "
        "that keeps the dry-run rehearsal alive."
    )


def test_scope_read_and_write_are_distinguished():
    """Pin the scope parsing itself, including the write-by-omission default."""
    read_step = {"uses": "./.github/actions/cloudflare-tokens-from-kv", "with": {"scope": "read"}}
    write_step = {"uses": "./.github/actions/cloudflare-tokens-from-kv", "with": {"scope": "write"}}
    default_step = {"uses": "./.github/actions/cloudflare-tokens-from-kv"}

    assert _write_credentials({"steps": [read_step]}) == []
    assert _write_credentials({"steps": [write_step]})
    assert _write_credentials({"steps": [default_step]}), (
        "a step that omits `scope:` gets a WRITER credential from these actions; "
        "treating the omission as read would hide exactly the silent case"
    )
    assert _write_credentials({"steps": [{"run": "az keyvault secret show --name wr-all-x"}]})
    assert _write_credentials({"steps": [{"run": "az keyvault secret show --name read-all-x"}]}) == []


# --- the fix ----------------------------------------------------------------


def test_983_jobs_are_skipped_on_a_dry_run():
    """The #983 fix: these four jobs must consult dry_run at JOB level."""
    by_job = {(n, j): (d, g) for n, j, d, g, _ in _credential_jobs()}

    violations = []
    for (name, job_id), rehearses in MUST_SKIP_ON_DRY_RUN.items():
        entry = by_job.get((name, job_id))
        assert entry, f"{name}: job '{job_id}' no longer loads a write credential — re-check #983"
        dry_input, guarded = entry
        if not guarded:
            violations.append(
                f"{name}: job '{job_id}' loads a write-scoped credential and runs "
                f"on {sorted(_job_environments(_job(name, job_id)))}, but its `if:` "
                f"never consults `inputs.{dry_input}` — so a dry run still parks on "
                f"the gate and still mints the credential (#983). The rehearsal is "
                f"preserved by {rehearses}."
            )
    assert not violations, "\n".join(violations)


def test_multi_trigger_workflows_use_not_true_rather_than_equals_false():
    """The trap that makes 111's exact expression wrong for 701.

    `inputs` is empty for any event that is not `workflow_dispatch` /
    `workflow_call`. 111 is dispatch-only, so `inputs.dry_run == false` is safe
    there. 701 also runs on `issues` and `repository_dispatch`, where that
    expression evaluates FALSE — it would skip the gated jobs on a REAL,
    issue-triggered provisioning run, i.e. silently break provisioning while
    looking like a security fix. `!= true` is correct for both shapes.
    """
    violations = []
    for name, wf in _workflows():
        dry_input = _dry_run_input(wf)
        if not dry_input:
            continue
        manual_only = set(_triggers(wf)) <= {"workflow_dispatch", "workflow_call"}
        if manual_only:
            continue
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            condition = str(job.get("if") or "")
            if re.search(rf"inputs\.{re.escape(dry_input)}\s*==\s*false", condition, re.IGNORECASE):
                violations.append(
                    f"{name}: job '{job_id}' uses `inputs.{dry_input} == false`, but "
                    f"this workflow also triggers on {sorted(set(_triggers(wf)) - {'workflow_dispatch', 'workflow_call'})}, "
                    "where the `inputs` context is empty and that expression is "
                    "FALSE — the job would be skipped on a real run. Use "
                    f"`inputs.{dry_input} != true`."
                )
    assert not violations, "\n".join(violations)


def test_a_skipped_gated_job_cannot_turn_a_dry_run_red():
    """A dry run must reach a terminal conclusion, not fail on the skip.

    A dependent job is skipped automatically when its dependency is — except
    under `always()`, where it runs anyway and has to tolerate the skip
    explicitly. 701's `repo`, `verify` and `finalize` all carry `always()`, so
    this is the shape that actually needs checking.
    """
    skipped_jobs = {}
    for name, job_id in MUST_SKIP_ON_DRY_RUN:
        skipped_jobs.setdefault(name, set()).add(job_id)

    violations = []
    for name, guarded_ids in skipped_jobs.items():
        wf = dict(_workflows())[name]
        dry_input = _dry_run_input(wf)
        for other_id, other in (wf.get("jobs") or {}).items():
            if other_id in guarded_ids:
                continue
            needs = other.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            depends_on = guarded_ids & set(needs)
            if not depends_on:
                continue
            condition = str(other.get("if") or "")
            if "always()" not in condition:
                continue  # skipped by dependency — cannot go red

            # Under always() the job runs even when a dependency was skipped, so
            # it must decide for itself. Consulting the `.result` of ANY guarded
            # dependency is enough: 701's `finalize` needs dns/repo/content and
            # requires `needs.repo.result == 'success'`, which is false when repo
            # is skipped — so finalize skips too, and never runs on empty outputs.
            tolerates = any(
                re.search(rf"needs\.{re.escape(dep)}\.result", condition) for dep in depends_on
            ) or re.search(rf"inputs\.{re.escape(dry_input or 'dry_run')}\b", condition, re.I)

            if not tolerates:
                violations.append(
                    f"{name}: job '{other_id}' runs under `always()` and needs "
                    f"{sorted(depends_on)}, but consults neither their `.result` "
                    f"nor `inputs.{dry_input}` — when those are skipped on a dry "
                    "run it will run with empty outputs and can fail the run."
                )
    assert not violations, "\n".join(violations)


# --- the debt stays enumerated ----------------------------------------------


def test_unguarded_credential_jobs_stay_exactly_enumerated():
    """Every write credential a dry run can still mint is on the list.

    Both directions fail. A NEW unguarded job is new debt nobody decided to
    take on — including a seventh copy-paste of the 701 shape, which is what
    #983 AC 4 asks this module to catch. A REMOVED one means somebody fixed it
    and left a stale line, and the list stops meaning anything.
    """
    actual = {
        (name, job_id)
        for name, job_id, dry_input, guarded, _ in _credential_jobs()
        if dry_input and not guarded
    }

    added = sorted(actual - KNOWN_UNGUARDED)
    removed = sorted(KNOWN_UNGUARDED - actual)

    problems = []
    if added:
        problems.append(
            "NEW jobs that mint a write-scoped credential on a dry run — a dry "
            "dispatch of these parks on a reviewer gate and materialises a "
            "writer credential before any step decides to do nothing (#983). "
            "Either add the job-level dry-run term, or record the decision "
            f"here with the reason: {added}"
        )
    if removed:
        problems.append(
            "These are no longer unguarded — delete them from KNOWN_UNGUARDED "
            f"(and add them to MUST_SKIP_ON_DRY_RUN if they are now skipped): {removed}"
        )
    assert not problems, "\n".join(problems)


def test_already_guarded_jobs_stay_guarded():
    """The five that were correct before #983 must not regress."""
    by_job = {(n, j): g for n, j, _, g, _ in _credential_jobs()}
    regressed = sorted(job for job in ALREADY_GUARDED if not by_job.get(job))
    assert not regressed, (
        f"these jobs consulted dry_run at job level before #983 and no longer do: {regressed}"
    )


def test_rehearsal_inside_jobs_are_not_skipped():
    """The counter-example, pinned so a future sweep cannot 'fix' it.

    "Add a dry-run term to every gated job" is wrong for these two, and wrong in
    the destructive direction — see the module docstring. This test fails if
    someone adds the skip, which is the point: the fix for them is the 111
    split, and it must be a deliberate change rather than a sweep.
    """
    by_job = {(n, j): g for n, j, _, g, _ in _credential_jobs()}
    for job, reason in REHEARSAL_INSIDE_THE_GATED_JOB.items():
        assert job in KNOWN_UNGUARDED, f"{job} must stay enumerated as debt"
        assert not by_job.get(job, False), (
            f"{job} was given a job-level dry-run skip, but {reason} Restructure "
            "it the way 111 does — an ungated rehearsal job plus a gated write "
            "job — rather than skipping the rehearsal."
        )


def test_gated_envs_match_the_hygiene_module():
    """This module and test_gated_env_hygiene.py must agree on what gates."""
    hygiene = (pathlib.Path(__file__).parent / "test_gated_env_hygiene.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r"GATED_ENVS\s*=\s*\{(.*?)\}", hygiene, re.S)
    assert m, "GATED_ENVS not found in test_gated_env_hygiene.py"
    theirs = set(re.findall(r"['\"]([a-z0-9-]+)['\"]", m.group(1)))
    assert theirs == GATED_ENVS, (
        "gated-environment lists drifted:\n"
        f"  test_gated_env_hygiene.py: {sorted(theirs)}\n"
        f"  {pathlib.Path(__file__).name}: {sorted(GATED_ENVS)}"
    )


def test_every_fixed_job_is_on_a_gated_environment():
    """Sanity: the four fixed jobs are the ones that actually park on a gate."""
    for name, job_id in MUST_SKIP_ON_DRY_RUN:
        gated = _job_environments(_job(name, job_id)) & GATED_ENVS
        assert gated, (
            f"{name}: job '{job_id}' is in MUST_SKIP_ON_DRY_RUN but sits on no "
            "gated environment — re-check why it is listed."
        )


def _job(workflow_name: str, job_id: str) -> dict:
    return dict(_workflows())[workflow_name]["jobs"][job_id]


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
