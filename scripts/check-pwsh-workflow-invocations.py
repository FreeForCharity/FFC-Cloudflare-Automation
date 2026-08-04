#!/usr/bin/env python3
r"""Guard: a workflow's inline pwsh must be able to CALL our PowerShell scripts (#1051).

`Update-CloudflareDns.ps1` carries 263 Pester cases. The ~20 places a workflow
decides how to *invoke* it carried none, and that seam is where #1050 broke:

    $argsList = @('-Zone', $Domain, '-EnforceStandard')
    & .\Update-CloudflareDns.ps1 @argsList

`$argsList` is an ARRAY, so `@argsList` is *array splatting*, which passes its
elements POSITIONALLY. The string '-Zone' arrives as a parameter *value*, never
as a parameter name. Only *hashtable* splatting binds by name. Run 30876332723
died with `A positional parameter cannot be found that accepts argument '-Zone'`
after a human had already spent an approval on the gate.

WHY THE SHAPE IS CHECKED AND NOT ONLY THE BINDING
    That run failed loudly by luck of the target's shape: `Update-CloudflareDns.ps1`
    has six parameter sets and no free positional slot, so binding refused. Where
    a positional slot exists the same code binds the FLAG STRING and says nothing.
    Measured on PowerShell 7.6.4 against a two-parameter stub:

        param([string]$Zone, [switch]$DryRun)
        $a = @('-Zone','abc','-DryRun'); & ./t.ps1 @a
        -> BOUND Zone='-Zone' DryRun=False        # exit 0, no warning

    So "bind it and see whether it throws" — the stronger check this guard also
    performs — cannot catch the dangerous case, because the dangerous case BINDS.
    The array-splat shape is therefore a finding in its own right, not a proxy
    for an unbindable call. A silent mis-bind in a Cloudflare write path is a
    charity's DNS.

WHAT IS AND IS NOT A FINDING
    Splatting is only wrong in one of the two positions this tree uses, and the
    other one is the majority, so getting the distinction wrong would flag ~20
    correct call sites. Both were measured, not reasoned about:

        & .\x.ps1 @array         -> Zone='-Zone'   BROKEN  (positional binding)
        & .\x.ps1 @hashtable     -> Zone='abc'     correct
        pwsh -File .\x.ps1 @array-> Zone='abc'     correct (native argv)
        pwsh -File .\x.ps1 @hash -> Zone='abc'     correct (renders -Key:Value)

    Only the first is reported. `pwsh -NoProfile -File <script> @args` is a
    NATIVE command: its splat becomes argv, and the child pwsh re-parses those
    strings as parameters, so an array is exactly right there.

THE BINDING CHECK (the general form)
    For every invocation whose target resolves inside this repo, every parameter
    name that is knowable statically — written as `-Name`, or a key of the
    splatted hashtable, or a flag string in an array splatted to a native host —
    is bound against the target's real `param()` block, read with `Get-Command`
    (which compiles parameter metadata WITHOUT executing the script). A name that
    matches no parameter or alias, a prefix that matches more than one, and a
    combination no single parameter set can satisfy are all findings.

FAIL CLOSED
    A `run:` body that does not parse, and a splat whose variable cannot be
    classified in the same body, are findings — not skips. A splat this guard
    cannot read is exactly the blind spot it exists to close.

Exit codes: 0 = every pwsh invocation of a repo script can bind, 1 = a finding
or a missing PowerShell host.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
FACTS_SCRIPT = "scripts/powershell-invocation-facts.ps1"

# Shells whose `run:` body is PowerShell. `powershell` is Windows PowerShell 5.1;
# the splatting semantics under test are identical in both.
PWSH_SHELLS = frozenset({"pwsh", "powershell"})

# Command names that run a script as a NATIVE child process rather than through
# the PowerShell binder.
NATIVE_HOSTS = frozenset({"pwsh", "pwsh.exe", "powershell", "powershell.exe"})

# `${{ … }}` is not PowerShell and would be parsed as a variable reference with a
# brace-quoted name. Replaced by a bare token BEFORE parsing; the substitution
# keeps the newline count so reported line numbers still point at the real line.
EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
EXPRESSION_TOKEN = "GHA_EXPR"

# Finding kinds.
ARRAY_SPLAT = "array-splat"
UNRESOLVED_SPLAT = "unresolved-splat"
UNKNOWN_PARAMETER = "unknown-parameter"
AMBIGUOUS_PARAMETER = "ambiguous-parameter"
NO_PARAMETER_SET = "no-parameter-set"
UNPARSEABLE = "unparseable"


@dataclass(frozen=True)
class Finding:
    kind: str
    workflow: str
    job: str
    step: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.workflow} :: {self.job} :: {self.step} (run: line {self.line}) [{self.kind}] {self.detail}"


@dataclass
class Unit:
    """One `run:` body written in PowerShell."""

    workflow: str
    job: str
    step: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.workflow}\x1f{self.job}\x1f{self.step}"


@dataclass
class Shape:
    """What a variable holds, merged across every assignment to it in one body."""

    kinds: set = field(default_factory=set)
    elements: list = field(default_factory=list)

    @property
    def kind(self) -> str:
        # `$a = @('-X', $v)` followed by `$a += '-Y'` is an array throughout: the
        # scalar `+=` never makes it something else. Only a hashtable assignment
        # competing with an array assignment is genuinely undecidable.
        if "hashtable" in self.kinds and "array" in self.kinds:
            return "conflicting"
        if "hashtable" in self.kinds:
            return "hashtable"
        if "array" in self.kinds:
            return "array"
        return "unknown"


def find_powershell() -> str | None:
    """A PowerShell host, or None. Same resolution order as the #930 guard."""
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def mask_expressions(text: str) -> str:
    """Replace `${{ … }}` with a bare token, preserving the line count."""

    def _replace(match: re.Match) -> str:
        return EXPRESSION_TOKEN + "\n" * match.group(0).count("\n")

    return EXPRESSION_RE.sub(_replace, text)


