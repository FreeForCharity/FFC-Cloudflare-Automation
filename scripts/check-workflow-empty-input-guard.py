#!/usr/bin/env python3
r"""Guard: a dispatch input moved into `env:` must fail closed when it is empty (#1150).

THE SECOND HALF OF THE #1080 REMEDY
    `scripts/check-workflow-input-interpolation.py` enforces the FIRST half —
    get the free-text dispatch input out of the `run:` text, where GitHub
    substitutes it as source code, and into a step-level `env:` mapping where
    it arrives as data. Nothing enforced the second half: the body must then
    notice when that mapping is empty.

    It is a statement about ARITY, not content. In a NATIVE command an empty
    `$env:X` renders as NO ARGUMENT AT ALL, so

        pwsh -NoProfile -File s.ps1 -Domain $env:DOMAIN -KeyPath $key

    becomes `-Domain -KeyPath $key`, `-Domain` binds the string `-KeyPath`,
    and every later argument shifts one position to the left. Ledger **L214**.
    Measured on pwsh 7.4.6 and 7.6.4, both hosts agreeing, variable absent:

        & pwsh -NoProfile -File s.ps1 -P $env:X -Q 'lit'   Missing an argument
        $a = @('-P', $env:X, '-Q', 'lit'); & pwsh -File s.ps1 @a
                                                          Missing an argument
        & ./s.ps1 -P $env:X -Q 'lit'                       P=[] Q=[lit]  SAFE

    `#1146` / `#1148` fixed 17 call sites by hand. This guard is what stops the
    18th, and every remaining #1080 burn-down manufactures a fresh instance of
    the hazard the moment it converts an interpolation into an `env:` mapping.

WHY THE INVOCATION FORM IS MODELLED AND NOT ASSUMED
    PowerShell's own binder accepts the empty value and shifts nothing; only a
    native command drops it. So `& .\scripts\x.ps1 -P $env:X` is CORRECT CODE
    and reporting it would be a false finding — `225-whmcs-domain-order-url-verify.yml`
    and `226-whmcs-application-triage.yml` are the two live sites that a
    form-blind matcher flags, and `226`'s `orderids` is LEGITIMATELY empty in
    its default `report` mode. "Fixing" it would break the common path. They
    are not findings here and they are not freeze entries either: a freeze
    entry records a defect, and there is no defect to record.

WHY BOTH CALL FORMS ARE MATCHED
    `@('-ApiUrl', $env:X, '-OutputFile', $out)` splatted into a native command
    drops the empty element exactly like the direct form does, and there is no
    `-Param $env:VAR` adjacency for a regex to see. That is precisely how
    #1146's hand count missed two live holes in `213`. On `main` the array form
    is the majority of all parameter-position references, so a checker written
    to the direct spelling would freeze the bigger half as compliant.

WHAT COUNTS AS A GUARD
    Anything that makes the reference non-empty at the call site, read out of
    the AST rather than pattern-matched, because this tree spells it three ways
    and all three are real guards:

      * fail closed   if ([string]::IsNullOrWhiteSpace($env:X)) { …; exit 1 }
      * default fill  if ([string]::IsNullOrWhiteSpace($env:X)) { $env:X = '…' }
      * gated append  if ($env:X) { $args += @('-Flag', $env:X) }

    …plus the same test written against a LOCAL COPY (`$d = $env:INPUT_DOMAIN`
    then `if ([string]::IsNullOrWhiteSpace($d))`), which is the false-positive
    class #1150 names: a predicate keyed on `$env:` alone does not see it and
    reports correct code. Local copies are tracked through the assignment's
    right-hand side, so the alias is followed rather than guessed at.

    The rule is deliberately permissive about the SHAPE of the test and strict
    about its POSITION: any condition that reads the variable (or an alias)
    ABOVE the call site counts. A body that tests a value and then ignores the
    answer is not something this guard can see, and pretending otherwise would
    make it fail on correct code — the direction that gets a guard switched off.

FAIL CLOSED
    A workflow whose YAML will not parse, a `run:` body PowerShell cannot
    parse, and a splat whose variable is never assigned in the same body are
    all findings, never skips. No PowerShell host on PATH is a refusal to
    report a pass, matching `scripts/check-pwsh-workflow-invocations.py`: this
    guard reads PowerShell with the PowerShell parser and will not certify
    something it did not measure.

Exit codes: 0 = the unguarded set is exactly the frozen one, 1 = a new
instance, a stale entry, an unreadable file, or no PowerShell host.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
FACTS_SCRIPT = "scripts/powershell-empty-input-facts.ps1"

# Shells whose `run:` body is PowerShell. `powershell` is Windows PowerShell
# 5.1; the argument-dropping semantics under test are identical in both.
PWSH_SHELLS = frozenset({"pwsh", "powershell"})

# Command names that start a NATIVE child process running a PowerShell script.
# The whole hazard lives here: a native command's argument array is built by
# rendering each element, and an empty element renders to nothing.
NATIVE_HOSTS = frozenset({"pwsh", "pwsh.exe", "powershell", "powershell.exe"})

# Native executables this tree calls that are NOT a PowerShell host. They drop
# an empty argument exactly the same way, but binding their arguments is not
# this guard's rule, so they are COUNTED and printed rather than silently
# ignored — the shape #1060 exists to fix one guard over.
OTHER_NATIVE_TOOLS = frozenset({
    "gh", "git", "az", "npm", "npx", "node", "python", "python3", "curl",
    "dotnet", "docker", "terraform",
})

# A reference to a dispatch input anywhere inside an expression, matching
# `check-workflow-input-interpolation.py` — including the `github.event.inputs`
# spelling and tolerating whitespace around the dots.
_INPUT_REF = re.compile(
    r"\b(?:github\s*\.\s*event\s*\.\s*)?inputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)

# `${{ inputs.x || 'https://…' }}` cannot arrive empty: the expression engine
# substitutes the literal before the body ever runs, so the mapping is its own
# guard. Matched on a NON-EMPTY literal specifically — `|| ''` defaults to
# empty and is no guard at all.
_EXPRESSION_FALLBACK = re.compile(r"\|\|\s*'[^']+'|\|\|\s*\"[^\"]+\"")

_EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
_EXPRESSION_TOKEN = "GHA_EXPR"

DIRECT = "direct"
ARRAY_SPLAT = "array-splat"


class WorkflowUnreadable(Exception):
    """The file could not be parsed. Always a finding — never a skip."""


@dataclass(frozen=True)
class Site:
    """One parameter-position reference to a dispatch-input `env:` mapping."""

    workflow: str
    job: str
    step: str
    variable: str
    expression: str
    form: str
    line: int
    guarded: bool
    guard_reason: str
    environments: tuple = ()

    @property
    def is_write(self) -> bool:
        """A lane nobody marked `-read` is treated as capable of writing.

        Deliberately the pessimistic reading, matching
        `check-workflow-input-interpolation.py`: a new environment is guarded
        by default rather than on the strength of its name.
        """
        return any(not name.endswith("-read") for name in self.environments)

    @property
    def key(self) -> str:
        return f"{self.job}::{self.variable}"

    def __str__(self) -> str:
        lane = ", ".join(self.environments) or "no environment"
        mark = "[W] " if self.is_write else ""
        return (
            f"{mark}{self.workflow} :: {self.job} :: {self.step} (run: line "
            f"{self.line}) [{self.form}] $env:{self.variable} <- {self.expression} "
            f"({lane})"
        )


@dataclass
class Unit:
    """One `run:` body the runner executes as PowerShell, with its env map."""

    workflow: str
    job: str
    step: str
    text: str
    env: dict  # VAR -> the `${{ }}` expression that fills it
    environments: tuple = ()

    @property
    def id(self) -> str:
        return f"{self.workflow}\x1f{self.job}\x1f{self.step}"


def find_powershell() -> str | None:
    """A PowerShell host, or None. Same resolution order as the #930 guard."""
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def mask_expressions(text: str) -> str:
    """Replace `${{ … }}` with a bare token, preserving the line count.

    `${{ }}` is not PowerShell — the parser reads it as a variable with a
    brace-quoted name — and the line numbers are reported to a human who has to
    open the file, so the substitution keeps the newlines.
    """

    def _replace(match: re.Match) -> str:
        return _EXPRESSION_TOKEN + "\n" * match.group(0).count("\n")

    return _EXPRESSION_RE.sub(_replace, text)


