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

- **Move the rehearsal out** of the gated job into an ungated one, then skip
  the gated remainder (111's shape). This is what 701's `dns` now does, via the
  `dns_preview` job on `cloudflare-prod-read`.
- **Skip the gated job outright**, but ONLY when some other, ungated job already
  performs the *same* rehearsal — which is far rarer than it looks. The first
  draft of #983's fix claimed 701's `zone_check` and 720's `preflight` qualified.
  Neither does: `zone_check` emits four booleans from `cloudflare-zone-get.ps1`
  and never computes the apex/www plan, and `preflight` only checks that a repo
  name is free. `test_a_skipped_job_has_a_real_rehearsal_counterpart` now makes
  the claim checkable instead of rhetorical — the named counterpart must be
  ungated, hold no write credential, and invoke the same script.

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
**27 credential-bearing jobs across 23 workflows**. Exactly one — 701's `dns` —
is fixed here, because it is the only one whose rehearsal could be relocated to
an ungated read lane today. The GitHub-side jobs (701 `repo`/`content`, 720
`create-repo`) need `github-prod-read`, which exists (#834) but whose credential
`read-all-cbm-github-pat` is dead (#848). The rest are enumerated in
`KNOWN_UNGUARDED` below, so the number is visible instead of implied.
"""

from __future__ import annotations

import functools
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
    ("701-website-provision.yml", "dns"): "dns_preview (cloudflare-prod-read)",
}

# The ungated job that performs the rehearsal a skipped job would otherwise have
# performed. This is the pairing that makes a skip legitimate, and it is checked
# — test_a_skipped_job_has_a_real_rehearsal_counterpart asserts the named job
# exists, is ungated, and actually runs the script the gated job runs.
#
# A zone-EXISTENCE probe does not qualify. 701's `zone_check` runs
# `cloudflare-zone-get.ps1` and emits four booleans; it never enumerates a
# record or computes the apex/www plan, so citing it as the preserved rehearsal
# (as the first draft of #983's fix did) is wrong.
REHEARSAL_COUNTERPART = {
    ("701-website-provision.yml", "dns"): ("dns_preview", "Update-CloudflareDns.ps1"),
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
    # Mechanical, and the same shape as its sibling 230 immediately below: the
    # dry run reads (GetClientsProducts + GetClientsDetails) to build the
    # preview, so the rehearsal needs a WHMCS credential — just a read-scoped
    # one. Splitting a `preview` job onto the ungated `whmcs-prod-read` lane and
    # leaving only `UpdateClient` behind the gate is the 701 `dns`/`dns_preview`
    # relocation, and is follow-up work rather than #825's scope.
    ("229-whmcs-client-field-populate.yml", "populate"),
    ("230-whmcs-record-field-set.yml", "record_field_set"),
    ("503-google-gtm-provision.yml", "provision"),
    ("505-google-ga-property-provision.yml", "provision"),
    # REHEARSAL-INSIDE: the dry run IS the clone + build. Needs the 111 split.
    ("702-ffc-ex-clone-deploy.yml", "clone-deploy"),
    ("704-website-analytics-wire.yml", "wire"),
    ("729-repo-add-collaborator.yml", "add-collaborator"),
    # REHEARSAL-INSIDE, blocked on a working GitHub read lane. These three run
    # `Create-GitHubRepo.ps1 -DryRun` / the content dry path, which is the only
    # rehearsal 701's repo side and 720 have — 720's `preflight` checks that the
    # name is free and nothing more, so skipping `create-repo` on a dry run
    # would make 720's `DryRun` input decorative. Relocating them the way `dns`
    # was relocated needs an ungated GitHub read lane: `github-prod-read` does
    # exist (#834), but its credential `read-all-cbm-github-pat` is dead (#848,
    # 502's deliver job 401s on it), so moving the rehearsal there today would
    # move it onto a credential that cannot authenticate. Blocked on #848.
    ("701-website-provision.yml", "repo"),
    ("701-website-provision.yml", "content"),
    ("720-create-repo.yml", "create-repo"),
    # Same shape and same #848 block as 720's create-repo immediately above:
    # 732 loops the identical Create-GitHubRepo.ps1 -DryRun rehearsal once per
    # domain, and its own preflight job (unlike 701's dns_preview) never
    # invokes that script — it only runs the collision/sibling checks — so it
    # is not a qualifying rehearsal counterpart under
    # test_a_skipped_job_has_a_real_rehearsal_counterpart either. Move both
    # once #848 makes github-prod-read's credential usable.
    ("732-bulk-create-repos.yml", "create-repos"),
    # 746 is 732's own follow-up (enable Pages on an already-existing repo) and
    # shares the identical shape: its preflight only checks repo/deploy.yml
    # existence, never rehearses the Pages-enable/dispatch calls themselves, so
    # there is no qualifying rehearsal counterpart either. Same #848 remedy.
    ("746-bulk-enable-pages.yml", "enable-pages"),
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


@functools.lru_cache(maxsize=1)
def _workflows_cached() -> tuple:
    parsed = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if isinstance(wf, dict):
            parsed.append((path.name, wf))
    return tuple(parsed)


def _workflows():
    """(name, parsed-yaml) for every workflow file, parsed once per session.

    Actually once, now — the docstring used to say "parsed once" while the body
    re-read and re-parsed all ~100 files on every call, and several tests call
    it indirectly through `_credential_jobs()` (raised in review on #985). The
    cache is safe because every consumer here only reads the parsed structures;
    nothing mutates them. Mutation testing works on the FILES and runs the
    module as a fresh process, so a stale cache cannot mask a mutation.
    """
    return list(_workflows_cached())


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


def _dry_run_input_type(wf: dict, input_name: str) -> str | None:
    """The declared `type:` of the dry-run input, or None if undeclared."""
    for trigger in ("workflow_dispatch", "workflow_call"):
        spec = _triggers(wf).get(trigger)
        if not isinstance(spec, dict):
            continue
        declared = (spec.get("inputs") or {}).get(input_name)
        if isinstance(declared, dict) and declared.get("type"):
            return str(declared["type"])
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
    """Does this job's `if:` SKIP the job when the dry-run input is on?

    Not "does it mention the input". The three spellings that actually skip:
    `== false` (111, 122, 742), `!= true` (120 and the #983 fixes), and the
    negation `!inputs.<name>`. What does NOT count:

    - the input appearing only inside a *step's* `if:` — that is precisely the
      defect #983 is about;
    - a bare truthy `if: inputs.dry_run`, or `== true`, which **inverts** the
      guard: the job would run *only* on dry runs, which is the worst possible
      outcome here and exactly what this module exists to prevent. Matching any
      reference would have scored that mis-edit as "guarded" and let a
      write-credentialed job run on every dry run without failing CI.
    """
    condition = str(job.get("if") or "")
    name = re.escape(input_name)
    skip_forms = (
        rf"inputs\.{name}\s*!=\s*true",
        rf"inputs\.{name}\s*==\s*false",
        # `!inputs.dry_run`. The `!=` form cannot collide: this alternative
        # requires `inputs` immediately after the `!`, not an `=`.
        rf"!\s*inputs\.{name}\b",
    )
    return any(re.search(form, condition, re.IGNORECASE) for form in skip_forms)


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


def test_only_skip_shaped_conditions_count_as_guarded():
    """Mentioning the input is not guarding on it — the inversion must fail.

    `if: inputs.dry_run` reads like a dry-run term and does the exact opposite:
    the job runs ONLY on dry runs, so every dry run would mint the write
    credential and no live run would ever create anything. A matcher that
    accepts any reference to the input scores that mis-edit as guarded and the
    whole module goes green over it (raised in review on #985).
    """
    guarded = ["inputs.dry_run != true", "inputs.dry_run == false", "!inputs.dry_run"]
    for form in guarded:
        assert _has_dry_run_term({"if": form}, "dry_run"), f"{form!r} skips the job on a dry run"
        # and the same term must still be found inside a compound condition
        compound = f"always() && {form} && needs.plan.result == 'success'"
        assert _has_dry_run_term({"if": compound}, "dry_run"), f"{compound!r} must match"

    inverted = ["inputs.dry_run", "${{ inputs.dry_run }}", "inputs.dry_run == true"]
    for form in inverted:
        assert not _has_dry_run_term({"if": form}, "dry_run"), (
            f"{form!r} makes the job run ONLY on dry runs — it must never count "
            "as guarded, or the guard passes over the inversion it exists to catch"
        )

    assert not _has_dry_run_term({"if": "needs.resolve.outputs.skip != 'true'"}, "dry_run")
    assert not _has_dry_run_term({}, "dry_run")

    # Casing: 720 spells it `DryRun`, and the match must respect the name it is
    # given rather than matching any dry-run-ish input that happens to be near.
    assert _has_dry_run_term({"if": "inputs.DryRun != true"}, "DryRun")
    assert not _has_dry_run_term({"if": "inputs.force != true"}, "DryRun")


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


def test_string_valued_dry_run_never_uses_equals_false():
    """`== false` is unsafe when the value can be a STRING, not when it can be null.

    Corrected on review of #985 — the first version of this test asserted that
    `== false` breaks on 701's `issues` / `repository_dispatch` paths because
    the empty `inputs` context makes it FALSE. That is backwards. Per the
    documented casting table for loose equality, **Null → 0** and
    **`false` → 0**, so `null == false` is `0 == 0` → **TRUE**: the job RUNS on
    an issue-triggered run, which is the desired behaviour. (An empty string
    also casts to 0, so the claim fails under that reading too.)

    The real hazard is the string. A **String** casts to "any legal JSON number
    format, otherwise NaN" — so `"false"` → NaN, and `"false" == false` is
    FALSE. That arises two ways, both live in this repo's future:

    - reading `github.event.inputs.<name>`, which is always a string, even for
      a `type: boolean` input;
    - retyping an input from `boolean` to `choice` / `string`.

    In either case a `== false` guard SKIPS the gated job on a **live** run —
    a missed DNS repoint or repo create that reports success. `!= true`
    survives both (NaN != 1), which is why the #983 fixes use it.

    So this test polices the shapes that actually carry a string, and leaves
    `== false` alone where the input is a declared boolean read via `inputs.`
    (111, 122, 742, 103 — all correct).
    """
    violations = []
    for name, wf in _workflows():
        dry_input = _dry_run_input(wf)
        if not dry_input:
            continue
        declared_type = _dry_run_input_type(wf, dry_input)
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            condition = str(job.get("if") or "")

            # `github.event.inputs.*` is a string even for a boolean input.
            if re.search(
                rf"github\.event\.inputs\.{re.escape(dry_input)}\s*==\s*false",
                condition,
                re.IGNORECASE,
            ):
                violations.append(
                    f"{name}: job '{job_id}' compares "
                    f"`github.event.inputs.{dry_input} == false`. That context yields "
                    'the STRING "false", which casts to NaN, so the comparison is '
                    "FALSE and the job is skipped on a LIVE run. Use "
                    f"`inputs.{dry_input} != true`."
                )

            if declared_type and declared_type != "boolean" and re.search(
                rf"(?<!event\.)inputs\.{re.escape(dry_input)}\s*==\s*false",
                condition,
                re.IGNORECASE,
            ):
                violations.append(
                    f"{name}: job '{job_id}' uses `inputs.{dry_input} == false`, but "
                    f"the input is declared `type: {declared_type}`, so its value is a "
                    'string. "false" casts to NaN and the comparison is FALSE — the '
                    f"job is skipped on a LIVE run. Use `inputs.{dry_input} != true`."
                )
    assert not violations, "\n".join(violations)


def test_a_skipped_job_has_a_real_rehearsal_counterpart():
    """A skip is only legitimate if some ungated job still does the rehearsal.

    This is the rule the module already applies to 702 and 736 in the other
    direction, applied to the jobs that ARE skipped — it is what stops the
    guard from endorsing "make the dry run a no-op" as a fix. The counterpart
    must exist, must be ungated, and must actually invoke the same script the
    gated job invokes; a job that merely touches the same API does not count.
    """
    for (name, job_id), (counterpart_id, script) in REHEARSAL_COUNTERPART.items():
        wf = dict(_workflows())[name]
        counterpart = (wf.get("jobs") or {}).get(counterpart_id)
        assert isinstance(counterpart, dict), (
            f"{name}: job '{job_id}' is skipped on dry runs and names "
            f"'{counterpart_id}' as the job that still rehearses — but that job "
            "does not exist. A skip with no counterpart deletes the rehearsal."
        )

        gated = _job_environments(counterpart) & GATED_ENVS
        assert not gated, (
            f"{name}: rehearsal job '{counterpart_id}' is itself on a gated "
            f"environment {sorted(gated)} — a dry run would still park on it."
        )
        assert not _write_credentials(counterpart), (
            f"{name}: rehearsal job '{counterpart_id}' loads a write-scoped "
            "credential, so relocating the rehearsal there defeats the purpose."
        )

        body = "\n".join(str(s.get("run") or "") for s in counterpart.get("steps") or [])
        assert script in body, (
            f"{name}: rehearsal job '{counterpart_id}' never invokes {script}, the "
            f"script '{job_id}' runs — so it is not a rehearsal of that job. (This "
            "is exactly why `zone_check` does not qualify: it runs "
            "cloudflare-zone-get.ps1 and emits four booleans.)"
        )


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
