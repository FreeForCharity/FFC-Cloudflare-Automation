#!/usr/bin/env python3
r"""Guard: an emptiness test on a dispatch input must not be `-ne ''` / `-eq ''` (#1213).

`-ne ''` is the natural way to write "this optional input was supplied", and it
is correct for exactly ONE of the three blank forms an input can take. Measured
here with PowerShell 7.4.6, the variable set from OUTSIDE the interpreter:

    input state                       -ne ''    -not IsNullOrWhiteSpace
    unset (env: mapping missing)      True      False      <- gate PASSES, wrong
    empty string (blank dispatch)     False     False         gate skips, correct
    whitespace ("   ")                True      False      <- gate PASSES, wrong
    real value                        True      True          gate passes, correct

Two of the four rows are wrong, and both are wrong in the direction that lets a
blank value through to the callee.

WHY THE WHITESPACE ROW IS THE ONE THAT BITES TODAY
    Replicating `205-whmcs-ticket-open.yml`'s argument build against a stub
    callee, a dispatch supplying `client_id: "   "` binds silently at exit 0:

        splat -> [-DeptId] [D1] ... [-ClientId] [   ]
        CALLEE OK  DeptId=[D1] ClientId=[   ] Email=[]
        exit=0

    That is the same "binds silently as `-DeptId ''`" outcome `205`'s own
    comment documents for its REQUIRED inputs, arrived at from the optional
    side — where the L214/#1150 hardening was never applied. `205` is
    `[WRITE]` (`whmcs-prod`), so the blank arrives after an approver has
    already spent a production credential.

WHY THE $null ROW IS SYSTEMIC RATHER THAN A FOURTH NIT
    The #1080 burn-down rewrites `'${{ inputs.x }}' -ne ''` as
    `$env:X -ne ''`. That changes the predicate's DOMAIN from *string* to
    *string-or-null*: the interpolated form could never be `$null`, the `env:`
    form can. So a burn-down lane that does its own job perfectly still lands a
    gate that is newly wrong in a case that did not previously exist.

    `check-workflow-input-interpolation.py` has no notion of predicates
    (grep it: zero occurrences of either form), so nothing catches the
    conversion. This guard is the thing that does, and it is deliberately
    written to fire on BOTH forms so it keeps holding across a lane landing.

WHY THIS GUARD NEEDS NO POWERSHELL HOST
    Unlike `check-workflow-empty-input-guard.py` (#1150), which reads
    PowerShell with the PowerShell parser and refuses to report a pass it did
    not measure, this guard is a purely textual predicate check. There is no
    tool whose absence could turn "could not run" into "everything passed" —
    the failure mode `tests/workflow-logic/test_tool_gated_skips_are_scoped.py`
    exists to prevent. It runs identically in every sandbox and in CI.

WHAT IS NOT A FINDING
    A comparison whose left operand is not a dispatch input. The three hits
    under `scripts/` use the same operator as a property-presence test on an
    API response object:

        scripts/whmcs-fraud-review.ps1:78     "$($Object.$n)" -ne ''
        scripts/candid-essentials-search.ps1:74
        scripts/candid-charity-check.ps1:64

    Those are not input gates and this guard does not look at `scripts/` at
    all. Within a workflow body, `$dir -ne ''` on a local variable is likewise
    ignored — only an interpolated `inputs.*` expression, or an `$env:` read of
    a variable the step's own `env:` maps from `inputs.*`, is judged.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# `boolean` and `choice` are values GitHub generated, so neither can arrive
# blank-but-present in a way the dispatcher chose. Same exclusion, and the same
# reasoning, as `check-workflow-input-interpolation.py`.
CONSTRAINED_TYPES = frozenset({"boolean", "choice"})

# The weak predicate, in the spellings PowerShell accepts. `-ne`/`-eq` with an
# empty single- or double-quoted string. `-ceq`/`-ine` and friends are included
# because they compare the same way for this purpose.
_WEAK = re.compile(r"-(?:c|i)?(?:ne|eq)\s*(?:''|\"\")")

# The left operand, immediately before the operator. Two shapes are judged:
#   '${{ inputs.name }}'   an interpolated input, quoted or bare
#   $env:NAME              an env read, resolved against the step's `env:`
_LEFT_EXPR_TAIL = re.compile(r"\$\{\{(.*?)\}\}[\"']?\s*$", re.DOTALL)
_LEFT_ENV = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)\s*$")

# A reference to a dispatch input ANYWHERE inside an expression. Deliberately
# the same pattern `check-workflow-input-interpolation.py` uses, for the reason
# stated there verbatim: the expression language permits `github.event.inputs.`
# and whitespace around the dots, and "a guard that can be evaded with a space
# is decoration".
#
# Anchoring on the bare `${{ inputs.x }}` spelling is what the first version of
# this file did, and it missed FOUR of five equivalent spellings — measured, not
# supposed. Copilot caught it on #1214; `test_every_equivalent_input_spelling_is_
# detected` now pins all five, on both the interpolated and the `$env:` side.
_INPUT_REF = re.compile(
    r"\b(?:github\s*\.\s*event\s*\.\s*)?inputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)

# ---------------------------------------------------------------------------
# The freeze.
#
# `KNOWN_WEAK_GATES` pins the sites that exist today and cannot be fixed here,
# keyed workflow -> input names, the same shape `KNOWN_UNGUARDED` uses in
# `check-workflow-input-interpolation.py`.
#
# ONE entry, and it is a deliberate cross-PR deferral rather than an
# endorsement. `205-whmcs-ticket-open.yml` is in flight on #1207 (and carried
# byte-identical by #1212), and BOTH of those branches already apply the
# `IsNullOrWhiteSpace` fix to these two gates. Editing `205` here would collide
# with that lane textually and destroy #1212's byte-identical-to-its-source
# property, which is the thing letting the Conductor still choose between the
# two landing paths. So the fix for `205` ships with the lane that owns the
# file, and this entry records the debt until it lands.
#
# WHEN #1207 OR #1212 LANDS: delete this entry. The guard prints a NOTE naming
# it and still exits 0 — see `compare()` for why that direction is not fatal.
# NOTE ON THE INPUT NAMES: `205`'s email input is declared `client_email`, not
# `email`. #1213's call-site table calls it `email` (it named the *parameter*
# the value binds to, `-Email`, not the dispatch input). Writing the issue's
# spelling here made the freeze silently fail to cover the real site — caught
# by this guard's own staleness NOTE on the first run, which is the argument
# for the NOTE existing at all.
KNOWN_WEAK_GATES: dict[str, tuple[str, ...]] = {
    "205-whmcs-ticket-open.yml": ("client_email", "client_id"),
}


class Finding:
    def __init__(self, workflow: str, job: str, step: str, input_name: str,
                 line: int | None, text: str) -> None:
        self.workflow = workflow
        self.job = job
        self.step = step
        self.input_name = input_name
        self.line = line
        self.text = text

    def __str__(self) -> str:
        where = f"{self.workflow}:{self.line}" if self.line else self.workflow
        return (
            f"{where} job '{self.job}' step '{self.step}' gates dispatch input "
            f"'{self.input_name}' with an empty-string comparison: {self.text.strip()}"
        )


def dispatch_inputs(workflow: dict) -> dict[str, str]:
    """Map every `workflow_dispatch` input name to its declared type.

    `on:` is the YAML 1.1 boolean `True` after `yaml.safe_load` (the Norway
    problem), so both spellings are read. Reading only the string key would
    return {} for every workflow and make this guard pass by inspecting
    nothing — `test_the_scan_sees_real_workflows` pins that it does not.
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


