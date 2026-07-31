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

    THE SECOND HALF — the child's encode (#962). Pinning `encoding=` fixes the
    parent's decode only. A **Python** child still writes stdout in the console
    codepage, so on Windows the parent now demands UTF-8 from a stream emitting
    cp1252 and gets

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 ...

    raised on `subprocess`'s reader *thread*. `subprocess.run` therefore still
    returns, with `proc.stdout is None`, and the caller dies later with
    `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'` —
    pointing at its own arithmetic rather than at an encoding problem. So when a
    call pins `encoding=` **and** its argv starts a Python interpreter, the child
    must be pinned too: `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` (or
    `PYTHONUTF8`, or `-X utf8` in the argv).

    Only a Python child is required to carry it: `node`, `git` and `pwsh` do not
    read `PYTHONIOENCODING`, so demanding it there would be noise.

WHAT IT CANNOT SEE — stated rather than papered over
    * `text=SOME_FLAG` (a non-literal) is reported, because the guard cannot
      prove the flag is always false and the failure mode is silent. Pin the
      encoding, or pass the literal.
    * An argv whose first element is a non-literal (`[EXE, script]`) is NOT
      treated as a Python child — the guard flags the child only where it can
      prove one, since a false demand on a `node` child is pure noise.
    * `env=SOME_DICT` built elsewhere IS reported: the guard cannot prove the
      variable carries the pin, and the failure is the silent None-stdout shape.
      Inline the mapping, or add the pin at the call site. Only KEY positions
      count as setting a variable — a name in a value (`{"FOO":
      "PYTHONIOENCODING"}`) sets nothing and does not satisfy the rule.
    * A helper that wraps `subprocess` and takes `**kwargs` is only checked at
      the wrapper's own call site.
    * `os.popen`, `pty`, and direct `io.TextIOWrapper` use are out of scope.

