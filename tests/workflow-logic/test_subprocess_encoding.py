"""Guard: text-mode subprocess calls pin an encoding, never the OS default.

Why this module exists (#945). After #944 made these modules runnable on Windows
at all, 18 still failed — and the largest single cause was that `text=True`
without `encoding=` decodes with `locale.getencoding()`: UTF-8 on the runners,
**cp1252 on a Windows host**. 41 test modules and 2 scripts carried that shape.
The trigger is ordinary content, not exotic input: the alert workflows open
issues titled `🚨 Scheduled workflow failing: …`, and any call whose child echoes
one of those titles aborts with

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 1000

It was not test-only. `scripts/generate-agentic-os-status.py` — which produces
the public status feed on ffcadmin.org — died the same way on its stdout path
unless the caller happened to set `PYTHONUTF8=1`.

Same shape as #943 / ledger L35: **the defect is invisible on the platform CI
runs on**, so the suite going red can never catch a regression. The rule is
therefore enforced statically against the source, where it is visible from every
platform, by `scripts/check-subprocess-encoding.py`.

`test_the_scanner_flags_the_pre_fix_shape` is the load-bearing test. It feeds the
scanner the exact construction that was in the tree before this fix and requires
a finding. Without it, this module only asserts a happy path — the tree is clean
by construction after the fix, so a scanner that returned `[]` unconditionally
would pass every other assertion here. That is the lesson #943's guard recorded,
applied to a second scanner.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "check_subprocess_encoding",
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-subprocess-encoding.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

find_violations = _MOD.find_violations
scan_repo = _MOD.scan_repo

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# The load-bearing one: the scanner must actually see the defect.
# --------------------------------------------------------------------------


def test_the_scanner_flags_the_pre_fix_shape():
    """The exact construction that was in the tree before #945."""
    src = (
        "import subprocess\n"
        "def run(cmd):\n"
        "    return subprocess.run(cmd, capture_output=True, text=True)\n"
    )
    found = find_violations(src, "pre_fix.py")
    assert len(found) == 1, f"scanner missed the pre-fix shape: {[str(f) for f in found]}"
    assert found[0].line == 3, f"wrong line: {found[0].line}"
    assert "text=True" in found[0].reason


def test_the_scanner_flags_universal_newlines_too():
    """`universal_newlines=True` is the older spelling of the same request."""
    src = "import subprocess\nsubprocess.check_output(['x'], universal_newlines=True)\n"
    found = find_violations(src, "un.py")
    assert len(found) == 1, "universal_newlines=True must be treated as text mode"
    assert "universal_newlines=True" in found[0].reason


def test_errors_alone_requests_text_mode():
    """`errors=` implies text mode even with no text= keyword, so it needs a codec."""
    src = "import subprocess\nsubprocess.run(['x'], errors='replace')\n"
    found = find_violations(src, "err.py")
    assert len(found) == 1, "errors= implies text mode and must require an encoding"


def test_a_non_literal_text_flag_is_reported():
    """The guard cannot prove a flag is never true, and the failure is silent."""
    src = "import subprocess\nFLAG=True\nsubprocess.run(['x'], text=FLAG)\n"
    found = find_violations(src, "flag.py")
    assert len(found) == 1, "a non-literal text= must be reported, not assumed false"
    assert "non-literal" in found[0].reason


# --------------------------------------------------------------------------
# It must not cry wolf: the correct shapes have to stay silent.
# --------------------------------------------------------------------------


def test_a_pinned_encoding_passes():
    src = "import subprocess\nsubprocess.run(['x'], text=True, encoding='utf-8')\n"
    assert find_violations(src, "ok.py") == [], "text=True with encoding= is the fixed shape"


def test_encoding_without_text_passes():
    """`encoding=` implies text mode on its own and pins it — nothing to flag."""
    src = "import subprocess\nsubprocess.run(['x'], encoding='utf-8')\n"
    assert find_violations(src, "ok2.py") == []


def test_binary_mode_is_not_a_violation():
    """Bytes have no codec to get wrong. Keying on text mode is the point."""
    src = "import subprocess\nsubprocess.run(['x'], capture_output=True)\n"
    assert find_violations(src, "bin.py") == [], "binary calls must not be flagged"


def test_explicit_text_false_is_not_a_violation():
    src = "import subprocess\nsubprocess.run(['x'], text=False)\n"
    assert find_violations(src, "false.py") == [], "text=False is binary mode"


def test_a_truthy_non_bool_constant_still_requests_text_mode():
    """`text=1` enables text mode — subprocess tests truth, not identity.

    Matching only `is True` made this the one text-mode spelling the guard
    waved through, so the cp1252 decode it exists to prevent came back
    undetected. Both spellings, since both are accepted keywords.
    """
    for kw in ("text", "universal_newlines"):
        src = f"import subprocess\nsubprocess.run(['x'], {kw}=1)\n"
        found = find_violations(src, "truthy.py")
        assert len(found) == 1, f"{kw}=1 is text mode and must be flagged, got {found}"
        assert kw in found[0].reason, f"the reason must name {kw}, got {found[0].reason}"


def test_falsy_non_bool_constants_stay_binary_mode():
    """The mirror of the above: 0 and None are binary, not findings."""
    for literal in ("0", "None"):
        src = f"import subprocess\nsubprocess.run(['x'], text={literal})\n"
        assert find_violations(src, "falsy.py") == [], f"text={literal} is binary mode"


def test_a_non_subprocess_call_named_run_is_ignored():
    """`self.run(text=True)` is not subprocess; the guard must not claim it."""
    src = "import subprocess\nother.run(['x'], text=True)\n"
    assert find_violations(src, "other.py") == [], "only subprocess entry points are in scope"


# --------------------------------------------------------------------------
# The tree itself.
# --------------------------------------------------------------------------


def test_the_repository_is_clean():
    found = scan_repo(REPO_ROOT)
    assert found == [], "text-mode subprocess without encoding=:\n" + "\n".join(
        f"  {v}" for v in found
    )


def test_the_status_feed_generator_pins_stdout():
    """The public-feed generator writes issue titles containing emoji to stdout.

    Its `--output` path was always pinned; stdout was not, so running it on a
    Windows host without `PYTHONUTF8=1` died with `UnicodeEncodeError` on the 🚨
    in an alert title. It is load-bearing for ffcadmin.org/agentic-os.
    """
    src = (REPO_ROOT / "scripts" / "generate-agentic-os-status.py").read_text(encoding="utf-8")
    assert "sys.stdout.reconfigure(encoding=" in src, (
        "generate-agentic-os-status.py must pin its stdout encoding — it emits issue "
        "titles that contain emoji and is the source of the public status page"
    )


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-subprocess-encoding.py" in ci, (
        "check-subprocess-encoding.py must run in 722-ci.yml, or the rule is documentation"
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
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
