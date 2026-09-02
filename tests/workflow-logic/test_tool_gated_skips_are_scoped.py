"""A whole-module `shutil.which` gate turns "could not run" into "everything passed".

WHAT THIS MODULE IS ABOUT
    A test module that requires ALL of its external tools up front and then
    `sys.exit(0)` does not report that it was unable to run. It reports that it
    ran and found nothing wrong. `run_all.py` counts it as a module with no
    failures, CI is green, and every static assertion the module carries --
    the ones that need no tool at all -- silently asserted nothing.

    This is the same shape `CLAUDE.md` already records for `assert rc != 0`:
    a harness that cannot start is indistinguishable from a system under test
    correctly rejecting a payload. It matters more here than elsewhere because
    the review method in this repo IS "run the guard and read its output"
    (AGENTS.md, "Reviewing a guard: reintroduce the defect it claims to
    catch"). A module that cannot fail is decoration, and a module that skips
    wholesale cannot fail.

    Measured on `main` 2026-08-12, ten modules carry the all-or-nothing form.
    Between them **32 cases make no subprocess call whatsoever** and are
    therefore masked by a gate on a tool they never touch:

        module                                  cases  toolless
        test_103_enforce_standard_wiring.py        14      7
        test_110_zone_create_wiring.py             12      4
        test_112_bulk_replace_wiring.py             7      1
        test_228_fraud_review.py                    8      0
        test_301_preflight_wiring.py                9      4
        test_303_domain_dkim_wiring.py             11      5
        test_304_dkim_wiring.py                    12      5
        test_306_uncaptured_comms_wiring.py        12      5
        test_704_analytics_wire_validation.py      10      1
        test_720_owner_parse.py                     3      0

    #1182's survey named nine of these; `test_306_uncaptured_comms_wiring.py`
    has the identical shape and was missed. That is the argument for a guard
    rather than a list -- the shape is easy to add and easy to overlook.

    The table is the `main` baseline. This PR rescopes 103 -- so 103 is NOT in
    the allowlist below, and `test_103_is_rescoped_and_not_allowlisted` keeps
    it that way -- and freezes the other nine with their debt measured.

WHY THE GATE IS INVISIBLE TO THE HARNESS THAT SHOULD CATCH IT
    `run_all.py` carries `WHOLE_MODULE_SKIP = "all"`, which stands its roster
    check down for any run that prints `  SKIP all`. That accommodation is
    correct on its own terms -- it stops a legitimate whole-module skip being
    reported as a truncated roster -- but it is also the reason nothing
    downstream can see the difference between "this module had nothing it
    could run" and "this module had ten static cases and ran none of them".
    The distinction has to be made here, at the shape.

WHAT COUNTS AS A FINDING
    A module whose `if __name__ == "__main__":` block can reach an
    unconditional `sys.exit(0)` from a `shutil.which(...)` gate placed BEFORE
    the loop that runs its tests. Read with `ast`, never a regex: the idiom
    varies already (`if shutil.which("pwsh") is None:` in eight modules, a
    `missing = [...]` comprehension in 103), and a source scan cannot tell a
    gate from the same text inside a string literal --
    `test_run_all_roster.py` embeds whole fixture modules as string constants
    and would be a false positive for any grep. `run_all.py:declared_tests`
    makes the same choice for the same reason (L48).

THE ALLOWLIST IS A FROZEN LEDGER, NOT A MUTE EXEMPTION
    An entry states a reason AND the number of its cases that spawn no
    subprocess at all. That count is asserted exactly, the shape #825 and
    #1080 use: rescope a module and its count moves, so the entry must be
    updated or removed and the burn-down cannot leave the list lying. An
    entry recording 0 is a genuine exemption -- every case shells out, there
    is nothing to rescue. An entry recording more than 0 is a debt with a
    number on it.

    "Spawns no subprocess" is a LOWER bound on what could run without the
    tool, and it errs in the honest direction: a case that spawns nothing
    provably does not need the gated tool, while a case that spawns something
    may still be spawning `git` or `python3` rather than `pwsh`. So the real
    debt is at least the number recorded, never less.

Refs #1182.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

# Attribute names that start a child process. `os.system` / `os.popen` are
# included for durability rather than because anything here uses them -- the
# survey above found only `subprocess.*` on `main`.
SPAWN_ATTRS = frozenset(
    {"run", "Popen", "check_output", "check_call", "call", "system", "popen"}
)


class Exemption:
    """One allowlist entry: why the whole-module gate is tolerated, and the debt.

    `toolless_cases` is the number of the module's top-level `def test_*`
    functions that reach no subprocess call, transitively through the module's
    own helpers. Asserted exactly by
    `test_allowlisted_modules_still_carry_the_debt_they_recorded`.
    """

    def __init__(self, toolless_cases: int, reason: str):
        self.toolless_cases = toolless_cases
        self.reason = reason


# Every entry here is #1182 acceptance criterion 5: 103 is rescoped in this PR,
# the rest are frozen with the debt measured so a later PR can burn them down
# one at a time. Two of them (228, 720) are genuine -- 0 toolless cases.
ALLOWLIST: dict[str, Exemption] = {
    "test_228_fraud_review.py": Exemption(
        0,
        "Genuine: all 8 cases execute the step body under pwsh. There is no "
        "static case to rescue, so the whole-module gate says exactly what is "
        "true. Re-check the count if a YAML-only case is ever added.",
    ),
    "test_720_owner_parse.py": Exemption(
        0,
        "Genuine: all 3 cases run the owner-parsing body under pwsh. Nothing "
        "to rescope.",
    ),
    "test_110_zone_create_wiring.py": Exemption(
        4,
        "TODO(#1182): 4 of 12 cases spawn nothing and are masked by the pwsh "
        "gate. Rescope with a NEEDS_PWSH set as 103 now does.",
    ),
    "test_112_bulk_replace_wiring.py": Exemption(
        1,
        "TODO(#1182): 1 of 7 cases spawns nothing and is masked by the pwsh "
        "gate. Rescope with a NEEDS_PWSH set as 103 now does.",
    ),
    "test_301_preflight_wiring.py": Exemption(
        4,
        "TODO(#1182): 4 of 9 cases spawn nothing and are masked by the pwsh "
        "gate. Rescope with a NEEDS_PWSH set as 103 now does.",
    ),
    "test_303_domain_dkim_wiring.py": Exemption(
        5,
        "TODO(#1182): 5 of 11 cases spawn nothing and are masked by the pwsh "
        "gate. Rescope with a NEEDS_PWSH set as 103 now does.",
    ),
    "test_304_dkim_wiring.py": Exemption(
        5,
        "TODO(#1182): 5 of 12 cases spawn nothing and are masked by the pwsh "
        "gate. Rescope with a NEEDS_PWSH set as 103 now does.",
    ),
    "test_306_uncaptured_comms_wiring.py": Exemption(
        5,
        "TODO(#1182): 5 of 12 cases spawn nothing and are masked by the pwsh "
        "gate. Not in #1182's survey -- found by this guard, which is the "
        "argument for having it.",
    ),
    "test_704_analytics_wire_validation.py": Exemption(
        1,
        "TODO(#1182): 1 of 10 cases spawns nothing and is masked by the bash "
        "gate. Rescope with a NEEDS_BASH set as 103 now does.",
    ),
}


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def _is_dunder_main(node: ast.stmt) -> bool:
    """True for `if __name__ == "__main__":`."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    names = {
        n.id for n in (left, right) if isinstance(n, ast.Name)
    }
    consts = {
        c.value for c in (left, right) if isinstance(c, ast.Constant)
    }
    return "__name__" in names and "__main__" in consts


