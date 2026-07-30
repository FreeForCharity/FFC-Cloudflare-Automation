#!/usr/bin/env python3
"""PowerShell command-resolution guard (issue #930).

`scripts/whmcs-application-search.ps1` called `Remove-Html`, a command defined
nowhere in the repository (#929). Every run of workflow 221 died on it. It stayed
invisible for as long as 221 sat behind an approval gate nobody granted, and
surfaced within minutes of #926 making that lane ungated.

PowerShell resolves commands at *runtime*, so nothing fails until that exact line
executes with that exact data — and the call site reads perfectly naturally, so a
reviewer will not catch it either. PSScriptAnalyzer does not catch it: it ran on
the fix PR and had nothing to say about the missing command. `scripts/` holds 40+
`.ps1` files, most reachable only through a gated `workflow_dispatch`, so any of
them can carry the same defect today with nobody the wiser.

This guard resolves, statically, every command each PowerShell file calls.

HOW A COMMAND RESOLVES
    A call resolves if the name is any of:
      1. a command the running PowerShell knows (Get-Command: cmdlets, functions
         and aliases from every module on PSModulePath), unioned with the
         committed floor snapshot config/powershell-cmdlet-inventory.json so the
         verdict does not depend on which optional modules happen to be present;
      2. a function the file itself defines, at any nesting depth;
      3. a function defined in anything the file dot-sources, transitively;
      4. for a *library* (a file other files dot-source), a function that EVERY
         one of its consumers has in scope. `scripts/whmcs-metrics-common.ps1`
         calls `Invoke-WhmcsApi`, which lives in `whmcs-api-common.ps1` — a
         sibling library that all four of its consumers dot-source alongside it.
         The intersection is deliberate: if even one consumer does not provide
         the function, that consumer's run breaks, and this reports it.

    Anything left is an error naming the file, line and command.

WHAT IT CANNOT SEE — stated here rather than papered over
    * `& $variable` / `& $scriptblock` invocation: the name is not in the source.
    * `Invoke-Expression` string bodies, and any name assembled at runtime.
    * Bare external executables (`az`, `gh`, `git`, `npx`): resolved from PATH on
      the runner, not from the source tree. Only Verb-Noun names are checked.
    * Commands from an external module (Microsoft.Graph, ExchangeOnlineManagement,
      ImportExcel). Those modules are installed at runtime by the scripts that
      need them and are far too large to inventory in CI, so a file importing one
      is reported as PARTIALLY scanned — its unresolvable calls are listed as
      unverified rather than silently dropped, and the module must be declared in
      DECLARED_EXTERNAL_MODULES below. Adding a new external dependency is a
      deliberate one-line declaration, not an open-ended allowlist of cmdlet
      names.

FAIL CLOSED
    A file that cannot be parsed is an error, never a skip — a silently skipped
    file is exactly the blind spot this closes. A file whose dot-source path is
    computed (`. $script:ScriptPath`) cannot have its resolution set determined,
    so it is skipped *and reported*, and the skip count is printed on success as
    well as failure.

REPORT, NEVER REWRITE
    The fix for a genuine finding is a human decision — define the function,
    dot-source the library that has it, or delete the call.

Exit codes: 0 = every call resolves, 1 = a finding, a parse error, or a missing
tool/inventory.

    python3 scripts/check-powershell-command-resolution.py
    python3 scripts/check-powershell-command-resolution.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import posixpath
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FACTS_SCRIPT = "scripts/powershell-command-facts.ps1"
INVENTORY_FILE = "config/powershell-cmdlet-inventory.json"

# Modules the tree imports at runtime that CI does not (and should not) install.
# A file importing one of these is scanned for everything else and reported as
# partially covered. Keep the reason with the entry: an undocumented exemption
# becomes a permanent allowance for the defect the guard exists to catch.
DECLARED_EXTERNAL_MODULES = {
    "Microsoft.Graph": "M365 tenant/domain reads; installed by the script at runtime",
    "ExchangeOnlineManagement": "DKIM + accepted-domain reads; installed at runtime",
    "ImportExcel": "optional .xlsx writer for the Zeffy payments draft",
}


@dataclass(frozen=True)
class Finding:
    """One unresolvable command call."""

    file: str
    line: int
    command: str

    def __str__(self) -> str:
        return (
            f"{self.file}:{self.line}: calls '{self.command}', which is not a known "
            f"cmdlet and is defined neither in this file nor in anything it "
            f"dot-sources. Define it, dot-source the library that has it, or "
            f"delete the call."
        )


@dataclass
class FileFacts:
    """Parsed facts for one PowerShell file (see the fact extractor's header)."""

    path: str
    functions: set[str] = field(default_factory=set)
    dot_sources: list[dict] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    parse_errors: list[dict] = field(default_factory=list)


def find_powershell() -> str | None:
    """Path to a PowerShell host, honouring a PWSH override."""
    override = os.environ.get("PWSH")
    if override and (shutil.which(override) or pathlib.Path(override).exists()):
        return override
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def tracked_powershell_files(root: pathlib.Path) -> list[str]:
    """Every tracked .ps1 / .psm1, repo-relative, sorted."""
    proc = subprocess.run(
        ["git", "ls-files", "*.ps1", "*.psm1"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in proc.stdout.splitlines() if line.strip())


def extract_facts(
    root: pathlib.Path, files: list[str], pwsh: str, include_inventory: bool = True
) -> dict:
    """Run the AST fact extractor over `files` and return its JSON payload.

    `include_inventory` drives a Get-Command sweep, which forces module
    auto-discovery across the whole PSModulePath — negligible on a bare host,
    seconds on a runner carrying the Az modules. Callers parsing a handful of
    fixture files should leave it off; the real scan asks for it once.
    """
    # Explicit UTF-8: the default text encoding is platform-dependent, and a
    # non-ASCII path would be written in one encoding and read in another.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as handle:
        handle.write("\n".join(files))
        list_file = handle.name
    try:
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-File", FACTS_SCRIPT, "-PathListFile", list_file]
            + (["-IncludeInventory"] if include_inventory else []),
            cwd=root,
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(list_file)

    if proc.returncode != 0:
        raise RuntimeError(
            f"the fact extractor failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"the fact extractor emitted non-JSON: {exc}") from exc


def load_facts(payload: dict) -> dict[str, FileFacts]:
    """Turn the extractor payload into FileFacts keyed by repo-relative path."""
    facts: dict[str, FileFacts] = {}
    for entry in payload["files"]:
        facts[entry["file"]] = FileFacts(
            path=entry["file"],
            functions=set(entry["functions"]),
            dot_sources=list(entry["dotSources"]),
            imports=list(entry["imports"]),
            commands=list(entry["commands"]),
            parse_errors=list(entry["parseErrors"]),
        )
    return facts


def dot_source_target(source_file: str, target: str) -> str:
    """Repo-relative path a dot-source resolves to.

    `target` is relative to the dot-sourcing file's own directory ($PSScriptRoot),
    with `..` hops preserved by the extractor.
    """
    base = posixpath.dirname(source_file)
    return posixpath.normpath(posixpath.join(base, target)) if base else posixpath.normpath(target)


def transitive_definitions(
    path: str, facts: dict[str, FileFacts], _seen: set[str] | None = None
) -> set[str]:
    """Functions in scope for `path`: its own, plus every file it dot-sources."""
    seen = _seen if _seen is not None else set()
    if path in seen or path not in facts:
        return set()
    seen.add(path)
    names = set(facts[path].functions)
    for dot in facts[path].dot_sources:
        if not dot["resolved"]:
            continue
        names |= transitive_definitions(dot_source_target(path, dot["target"]), facts, seen)
    return names


def consumers_of(path: str, facts: dict[str, FileFacts]) -> list[str]:
    """Files that dot-source `path` (its callers, if it is a library)."""
    out = []
    for other, other_facts in facts.items():
        if other == path:
            continue
        for dot in other_facts.dot_sources:
            if dot["resolved"] and dot_source_target(other, dot["target"]) == path:
                out.append(other)
                break
    return sorted(out)


def skipped_files(facts: dict[str, FileFacts]) -> dict[str, str]:
    """Files whose resolution set is unknowable, with the reason."""
    skipped = {}
    for path, file_facts in facts.items():
        computed = [d for d in file_facts.dot_sources if not d["resolved"]]
        if computed:
            shape = computed[0]["text"].strip().replace("\n", " ")[:60]
            skipped[path] = f"dot-sources a computed path ({shape})"
    return skipped


def missing_dot_source_targets(facts: dict[str, FileFacts]) -> list[str]:
    """Dot-sources pointing at a file that is not in the tree."""
    problems = []
    for path, file_facts in facts.items():
        for dot in file_facts.dot_sources:
            if not dot["resolved"]:
                continue
            target = dot_source_target(path, dot["target"])
            if target not in facts:
                problems.append(
                    f"{path}:{dot['line']}: dot-sources '{target}', which is not a "
                    f"tracked PowerShell file in this repository."
                )
    return sorted(problems)


def resolution_set(path: str, facts: dict[str, FileFacts], skipped: dict[str, str]) -> set[str]:
    """Every function name in scope for `path`, including the library rule."""
    names = transitive_definitions(path, facts)
    consumers = [c for c in consumers_of(path, facts) if c not in skipped]
    if consumers:
        # A library may call what all of its consumers guarantee — and only that.
        shared: set[str] | None = None
        for consumer in consumers:
            available = transitive_definitions(consumer, facts)
            shared = available if shared is None else (shared & available)
        names |= shared or set()
    return names


def audit(
    facts: dict[str, FileFacts], inventory: set[str]
) -> tuple[list[Finding], dict[str, list[Finding]], dict[str, str], list[str]]:
    """Resolve every call.

    Returns (findings, unverified-by-file, skipped-by-file, structural errors).
    """
    known = {name.lower() for name in inventory}
    skipped = skipped_files(facts)
    structural = missing_dot_source_targets(facts)

    for path, file_facts in facts.items():
        for err in file_facts.parse_errors:
            structural.append(f"{path}:{err['line']}: parse error: {err['message']}")

    findings: list[Finding] = []
    unverified: dict[str, list[Finding]] = {}

    for path in sorted(facts):
        if path in skipped:
            continue
        file_facts = facts[path]
        # An import of a module that is NOT declared here gets no exemption: its
        # unresolvable calls stay errors, which is what forces the declaration.
        external = [m for m in file_facts.imports if m in DECLARED_EXTERNAL_MODULES]
        local ={name.lower() for name in resolution_set(path, facts, skipped)}

        for call in file_facts.commands:
            name = call["name"]
            if "-" not in name:
                continue  # bare external executable; not statically resolvable
            lowered = name.lower()
            if lowered in known or lowered in local:
                continue
            finding = Finding(file=path, line=call["line"], command=name)
            if external:
                unverified.setdefault(path, []).append(finding)
            else:
                findings.append(finding)

    return findings, unverified, skipped, sorted(structural)


def load_inventory(root: pathlib.Path) -> set[str]:
    """The committed floor snapshot of known command names."""
    path = root / INVENTORY_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["inventory"])


def run(root: pathlib.Path, verbose: bool = False) -> int:
    pwsh = find_powershell()
    if not pwsh:
        print(
            "::error::no PowerShell host found (looked for $PWSH, pwsh, powershell). "
            "This guard parses PowerShell with the PowerShell parser; without a host "
            "it cannot make a claim, so it fails rather than passing silently."
        )
        return 1

    files = tracked_powershell_files(root)
    if not files:
        print("::error::no tracked PowerShell files found — the scan would be vacuous.")
        return 1

    try:
        payload = extract_facts(root, files, pwsh)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1

    # A corrupt snapshot or an unexpected payload shape is a scan that did not
    # happen. Report it as such rather than as a traceback: the docstring
    # promises this path fails closed, and a traceback in a CI log reads as a
    # broken script rather than as an unmet precondition.
    try:
        inventory = load_inventory(root) | set(payload.get("inventory", []))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"::error::could not read {INVENTORY_FILE}: {exc}. Regenerate it with "
            f"`pwsh -NoProfile -File {FACTS_SCRIPT} -InventoryOnly`."
        )
        return 1
    try:
        facts = load_facts(payload)
    except (KeyError, TypeError) as exc:
        print(
            f"::error::the fact extractor's payload is not the expected shape "
            f"({exc}) — nothing was scanned."
        )
        return 1
    findings, unverified, skipped, structural = audit(facts, inventory)

    for path, reason in sorted(skipped.items()):
        print(f"::warning::{path} not scanned: {reason}")
    for path, items in sorted(unverified.items()):
        modules = ", ".join(m for m in facts[path].imports if m in DECLARED_EXTERNAL_MODULES)
        names = ", ".join(sorted({f.command for f in items}))
        print(f"::warning::{path} partially scanned (imports {modules}): unverified {names}")

    if verbose:
        for path in sorted(facts):
            print(f"  {path}: {len(facts[path].commands)} call(s)")

    if not findings and not structural:
        calls = sum(len(f.commands) for f in facts.values())
        print(
            f"PowerShell command resolution OK: {calls} call(s) across "
            f"{len(files)} file(s) resolve; {len(skipped)} file(s) skipped "
            f"(computed dot-source), {len(unverified)} partially scanned "
            f"(external module)."
        )
        return 0

    for problem in structural:
        print(f"::error::{problem}")
    for finding in findings:
        print(f"::error::{finding}")
    print(
        f"FAILED: {len(findings)} unresolvable command call(s), "
        f"{len(structural)} structural error(s)."
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verbose", action="store_true", help="list every scanned file and its call count"
    )
    args = parser.parse_args(argv)
    return run(REPO_ROOT, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
