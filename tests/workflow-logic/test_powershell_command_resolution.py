"""Unit tests for scripts/check-powershell-command-resolution.py (#930).

The checker is the shipped implementation — this module imports it rather than
re-deriving its rules, so the logic under test is the logic that runs in CI.

What the guard is for: `scripts/whmcs-application-search.ps1` called `Remove-Html`,
a command defined nowhere in the repo (#929). Every run of workflow 221 died on
it, and it stayed invisible for as long as 221 sat behind an approval gate nobody
granted. PowerShell resolves commands at runtime; PSScriptAnalyzer had nothing to
say about it; the call site reads perfectly naturally to a reviewer.

The pre-fix shape is pinned HERE, by deep-copying the two real scripts into a
fixture tree and deleting `function Remove-Html` from the library. The live
defect was fixed by #931, and a guard whose evidence is a defect left in the tree
is a guard that has to be disabled by whoever fixes the defect (ledger L09, and
the #926 precedent).

Both directions matter, so both are asserted:

  * a call that resolves nowhere is an ERROR — `test_the_remove_html_shape_is_flagged`
  * the shapes that merely LOOK unresolvable are not findings — a Verb-Noun in a
    comment or a string, `& $variable` invocation, splatted parameters, a
    function defined inside an `if` block, a bare external like `az`. That is
    where the first draft of a regex-based scan goes wrong, and a guard that
    reports ~50 false findings on working scripts gets switched off within a
    month (ledger L09 / the #927 precedent).

Every rule branch has a test that fails when the branch is deleted; the mutation
run is recorded in the PR body.

PowerShell dependency: the AST-backed tests need a PowerShell host. CI always has
one (722-ci.yml already runs several `shell: pwsh` steps), and the guard itself
fails closed when no host is present — asserted by
`test_a_missing_powershell_host_fails_closed`, which needs no host to run. On a
machine without PowerShell those tests report SKIPPED rather than a false red,
per the CLAUDE.md rule that CI, not a local sandbox, is authoritative here.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-powershell-command-resolution.py"

spec = importlib.util.spec_from_file_location("check_ps_command_resolution", SCRIPT)
checker = importlib.util.module_from_spec(spec)
assert spec.loader is not None
# Register before exec: the checker's @dataclass resolves its annotations through
# sys.modules, and an unregistered module makes that lookup fail.
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)

PWSH = checker.find_powershell()
SKIPPED: list[str] = []


class Skip(Exception):
    """Raised by an AST-backed test when no PowerShell host is available."""


def require_pwsh() -> str:
    if not PWSH:
        raise Skip("no PowerShell host")
    return PWSH


def facts_for(root: pathlib.Path, files: list[str]) -> dict:
    """Parse `files` inside fixture `root` with the real AST extractor."""
    pwsh = require_pwsh()
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / checker.FACTS_SCRIPT, root / checker.FACTS_SCRIPT)
    # No inventory: these fixtures assert against an explicit command set, and a
    # Get-Command sweep per fixture costs seconds each on a CI runner.
    payload = checker.extract_facts(root, files, pwsh, include_inventory=False)
    assert payload["inventory"] == [], "fixture parses must not pay for a Get-Command sweep"
    return checker.load_facts(payload)


def write(root: pathlib.Path, rel: str, body: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return rel


def audit_names(facts: dict, inventory: set[str] | None = None) -> list[str]:
    """Findings as 'file:line:command' strings, for terse assertions."""
    findings, _unverified, _skipped, structural = checker.audit(
        facts,
        inventory
        or {"Write-Host", "Get-Content", "Join-Path", "Split-Path", "Import-Module"},
    )
    assert not structural, structural
    return [f"{f.file}:{f.line}:{f.command}" for f in findings]


# --------------------------------------------------------------------------
# The defect this exists for, pinned by mutating a copy of the real scripts.
# --------------------------------------------------------------------------


def test_the_remove_html_shape_is_flagged():
    """Delete `function Remove-Html` from the library; the call must be reported.

    This is #929 exactly: an entry script calling a Verb-Noun command that its
    dot-sourced library does not define. Built by deep-copying the two real
    scripts and reverting the fix, so the evidence does not depend on the defect
    surviving in the tree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "scripts").mkdir(parents=True)
        for name in ("whmcs-api-common.ps1", "whmcs-application-search.ps1"):
            shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)

        lib = root / "scripts" / "whmcs-api-common.ps1"
        source = lib.read_text(encoding="utf-8")
        assert "function Remove-Html" in source, "the real library no longer defines Remove-Html"

        # Excise the function body, brace-balanced, leaving every other line intact.
        lines = source.splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line.startswith("function Remove-Html"))
        depth, end = 0, None
        for i in range(start, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if depth == 0 and i > start:
                end = i
                break
        assert end is not None, "could not find the end of function Remove-Html"
        mutated = lines[:start] + lines[end + 1 :]
        lib.write_text("".join(mutated), encoding="utf-8")
        assert "function Remove-Html" not in lib.read_text(encoding="utf-8")

        facts = facts_for(root, ["scripts/whmcs-api-common.ps1", "scripts/whmcs-application-search.ps1"])
        findings, _unverified, _skipped, _structural = checker.audit(facts, set())
        remove_html = [f for f in findings if f.command == "Remove-Html"]

        assert remove_html, (
            "the guard did not flag Remove-Html after it was deleted from the "
            "library — it would not have caught #929."
        )
        assert any(f.file == "scripts/whmcs-application-search.ps1" for f in remove_html), (
            f"the finding must name the calling script; got {[f.file for f in remove_html]}"
        )
        assert "Define it, dot-source the library" in str(remove_html[0]), (
            "the message must tell the reader what to do about it"
        )


def test_the_fixed_shape_resolves():
    """The same two scripts, unmutated: the call resolves through the library."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "scripts").mkdir(parents=True)
        for name in ("whmcs-api-common.ps1", "whmcs-application-search.ps1"):
            shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)
        facts = facts_for(root, ["scripts/whmcs-api-common.ps1", "scripts/whmcs-application-search.ps1"])
        findings, _u, _s, _st = checker.audit(facts, set())
        assert not [f for f in findings if f.command == "Remove-Html"], (
            "Remove-Html is defined in the dot-sourced library and must resolve"
        )


# --------------------------------------------------------------------------
# The false-positive surface — where a guard like this earns its deletion.
# --------------------------------------------------------------------------


def test_a_verb_noun_in_a_comment_or_string_is_not_a_call():
    """The AST is the whole reason this is not a regex."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/prose.ps1",
            "# Call Invoke-NothingAtAll here one day\n"
            "$name = 'Get-ImaginaryThing'\n"
            "$doc = @\"\nRun Remove-Everything to clean up\n\"@\n"
            "Write-Host $name $doc\n",
        )
        assert audit_names(facts_for(root, ["scripts/prose.ps1"])) == []


