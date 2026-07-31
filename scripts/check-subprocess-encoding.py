#!/usr/bin/env python3
"""Text-mode subprocess decoding guard (issue #945).

`subprocess` in text mode decodes the child's output with `locale.getencoding()`.
That is UTF-8 on the GitHub runners and **cp1252 on Windows**, so a call that
passes `text=True` without pinning `encoding=` reads correctly in CI and dies on
a Windows host the moment the child emits any non-Latin-1 byte:

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 1000

The bytes that trigger it are ordinary in this repository — an emoji in an issue
title (the alert workflows open issues titled `🚨 Scheduled workflow failing:`),
a box-drawing character in a job summary, a curly quote in a PR body.

WHY THIS IS STATIC AND NOT A TEST
    The defect is invisible on the platform CI runs on. 41 test modules and 2
    scripts carried it while the suite was green on `ubuntu-latest`; it surfaced
    only when a conductor ran the suite on Windows (#945, and the same asymmetry
    as #944 / ledger L35). A red test cannot guard a bug that only exists on a
    platform the tests never run on, so the check has to read the *source*, where
    the defect is visible from every host.

WHAT COUNTS AS A VIOLATION
    A `subprocess` call that requests text mode without pinning the codec:

      * text mode is requested by `text=True`, `universal_newlines=True`, or by
        passing `errors=` (which implies text mode on its own);
      * the codec is pinned by `encoding=<anything>`.

    So `text=True, encoding="utf-8"` passes, `encoding="utf-8"` alone passes
    (encoding implies text mode and pins it), and `text=True` alone fails.

    A call with neither text mode nor `encoding=` is BINARY and is not a
    violation — bytes have no codec to get wrong. This is why the check keys on
    text mode rather than on "every subprocess call".

WHAT IT CANNOT SEE — stated rather than papered over
    * `text=SOME_FLAG` (a non-literal) is reported, because the guard cannot
      prove the flag is always false and the failure mode is silent. Pin the
      encoding, or pass the literal.
    * A helper that wraps `subprocess` and takes `**kwargs` is only checked at
      the wrapper's own call site.
    * `os.popen`, `pty`, and direct `io.TextIOWrapper` use are out of scope.

Exit code 0 when clean, 1 when any violation is found. Import
`find_violations()` to test it against a source string without touching disk.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories scanned. Both hold Python that shells out and both are run by
# humans on Windows as well as by CI on Linux.
SCAN_DIRS = ("scripts", "tests")

# The subprocess entry points that accept text-mode keywords.
SUBPROCESS_CALLS = frozenset(
    {"run", "check_output", "check_call", "call", "Popen", "getoutput", "getstatusoutput"}
)

# Passing any of these requests text mode.
TEXT_MODE_KEYWORDS = ("text", "universal_newlines")


class Violation:
    """One text-mode subprocess call that does not pin its codec."""

    def __init__(self, path: str, line: int, call: str, reason: str) -> None:
        self.path = path
        self.line = line
        self.call = call
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.call}(...) {self.reason}"


def _callee_name(node: ast.Call) -> str | None:
    """Return the subprocess entry point this call targets, else None.

    Matches both `subprocess.run(...)` and a bare `run(...)` imported via
    `from subprocess import run`.
    """
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_CALLS:
        base = func.value
        if isinstance(base, ast.Name) and base.id == "subprocess":
            return f"subprocess.{func.attr}"
        return None
    if isinstance(func, ast.Name) and func.id in SUBPROCESS_CALLS:
        return func.id
    return None


def _keyword(node: ast.Call, name: str) -> ast.keyword | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw
    return None


def find_violations(source: str, path: str = "<string>") -> list[Violation]:
    """Every text-mode subprocess call in `source` that does not pin `encoding=`."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # a file we cannot parse is a failure, not a pass
        return [Violation(path, exc.lineno or 0, "<unparseable>", f"cannot parse: {exc.msg}")]

    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name is None:
            continue

        # `encoding=` pins the codec and implies text mode. Nothing else to check.
        if _keyword(node, "encoding") is not None:
            continue

        requested_by = None
        for kw_name in TEXT_MODE_KEYWORDS:
            kw = _keyword(node, kw_name)
            if kw is None:
                continue
            if isinstance(kw.value, ast.Constant):
                # subprocess tests these for TRUTH, not identity: `text=1` enables
                # text mode exactly as `text=True` does. Matching only `is True`
                # let `text=1` through as if it were binary mode — a silent
                # false negative in the guard, which is the failure shape this
                # scanner exists to catch. Falsy constants (False, 0, None) are
                # genuinely binary mode and correctly not a finding.
                if kw.value.value:
                    requested_by = f"{kw_name}={kw.value.value!r}"
                    break
                continue
            # Non-literal: cannot prove it is never true, and the failure is silent.
            requested_by = f"{kw_name}=<non-literal>"
            break

        # `errors=` implies text mode even with no text= keyword at all.
        if requested_by is None and _keyword(node, "errors") is not None:
            requested_by = "errors="

        if requested_by is None:
            continue  # binary mode: no codec to get wrong

        out.append(
            Violation(
                path,
                node.lineno,
                name,
                f"requests text mode ({requested_by}) without encoding= — "
                f"decodes with the OS ANSI codepage (cp1252 on Windows)",
            )
        )
    return out


def scan_repo(root: pathlib.Path | None = None) -> list[Violation]:
    root = root or REPO_ROOT
    found: list[Violation] = []
    for rel in SCAN_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            found.extend(find_violations(text, str(py.relative_to(root)).replace("\\", "/")))
    return found


def main() -> int:
    violations = scan_repo()
    if violations:
        print(f"Text-mode subprocess calls without a pinned encoding: {len(violations)}\n")
        for v in violations:
            print(f"  {v}")
        print(
            "\nAdd `encoding=\"utf-8\"` to each call. Text mode without it decodes with\n"
            "locale.getencoding() — UTF-8 in CI, cp1252 on Windows — so this passes here\n"
            "and dies on a maintainer's machine the first time a child emits an emoji.\n"
            "See docs/lessons-ledger.md and issue #945."
        )
        return 1

    scanned = sum(1 for d in SCAN_DIRS for _ in (REPO_ROOT / d).rglob("*.py"))
    print(f"Subprocess encoding OK: {scanned} Python files, every text-mode call pins encoding=.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