class WhichBindings:
    """The local names that resolve to `shutil` / `shutil.which` in one module.

    Matching `.which(...)` on the ATTRIBUTE NAME alone -- which this file did
    until #1205 -- makes the detector fire on any unrelated API that happens to
    expose a `which` method (`resolver.which()`, `db.which(...)`). Matching only
    the literal text `shutil.which` instead would be worse: it drops
    `import shutil as sh`, and a false negative here is the exact failure this
    module exists to prevent -- a whole-module gate that goes unflagged and
    masks every static case behind it.

    So resolve the binding rather than the spelling, the same transitive shape
    `toolless_cases.spawns()` already uses for subprocess calls.
    """

    def __init__(self, modules: frozenset[str], direct: frozenset[str]):
        self.modules = modules  # names bound to the `shutil` MODULE
        self.direct = direct  # names bound to `shutil.which` ITSELF


def which_bindings(tree: ast.AST) -> WhichBindings:
    """Resolve `shutil` / `shutil.which` to the local names this module gave them.

    Walked at any depth, not just module top level: an `import shutil` inside
    the helper that carries the gate binds the name just as effectively, and
    missing it would be a false negative.
    """
    modules: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "shutil":
                    modules.add(alias.asname or "shutil")
                elif alias.name.startswith("shutil.") and alias.asname is None:
                    # `import shutil.x` binds the top package name too.
                    modules.add("shutil")
        elif isinstance(node, ast.ImportFrom):
            if node.module != "shutil" or node.level:
                continue
            for alias in node.names:
                if alias.name == "which":
                    direct.add(alias.asname or "which")
    return WhichBindings(frozenset(modules), frozenset(direct))