def test_ampersand_invocation_is_out_of_scope():
    """`& $var` has no statically known name; claiming otherwise invents findings."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/dynamic.ps1",
            "$tool = 'Get-Something'\n& $tool\n$block = { Write-Host 'hi' }\n& $block\n",
        )
        assert audit_names(facts_for(root, ["scripts/dynamic.ps1"])) == []


def test_splatting_and_parameter_values_are_not_commands():
    """Only command position counts."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/splat.ps1",
            "$params = @{ Path = 'Get-Fake-Value'; Name = 'Set-Nothing' }\n"
            "Write-Host @params\n"
            "Write-Host -Object 'New-Illusion'\n",
        )
        assert audit_names(facts_for(root, ["scripts/splat.ps1"])) == []


def test_a_function_defined_inside_another_function_resolves():
    """`Update-CloudflareDns.ps1` defines Get-DmarcManagementStatus inside a
    function body and calls it from the enclosing one. Searching only the
    top-level scope (FindAll's nested-scriptblock flag off) reports three such
    calls in that one file as broken — a working script, red.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/nested.ps1",
            "function Invoke-Outer {\n"
            "    function Get-Inner { 'inner' }\n"
            "    Get-Inner\n"
            "}\n"
            "if ($true) {\n    function Get-Conditional { 'ok' }\n}\n"
            "Get-Conditional\nInvoke-Outer\n",
        )
        assert audit_names(facts_for(root, ["scripts/nested.ps1"])) == []


def test_bare_external_executables_are_not_flagged():
    """`az`, `gh`, `git` resolve from PATH on the runner, not from the tree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/ext.ps1", "az account show\ngh pr list\ngit status\nnpx prettier --check .\n")
        assert audit_names(facts_for(root, ["scripts/ext.ps1"])) == []


