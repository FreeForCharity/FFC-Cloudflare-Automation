#!/usr/bin/env python3
r"""Guard: a free-text `workflow_dispatch` input must not be interpolated into a
script body (#1080).

GitHub substitutes `${{ }}` into the script **text** before any shell or
PowerShell or JS parser sees it. So an input value is not data arriving in a
variable — it is source code pasted into the middle of a program. Two live
instances in `720-create-repo.yml`, reproduced locally before #1080 was filed:

    Description = "${{ inputs.Description }}"          # :193, pwsh
    $ Description = "$(Write-Host 'INJECTED-PWSH')"
    INJECTED-PWSH                                      <- executed

    repo_input="${{ inputs.RepoName }}"                # :274, bash
    $ RepoName='$(echo INJECTED-BASH)'
    repo_input=[INJECTED-BASH]

Neither needs a quote to break out: double-quoted `$( )` runs in both shells.

WHY THIS IS A FINDING EVEN THOUGH DISPATCH REQUIRES WRITE ACCESS
    The usual dismissal — "only someone with write access can dispatch, and they
    could edit the workflow anyway" — does not hold for a **gated** workflow, and
    that is the whole point.

    `environment:` approval is a trust boundary between the DISPATCHER and the
    APPROVER. The approver sees the run name, the input values and the workflow's
    committed source, then decides whether to spend a production credential.
    An interpolated input is code that runs AFTER that approval, inside the job,
    holding `cloudflare-prod-write` / `whmcs-prod` / `github-prod` / `m365-prod`
    credentials the dispatcher never had. Editing the workflow instead would show
    up in the diff the approver reads. An injected input never appears in the
    repository at all.

    That is also why findings are ordered by environment, not by file.

RELATIONSHIP TO tests/workflow-logic/test_no_untrusted_expression_in_run.py
    That module bans `${{ }}` in a `run:` of a workflow reachable from an
    UNTRUSTED EVENT (an issue body, a comment, a fork's PR). It deliberately
    carves dispatch-only workflows out — "no outsider can fire it" — and one of
    its clean samples asserts that `${{ inputs.domain }}` in a `run:` is NOT a
    finding there.

    This guard covers exactly the set that one declines to cover, for the
    different reason above. The two do not overlap and neither weakens the other:
    a workflow with an untrusted trigger is that module's business, and the rule
    there is absolute (no expression at all); the rule here is narrower (only
    free-text dispatch inputs), because a dispatch-only workflow interpolating
    `github.run_id` is not an injection risk.

WHICH INPUT TYPES COUNT AS FREE TEXT
    `boolean` and `choice` are excluded: GitHub constrains both to a value it
    generated, so neither can carry a payload. Everything else counts, including
    `number` — and that is a deliberate widening of #1080's own table, which
    counted `type: string` only. It is the fail-safe direction and it is not
    hypothetical here:

        101-domain-status.yml:684   const issueNumber = Number('${{ inputs.issue_number }}');

    `issue_number` is `type: number`, sitting in a SINGLE-quoted JS literal in a
    `github-script` step, in a workflow that enters `m365-prod`. If GitHub does
    not validate the type, an apostrophe ends the literal and the rest is JS with
    the job's credentials. GitHub's own documentation states that `choice`
    "resolves to a string" and says nothing equivalent about `number`, and this
    repo's CLAUDE.md records that the dispatch API requires every input to be
    SENT as a string (`issue_number: 609` is rejected 422; `"609"` is accepted).
    Absent a documented constraint, an unconstrained-by-default reading is the
    only safe one — so the type set is a DENYLIST of the two provably-safe types,
    and any type nobody has classified (a future `environment`, a typo) is
    treated as free text rather than waved through.

WHAT IS NOT A FINDING
    Expressions in `env:`, `with:`, `if:` and `name:` are untouched. The first is
    the REMEDY — `env: FOO: ${{ inputs.foo }}` then `"$FOO"` — and a guard that
    flagged it would have to be switched off to land the fix for the thing it
    guards. `if:` is evaluated by the expression engine and never becomes script
    text.

FAIL CLOSED
    A workflow whose YAML will not parse is a finding, never a silent skip — an
    unreadable file is exactly the blind spot this exists to close.

THE FREEZE
    `KNOWN_UNGUARDED` pins the call sites that already exist, asserted EXACTLY in
    both directions: a workflow (or a newly-interpolated input in an already
    listed workflow) that is not in the list fails the run, and an entry that no
    longer matches anything ALSO fails it, so the burn-down cannot leave the list
    lying. Fixing a call site therefore requires deleting its entry in the same
    PR. The list is a freeze on new instances, not an endorsement of the old ones.

Exit codes: 0 = the interpolation set is exactly the frozen one, 1 = a new
instance, a stale entry, or a file that could not be read.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The only two `workflow_dispatch` input types GitHub constrains to a value it
# generated itself. A denylist, not an allowlist: see the module docstring —
# an unrecognised type must read as free text, because the failure direction of
# guessing wrong is an unguarded injection.
CONSTRAINED_TYPES = frozenset({"boolean", "choice"})

# `${{ … }}`, non-greedy, DOTALL because an expression may be wrapped across
# lines inside a block scalar.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)

# A reference to a dispatch input ANYWHERE inside an expression, not only the
# bare `${{ inputs.x }}` spelling. `702-ffc-ex-clone-deploy.yml:271` is why:
#
#     ${{ inputs.exclude && format('--exclude {0}', inputs.exclude) || '' }}
#
# is a free-text input reaching the script text through a `format()`, and a
# pattern anchored on the bare spelling reads it as clean. #1080's own table
# missed this call site for exactly that reason.
#
# Whitespace is tolerated around the dots because the expression language allows
# it and a guard that can be evaded with a space is decoration.
_INPUT_REF = re.compile(
    r"\b(?:github\s*\.\s*event\s*\.\s*)?inputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)


class WorkflowUnreadable(Exception):
    """The file could not be parsed. Always a finding — never a skip."""


def dispatch_inputs(workflow: dict) -> dict[str, str]:
    """Map every `workflow_dispatch` input name to its declared type.

    `on:` is the YAML 1.1 boolean `True` after `yaml.safe_load` (the Norway
    problem). Reading only the string key would silently return {} for every
    workflow and make this guard pass by inspecting nothing, so both spellings
    are checked and `test_the_scan_sees_real_workflows` pins the result.
    """
    on = workflow.get(True, workflow.get("on"))
    if not isinstance(on, dict):
        return {}
    dispatch = on.get("workflow_dispatch")
    if not isinstance(dispatch, dict):
        return {}
    inputs = dispatch.get("inputs")
    if not isinstance(inputs, dict):
        return {}
    types: dict[str, str] = {}
    for name, spec in inputs.items():
        declared = spec.get("type", "string") if isinstance(spec, dict) else "string"
        types[str(name)] = str(declared)
    return types


def free_text_inputs(workflow: dict) -> set[str]:
    """Dispatch inputs whose value the dispatcher chooses freely."""
    return {
        name
        for name, declared in dispatch_inputs(workflow).items()
        if declared not in CONSTRAINED_TYPES
    }


def environments(workflow: dict) -> set[str]:
    """Every `environment:` any job in the workflow enters.

    Accepts the three shapes the schema allows: a bare string, a mapping with a
    `name`, and a list of either.
    """
    found: set[str] = set()

    def add(value) -> None:
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            found.add(value["name"])
        elif isinstance(value, list):
            for item in value:
                add(item)

    for job in (workflow.get("jobs") or {}).values():
        if isinstance(job, dict):
            add(job.get("environment"))
    return found


def is_write_environment(name: str) -> bool:
    """A write lane is any environment not explicitly suffixed `-read`.

    Deliberately the pessimistic reading: a lane nobody marked read-only is
    treated as capable of writing, so a new environment is guarded by default
    rather than on the strength of its name.
    """
    return not name.endswith("-read")


def script_bodies(workflow: dict):
    """Yield (job_id, step_label, kind, body) for every embedded script.

    Two kinds carry substituted text into a parser: a `run:` block, and the
    `script:` input of `actions/github-script`, whose body is JS. #1080's
    criterion 6 requires both; the `github-script` half is where 101's
    `Number('${{ inputs.issue_number }}')` lives.
    """
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            label = str(step.get("name") or f"step {index}")
            if isinstance(step.get("run"), str):
                yield job_id, label, "run", step["run"]
            if "github-script" in str(step.get("uses") or ""):
                script = (step.get("with") or {}).get("script")
                if isinstance(script, str):
                    yield job_id, label, "github-script", script


def _line_of(raw: str, needle: str) -> int | None:
    """1-based line number of the first raw line containing `needle`, if any.

    Only ever used to make a finding easier to open. A miss degrades the message
    and never changes the verdict.
    """
    for number, line in enumerate(raw.splitlines(), start=1):
        if needle and needle in line:
            return number
    return None


class Finding:
    def __init__(self, workflow: str, job: str, step: str, kind: str,
                 input_name: str, expression: str, line: int | None) -> None:
        self.workflow = workflow
        self.job = job
        self.step = step
        self.kind = kind
        self.input_name = input_name
        self.expression = expression
        self.line = line

    def __str__(self) -> str:
        where = f"{self.workflow}:{self.line}" if self.line else self.workflow
        return (
            f"{where} job '{self.job}' step '{self.step}' ({self.kind}) "
            f"interpolates free-text input '{self.input_name}' via "
            f"`${{{{{self.expression}}}}}`"
        )


def scan_workflow(path: pathlib.Path) -> list[Finding]:
    """Findings for one workflow file. Raises WorkflowUnreadable on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowUnreadable(f"{path.name}: cannot be read ({error})") from error
    try:
        workflow = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise WorkflowUnreadable(f"{path.name}: YAML does not parse ({error})") from error
    if not isinstance(workflow, dict):
        raise WorkflowUnreadable(
            f"{path.name}: top level is {type(workflow).__name__}, not a mapping"
        )

    free_text = free_text_inputs(workflow)
    if not free_text:
        return []

    findings: list[Finding] = []
    for job_id, step, kind, body in script_bodies(workflow):
        for match in _EXPRESSION.finditer(body):
            expression = match.group(1)
            for name in _INPUT_REF.findall(expression):
                if name not in free_text:
                    continue
                anchor = match.group(0).splitlines()[0]
                findings.append(
                    Finding(path.name, job_id, step, kind, name, expression,
                            _line_of(raw, anchor))
                )
    return findings