def _references_which(node: ast.AST, bindings: WhichBindings) -> bool:
    """True if the subtree calls `shutil.which` under any of its local names.

    `bindings` comes from `which_bindings(tree)` for the module the subtree
    belongs to. A `which` that the module never imported from `shutil` is
    somebody else's method, not this gate.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == "which":
            if isinstance(func.value, ast.Name) and func.value.id in bindings.modules:
                return True
        elif isinstance(func, ast.Name) and func.id in bindings.direct:
            return True
    return False


def _exits_zero(body: list[ast.stmt]) -> bool:
    """True if this block unconditionally exits with status 0.

    `sys.exit(0)`, a bare `sys.exit()`, `sys.exit(None)`, `raise SystemExit(0)`
    and `os._exit(0)` all qualify. `sys.exit(1 if failures else 0)` does
    NOT -- that is the normal end of a runner, and matching it would flag every
    module in the directory.
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        call = None
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            call = node.exc
        elif isinstance(node, ast.Call):
            call = node
        if call is None:
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in ("exit", "SystemExit", "_exit"):
            continue
        if not call.args:
            return True
        if len(call.args) == 1 and isinstance(call.args[0], ast.Constant):
            if call.args[0].value in (0, None):
                return True
    return False


def whole_module_tool_gate(tree: ast.Module) -> ast.If | None:
    """The `if <tool missing>: sys.exit(0)` that skips the module, or None.

    What makes a gate whole-module is that it EXITS THE PROCESS, not where it
    sits. A per-case gate -- `test_1146`'s `NEEDS_PWSH` set, `test_741`'s
    `if not shutil.which("npm")` -- bails with `continue` or `return` so the
    rest of the module still runs, and so is never flagged.

    Position is deliberately not used. An earlier form of this function only
    looked at top-level `ast.If` nodes in the main block appearing before the
    first loop, which five rewrites of the same gate walked straight through:
    a `missing` list built by an explicit `for` instead of a comprehension
    (the loop moved the window), a gate in a helper, a gate at module top
    level, a gate wrapped in `try:`, and `os._exit(0)`. Each still skipped
    every case; only the spelling differed. Matching on the exit is what makes
    the check about the behaviour rather than about one idiom.
    """
    funcs = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    bindings = which_bindings(tree)

    def gate_in(nodes: list[ast.stmt], seen: frozenset[str]) -> ast.If | None:
        """A tool-conditioned exit-zero anywhere in these statements."""
        # Names bound from an expression that consults `shutil.which` -- 103's
        # `missing = [t for t in (...) if shutil.which(t) is None]`, whose `if`
        # then tests the NAME and never mentions `which` at all. Collected at
        # any depth, so a name bound inside a `for` or `try` still counts.
        tool_names: set[str] = set()
        for stmt in nodes:
            for node in ast.walk(stmt):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    if not _references_which(node.value, bindings):
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            tool_names.add(target.id)
                elif isinstance(node, ast.AugAssign) and _references_which(node.value, bindings):
                    if isinstance(node.target, ast.Name):
                        tool_names.add(node.target.id)
            # 103's comprehension written as an explicit loop:
            # `for t in (...): if shutil.which(t) is None: missing.append(t)`.
            # The name is never the target of an assignment, so the pass above
            # cannot see it -- it is MUTATED inside a block that consults
            # `which`. Which of the two an author types is a coin flip, and
            # the gate that follows is identical.
            if _references_which(stmt, bindings):
                for node in ast.walk(stmt):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    if func.attr not in ("append", "add", "extend", "update"):
                        continue
                    if isinstance(func.value, ast.Name):
                        tool_names.add(func.value.id)

        for stmt in nodes:
            for node in ast.walk(stmt):
                if isinstance(node, ast.If):
                    mentions_tool = _references_which(node.test, bindings) or any(
                        isinstance(n, ast.Name) and n.id in tool_names
                        for n in ast.walk(node.test)
                    )
                    if mentions_tool and _exits_zero(node.body):
                        return node
                # The gate may live in a helper the main block calls --
                # `_require_tools()`. Resolve through the module's own
                # top-level functions, as `toolless_cases` already does for
                # spawning.
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    name = node.func.id
                    if name in funcs and name not in seen:
                        found = gate_in(funcs[name].body, seen | {name})
                        if found is not None:
                            return found
        return None

    # Module top level, excluding function bodies (whose `which` calls are the
    # correct per-case pattern) -- a gate placed here skips the module just as
    # thoroughly as one inside `if __name__ == "__main__":`.
    top_level = [
        n
        for n in tree.body
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not _is_dunder_main(n)
    ]
    found = gate_in(top_level, frozenset())
    if found is not None:
        return found

    for node in tree.body:
        if not _is_dunder_main(node):
            continue
        found = gate_in(node.body, frozenset())
        if found is not None:
            return found
    return None