def _default_shell(container: dict) -> str | None:
    defaults = container.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    shell = run.get("shell")
    return shell if isinstance(shell, str) else None


def pwsh_units(workflow_text: str, workflow_name: str) -> list[Unit]:
    """Every `run:` body in one workflow that the runner executes as PowerShell."""
    doc = yaml.safe_load(workflow_text)
    if not isinstance(doc, dict):
        return []

    workflow_shell = _default_shell(doc)
    units: list[Unit] = []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_shell = _default_shell(job) or workflow_shell
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            shell = step.get("shell") or job_shell
            if shell not in PWSH_SHELLS:
                continue
            name = step.get("name") or "(unnamed)"
            units.append(
                Unit(
                    workflow=workflow_name,
                    job=str(job_id),
                    step=f'step {index} "{name}"',
                    text=mask_expressions(step["run"]),
                )
            )
    return units


def _run_facts(pwsh: str, bodies: list[Unit], scripts: list[str]) -> dict:
    """One PowerShell process for the whole repository, not one per call site."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        arguments = [pwsh, "-NoProfile", "-File", str(REPO_ROOT / FACTS_SCRIPT)]

        if bodies:
            body_file = tmp_path / "bodies.json"
            body_file.write_text(
                json.dumps([{"id": unit.id, "text": unit.text} for unit in bodies]),
                encoding="utf-8",
            )
            arguments += ["-BodyListFile", str(body_file)]
        if scripts:
            script_file = tmp_path / "scripts.json"
            script_file.write_text(json.dumps(scripts), encoding="utf-8")
            arguments += ["-ScriptListFile", str(script_file)]

        # encoding= is explicit: text mode decodes with the locale codec, which
        # is cp1252 on a Windows host, and workflow bodies carry em-dashes.
        completed = subprocess.run(
            arguments,
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


def resolve_script(raw: str) -> pathlib.Path | None:
    """A `.ps1` path as written in a workflow -> a file in this repo, or None.

    Windows separators, `.\\`, `./` and a bare `scripts/` prefix all appear in
    this tree. A path that resolves to nothing is not this guard's business:
    702 and 121 run scripts out of a repository they clone at runtime, and the
    working-directory guard (#972) already owns "the path is wrong".
    """
    if not raw:
        return None
    text = raw.replace("\\", "/").strip().strip("'\"")
    if not text.lower().endswith(".ps1"):
        return None
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or ".." in text.split("/"):
        return None
    candidate = REPO_ROOT / text
    if candidate.is_file():
        return candidate
    return None


def _shapes(assignments: list[dict]) -> dict[str, Shape]:
    shapes: dict[str, Shape] = {}
    for assignment in assignments:
        shape = shapes.setdefault(assignment["name"].lower(), Shape())
        kind = assignment["kind"]
        if kind == "key":
            # `$params.DryRun = $true` only makes sense on a hashtable.
            shape.kinds.add("hashtable")
            shape.elements.extend(e for e in assignment["elements"] if e)
            continue
        if kind == "scalar":
            # An element appended with `+=`; it does not decide the container.
            shape.elements.extend(e for e in assignment["elements"] if e)
            continue
        shape.kinds.add(kind)
        shape.elements.extend(e for e in assignment["elements"] if e)
    return shapes


def _target_of(command: dict) -> tuple[str | None, bool, list[dict]]:
    """(script path as written, is-native-invocation, arguments meant for it).

    For `& .\\x.ps1 -Zone $d` every argument belongs to the script. For
    `pwsh -NoProfile -File .\\x.ps1 @a` only what follows the script path does —
    `-NoProfile` and `-File` are the HOST's parameters, and attributing them to
    the script would report the host's own flags as unknown parameters.
    """
    name = (command.get("name") or "").strip()
    arguments = command.get("arguments") or []

    if name.lower().endswith(".ps1"):
        return name, False, list(arguments)

    if pathlib.PurePosixPath(name.replace("\\", "/")).name.lower() in NATIVE_HOSTS:
        for position, argument in enumerate(arguments):
            text = (argument.get("text") or "").strip()
            if argument["kind"] == "positional" and text.lower().endswith(".ps1"):
                return text, True, list(arguments[position + 1 :])
    return None, False, []


def _resolve_parameter(name: str, parameters: list[dict]) -> tuple[str | None, list[str]]:
    """PowerShell's own name resolution: exact, else alias, else unique prefix."""
    lowered = name.lower()
    for parameter in parameters:
        if parameter["name"].lower() == lowered:
            return parameter["name"], []
        if any(alias.lower() == lowered for alias in parameter["aliases"]):
            return parameter["name"], []

    prefixed = [
        parameter["name"]
        for parameter in parameters
        if parameter["name"].lower().startswith(lowered)
        or any(alias.lower().startswith(lowered) for alias in parameter["aliases"])
    ]
    if len(prefixed) == 1:
        return prefixed[0], []
    return None, sorted(prefixed)


def _bind(
    supplied: list[tuple[str, int]],
    script_facts: dict,
    context: str,
) -> list[tuple[str, int, str]]:
    """Bind statically-known parameter NAMES against a real `param()` block."""
    results: list[tuple[str, int, str]] = []
    parameters = script_facts["parameters"]
    resolved: list[str] = []

    for name, line in supplied:
        canonical, ambiguous = _resolve_parameter(name, parameters)
        if canonical is None and ambiguous:
            results.append(
                (
                    AMBIGUOUS_PARAMETER,
                    line,
                    f"{context}: -{name} matches more than one parameter of "
                    f"{script_facts['path']} ({', '.join(ambiguous)})",
                )
            )
            continue
        if canonical is None:
            results.append(
                (
                    UNKNOWN_PARAMETER,
                    line,
                    f"{context}: -{name} is not a parameter of {script_facts['path']}",
                )
            )
            continue
        resolved.append(canonical)

    # Every name resolved on its own; the combination still has to live in ONE
    # parameter set, which is what refused #1050's call at runtime.
    sets = script_facts["parameterSets"]
    if resolved and sets:
        wanted = set(resolved)
        if not any(wanted <= set(one["parameters"]) for one in sets):
            names = ", ".join(f"-{name}" for name in sorted(wanted))
            available = ", ".join(one["name"] for one in sets)
            results.append(
                (
                    NO_PARAMETER_SET,
                    supplied[0][1],
                    f"{context}: no parameter set of {script_facts['path']} accepts "
                    f"{{{names}}} together (sets: {available})",
                )
            )
    return results


def find_findings(unit: Unit, body: dict, script_facts: dict[str, dict]) -> list[Finding]:
    """Judge one `run:` body against the facts extracted from it."""
    findings: list[Finding] = []

    def report(kind: str, line: int, detail: str) -> None:
        findings.append(
            Finding(
                kind=kind,
                workflow=unit.workflow,
                job=unit.job,
                step=unit.step,
                line=line,
                detail=detail,
            )
        )

    if body["parseErrors"]:
        first = body["parseErrors"][0]
        report(UNPARSEABLE, first["line"], f"PowerShell parse error: {first['message']}")
        return findings

    shapes = _shapes(body["assignments"])

    for command in body["commands"]:
        raw_path, is_native, arguments = _target_of(command)
        if raw_path is None:
            continue

        splats = [argument for argument in arguments if argument["kind"] == "splat"]

        # --- the #1050 shape -------------------------------------------------
        for splat in splats:
            shape = shapes.get(splat["text"].lower(), Shape())
            if is_native:
                # Native argv: an array is correct and a hashtable renders as
                # -Key:Value, which the child pwsh binds by name. Neither is a
                # finding, so nothing is reported for a native splat.
                continue
            if shape.kind == "array":
                flags = [e for e in shape.elements if e and e.startswith("-")]
                lead = f" — '{flags[0]}' is passed as a VALUE, not as a parameter name" if flags else ""
                report(
                    ARRAY_SPLAT,
                    splat["line"],
                    f"@{splat['text']} splats an ARRAY into {raw_path}, which binds "
                    f"positionally{lead}. Build the arguments as a hashtable "
                    f"(@{{Zone = $Domain}}) so they bind by name.",
                )
            elif shape.kind != "hashtable":
                report(
                    UNRESOLVED_SPLAT,
                    splat["line"],
                    f"@{splat['text']} splats into {raw_path} and this guard cannot tell "
                    f"whether it is an array or a hashtable ({shape.kind}). Assign it a "
                    f"literal in the same run: block, or the difference between binding "
                    f"by name and binding positionally is unreviewable.",
                )

        # --- the general binding check --------------------------------------
        target = resolve_script(raw_path)
        if target is None:
            continue
        facts = script_facts.get(target.relative_to(REPO_ROOT).as_posix())
        if facts is None or not facts["resolved"]:
            continue

        supplied: list[tuple[str, int]] = []
        for argument in arguments:
            if argument["kind"] == "parameter":
                supplied.append((argument["text"], argument["line"]))
            elif argument["kind"] == "splat":
                shape = shapes.get(argument["text"].lower(), Shape())
                if shape.kind == "hashtable":
                    supplied.extend((key, argument["line"]) for key in shape.elements if key)
                elif shape.kind == "array" and is_native:
                    # argv for the child host: the flag-shaped literals in the
                    # array are the script's parameter names.
                    supplied.extend(
                        (element[1:], argument["line"])
                        for element in shape.elements
                        if element and element.startswith("-") and len(element) > 1
                    )

        for kind, line, detail in _bind(supplied, facts, f"call to {raw_path}"):
            report(kind, line, detail)

    return findings


def scan_repo(
    root: pathlib.Path = REPO_ROOT,
    workflows: pathlib.Path = WORKFLOWS,
    pwsh: str | None = None,
) -> tuple[list[Finding], int]:
    """(findings, number of pwsh run: bodies scanned)."""
    pwsh = pwsh or find_powershell()
    if not pwsh:
        raise RuntimeError(
            "no PowerShell host on PATH. This guard reads PowerShell with the "
            "PowerShell parser and refuses to report a pass it did not measure."
        )

    units: list[Unit] = []
    for path in sorted(list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml"))):
        units.extend(pwsh_units(path.read_text(encoding="utf-8"), path.name))
    if not units:
        return [], 0

    facts = _run_facts(pwsh, units, [])
    bodies = {body["id"]: body for body in facts["bodies"]}

    # Second pass: only now is the set of target scripts known.
    wanted: set[str] = set()
    for unit in units:
        for command in bodies.get(unit.id, {}).get("commands", []):
            raw_path, _, _ = _target_of(command)
            target = resolve_script(raw_path) if raw_path else None
            if target is not None:
                wanted.add(target.relative_to(root).as_posix())

    script_facts: dict[str, dict] = {}
    if wanted:
        for entry in _run_facts(pwsh, [], sorted(wanted))["scripts"]:
            script_facts[entry["path"]] = entry

    findings: list[Finding] = []
    for unit in units:
        body = bodies.get(unit.id)
        if body is None:
            raise RuntimeError(f"{FACTS_SCRIPT} returned no facts for {unit.id}")
        findings.extend(find_findings(unit, body, script_facts))
    return findings, len(units)


def main() -> int:
    try:
        findings, scanned = scan_repo()
    except RuntimeError as error:
        print(f"pwsh invocation guard could not run: {error}")
        return 1

    if findings:
        print(f"pwsh invocations of repo scripts that cannot bind: {len(findings)}\n")
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nArray splatting (@list) into a script invocation binds POSITIONALLY, so a\n"
            "'-Zone' in the list arrives as a VALUE. Where the target has a free positional\n"
            "slot this succeeds silently with the flag as the value — measured, exit 0 — so\n"
            "the shape is the finding, not the error. Hashtable splatting (@{Zone = $d})\n"
            "binds by name. See issue #1051 and PR #1050."
        )
        return 1

    print(
        f"pwsh invocation binding OK: {scanned} PowerShell run: bodies scanned; every "
        f"splat into a repo script binds by name and every parameter name resolves."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