def workflow_paths() -> list[pathlib.Path]:
    return sorted(
        list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))
    )


def scan_all(paths=None):
    """(findings, unreadable, scanned) across every workflow file."""
    findings: list[Finding] = []
    unreadable: list[str] = []
    paths = workflow_paths() if paths is None else list(paths)
    for path in paths:
        try:
            findings.extend(scan_workflow(path))
        except WorkflowUnreadable as error:
            unreadable.append(str(error))
    return findings, unreadable, len(paths)


def current_map(findings: list[Finding]) -> dict[str, tuple[str, ...]]:
    """workflow file -> the sorted free-text inputs it interpolates."""
    grouped: dict[str, set[str]] = {}
    for finding in findings:
        grouped.setdefault(finding.workflow, set()).add(finding.input_name)
    return {name: tuple(sorted(v)) for name, v in sorted(grouped.items())}


# --- the freeze -------------------------------------------------------------
#
# Every call site that already existed when this guard landed, as
# `workflow file -> the free-text inputs it interpolates`. Asserted EXACTLY in
# both directions by `compare()`.
#
# `[W]` marks a workflow that enters at least one environment not suffixed
# `-read`, i.e. one where the injected code would run holding a production
# write credential the dispatcher could not otherwise reach. Burn this list down
# in that order.
#
# RECONCILIATION WITH #1080's TABLE (34 workflows / 20 write, dated 2026-08-05).
# This list is 36 / 21, and every one of the two extra workflows is accounted
# for — the guard was not widened by accident:
#
#   +1 write  229-whmcs-client-field-populate.yml — #825 landed 2026-08-05
#             (cee6630), after the table was written. #1080 says in as many
#             words: "add 229 to the allowlist when #825 lands".
#   +1 read   115-domain-transfer-preflight.yml — all three of its interpolated
#             inputs are `type: number`, so a string-only count could not see
#             it. See the docstring for why `number` counts here.
#
# Two entries also carry more inputs than the table lists, without changing the
# workflow count:
#   101 / 102 / 103  +issue_number   `type: number`, same reason as 115.
#   702              +exclude        `type: string`, and simply missed by a
#                    pattern anchored on the bare `${{ inputs.x }}` spelling:
#                    it reaches the script through
#                    `${{ inputs.exclude && format('--exclude {0}', …) }}`.
KNOWN_UNGUARDED: dict[str, tuple[str, ...]] = {
    # [W] --- Cloudflare / domain -------------------------------------------
    "101-domain-status.yml": ("domain", "issue_number"),
    "102-domain-add-ffc-cloudflare-and-whmcs.yml": ("domain", "issue_number"),
    "103-enforce-domain-standard.yml": ("domain", "issue_number"),
    "107-audit-compliance.yml": ("domain",),
    "109-dns-export-all-records.yml": ("domain",),
    "110-cloudflare-zone-create.yml": ("domain",),
    # 112-dns-bulk-replace-a-ip.yml burned down: `old_ip` / `new_ip` now reach the
    # pwsh body through step-level `env:`. It rewrites A records across every zone in
    # both Cloudflare accounts on cloudflare-prod-write, and the callee's
    # `[ValidatePattern]` on both parameters could never have stopped a payload — it
    # runs after the injected expression, so its rejection message reads like a
    # control working on a run where the code had already executed.
    "115-domain-transfer-preflight.yml": (
        "issue_number",
        "min_days_to_expiry",
        "post_reg_lock_days",
    ),
    "116-domain-transfer-epp-probe.yml": ("domain",),
    "117-domain-transfer-verify.yml": ("domain",),
    "118-whmcs-domain-lock.yml": ("domain",),
    "119-bulk-staging-cname-github-pages.yml": ("domains", "target"),
    # 120-bulk-cutover-to-github-pages.yml burned down: `domains` now reaches all four
    # bodies (two pwsh, two bash) through step-level `env:`. It held two write
    # environments at once — cloudflare-prod-write for the apex flip and github-prod for
    # the CNAME flip — so an injected payload ran twice, under two credentials.
    # --- WHMCS --------------------------------------------------------------
    "201-whmcs-export-domains.yml": ("output_file",),
    "202-whmcs-export-products.yml": (
        "client_products_output_file",
        "products_output_file",
    ),
    "203-whmcs-export-payment-methods.yml": ("output_file",),
    "205-whmcs-ticket-open.yml": ("client_id", "deptid"),
    "208-whmcs-tickets-export.yml": ("output_file", "status"),
    "213-whmcs-zeffy-payments-import-draft.yml": (
        "clients_output",
        "end_date",
        "invoices_output",
        "max_rows",
        "max_rows_per_file",
        "start_date",
        "transactions_output",
        "zeffy_output",
        "zeffy_output_xlsx",
    ),
    "214-whmcs-clients-metrics.yml": ("output_file",),
    "215-whmcs-nonprofit-clients-metrics.yml": ("output_file",),
    "216-whmcs-activity-metrics.yml": ("charity_gids", "output_file"),
    "217-whmcs-client-fields-survey.yml": ("output_file", "throttle_ms"),
    "218-whmcs-siteslist-reconciliation.yml": (
        "cloudflare_pid",
        "github_pages_pid",
        "output_file",
    ),
    "220-whmcs-served-metrics.yml": ("charity_gids", "output_file"),
    "222-whmcs-product-alignment.yml": ("product_id",),
    "224-whmcs-github-pages-product-alignment.yml": ("product_id",),
    "229-whmcs-client-field-populate.yml": ("client_id", "email"),
    # --- Microsoft 365 ------------------------------------------------------
    "301-m365-domain-preflight.yml": ("domain",),
    "303-m365-domain-and-dkim.yml": ("domain",),
    # 304-m365-dkim-enable.yml burned down: `domain` now reaches all three bodies
    # through step-level `env:`. It was the only remaining entry interpolating one
    # input into three jobs under two gated environments — m365-prod twice and
    # cloudflare-prod-write once — so a payload ran three times, under two credentials,
    # on one approval. Sharper than the neighbouring entries in WHERE the credential
    # sat: both m365-prod steps carry FFC_EXO_CERT_PFX_BASE64 / FFC_EXO_CERT_PASSWORD
    # in the SAME step's `env:` as the injection point, so the Exchange Online
    # certificate was already in the process environment of the body being injected
    # into. Measured against the shipped body: the cert password was written to a file,
    # m365-dkim.ps1 was then called with a legal `Domain=[ffcworkingsite1.org]`, and the
    # step exited 0.
    "306-discover-uncaptured-comms.yml": ("mailboxes", "since_days"),
    # --- WPMUDEV ------------------------------------------------------------
    "601-wpmudev-export-sites.yml": ("output_file",),
    # --- GitHub -------------------------------------------------------------
    "702-ffc-ex-clone-deploy.yml": ("depth", "domain", "exclude"),
    # 704-website-analytics-wire.yml burned down: `gtm_id` / `measurement_id` now reach
    # the bash body through step-level `env:`. Its only interpolating step was the one
    # named `Validate inputs` — the step whose purpose is to reject a malformed id,
    # performing that check by pasting the value into the program doing the checking,
    # one step before `wr-all-cbm-github-pat` is loaded on github-prod. The neighbouring
    # step already used `env:` correctly, so the file read as if the pattern held.
    # 720-create-repo.yml burned down: its three free-text inputs now reach both the
    # pwsh and the bash bodies through step-level `env:`. It was the instance #1080
    # reproduced live in both directions, on github-prod.
}