# --------------------------------------------------------------------------
# Resolution rules, each with the mutation that must fail it.
# --------------------------------------------------------------------------


def test_a_transitive_dot_source_resolves():
    """a → b → c: a may call what c defines. Deleting the recursion fails this."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/c.ps1", "function Get-Deep { 'deep' }\n")
        write(root, "scripts/b.ps1", ". (Join-Path $PSScriptRoot 'c.ps1')\nfunction Get-Mid { 'mid' }\n")
        write(root, "scripts/a.ps1", ". (Join-Path $PSScriptRoot 'b.ps1')\nGet-Deep\nGet-Mid\n")
        assert audit_names(facts_for(root, ["scripts/a.ps1", "scripts/b.ps1", "scripts/c.ps1"])) == []


def test_a_parent_directory_dot_source_resolves():
    """`. (Join-Path $PSScriptRoot '..' 'scripts' 'x.ps1')` — the Tests.ps1 shape.

    Collapsing the `..` hop onto the file's own directory (an easy extractor bug)
    makes the target unresolvable and turns every Pester file into findings.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/thing.ps1", "function Get-Thing { 'thing' }\n")
        write(
            root,
            "tests/thing.Tests.ps1",
            ". (Join-Path $PSScriptRoot '..' 'scripts' 'thing.ps1')\nGet-Thing\n",
        )
        assert audit_names(facts_for(root, ["scripts/thing.ps1", "tests/thing.Tests.ps1"])) == []


def test_a_split_path_parent_dot_source_inside_a_scriptblock_resolves():
    """The real Pester shape: `. (Join-Path (Split-Path $PSScriptRoot -Parent) …)`
    inside a `BeforeAll { }` block.

    Two rules meet here — the parent-directory hop, and finding a dot-source
    that lives inside a nested scriptblock. Losing either turns every
    scripts/tests/*.Tests.ps1 file into a page of findings.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/lib.ps1", "function Get-Subject { 'subject' }\n")
        write(
            root,
            "scripts/tests/lib.Tests.ps1",
            "BeforeAll {\n"
            "    . (Join-Path (Split-Path $PSScriptRoot -Parent) 'lib.ps1')\n"
            "}\n"
            "Get-Subject\n",
        )
        assert audit_names(facts_for(root, ["scripts/lib.ps1", "scripts/tests/lib.Tests.ps1"])) == []


def test_a_dot_source_with_a_computed_segment_is_not_treated_as_literal():
    """`. "$PSScriptRoot/$name.ps1"` names a file only at runtime.

    Accepting it as literal would silently resolve against the wrong file — a
    guard confidently scanning the wrong resolution set is worse than one that
    admits it cannot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/computed-segment.ps1",
            "$name = 'lib'\n. \"$PSScriptRoot/$name.ps1\"\nGet-WhateverThatLoads\n",
        )
        facts = facts_for(root, ["scripts/computed-segment.ps1"])
        findings, _u, skipped, _st = checker.audit(facts, set())
        assert "scripts/computed-segment.ps1" in skipped, (
            "a dot-source path built from a variable must not be treated as literal"
        )
        assert not findings