def _default_shell(container: dict) -> str | None:
    defaults = container.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    shell = run.get("shell")
    return shell if isinstance(shell, str) else None


def dispatch_env_map(*envs: dict) -> dict:
    """VAR -> expression, for every mapping fed by a dispatch input.

    Later mappings win, which is the runner's own precedence (step over job
    over workflow). A mapping whose expression carries a non-empty literal
    fallback is dropped: it cannot arrive empty, so it is not a hazard source
    and freezing it would record a defect that does not exist.
    """
    merged: dict = {}
    for env in envs:
        if not isinstance(env, dict):
            continue
        for name, value in env.items():
            if not isinstance(value, str):
                continue
            if not _INPUT_REF.search(value):
                continue
            if _EXPRESSION_FALLBACK.search(value):
                continue
            merged[str(name)] = value.strip()
    return merged


def job_environments(job: dict) -> tuple:
    """Every `environment:` one job enters, in the three shapes the schema allows."""
    found: set = set()

    def add(value) -> None:
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            found.add(value["name"])
        elif isinstance(value, list):
            for item in value:
                add(item)

    add(job.get("environment"))
    return tuple(sorted(found))


def pwsh_units(workflow_text: str, workflow_name: str) -> list[Unit]:
    """Every PowerShell `run:` body that maps at least one dispatch input."""
    doc = yaml.safe_load(workflow_text)
    if not isinstance(doc, dict):
        return []

    workflow_shell = _default_shell(doc)
    workflow_env = doc.get("env") if isinstance(doc.get("env"), dict) else {}
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    units: list[Unit] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_shell = _default_shell(job) or workflow_shell
        job_env = job.get("env") if isinstance(job.get("env"), dict) else {}
        job_envs = job_environments(job)
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            if (step.get("shell") or job_shell) not in PWSH_SHELLS:
                continue
            step_env = step.get("env") if isinstance(step.get("env"), dict) else {}
            env = dispatch_env_map(workflow_env, job_env, step_env)
            if not env:
                continue
            name = step.get("name") or "(unnamed)"
            units.append(
                Unit(
                    workflow=workflow_name,
                    job=str(job_id),
                    step=f'step {index} "{name}"',
                    text=mask_expressions(step["run"]),
                    env=env,
                    environments=job_envs,
                )
            )
    return units