def compare(current: dict[str, tuple[str, ...]],
            known: dict[str, tuple[str, ...]] | None = None) -> tuple[list[str], list[str]]:
    """(new_instances, stale_entries) between the tree and the freeze.

    Exact in both directions, per #1080 criteria 2/3/5. A workflow present in
    both but whose interpolated input set has CHANGED reports on both sides:
    an added input is a new instance to justify, a removed one is a fixed call
    site whose entry must be deleted.
    """
    known = KNOWN_UNGUARDED if known is None else known
    new: list[str] = []
    stale: list[str] = []

    for workflow, inputs in sorted(current.items()):
        if workflow not in known:
            new.append(
                f"{workflow}: NOT in KNOWN_UNGUARDED — interpolates "
                f"{', '.join(inputs)}"
            )
            continue
        added = sorted(set(inputs) - set(known[workflow]))
        if added:
            new.append(
                f"{workflow}: newly interpolates {', '.join(added)} "
                f"(frozen set was {', '.join(known[workflow])})"
            )

    for workflow, inputs in sorted(known.items()):
        if workflow not in current:
            stale.append(
                f"{workflow}: listed in KNOWN_UNGUARDED but nothing is "
                f"interpolated any more — delete the entry"
            )
            continue
        removed = sorted(set(inputs) - set(current[workflow]))
        if removed:
            stale.append(
                f"{workflow}: KNOWN_UNGUARDED still lists {', '.join(removed)}, "
                f"which is no longer interpolated — narrow the entry to "
                f"{', '.join(current[workflow])}"
            )

    return new, stale