def step_env_map(step: dict) -> dict[str, str]:
    """Env var name -> the dispatch input it forwards, for this step's `env:`.

    This is what makes the post-#1080 form judgeable at all: after a burn-down
    lane converts a site, the input name appears only in the `env:` block and
    the comparison names a variable.
    """
    env = step.get("env")
    if not isinstance(env, dict):
        return {}
    forwarded: dict[str, str] = {}
    for var, value in env.items():
        names = _INPUT_REF.findall(str(value))
        if names:
            forwarded[str(var)] = names[0]
    return forwarded


def _line_of(raw: str, needle: str) -> int | None:
    """1-based line number of the first raw line containing `needle`.

    Only ever used to make a finding easier to open. A miss degrades the
    message and never changes the verdict.
    """
    for number, line in enumerate(raw.splitlines(), start=1):
        if needle and needle in line:
            return number
    return None


def scan_body(body: str, free_text: set[str], env_map: dict[str, str]):
    """Yield (input_name, offending_text) for every weak gate in one body."""
    for line in body.splitlines():
        for match in _WEAK.finditer(line):
            left = line[: match.start()]
            tail = _LEFT_EXPR_TAIL.search(left)
            if tail:
                # An expression may reference more than one input (a `format()`
                # call, a `&&`/`||` chain). Report the first free-text one — the
                # finding is "this gate tests a dispatch input with the wrong
                # predicate", which is true regardless of which one it names.
                named = [n for n in _INPUT_REF.findall(tail.group(1)) if n in free_text]
                if named:
                    yield named[0], line
                    continue
            env_ref = _LEFT_ENV.search(left)
            if env_ref:
                name = env_map.get(env_ref.group(1))
                if name and name in free_text:
                    yield name, line