def test_a_library_resolves_against_what_every_consumer_provides():
    """`whmcs-metrics-common.ps1` calls `Invoke-WhmcsApi` from a sibling library.

    Its consumers dot-source both. Deleting the consumer-intersection branch
    turns that working pattern into a finding.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/api.ps1", "function Invoke-Api { 'api' }\n")
        write(root, "scripts/metrics.ps1", "function Get-Metric { Invoke-Api }\n")
        for name in ("one", "two"):
            write(
                root,
                f"scripts/{name}.ps1",
                ". (Join-Path $PSScriptRoot 'api.ps1')\n"
                ". (Join-Path $PSScriptRoot 'metrics.ps1')\nGet-Metric\n",
            )
        files = ["scripts/api.ps1", "scripts/metrics.ps1", "scripts/one.ps1", "scripts/two.ps1"]
        assert audit_names(facts_for(root, files)) == []


def test_a_function_only_some_consumers_provide_is_still_flagged():
    """The intersection is the point: one consumer short is a broken run.

    Widening the rule from intersection to union would pass this — and would
    hide a library call that dies for every consumer but one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/api.ps1", "function Invoke-Api { 'api' }\n")
        write(root, "scripts/metrics.ps1", "function Get-Metric { Invoke-Api }\n")
        write(
            root,
            "scripts/one.ps1",
            ". (Join-Path $PSScriptRoot 'api.ps1')\n. (Join-Path $PSScriptRoot 'metrics.ps1')\nGet-Metric\n",
        )
        write(root, "scripts/two.ps1", ". (Join-Path $PSScriptRoot 'metrics.ps1')\nGet-Metric\n")
        files = ["scripts/api.ps1", "scripts/metrics.ps1", "scripts/one.ps1", "scripts/two.ps1"]
        found = audit_names(facts_for(root, files))
        assert any("Invoke-Api" in f for f in found), (
            "scripts/two.ps1 does not provide Invoke-Api, so metrics.ps1's call "
            f"breaks under it — expected a finding, got {found}"
        )


def test_an_unknown_command_with_no_dot_source_is_flagged():
    """The base case: a call to a command that exists nowhere."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/lonely.ps1", "Write-Host 'start'\nGet-NonExistentThing -Path x\n")
        found = audit_names(facts_for(root, ["scripts/lonely.ps1"]))
        assert found == ["scripts/lonely.ps1:2:Get-NonExistentThing"], found


def test_a_known_cmdlet_resolves_from_the_inventory():
    """The generated inventory is what keeps built-ins from being findings."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/builtin.ps1", "Get-ChildItem -Path .\n")
        facts = facts_for(root, ["scripts/builtin.ps1"])
        assert audit_names(facts, {"Get-ChildItem"}) == []
        assert audit_names(facts, set()) == ["scripts/builtin.ps1:1:Get-ChildItem"]


def test_the_committed_inventory_covers_the_common_builtins():
    """A truncated or corrupt snapshot must not read as a clean scan."""
    inventory = checker.load_inventory(REPO_ROOT)
    for name in ("Write-Host", "Get-Content", "Join-Path", "ConvertTo-Json", "Invoke-RestMethod"):
        assert name in inventory, f"{name} missing from {checker.INVENTORY_FILE}"
    assert len(inventory) > 200, f"inventory suspiciously small: {len(inventory)}"


# --------------------------------------------------------------------------
# Fail closed: an unscannable file is never a silent pass.
# --------------------------------------------------------------------------


def test_a_computed_dot_source_skips_the_file_loudly():
    """`. $script:ScriptPath` — the resolution set is unknowable, so say so."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(
            root,
            "scripts/computed.ps1",
            "$script:Target = 'x.ps1'\n. $script:Target\nGet-WhateverThatLoads\n",
        )
        facts = facts_for(root, ["scripts/computed.ps1"])
        findings, _unverified, skipped, _structural = checker.audit(facts, set())
        assert "scripts/computed.ps1" in skipped, "a computed dot-source must skip the file"
        assert "computed path" in skipped["scripts/computed.ps1"]
        assert not findings, "a skipped file must not also produce findings"


def test_a_parse_error_is_an_error_not_a_skip():
    """A file that cannot be parsed is the blind spot this guard closes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/broken.ps1", "function Get-Broken {\n    'unclosed'\n")
        facts = facts_for(root, ["scripts/broken.ps1"])
        _findings, _unverified, _skipped, structural = checker.audit(facts, set())
        assert any("parse error" in s for s in structural), (
            f"an unparseable file must be an error, not a skip; got {structural}"
        )


def test_a_dot_source_of_a_missing_file_is_an_error():
    """Dot-sourcing a file that is not in the tree is itself a defect."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/orphan.ps1", ". (Join-Path $PSScriptRoot 'gone.ps1')\nGet-Gone\n")
        facts = facts_for(root, ["scripts/orphan.ps1"])
        _f, _u, _s, structural = checker.audit(facts, set())
        assert any("gone.ps1" in s and "not a tracked" in s for s in structural), structural


def test_a_missing_powershell_host_fails_closed():
    """No parser, no claim: run() must fail rather than print a clean bill."""
    original = checker.find_powershell
    checker.find_powershell = lambda: None
    try:
        assert checker.run(REPO_ROOT) == 1, (
            "with no PowerShell host the guard returned 0 — CI would go green "
            "having scanned nothing at all."
        )
    finally:
        checker.find_powershell = original


# --------------------------------------------------------------------------
# External modules: declared exemptions, and only those.
# --------------------------------------------------------------------------


def test_external_module_calls_are_unverified_rather_than_errors():
    """Microsoft.Graph et al. are installed at runtime and too large to inventory."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/graph.ps1", "Import-Module Microsoft.Graph\nConnect-MgGraph\nGet-MgDomain\n")
        facts = facts_for(root, ["scripts/graph.ps1"])
        findings, unverified, _skipped, _structural = checker.audit(facts, {"Import-Module"})
        assert not findings, f"a declared module's cmdlets must not be errors: {findings}"
        assert "scripts/graph.ps1" in unverified
        assert {f.command for f in unverified["scripts/graph.ps1"]} == {"Connect-MgGraph", "Get-MgDomain"}