REMEDY = (
    "Pass the value through `env:` instead of interpolating it into the script "
    "body, which is what 105-manage-record.yml:178-184 already does:\n"
    "    env:\n"
    "      DOMAIN: ${{ inputs.domain }}\n"
    "    run: |\n"
    '      pwsh -File ./x.ps1 -Zone $env:DOMAIN      # or "$DOMAIN" in bash\n'
    "An `env:` value is handed to the process as data and is never parsed as "
    "code, so no quoting in the value can change the program."
)


def main() -> int:
    findings, unreadable, scanned = scan_all()

    if unreadable:
        print(f"workflow input interpolation guard: {len(unreadable)} file(s) unreadable\n")
        for problem in unreadable:
            print(f"  {problem}")
        print(
            "\nThis guard fails closed: a workflow it cannot parse is a finding, "
            "because an unreadable file is exactly the blind spot it exists to close."
        )
        return 1

    current = current_map(findings)
    new, stale = compare(current)

    if new or stale:
        if new:
            print(
                f"free-text dispatch inputs newly interpolated into a script body: "
                f"{len(new)}\n"
            )
            for item in new:
                print(f"  {item}")
            print()
            print(REMEDY)
            print(
                "\nIf this really must ship as-is, add it to KNOWN_UNGUARDED in "
                f"{pathlib.Path(__file__).name} with a reason — but read #1080 first: "
                "on a gated workflow the injected code runs AFTER the approval, "
                "holding a credential the dispatcher does not have."
            )
        if stale:
            if new:
                print()
            print(f"stale KNOWN_UNGUARDED entries: {len(stale)}\n")
            for item in stale:
                print(f"  {item}")
            print(
                "\nA call site was fixed without deleting its freeze entry. Remove it "
                "in the same PR, or the list stops describing the tree and the next "
                "reader cannot tell what is left to burn down."
            )
        return 1

    write_workflows = []
    for workflow in current:
        try:
            parsed = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if any(is_write_environment(e) for e in environments(parsed)):
            write_workflows.append(workflow)

    print(
        f"workflow input interpolation OK: {scanned} workflow files scanned; "
        f"{len(current)} interpolate a free-text dispatch input into a script body "
        f"({len(findings)} call sites), of which {len(write_workflows)} enter a write "
        f"environment. All are in the KNOWN_UNGUARDED freeze and no entry is stale.\n"
        f"This is a FREEZE on new instances, not an endorsement: every entry is a "
        f"place a dispatcher can supply code that runs after an approver spends a "
        f"production credential (#1080). Burn down the {len(write_workflows)} write "
        f"ones first.\n"
        f"Not judged: composite actions under .github/actions/ (an action's `inputs.*` "
        f"is a different context), and reusable-workflow `workflow_call` inputs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