def toolless_cases(tree: ast.Module) -> list[str]:
    """Top-level `test_*` functions that reach no subprocess call.

    Transitive through the module's own top-level helpers, which is where the
    spawning actually lives (`_run_pwsh`, `_run_github_script`). A case that
    spawns nothing provably does not need the gated tool; see the module
    docstring on why this is a lower bound and why that is the safe direction.
    """
    funcs = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def spawns(name: str, seen: frozenset[str] = frozenset()) -> bool:
        node = funcs.get(name)
        if node is None:
            return False
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in SPAWN_ATTRS:
                if isinstance(func.value, ast.Name) and func.value.id in ("subprocess", "os"):
                    return True
            if isinstance(func, ast.Name) and func.id in funcs and func.id not in seen:
                if spawns(func.id, seen | {func.id}):
                    return True
        return False

    return [name for name in sorted(funcs) if name.startswith("test_") and not spawns(name)]


def scan(directory: pathlib.Path) -> tuple[dict[str, list[str]], list[str]]:
    """(modules with a whole-module gate -> their toolless cases, parse errors).

    Fails closed: a module that will not parse is returned as an error, never
    skipped. A guard that quietly ignores what it cannot read reports a clean
    tree for a directory it never examined -- the very failure this module is
    about, one level up.
    """
    gated: dict[str, list[str]] = {}
    errors: list[str] = []
    # This module is NOT excluded from its own scan. An unexplained exemption is
    # the thing this file is about, and a guard that declines to police itself
    # has one by construction.
    for path in sorted(directory.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError) as exc:
            errors.append(f"{path.name}: cannot be parsed, so its gate cannot be ruled out ({exc})")
            continue
        if whole_module_tool_gate(tree) is not None:
            gated[path.name] = toolless_cases(tree)
    return gated, errors


# --------------------------------------------------------------------------
# The repo scan
# --------------------------------------------------------------------------


def test_no_unallowlisted_module_skips_itself_wholesale():
    gated, errors = scan(HERE)
    assert not errors, "modules the guard could not read: " + "; ".join(errors)
    unlisted = {name: cases for name, cases in gated.items() if name not in ALLOWLIST}
    assert not unlisted, (
        "these modules exit 0 from a `shutil.which` gate before running any "
        "test, so on a host without the tool they report green having asserted "
        "nothing: "
        + "; ".join(
            f"{name} ({len(cases)} of its cases spawn no subprocess at all and "
            f"are masked for no reason)"
            for name, cases in sorted(unlisted.items())
        )
        + ". Scope the gate to the cases that need the tool (see "
        "`test_1146_empty_input_argument_binding.py`'s NEEDS_PWSH set, or "
        "`test_103_enforce_standard_wiring.py`), or add an ALLOWLIST entry "
        "stating why every case genuinely needs it."
    )


def test_allowlist_carries_no_stale_entry():
    gated, errors = scan(HERE)
    assert not errors, "modules the guard could not read: " + "; ".join(errors)
    stale = sorted(set(ALLOWLIST) - set(gated))
    assert not stale, (
        f"ALLOWLIST entries that no longer match anything: {stale}. Either the "
        f"module was rescoped -- in which case delete the entry, that is the "
        f"burn-down working -- or it was renamed or removed. An exemption that "
        f"outlives its subject is how a list stops describing the repo."
    )