def collect_units(paths=None) -> tuple[list[Unit], list[str], int]:
    """(units, unreadable, files scanned) across every workflow file."""
    paths = workflow_paths() if paths is None else list(paths)
    units: list[Unit] = []
    unreadable: list[str] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            unreadable.append(f"{path.name}: cannot be read ({error})")
            continue
        try:
            units.extend(pwsh_units(raw, path.name))
        except yaml.YAMLError as error:
            unreadable.append(f"{path.name}: YAML does not parse ({error})")
    return units, unreadable, len(paths)


def workflow_paths() -> list[pathlib.Path]:
    return sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))


def run_facts(pwsh: str, units: list[Unit]) -> dict:
    """One PowerShell process for the whole repository, not one per body."""
    if not units:
        return {"bodies": []}
    with tempfile.TemporaryDirectory() as tmp:
        body_file = pathlib.Path(tmp) / "bodies.json"
        body_file.write_text(
            json.dumps([{"id": unit.id, "text": unit.text} for unit in units]),
            encoding="utf-8",
        )
        # encoding= is explicit: text mode decodes with the locale codec, which
        # is cp1252 on a Windows host, and workflow bodies carry em-dashes.
        completed = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(REPO_ROOT / FACTS_SCRIPT),
             "-BodyListFile", str(body_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(REPO_ROOT),
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{FACTS_SCRIPT} exited {completed.returncode}\n"
            f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
        )
    return json.loads(completed.stdout)


