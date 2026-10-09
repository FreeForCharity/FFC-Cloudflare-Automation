"""Guard: no hook module calls a name that does not exist in it (#1574).

A `NameError` inside `.claude/hooks/guard_bash.py` does not surface as a crash.
Its entrypoint ends with

    except Exception:
        # Never let a hook bug block legitimate work.
        sys.exit(0)

which is the right default — a guard bug must not stop real work — but it means a
broken rule is **indistinguishable from an approved command**: rc=0, empty stderr.
Worse, the rules accumulate into `WARNINGS` and only `finish()` flushes them, so a
raise in a LATE rule silently discards every warning the EARLIER rules produced.

Measured on 2026-10-08, merging the four open hooks PRs (#1313, #1336, #1519, #1521):

  * `main` carried two near-identical quote blankers, `_blank_quoted` and
    `_strip_quoted`.
  * #1313/#1336 (the rule-2 consolidation) DELETE `_blank_quoted`.
  * #1521 ADDED three new callers of `_blank_quoted`.

The two changes touch different regions of the file, so **git merged them clean** and
every PR was individually green. On the merged tree `test_hooks.py` went
`446 passed, 25 failed`, and the failures were rules **9, 10, 11 and 12** all
reporting `want=warn got=silent` — including rules neither PR touched, because one
late raise threw away the whole batch.

No runtime signal could have caught this, by design: the module under test is the one
that swallows the error. Only a static check can, which is why this lives in CI.

Deliberately conservative to stay false-positive-free: a called name counts as
resolved if the module defines, imports, or assigns it ANYWHERE, or if it is a
builtin. That is looser than real scoping and still catches the whole class, because
a deleted helper appears in none of those positions.
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
HOOKS = REPO_ROOT / ".claude" / "hooks"

# Modules the hooks import from the standard library or each other by module name
# are irrelevant here: this check only looks at BARE-NAME calls, `foo(...)`, never
# at attribute calls like `re.search(...)`.


def _resolved_names(tree: ast.AST) -> set[str]:
    """Every name the module binds, in any position, at any depth."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(
                    n.id for n in ast.walk(target) if isinstance(n, ast.Name)
                )
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.arguments):
            for arg in (
                list(node.posonlyargs)
                + list(node.args)
                + list(node.kwonlyargs)
                + ([node.vararg] if node.vararg else [])
                + ([node.kwarg] if node.kwarg else [])
            ):
                names.add(arg.arg)
        elif isinstance(node, (ast.For, ast.comprehension)):
            names.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    names.update(
                        n.id
                        for n in ast.walk(item.optional_vars)
                        if isinstance(n, ast.Name)
                    )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def unresolved_calls(source: str) -> list[str]:
    """Bare-name calls in `source` that the module never binds. Pure, so the
    falsification tests below can drive it without touching the tree."""
    tree = ast.parse(source)
    resolved = _resolved_names(tree)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name in resolved or hasattr(builtins, name):
            continue
        bad.append(f"line {node.lineno}: {name}(...)")
    return sorted(set(bad))


def _hook_modules() -> list[pathlib.Path]:
    return sorted(p for p in HOOKS.glob("*.py") if p.name != "__init__.py")


def test_every_hook_module_resolves_every_bare_name_call():
    assert _hook_modules(), f"no hook modules found under {HOOKS} — check the path"
    violations = []
    for path in _hook_modules():
        for bad in unresolved_calls(path.read_text(encoding="utf-8")):
            violations.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}: {bad} is called but the "
                "module never defines, imports or assigns it. In a hook this does NOT "
                "crash — `except Exception: sys.exit(0)` turns it into rc=0 with empty "
                "stderr, and every warning already accumulated is discarded with it."
            )
    assert not violations, "\n".join(violations)


def test_the_check_sees_the_defect_that_motivated_it():
    """Positive control: the exact #1521-after-#1336 shape must be caught."""
    found = unresolved_calls(
        "def _strip_quoted(text):\n"
        "    return text\n"
        "\n"
        "def rule(cmd):\n"
        "    return _blank_quoted(cmd)\n"
    )
    assert found == ["line 5: _blank_quoted(...)"], found


def test_the_check_leaves_a_resolvable_module_alone():
    """Negative control: defs, imports, params, walrus and loop targets all count."""
    found = unresolved_calls(
        "import re\n"
        "from os import getenv\n"
        "\n"
        "HANDLER = str\n"
        "\n"
        "def helper(x):\n"
        "    return x\n"
        "\n"
        "def rule(cmd, fn=helper):\n"
        "    for conv in (str, repr):\n"
        "        conv(cmd)\n"
        "    if (m := re.search('a', cmd)):\n"
        "        getenv('X')\n"
        "    HANDLER(m)\n"
        "    return helper(cmd) + fn(cmd) + len(cmd)\n"
    )
    assert found == [], found


def test_an_attribute_call_is_not_mistaken_for_a_bare_name():
    """`re.search(...)` is resolved by the import of `re`, not by a name `search`."""
    found = unresolved_calls("import re\n\ndef r(c):\n    return re.search('a', c)\n")
    assert found == [], found


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
        except Exception as e:  # noqa: BLE001 - harness crash, not a verdict
            # Reported separately and deliberately: a module that dies in its
            # harness gives NO verdict in either direction (L194), and conflating
            # that with a real failure is what makes a red module unreadable.
            failures += 1
            print(f"  FAIL {t.__name__}: HARNESS {type(e).__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