def test_allowlisted_modules_still_carry_the_debt_they_recorded():
    gated, _ = scan(HERE)
    drifted = []
    for name, exemption in sorted(ALLOWLIST.items()):
        if name not in gated:
            continue  # reported by the stale-entry test
        actual = len(gated[name])
        if actual != exemption.toolless_cases:
            drifted.append(
                f"{name}: recorded {exemption.toolless_cases} toolless cases, "
                f"measured {actual}"
            )
    assert not drifted, (
        "ALLOWLIST counts are out of date: "
        + "; ".join(drifted)
        + ". The count is asserted exactly so the burn-down cannot leave the "
        "list lying: if a case was rescoped the entry should be updated or "
        "removed, and if a NEW toolless case was added to a gated module the "
        "debt just grew and should be visible."
    )


def test_every_allowlist_entry_states_a_reason():
    unexplained = sorted(
        name for name, e in ALLOWLIST.items() if not e.reason.strip()
    )
    assert not unexplained, (
        f"ALLOWLIST entries with no stated reason: {unexplained}. An exemption "
        f"that cannot say why it exists is the one nobody re-examines."
    )


def test_the_genuine_exemptions_really_have_nothing_to_run():
    """0 recorded must MEAN 0 -- otherwise "genuine" is just an unchecked word."""
    gated, _ = scan(HERE)
    wrong = []
    for name, exemption in sorted(ALLOWLIST.items()):
        if exemption.toolless_cases != 0 or name not in gated:
            continue
        if gated[name]:
            wrong.append(f"{name} -> {gated[name]}")
    assert not wrong, (
        "entries recorded as having nothing to rescue in fact have toolless "
        "cases: " + "; ".join(wrong)
    )


def test_103_is_rescoped_and_not_allowlisted():
    """The one module #1182 requires actually fixed, pinned so it stays fixed."""
    path = HERE / "test_103_enforce_standard_wiring.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert whole_module_tool_gate(tree) is None, (
        "test_103_enforce_standard_wiring.py has regained a whole-module tool "
        "gate. Its 7 static cases pin the #1080 security claim and must run on "
        "a host with no pwsh."
    )
    assert "test_103_enforce_standard_wiring.py" not in ALLOWLIST


# --------------------------------------------------------------------------
# The detector's own behaviour, on synthetic modules
# --------------------------------------------------------------------------

_BAD_MODULE = '''
import shutil
import sys


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed)")
        sys.exit(0)
    for t in TESTS:
        t()
    sys.exit(0)
'''

_BAD_MODULE_VIA_NAME = '''
import shutil
import sys


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    missing = [t for t in ("pwsh", "node") if shutil.which(t) is None]
    if missing:
        print("  SKIP all")
        sys.exit(0)
    for t in TESTS:
        t()
'''

# Five rewrites of the SAME gate. Each still exits 0 before any case runs, so
# each masks `test_static` exactly as `_BAD_MODULE` does; only the spelling
# differs. They are here because a detector justified as "a detector rather
# than a list" is only worth more than the list if it survives the next
# author's idiom.

_BAD_MODULE_LOOP_BUILT_NAME = '''
import shutil
import sys


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    missing = []
    for tool in ("pwsh", "node"):
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        print("  SKIP all")
        sys.exit(0)
    for t in TESTS:
        t()
'''

_BAD_MODULE_GATE_IN_HELPER = '''
import shutil
import sys


def _require_tools():
    if shutil.which("pwsh") is None:
        print("  SKIP all")
        sys.exit(0)


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    _require_tools()
    for t in TESTS:
        t()
'''

_BAD_MODULE_GATE_AT_TOP_LEVEL = '''
import shutil
import sys

if shutil.which("pwsh") is None:
    print("  SKIP all")
    sys.exit(0)


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    for t in TESTS:
        t()
'''

_BAD_MODULE_GATE_IN_TRY = '''
import shutil
import sys


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    try:
        if shutil.which("pwsh") is None:
            print("  SKIP all")
            sys.exit(0)
    except OSError:
        pass
    for t in TESTS:
        t()
'''

_BAD_MODULE_OS_EXIT = '''
import os
import shutil


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all")
        os._exit(0)
    for t in TESTS:
        t()
'''


_GOOD_PER_CASE = '''
import shutil
import subprocess
import sys


def test_static():
    assert True


def test_shells_out():
    subprocess.run(["pwsh", "-c", "1"], check=False)


TESTS = [test_static, test_shells_out]
NEEDS_PWSH = {"test_shells_out"}

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed)")
            continue
        t()
    sys.exit(1 if failures else 0)
'''