def test_an_undeclared_module_import_gets_no_exemption():
    """Otherwise any `Import-Module Whatever` silently disables the guard."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "scripts/sneaky.ps1", "Import-Module TotallyLegitModule\nInvoke-AnythingIWant\n")
        facts = facts_for(root, ["scripts/sneaky.ps1"])
        findings, unverified, _s, _st = checker.audit(facts, {"Import-Module"})
        assert not unverified, "an undeclared module must not confer an exemption"
        assert [f.command for f in findings] == ["Invoke-AnythingIWant"], findings


def test_every_declared_external_module_is_still_imported_somewhere():
    """A stale exemption rots into a permanent allowance (the #912 pattern)."""
    imports = set()
    for path in REPO_ROOT.glob("scripts/*.ps1"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for module in checker.DECLARED_EXTERNAL_MODULES:
            if f"Import-Module {module}" in text:
                imports.add(module)
    stale = set(checker.DECLARED_EXTERNAL_MODULES) - imports
    assert not stale, (
        f"declared but no longer imported: {sorted(stale)} — drop the entry in the "
        "same PR that removed the last import."
    )


# --------------------------------------------------------------------------
# The exit code is the entire interface CI consumes.
# --------------------------------------------------------------------------


def test_main_fails_the_build_when_there_is_a_finding():
    """Run against the real tree, run() returns 0 — so a mutation making it
    return 0 unconditionally would leave every other test here passing while CI
    stopped failing on the defect. Asserted on a substituted verdict rather than
    on the tree, which is clean by construction (the #927 lesson).
    """
    original = checker.audit
    planted = checker.Finding(file="scripts/x.ps1", line=7, command="Get-Planted")
    checker.audit = lambda facts, inventory: ([planted], {}, {}, [])
    try:
        assert checker.run(REPO_ROOT) == 1, (
            "run() returned 0 with a finding in hand — CI would go green over the "
            "exact defect this script exists to fail."
        )
        checker.audit = lambda facts, inventory: ([], {}, {}, ["a structural problem"])
        assert checker.run(REPO_ROOT) == 1, "a structural error must also fail the build"
        checker.audit = lambda facts, inventory: ([], {}, {}, [])
        assert checker.run(REPO_ROOT) == 0
    finally:
        checker.audit = original


def test_the_real_tree_is_clean():
    """The guard as wired into CI, over the repository as it stands."""
    require_pwsh()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PowerShell command resolution OK" in proc.stdout, proc.stdout
    assert "::error::" not in proc.stdout, proc.stdout


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Skip as e:
            SKIPPED.append(t.__name__)
            print(f"  SKIP {t.__name__}: {e} (CI always has one; see module docstring)")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    if SKIPPED:
        print(f"  {len(SKIPPED)} test(s) skipped for want of a PowerShell host.")
    sys.exit(1 if failures else 0)
