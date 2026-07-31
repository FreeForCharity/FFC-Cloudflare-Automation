"""Guard: workflow-logic child environments are inherited, never built from nothing.

Why this module exists (#943). 26 of these test modules could not run at all on a
Windows host while staying green on Linux and in CI. Each handed `subprocess.run`
a freshly-built dict — `{"PATH": "/usr/bin:/bin", ...}` — and node aborts during
startup when it cannot see the platform's own variables (SYSTEMROOT above all).
It dies before any test logic runs, so the module reports `harness crashed:`
rather than a result. Two consecutive conductor runs lost time hand-writing
standalone probes because the official harness would not execute on the host
they were reviewing from.

The failure mode this guard defends against is specific: **the defect is
invisible on the platform CI runs on.** A regression here cannot be caught by
the suite going red, because on Linux a minimal env dict works fine. So the
rule is enforced statically, against the source of the test modules themselves,
where it is visible on every platform.

`test_the_scanner_flags_the_pre_fix_shape` is the load-bearing one: it feeds the
scanner the exact construction that was in the tree before #943 and requires a
finding. Without it this module asserts the happy path — the tree is clean by
construction after the fix, so a scanner that returned `[]` unconditionally
would pass every other test here.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env

TESTS_DIR = pathlib.Path(__file__).resolve().parent
HARNESS_DIR = TESTS_DIR / "harness"


# --- the scanner ------------------------------------------------------------


def _splats_an_inherited_environment(node: ast.Dict) -> bool:
    """True for `{**os.environ, ...}` — a dict literal that starts from the
    parent environment. A `None` key is how ast spells `**`."""
    return any(
        key is None and "environ" in ast.unparse(value)
        for key, value in zip(node.keys, node.values)
    )


def scan_source(source: str, filename: str) -> list[tuple[str, int, str]]:
    """Findings of the form (filename, line, rule).

    Two rules, because the defect has two spellings:

    A. a dict literal carrying a hardcoded "PATH" — the shape that was in the
       tree, whether or not it is passed to subprocess.run on the same line;
    B. `subprocess.run(env={...})` given a literal at all, which strips the
       platform variables even when it sets no PATH.
    """
    findings: list[tuple[str, int, str]] = []
    tree = ast.parse(source, filename=filename)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and not _splats_an_inherited_environment(node):
            if any(
                isinstance(k, ast.Constant) and k.value == "PATH" for k in node.keys if k
            ):
                findings.append((filename, node.lineno, "A: hardcoded PATH in a fresh dict"))

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "run":
                continue
            for kw in node.keywords:
                if kw.arg != "env" or not isinstance(kw.value, ast.Dict):
                    continue
                if not _splats_an_inherited_environment(kw.value):
                    findings.append(
                        (filename, kw.value.lineno, "B: env= built from a bare dict literal")
                    )

    return findings


def _scan_tree() -> list[tuple[str, int, str]]:
    findings = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        findings += scan_source(path.read_text(encoding="utf-8"), path.name)
    return findings


# --- the rule holds over the real tree --------------------------------------


def test_no_module_builds_a_child_environment_from_nothing():
    findings = _scan_tree()
    assert findings == [], "\n".join(f"{f}:{ln}: {rule}" for f, ln, rule in findings)


# --- the scanner can actually fail (the mutation, pinned) -------------------


PRE_FIX_RULE_A = '''
import subprocess
def _run():
    env = {
        "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
        "HOME": "/tmp/x",
    }
    return subprocess.run(["bash", "-c", "true"], env=env)
'''

PRE_FIX_RULE_B = '''
import subprocess
def _run(script_file):
    return subprocess.run(
        ["node", "harness.mjs"],
        env={"TEST_SCRIPT_FILE": str(script_file)},
    )
'''

ALREADY_CORRECT = '''
import os
import subprocess
from wf_extract import child_env
def _run(harness):
    subprocess.run(["node", "-e", "1"], env=child_env(harness, TEST_X="1"))
    subprocess.run(["node", "-e", "1"], env={**os.environ, "ONLY_REPOS": "a"})
    subprocess.run(["node", "-e", "1"])
'''


def test_the_scanner_flags_the_pre_fix_shape():
    """The construction that made 26 modules unrunnable must be a finding.

    This is what stops the guard from being decorative: the tree is clean after
    the fix, so every other assertion here passes against a scanner that never
    reports anything.
    """
    a = scan_source(PRE_FIX_RULE_A, "pre_fix_a.py")
    assert len(a) == 1, a
    assert a[0][2].startswith("A:"), a

    b = scan_source(PRE_FIX_RULE_B, "pre_fix_b.py")
    assert len(b) == 1, b
    assert b[0][2].startswith("B:"), b


def test_the_scanner_does_not_flag_the_corrected_forms():
    # Both remedies, plus an inheriting call that passes no env at all.
    assert scan_source(ALREADY_CORRECT, "ok.py") == []


# --- child_env's own contract -----------------------------------------------


def test_child_env_inherits_every_parent_variable():
    env = child_env()
    missing = [k for k in os.environ if k not in env]
    assert missing == [], missing


def test_child_env_applies_overrides_on_top_of_the_inherited_values():
    env = child_env(TEST_ONLY_MARKER="marker")
    assert env["TEST_ONLY_MARKER"] == "marker"
    assert len(env) >= len(os.environ)


def test_child_env_prepends_with_the_platform_path_separator():
    """os.pathsep, not a hardcoded ':' — on Windows a colon-joined PATH is one
    unparsable entry, so the prepended harness dir would not be found and the
    inherited entries would be lost with it."""
    env = child_env("/first", "/second")
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/first", env["PATH"]
    assert parts[1] == "/second", env["PATH"]
    # The inherited PATH survives as the tail rather than being replaced.
    for entry in os.environ["PATH"].split(os.pathsep):
        assert entry in parts, (entry, env["PATH"])


def test_child_env_without_a_prepend_leaves_path_untouched():
    assert child_env()["PATH"] == os.environ["PATH"]


def test_child_env_joins_with_os_pathsep_in_its_source():
    """Asserted statically because it CANNOT be asserted at runtime here.

    On POSIX `os.pathsep` is ':', so a hardcoded ':' behaves identically and
    every runtime assertion above passes against it — while on Windows it
    produces a single unparsable PATH entry and reintroduces #943 from the
    other direction. The only platform-independent evidence available from a
    Linux host is the source itself.
    """
    src = (TESTS_DIR / "wf_extract.py").read_text(encoding="utf-8")
    body = src.split("def child_env(")[1].split("\ndef ")[0]
    assert "os.pathsep" in body, body
    assert '":"' not in body and "':'" not in body, body
    assert "dict(os.environ)" in body, body


def test_the_harness_dir_is_never_on_an_inherited_path():
    """The hermeticity that `test_502_deliver` relies on.

    Its seed/read helpers keep the fake `gh` unreachable by NOT prepending the
    harness dir. That only works because the harness dir is a test-local
    directory that is never on a real PATH — an assumption worth pinning rather
    than leaving implicit, since inheriting means an omission is no longer a
    guarantee.
    """
    inherited = child_env()["PATH"].split(os.pathsep)
    assert str(HARNESS_DIR) not in inherited, inherited


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