_GOOD_NO_GATE = '''
import sys


def test_static():
    assert True


TESTS = [test_static]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        t()
    sys.exit(1 if failures else 0)
'''

# The `test_run_all_roster.py` trap: a module that carries the bad shape inside
# a STRING is not a module that has the bad shape. Any regex-based guard reports
# this; an ast-based one cannot.
_GATE_ONLY_IN_A_STRING = '''
import sys

FIXTURE = """
if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        sys.exit(0)
"""


def test_static():
    assert FIXTURE


TESTS = [test_static]

if __name__ == "__main__":
    for t in TESTS:
        t()
    sys.exit(1 if failures else 0)
'''


def _detect(source: str) -> bool:
    return whole_module_tool_gate(ast.parse(source)) is not None


def test_the_detector_fires_on_the_direct_gate():
    assert _detect(_BAD_MODULE), (
        "the plain `if shutil.which(...) is None: sys.exit(0)` form -- eight of "
        "the ten modules on main -- was not detected"
    )


def test_the_detector_fires_when_the_gate_tests_a_name():
    assert _detect(_BAD_MODULE_VIA_NAME), (
        "103's form, where the `if` tests a name bound from a comprehension "
        "over shutil.which and never mentions `which` itself, was not detected"
    )


def test_the_detector_fires_when_the_name_is_built_by_a_loop():
    assert _detect(_BAD_MODULE_LOOP_BUILT_NAME), (
        "103's `missing` list written as an explicit `for` instead of a "
        "comprehension went undetected. This is the strongest of the five: it "
        "is the same logic as the form directly above, and which one an author "
        "types is a coin flip"
    )


def test_the_detector_fires_when_the_gate_lives_in_a_helper():
    assert _detect(_BAD_MODULE_GATE_IN_HELPER), (
        "a gate moved into `_require_tools()` went undetected -- the module "
        "still exits 0 before any case runs"
    )


def test_the_detector_fires_on_a_gate_at_module_top_level():
    assert _detect(_BAD_MODULE_GATE_AT_TOP_LEVEL), (
        "a gate outside the `__main__` block went undetected; it skips the "
        "module just as thoroughly, and on import as well"
    )


def test_the_detector_fires_on_a_gate_wrapped_in_try():
    assert _detect(_BAD_MODULE_GATE_IN_TRY), (
        "wrapping the gate in `try:` moved it out of the main block's "
        "top-level statements and hid it"
    )


def test_the_detector_fires_on_os_exit():
    assert _detect(_BAD_MODULE_OS_EXIT), (
        "`os._exit(0)` ends the process exactly as `sys.exit(0)` does, and was "
        "not in the recognised name set"
    )

def test_the_detector_ignores_a_per_case_gate():
    assert not _detect(_GOOD_PER_CASE), (
        "the CORRECT pattern was flagged. A guard that cannot tell the fix "
        "from the defect makes the fix unlandable."
    )


def test_the_detector_ignores_a_module_with_no_gate():
    assert not _detect(_GOOD_NO_GATE)


# --------------------------------------------------------------------------
# #1205: `which` is resolved from the module's imports, not matched by name
#
# The six rows below are the probe from #1205, each as its own case. Three were
# false positives before the binding resolution landed -- `_references_which`
# returned True for ANY attribute call named `which`. Row 3 is why the obvious
# narrowing (match the literal text `shutil.which`) is the wrong fix: it would
# trade three theoretical false positives for one real false NEGATIVE, and a
# false negative here is the failure this whole module exists to prevent.
#
# These probe the helper directly rather than through `_detect`, because the
# helper is where the resolution lives and an end-to-end fixture cannot place
# `self.which(...)` anywhere the detector would look without contorting the
# fixture into something no author would ever write. The two end-to-end cases
# that follow cover the wiring.
# --------------------------------------------------------------------------


def _which_verdict(source: str) -> bool:
    """`_references_which` on the last statement of `source`, with its imports resolved."""
    tree = ast.parse(source)
    return _references_which(tree.body[-1], which_bindings(tree))


def test_which_resolves_a_plain_shutil_import():
    assert _which_verdict("import shutil\nshutil.which('pwsh')\n"), (
        "`import shutil` + `shutil.which(...)` -- the form eight modules on "
        "main use -- stopped being recognised"
    )