Exit code 0 when clean, 1 when any violation is found. Import
`find_violations()` to test it against a source string without touching disk.
"""

from __future__ import annotations

import ast
import pathlib
import re
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

# argv[0] spellings of a Python interpreter: `python`, `python3`, `python3.12`,
# the Windows launcher `py`, any of them with a `.exe` suffix or a leading path.
PYTHON_EXE_RE = re.compile(
    r"^(?:.*[\\/])?(?:python(?:\d+(?:\.\d+)?)?|py)(?:\.exe)?$", re.IGNORECASE
)

# Environment variables that make a Python child emit UTF-8.
CHILD_ENCODING_ENV_VARS = frozenset({"PYTHONIOENCODING", "PYTHONUTF8"})

# The argv-flag equivalent: `python -X utf8 …` / `python -Xutf8 …`. Matched as
# the joined form or as the adjacent pair — never as a bare `utf8` token, which
# would let an ordinary script argument read as a pin.
CHILD_ENCODING_ARGV_JOINED = "-Xutf8"
CHILD_ENCODING_ARGV_PAIR = ("-X", "utf8")

# Violation kinds, so `main()` can print the right remedy for each.
PARENT_DECODE = "parent"
CHILD_ENCODE = "child"


class Violation:
    """One subprocess call that leaves a codec to the OS default."""

    def __init__(
        self, path: str, line: int, call: str, reason: str, kind: str = PARENT_DECODE
    ) -> None:
        self.path = path
        self.line = line
        self.call = call
        self.reason = reason
        self.kind = kind

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


def _argv_literals(node: ast.Call) -> list[str | None]:
    """The call's argv as literal strings, with `None` for anything unreadable.

    Handles the two shapes that appear here: a list/tuple of arguments, and a
    single command string (`shell=True`, `getoutput`). `sys.executable` is
    resolved to the literal `"python"` — it is by definition a Python
    interpreter, and it is how every call site in this repo spells one.
    """
    args = node.args[0] if node.args else _keyword(node, "args")
    if isinstance(args, ast.keyword):
        args = args.value
    if args is None:
        return []

    if isinstance(args, ast.Constant) and isinstance(args.value, str):
        return list(args.value.split())

    if not isinstance(args, (ast.List, ast.Tuple)):
        return []

    out: list[str | None] = []
    for elt in args.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        elif (
            isinstance(elt, ast.Attribute)
            and elt.attr == "executable"
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "sys"
        ):
            out.append("python")
        else:
            out.append(None)  # a variable: readable position, unreadable value
    return out


def _is_python_child(argv: list[str | None]) -> bool:
    """True only when argv[0] is provably a Python interpreter.

    Deliberately asymmetric with the `text=<non-literal>` rule: an unreadable
    argv[0] is NOT reported. There, the guard could not prove the call was safe;
    here, it cannot prove the child is Python, and requiring PYTHONIOENCODING of
    a `node` or `git` child would be noise (#962).
    """
    return bool(argv) and argv[0] is not None and PYTHON_EXE_RE.match(argv[0]) is not None


def _env_var_names(env: ast.expr) -> tuple[set[str], bool]:
    """The environment variable NAMES an `env=` expression sets, and whether
    that reading is complete.

    Only key positions count. A name appearing as a *value* — `{"FOO":
    "PYTHONIOENCODING"}` — sets no such variable, and treating it as one let a
    genuinely unpinned call through.

    Completeness is the second half: `{**BASE, "A": "1"}` and `dict(base, A=1)`
    both merge a mapping this scanner cannot see, so the pin may be in there.
    Those read as incomplete; a dict literal with all-constant keys is complete,
    and its omission of the pin is then a proof, not a guess.
    """
    names: set[str] = set()

    if isinstance(env, ast.Dict):
        complete = True
        for key in env.keys:
            if key is None:  # `**other` — an unreadable mapping merged in
                complete = False
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
            else:  # a computed key: cannot say what it names
                complete = False
        return names, complete

    if isinstance(env, ast.Call) and isinstance(env.func, ast.Name) and env.func.id == "dict":
        complete = not env.args  # dict(os.environ, …) merges an unreadable base
        for kw in env.keywords:
            if kw.arg is None:  # dict(**other)
                complete = False
            else:
                names.add(kw.arg)
        return names, complete

    return set(), False  # a variable, a comprehension, a helper call


def _child_encoding_pinned(node: ast.Call, argv: list[str | None]) -> bool | None:
    """Whether the child's own output encoding is pinned.

    True when pinned; False when provably not (no `env=` at all, or an `env=`
    the guard can read in full that omits the pin); None when an `env=` is
    passed whose contents cannot be read out. All three of False and None are
    reported — they differ only in the message, which has to be honest about
    which one it is.
    """
    if CHILD_ENCODING_ARGV_JOINED in argv:
        return True
    if any(pair == CHILD_ENCODING_ARGV_PAIR for pair in zip(argv, argv[1:])):
        return True

    env = _keyword(node, "env")
    if env is None:
        return False

    names, complete = _env_var_names(env.value)
    if names & CHILD_ENCODING_ENV_VARS:
        return True
    return False if complete else None


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

        # `encoding=` pins the codec and implies text mode. The parent is right;
        # a Python child then has to agree with it (#962).
        if _keyword(node, "encoding") is not None:
            argv = _argv_literals(node)
            if not _is_python_child(argv):
                continue
            pinned = _child_encoding_pinned(node, argv)
            if pinned is True:
                continue
            detail = (
                "passes an env= this scanner cannot read, so the pin cannot be proven"
                if pinned is None
                else "the child's own encoding is unpinned"
            )
            out.append(
                Violation(
                    path,
                    node.lineno,
                    name,
                    f"pins the parent's encoding= for a Python child, but {detail} — "
                    f"on Windows the child emits cp1252, the decode raises on "
                    f"subprocess's reader thread, and proc.stdout comes back None",
                    kind=CHILD_ENCODE,
                )
            )
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
    parent = [v for v in violations if v.kind == PARENT_DECODE]
    child = [v for v in violations if v.kind == CHILD_ENCODE]

    if parent:
        print(f"Text-mode subprocess calls without a pinned encoding: {len(parent)}\n")
        for v in parent:
            print(f"  {v}")
        print(
            "\nAdd `encoding=\"utf-8\"` to each call. Text mode without it decodes with\n"
            "locale.getencoding() — UTF-8 in CI, cp1252 on Windows — so this passes here\n"
            "and dies on a maintainer's machine the first time a child emits an emoji.\n"
            "See docs/lessons-ledger.md and issue #945."
        )

    if child:
        if parent:
            print()
        print(f"Python children whose own output encoding is unpinned: {len(child)}\n")
        for v in child:
            print(f"  {v}")
        print(
            '\nAdd `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` to each call (or\n'
            "`-X utf8` in the argv). Pinning only the parent turns a silently mojibake'd\n"
            "line into a UnicodeDecodeError raised on subprocess's reader thread: the call\n"
            "still returns, proc.stdout is None, and the traceback blames the caller's\n"
            "arithmetic. See docs/lessons-ledger.md and issue #962."
        )

    if violations:
        return 1

    scanned = sum(1 for d in SCAN_DIRS for _ in (REPO_ROOT / d).rglob("*.py"))
    print(
        f"Subprocess encoding OK: {scanned} Python files, every text-mode call pins "
        f"encoding= and every Python child pins its own."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