def scan_workflow(path: pathlib.Path) -> list[Finding]:
    import yaml

    raw = path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # A workflow this guard cannot parse is a finding, not a skip. A silent
        # `continue` here is how a guard reports a clean tree it never read.
        return [Finding(path.name, "-", "-", "-", None, f"unparseable YAML: {exc}")]
    if not isinstance(parsed, dict):
        return []
    free_text = free_text_inputs(parsed)
    if not free_text:
        return []

    findings: list[Finding] = []
    for job_id, job in (parsed.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            body = step.get("run")
            if not isinstance(body, str):
                continue
            label = str(step.get("name") or f"step {index}")
            env_map = step_env_map(step)
            for name, text in scan_body(body, free_text, env_map):
                findings.append(
                    Finding(path.name, job_id, label, name, _line_of(raw, text.strip()), text)
                )
    return findings


def workflow_paths() -> list[pathlib.Path]:
    return sorted(p for p in WORKFLOWS.glob("*.yml") if p.is_file())


def scan_all(paths=None) -> tuple[list[Finding], int]:
    paths = workflow_paths() if paths is None else list(paths)
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_workflow(path))
    return findings, len(paths)


def current_map(findings: list[Finding]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for finding in findings:
        grouped.setdefault(finding.workflow, set()).add(finding.input_name)
    return {k: tuple(sorted(v)) for k, v in sorted(grouped.items())}


def compare(current: dict[str, tuple[str, ...]],
            known: dict[str, tuple[str, ...]] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, notes).

    ERRORS (exit 1) are the direction that matters: a weak gate the freeze does
    not cover, or a freeze entry naming a workflow file that does not exist.

    NOTES (exit 0) are the other direction: a freeze entry whose site has since
    been FIXED. `check-workflow-input-interpolation.py` makes that fatal,
    because there every entry is removed by the same lane that fixes it. Here
    the single entry is fixed by a PR that predates this guard and cannot edit
    it (#1207 / #1212), so making it fatal would turn someone else's merge into
    a red `main` — a landmine, not a forcing function. The trade-off is real
    and stated rather than hidden: a fixed entry can linger, which is a
    documentation staleness cost, not a hole in the guard.
    """
    known = KNOWN_WEAK_GATES if known is None else known
    errors: list[str] = []
    notes: list[str] = []

    for workflow, names in sorted(current.items()):
        frozen = set(known.get(workflow, ()))
        new = sorted(set(names) - frozen)
        if new:
            errors.append(
                f"{workflow}: NOT in KNOWN_WEAK_GATES — gates {', '.join(new)} "
                f"with an empty-string comparison. Use "
                f"`-not [string]::IsNullOrWhiteSpace(...)`, which refuses "
                f"whitespace and an absent mapping as well as an empty string."
            )

    for workflow, names in sorted(known.items()):
        if not (WORKFLOWS / workflow).is_file():
            errors.append(
                f"{workflow}: listed in KNOWN_WEAK_GATES but the workflow file "
                f"does not exist. The freeze has stopped describing the tree."
            )
            continue
        fixed = sorted(set(names) - set(current.get(workflow, ())))
        if fixed:
            notes.append(
                f"{workflow}: KNOWN_WEAK_GATES still lists {', '.join(fixed)}, "
                f"which no longer gate with an empty-string comparison. The lane "
                f"that owns this file has landed — delete the entry."
            )
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    findings, scanned = scan_all()
    current = current_map(findings)
    errors, notes = compare(current)

    for note in notes:
        print(f"NOTE: {note}")
    if notes:
        print()

    if errors:
        print("::error::empty-string input gate(s) found\n")
        for error in errors:
            print(f"  {error}")
        print()
        for finding in findings:
            if finding.workflow in {e.split(":")[0] for e in errors}:
                print(f"  {finding}")
        print(
            "\n`-ne ''` is True for an UNSET variable and for whitespace, so an "
            "optional input gated this way reaches the callee blank — inside a "
            "write environment, after the approval. If a site really must ship "
            "as-is, add it to KNOWN_WEAK_GATES in "
            "scripts/check-workflow-empty-string-input-gate.py with the reason."
        )
        return 1

    total = sum(len(v) for v in current.values())
    print(
        f"empty-string input gate OK: {scanned} workflow files scanned; "
        f"{total} dispatch-input gate(s) still use an empty-string comparison, "
        f"across {len(current)} workflow(s), and all are in the KNOWN_WEAK_GATES "
        f"freeze.\n"
        f"This is a FREEZE on new instances, not an endorsement: `-ne ''` passes "
        f"whitespace and passes an absent `env:` mapping, so every entry is a "
        f"place a blank value reaches a callee that was told it had a real one "
        f"(#1213). Prefer `-not [string]::IsNullOrWhiteSpace(...)`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