def test_which_resolves_a_bare_from_import():
    assert _which_verdict("from shutil import which\nwhich('pwsh')\n"), (
        "`from shutil import which` + a bare `which(...)` call stopped being "
        "recognised"
    )


def test_which_resolves_an_aliased_module_import():
    assert _which_verdict("import shutil as sh\nsh.which('pwsh')\n"), (
        "`import shutil as sh` + `sh.which(...)` is an ordinary spelling and "
        "must still be recognised. This is the row the narrow fix suggested on "
        "#1205 would have broken -- a false negative here means a whole-module "
        "gate goes unflagged and masks every static case behind it"
    )


def test_which_resolves_an_aliased_from_import():
    assert _which_verdict("from shutil import which as w\nw('pwsh')\n"), (
        "`from shutil import which as w` binds the function under another "
        "name; the call is still shutil.which"
    )


def test_which_does_not_match_a_method_on_self():
    assert not _which_verdict("import sys\nself.which('x')\n"), (
        "`self.which(...)` is somebody else's method. Matching on the "
        "attribute NAME alone made every such call look like a tool gate"
    )


def test_which_does_not_match_a_method_on_an_unrelated_object():
    assert not _which_verdict("import sys\ndb.which('x')\n"), (
        "`db.which(...)` names an unrelated API and was a false positive"
    )


def test_which_does_not_match_a_zero_argument_method_call():
    assert not _which_verdict("import sys\nresolver.which()\n"), (
        "`resolver.which()` takes no tool name and cannot be a shutil.which "
        "gate; it was a false positive"
    )


def test_which_does_not_match_when_shutil_was_never_imported():
    assert not _which_verdict("import sys\nwhich('pwsh')\n"), (
        "a bare `which(...)` in a module that never imported it from shutil "
        "would NameError at runtime; it is not this gate"
    )


def test_the_detector_still_fires_through_an_aliased_import():
    assert _detect(
        "import shutil as sh\n"
        "import sys\n"
        "if sh.which('pwsh') is None:\n"
        "    print('  SKIP all')\n"
        "    sys.exit(0)\n"
        "def test_static():\n"
        "    assert True\n"
    ), (
        "end to end: a whole-module gate written with an aliased shutil import "
        "must still be flagged. The unit rows above can pass while the "
        "bindings are never threaded into `whole_module_tool_gate`"
    )


def test_the_detector_still_fires_on_a_helper_local_import():
    assert _detect(
        "import sys\n"
        "def _require_tools():\n"
        "    import shutil as sh\n"
        "    if sh.which('pwsh') is None:\n"
        "        print('  SKIP all')\n"
        "        sys.exit(0)\n"
        "def test_static():\n"
        "    assert True\n"
        "if __name__ == '__main__':\n"
        "    _require_tools()\n"
    ), (
        "`which_bindings` walks imports at ANY depth, and this is the case that "
        "holds it there: the only import at module top level is `sys`, so a "
        "collector narrowed to `tree.body` sees no shutil binding, and the gate "
        "in `_require_tools` -- which skips every case in the module -- goes "
        "unflagged. That is a false NEGATIVE, the direction this module exists "
        "to prevent. Reported by Copilot on #1232 as an untested claim in the "
        "docstring; the behaviour was already correct, the coverage was not"
    )


def test_the_detector_does_not_fire_on_an_unrelated_which_method():
    assert not _detect(
        "import sys\n"
        "from mydb import db\n"
        "if db.which('x') is None:\n"
        "    sys.exit(0)\n"
        "def test_static():\n"
        "    assert True\n"
    ), (
        "end to end: an unrelated `.which()` must not be read as a tool gate. "
        "Before #1205 this module was flagged, and the failure surfaces as a "
        "confusing red on a module that has no gate at all"
    )


def test_the_detector_ignores_a_gate_inside_a_string_literal():
    assert not _detect(_GATE_ONLY_IN_A_STRING), (
        "a gate quoted inside a string constant was read as code -- this is "
        "the false positive that makes a regex unusable here, and "
        "test_run_all_roster.py is a live instance of it"
    )


def test_a_normal_runner_exit_is_not_read_as_a_skip():
    """`sys.exit(1 if failures else 0)` must never count as exiting 0."""
    source = '''
import shutil
import sys
TESTS = []
if __name__ == "__main__":
    failures = 0
    if shutil.which("pwsh") is None:
        failures = 1
    sys.exit(1 if failures else 0)
'''
    assert not _detect(source)