def _as_list(value) -> list:
    """ConvertTo-Json renders a one-element list as the element itself."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def alias_map(assignments: list[dict]) -> dict:
    """local variable name -> the `$env:` names its value was read from.

    `$d = $env:INPUT_DOMAIN` makes `$d` an alias of INPUT_DOMAIN, so a test on
    `$d` guards INPUT_DOMAIN. Accumulated across every assignment rather than
    taking the last, because a variable rebuilt in a branch is still carrying
    the same input.
    """
    aliases: dict = {}
    for assignment in assignments:
        name = assignment.get("name")
        refs = _as_list(assignment.get("envRefs"))
        if not name or not refs:
            continue
        aliases.setdefault(str(name), set()).update(str(r) for r in refs)
    return aliases


def guard_for(variable: str, offset: int, facts: dict, aliases: dict) -> str | None:
    """Why `variable` is guarded before source offset `offset`, or None.

    Position is what is checked, not the shape of the test: a condition that
    READS the variable — or a local copy of it — above the call site is a
    guard, whether it exits, fills a default or gates the append. All three
    spellings are live in this tree and all three make the reference non-empty
    at the call site.

    Compared by source OFFSET and not by line, because the dominant guard in
    this tree is a ONE-LINE `if`:

        if ($env:ACCOUNT_ID) { $scriptArgs += @('-AccountId', $env:ACCOUNT_ID) }

    The condition and the reference it protects sit on the same line, so a
    line-granular `<` reports every gated append as unguarded — 12 of 505 and
    503's 11 references between them, all correct code. That is the
    false-finding direction, which is how a guard gets switched off.
    """
    for condition in _as_list(facts.get("conditions")):
        if condition.get("offset", -1) >= offset:
            continue
        if variable in {str(r) for r in _as_list(condition.get("envRefs"))}:
            return f"tested at line {condition['line']}: {condition.get('text', '')}"
        for local in _as_list(condition.get("varRefs")):
            if variable in aliases.get(str(local), set()):
                return (
                    f"tested through the local copy ${local} at line "
                    f"{condition['line']}: {condition.get('text', '')}"
                )
    for assignment in _as_list(facts.get("assignments")):
        # The body writes the variable itself before using it, so whatever the
        # mapping delivered is no longer what reaches the call.
        if assignment.get("envName") == variable and assignment.get("offset", -1) < offset:
            return f"assigned by the body at line {assignment['line']}"
    return None


def splat_elements(name: str, assignments: list[dict]) -> list[dict]:
    """Every element assigned to `$name`, in line order, as one argument list.

    Concatenated across assignments rather than read per assignment, because
    the flag and its value can land in separate statements —
    `$a = @('-P'); $a += $env:X` — and adjacency is the whole signal.
    """
    elements: list[dict] = []
    for assignment in sorted(
        (a for a in assignments if str(a.get("name")) == name),
        key=lambda a: a.get("offset", 0),
    ):
        elements.extend(_as_list(assignment.get("elements")))
    return elements


def _is_flag(element: dict) -> bool:
    """A literal that starts a parameter token.

    The `-` must START the token: otherwise a hyphenated cmdlet name matches
    its own suffix and `Test-Path` reads as `-Path`, which inflated #1146's
    first measurement from 16 to 18.
    """
    text = element.get("text")
    return element.get("kind") == "literal" and isinstance(text, str) and text.startswith("-")


def sites_for(unit: Unit, facts: dict) -> tuple[list[Site], list[str], int]:
    """(sites, problems, unjudged native commands) for one body."""
    problems = [
        f"{unit.workflow} :: {unit.job} :: {unit.step}: PowerShell does not parse "
        f"(line {error.get('line')}: {error.get('message')})"
        for error in _as_list(facts.get("parseErrors"))
    ]
    if problems:
        return [], problems, 0

    assignments = _as_list(facts.get("assignments"))
    aliases = alias_map(assignments)
    assigned_names = {str(a.get("name")) for a in assignments}

    sites: list[Site] = []
    unjudged = 0

    def record(variable: str, form: str, line: int, offset: int) -> None:
        expression = unit.env.get(variable)
        if expression is None:
            return
        reason = guard_for(variable, offset, facts, aliases)
        sites.append(
            Site(
                workflow=unit.workflow,
                job=unit.job,
                step=unit.step,
                variable=variable,
                expression=expression,
                form=form,
                line=line,
                guarded=reason is not None,
                guard_reason=reason or "",
                environments=unit.environments,
            )
        )

    for command in _as_list(facts.get("commands")):
        name = command.get("name")
        arguments = _as_list(command.get("arguments"))
        if not isinstance(name, str):
            continue
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if base not in NATIVE_HOSTS:
            # A cmdlet is not counted: PowerShell's own binder takes an empty
            # value and shifts nothing, which is the measured reason 225/226
            # are not findings. Only a real native tool belongs in the
            # not-judged count, or the number reads as 363 blind spots when
            # every one of them is a `Join-Path`.
            if base in OTHER_NATIVE_TOOLS or base.endswith(".exe"):
                unjudged += sum(
                    1
                    for index, argument in enumerate(arguments)
                    if argument.get("kind") == "env"
                    and str(argument.get("text")) in unit.env
                    and index > 0
                    and arguments[index - 1].get("kind") == "parameter"
                )
            continue

        for index, argument in enumerate(arguments):
            if argument.get("kind") == "env" and index > 0 and arguments[index - 1].get("kind") == "parameter":
                record(
                    str(argument.get("text")),
                    DIRECT,
                    int(argument.get("line") or command.get("line") or 0),
                    int(argument.get("offset", -1)),
                )
            if argument.get("kind") != "splat":
                continue
            splat_name = str(argument.get("text"))
            if splat_name not in assigned_names:
                problems.append(
                    f"{unit.workflow} :: {unit.job} :: {unit.step} (run: line "
                    f"{command.get('line')}): splat @{splat_name} is never assigned "
                    f"in this body, so its arguments cannot be read"
                )
                continue
            elements = splat_elements(splat_name, assignments)
            for position, element in enumerate(elements):
                if element.get("kind") != "env" or position == 0:
                    continue
                if _is_flag(elements[position - 1]):
                    record(
                        str(element.get("text")),
                        ARRAY_SPLAT,
                        int(element.get("line") or 0),
                        int(element.get("offset", -1)),
                    )

    return sites, problems, unjudged


def scan(paths=None):
    """(sites, problems, files scanned, bodies scanned, unjudged)."""
    units, unreadable, scanned = collect_units(paths)
    problems = list(unreadable)

    pwsh = find_powershell()
    if pwsh is None:
        raise RuntimeError(
            "no PowerShell host on PATH. This guard reads PowerShell with the "
            "PowerShell parser and refuses to report a pass it did not measure."
        )

    facts = run_facts(pwsh, units)
    by_id = {body.get("id"): body for body in _as_list(facts.get("bodies"))}

    sites: list[Site] = []
    unjudged = 0
    for unit in units:
        body = by_id.get(unit.id)
        if body is None:
            problems.append(
                f"{unit.workflow} :: {unit.job} :: {unit.step}: the facts script "
                f"returned nothing for this body"
            )
            continue
        found, body_problems, body_unjudged = sites_for(unit, body)
        sites.extend(found)
        problems.extend(body_problems)
        unjudged += body_unjudged

    return sites, problems, scanned, len(units), unjudged


def current_map(sites: list[Site]) -> dict:
    """workflow file -> the sorted `job::VAR` keys that are UNGUARDED."""
    grouped: dict = {}
    for site in sites:
        if site.guarded:
            continue
        grouped.setdefault(site.workflow, set()).add(site.key)
    return {name: tuple(sorted(keys)) for name, keys in sorted(grouped.items())}


# --- the freeze -------------------------------------------------------------
#
# Every parameter-position reference to a dispatch-input `env:` mapping that
# reaches a NATIVE PowerShell host with no emptiness test above it, as
# `workflow file -> ('job::VARIABLE', …)`. Asserted EXACTLY in both directions
# by `compare()`: an unfrozen instance fails the run, and an entry that no
# longer matches anything ALSO fails it, so the burn-down cannot leave the list
# lying.
#
# `[W]` marks a workflow entering an environment not suffixed `-read` — where
# the shifted argument spends a production credential. Burn down in that order.
#
# What is NOT here, and why the absence is the point:
#   * the 17 sites #1148 fixed. If one appears, this guard's guard-detection is
#     wrong, not the workflow (#1150 acceptance criterion 2).
#   * 225 / 226, which invoke through the PowerShell binder (`& .\x.ps1`) and
#     therefore cannot lose an argument. They are correct code; a freeze entry
#     would record a defect that does not exist (criterion 3).
#
# 24 references across 10 workflows, 17 of them on a write lane. Larger than
# the 16 #1146 counted from the direct form alone and smaller than the 32 the
# array-aware regex reported, and the difference in both directions is the
# point: the array-splat form adds sites a `-Param $env:VAR` adjacency cannot
# see, and modelling the guard removes the ones that are correct code
# (`if ($env:ACCOUNT_ID) { $a += @('-AccountId', $env:ACCOUNT_ID) }` is 11 of
# 503's and 505's references between them).
KNOWN_UNGUARDED: dict = {
    # [W] cloudflare-prod-read + cloudflare-prod-write. `preview` and `apply`
    # run the same body on the two lanes, so each variable is frozen twice.
    "111-dns-create-redirect-rule.yml": (
        "apply::INPUT_SOURCE",
        "apply::INPUT_TARGET",
        "preview::INPUT_SOURCE",
        "preview::INPUT_TARGET",
    ),
    # [W] cloudflare-prod-read + cloudflare-prod-write — invites a zone member.
    "122-cloudflare-zone-member-add.yml": (
        "apply::INPUT_DOMAIN",
        "apply::INPUT_EMAIL",
        "preview::INPUT_DOMAIN",
        "preview::INPUT_EMAIL",
    ),
    # [W] cloudflare-prod-read + cloudflare-prod-write.
    "124-cloudflare-cache-purge.yml": (
        "apply::INPUT_DOMAIN",
        "apply::INPUT_URLS",
        "preview::INPUT_DOMAIN",
        "preview::INPUT_URLS",
    ),
    # cloudflare-prod-read — the only read-lane entry in the freeze.
    "125-cloudflare-cache-rules-audit.yml": ("audit::INPUT_DOMAIN",),
    # [W] whmcs-prod. `client_email` IS guarded here (`elseif ($env:TICKET_EMAIL
    # -ne '')`), which is why only two of the three appear.
    "205-whmcs-ticket-open.yml": (
        "open_ticket::TICKET_MESSAGE",
        "open_ticket::TICKET_SUBJECT",
    ),
    # [W] whmcs-prod. `REASON` is guarded; `ACTION` is not.
    "211-whmcs-order-update.yml": ("order_update::ACTION",),
    # [W] whmcs-prod — sets a field on a WHMCS record.
    "230-whmcs-record-field-set.yml": (
        "record_field_set::FIELD",
        "record_field_set::TARGET",
        "record_field_set::VALUE",
    ),
    # [W] m365-prod.
    "305-m365-add-tenant-domain.yml": ("add-tenant-domain::INPUT_DOMAIN",),
    # [W] google-prod-write — provisions a GTM container. The three optional ids
    # (CLARITY_ID, META_PIXEL_ID, GRANTEE_EMAIL) are gated appends and correct.
    "503-google-gtm-provision.yml": (
        "provision::ACCOUNT_ID",
        "provision::DOMAIN",
        "provision::MEASUREMENT_ID",
    ),
    # [W] google-prod-write — provisions a GA4 property. Four of its five
    # references are gated appends; `DOMAIN` is the unconditional one.
    "505-google-ga-property-provision.yml": ("provision::DOMAIN",),
}


def compare(current: dict, known: dict | None = None) -> tuple[list[str], list[str]]:
    """(new_instances, stale_entries) between the tree and the freeze."""
    known = KNOWN_UNGUARDED if known is None else known
    new: list[str] = []
    stale: list[str] = []

    for workflow, keys in sorted(current.items()):
        frozen = known.get(workflow)
        if frozen is None:
            new.append(f"{workflow}: NOT in KNOWN_UNGUARDED — {', '.join(keys)}")
            continue
        added = sorted(set(keys) - set(frozen))
        if added:
            new.append(
                f"{workflow}: newly unguarded {', '.join(added)} "
                f"(frozen set was {', '.join(frozen)})"
            )

    for workflow, keys in sorted(known.items()):
        present = current.get(workflow)
        if present is None:
            stale.append(
                f"{workflow}: listed in KNOWN_UNGUARDED but nothing is unguarded "
                f"any more — delete the entry"
            )
            continue
        removed = sorted(set(keys) - set(present))
        if removed:
            stale.append(
                f"{workflow}: KNOWN_UNGUARDED still lists {', '.join(removed)}, "
                f"which is guarded now — narrow the entry to {', '.join(present)}"
            )

    return new, stale


REMEDY = (
    "Make the reference non-empty at the call site, the way the 17 sites #1148\n"
    "converted already do. Which remedy depends on whether the input has a\n"
    "meaningful default:\n"
    "    # required, no sane default — state the cause and stop\n"
    "    if ([string]::IsNullOrWhiteSpace($env:DOMAIN)) {\n"
    "      Write-Output '::error::DOMAIN is empty; pass domain to the dispatch.'\n"
    "      exit 1\n"
    "    }\n"
    "    # optional — gate the append instead\n"
    "    if ($env:ACCOUNT_ID) { $scriptArgs += @('-AccountId', $env:ACCOUNT_ID) }\n"
    "An empty value must never reach a native command's argument list: it does\n"
    "not arrive empty, it does not arrive at all."
)


def main() -> int:
    try:
        sites, problems, scanned, bodies, unjudged = scan()
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        # OSError covers the host vanishing between `which` and `subprocess.run`,
        # and JSONDecodeError the facts script exiting 0 with output that is not
        # the contract. Both already failed closed — an uncaught exception still
        # exits non-zero — but through a traceback, so the run said "crashed"
        # where it meant "could not measure".
        print(f"empty-input guard could not run: {error}")
        return 1

    if problems:
        print(f"empty-input guard: {len(problems)} file(s)/body(ies) could not be read\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nThis guard fails closed: something it cannot parse is a finding, "
            "because an unreadable body is exactly the blind spot it exists to close."
        )
        return 1

    current = current_map(sites)
    new, stale = compare(current)
    unguarded = [site for site in sites if not site.guarded]

    if new or stale:
        if new:
            print(f"dispatch inputs that can vanish from a native call: {len(new)}\n")
            for item in new:
                print(f"  {item}")
            print()
            for site in unguarded:
                if site.workflow in {entry.split(":")[0] for entry in new}:
                    print(f"  {site}")
            print()
            print(REMEDY)
        if stale:
            if new:
                print()
            print(f"stale KNOWN_UNGUARDED entries: {len(stale)}\n")
            for item in stale:
                print(f"  {item}")
            print(
                "\nA call site was guarded without deleting its freeze entry. Remove "
                "it in the same PR, or the list stops describing the tree and the "
                "next reader cannot tell what is left to burn down."
            )
        return 1

    guarded = [site for site in sites if site.guarded]
    write = [site for site in unguarded if site.is_write]
    print(
        f"empty-input guard OK: {scanned} workflow files scanned, {bodies} PowerShell "
        f"run: bodies map a dispatch input; {len(sites)} parameter-position references "
        f"reach a native PowerShell host ({len(guarded)} guarded, {len(unguarded)} "
        f"unguarded and frozen, {len(write)} of those on a write lane).\n"
        f"This is a FREEZE on new instances, not an endorsement: every entry is a "
        f"place where an empty dispatch input does not arrive empty — it does not "
        f"arrive at all, and every later argument binds one position to the left "
        f"(#1150, ledger L214).\n"
        f"Not judged: {unjudged} parameter-position reference(s) inside native "
        f"commands that are not a PowerShell host (gh, git, az), which drop an empty "
        f"argument the same way but whose binding is not this rule; "
        f"references through the PowerShell binder (`& .\\x.ps1`), which bind empty "
        f"and shift nothing — measured, not assumed; and a guard that reads the "
        f"variable but ignores the answer, which this guard counts as a guard."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