def test_the_scan_reports_an_unparseable_module_instead_of_skipping_it():
    """Fail closed (#1182 AC7 in spirit, and AGENTS.md on guards that skip)."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = pathlib.Path(tmp)
        (directory / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")
        gated, errors = scan(directory)
        assert not gated
        assert errors and "test_broken.py" in errors[0], (
            f"an unparseable module was silently skipped rather than reported: "
            f"{errors}"
        )


def test_the_guard_actually_fails_on_a_directory_carrying_the_shape():
    """L47: prove the guard fails without its fix, end to end, not just the detector.

    The detector unit tests above could all pass while the assertion that CI
    reads is wired to the wrong thing. This runs `scan` over a directory that
    contains one bad module and asserts the finding names it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = pathlib.Path(tmp)
        (directory / "test_offender.py").write_text(_BAD_MODULE, encoding="utf-8")
        (directory / "test_innocent.py").write_text(_GOOD_PER_CASE, encoding="utf-8")
        gated, errors = scan(directory)
        assert not errors
        assert set(gated) == {"test_offender.py"}, (
            f"expected exactly the offending module, got {sorted(gated)}"
        )
        assert gated["test_offender.py"] == ["test_static"], (
            f"the finding should name the masked case, got {gated['test_offender.py']}"
        )


def test_103_runs_its_static_cases_with_no_pwsh_on_path():
    """The measurement #1182 asks for, made rather than asserted from the source.

    Runs 103 in a child with every PATH entry containing a PowerShell host
    removed, and reads its roster. This is the case the whole issue is about:
    before the rescope this printed `SKIP all` and exited 0.
    """
    path = HERE / "test_103_enforce_standard_wiring.py"
    import os

    # `shutil.which(..., path=entry)` rather than testing for a literal `pwsh`
    # file: on Windows the host is `pwsh.exe`, and a bare-name existence check
    # leaves its directory on PATH. `which` honours PATHEXT, so the same
    # expression scrubs both platforms.
    scrubbed = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and shutil.which("pwsh", path=entry) is None
    )
    # The control, asserted before the measurement rather than inferred from
    # it. If the scrub does not actually hide pwsh, the child runs all 14 cases
    # and this test fails below on an empty skip list -- a failure that reads as
    # "the rescope is broken" when the truth is "the harness never removed the
    # tool". Naming it here is the difference between a diagnosis and a hunt.
    assert shutil.which("pwsh", path=scrubbed) is None, (
        f"PATH scrub failed: pwsh is still reachable at "
        f"{shutil.which('pwsh', path=scrubbed)!r} after removing every entry "
        f"that carries it. This test cannot measure the no-pwsh behaviour it "
        f"exists to measure -- fix the scrub, do not weaken the assertions."
    )
    # Inherit the environment and override only PATH: a scrubbed env dict is
    # what #943 spent a fortnight on (CLAUDE.md, "never pass a scrubbed env=").
    env = dict(os.environ)
    env["PATH"] = scrubbed
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        # `-X utf8` rather than an inline `env={**os.environ, ...}`: this call
        # needs a computed `env` for the PATH scrub, and a computed env is one
        # `scripts/check-subprocess-encoding.py` cannot read to prove the pin.
        # The flag pins the child's own output encoding where the scanner (and
        # a reader) can see it. #962.
        [sys.executable, "-X", "utf8", str(path)],
        capture_output=True,
        text=True,
        # Pinned, not inherited: text mode otherwise decodes with
        # locale.getencoding(), which is cp1252 on the Windows host and dies on
        # the first non-ASCII byte a child emits (scripts/check-subprocess-encoding.py).
        encoding="utf-8",
        env=env,
        cwd=str(HERE),
        timeout=300,
    )
    out = proc.stdout
    assert "SKIP all" not in out, (
        "103 still skips wholesale when pwsh is absent -- the defect #1182 is "
        f"about. Output: {out!r}"
    )
    passed = [line for line in out.splitlines() if line.startswith("  PASS ")]
    skipped = [line for line in out.splitlines() if line.startswith("  SKIP ")]
    assert len(passed) >= 7, (
        f"expected at least the 7 cases that spawn nothing to run with pwsh "
        f"absent, got {len(passed)}. Output: {out!r}"
    )
    assert skipped, "expected the pwsh cases to be reported as skipped, by name"
    for line in skipped:
        assert "pwsh" in line, (
            f"a skip line must say which tool it wanted: {line!r}"
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
