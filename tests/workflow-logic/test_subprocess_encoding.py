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
# The other half of the fix (#962): the CHILD's encode, not just the parent's
# decode. A Python child writes stdout in the console codepage, so pinning only
# this side converts mojibake into a UnicodeDecodeError raised on subprocess's
# reader thread — the call still returns, with proc.stdout None, and the caller
# dies later on `None + str`. Observed for real on 2026-07-31 (run 60).
# --------------------------------------------------------------------------


def test_a_python_child_with_only_the_parent_pinned_is_flagged():
    """The exact shape that produced the None-stdout crash."""
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], capture_output=True,\n"
        "               text=True, encoding='utf-8')\n"
    )
    found = find_violations(src, "half.py")
    assert len(found) == 1, f"the child half must be flagged, got {[str(f) for f in found]}"
    assert found[0].kind == "child", f"wrong kind: {found[0].kind}"
    assert "unpinned" in found[0].reason, found[0].reason


def test_pinning_the_child_clears_it():
    """The fix, in the spelling the repo uses."""
    src = (
        "import os, subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8',\n"
        "               env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})\n"
    )
    assert find_violations(src, "fixed.py") == [], "the pinned-both-ends shape is the fix"


def test_the_other_spellings_of_a_child_pin_are_accepted():
    """PYTHONUTF8 and `-X utf8` make the child emit UTF-8 just as well."""
    variants = (
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8',\n"
        "               env=dict(os.environ, PYTHONUTF8='1'))\n",
        "subprocess.run([sys.executable, '-X', 'utf8', 'x.py'], text=True, encoding='utf-8')\n",
        "subprocess.run([sys.executable, '-Xutf8', 'x.py'], text=True, encoding='utf-8')\n",
    )
    for v in variants:
        src = "import os, subprocess, sys\n" + v
        assert find_violations(src, "variant.py") == [], f"must accept:\n{v}"


def test_a_stray_utf8_argument_is_not_mistaken_for_a_pin():
    """`-X utf8` pins the child; the word `utf8` sitting in argv does not.

    Accepting a bare token would let an ordinary script argument (a mode name, a
    filename) silence the rule — a false negative in the guard is worse than a
    false positive, because nothing ever tells you it happened.
    """
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py', 'utf8'], text=True, encoding='utf-8')\n"
    )
    found = find_violations(src, "stray.py")
    assert len(found) == 1, "a positional 'utf8' argument is not `-X utf8`"


def test_a_bare_python_argv_counts_as_a_python_child():
    """Not every call spells the interpreter `sys.executable`."""
    for exe in ("python", "python3", "python3.12", "py", "python.exe"):
        src = f"import subprocess\nsubprocess.run(['{exe}', 'x.py'], text=True, encoding='utf-8')\n"
        found = find_violations(src, "bare.py")
        assert len(found) == 1, f"{exe} is a Python child and must be flagged, got {found}"


def test_a_non_python_child_is_not_asked_for_pythonioencoding():
    """`node`/`git`/`pwsh` never read it — demanding it there would be noise."""
    for exe in ("node", "git", "pwsh", "npx"):
        src = f"import subprocess\nsubprocess.run(['{exe}', 'x'], text=True, encoding='utf-8')\n"
        assert find_violations(src, "other_child.py") == [], (
            f"{exe} does not read PYTHONIOENCODING; flagging it is pure noise"
        )


def test_an_unreadable_argv0_is_not_assumed_to_be_python():
    """Deliberately asymmetric with the `text=<non-literal>` rule.

    There the guard could not prove the call was *safe*. Here it cannot prove
    the child is *Python*, and a false demand on a `node` child is noise.
    """
    src = "import subprocess\nEXE='node'\nsubprocess.run([EXE, 'x'], text=True, encoding='utf-8')\n"
    assert find_violations(src, "unknown.py") == [], "an unprovable argv[0] is not a Python child"


def test_the_var_name_in_a_VALUE_position_does_not_satisfy_the_rule():
    """`{"FOO": "PYTHONIOENCODING"}` sets FOO, not PYTHONIOENCODING.

    The first cut matched the name anywhere under `env=`, so this shape — a
    genuinely unpinned call — read as fixed. A false negative in a guard is the
    worst outcome available to it: the defect ships *and* the check reports
    clean. Only key positions count (Copilot review, #963).
    """
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8',\n"
        "               env={'FOO': 'PYTHONIOENCODING'})\n"
    )
    found = find_violations(src, "value_position.py")
    assert len(found) == 1, "a var name in a value position pins nothing"
    assert "unpinned" in found[0].reason, found[0].reason


def test_a_readable_env_without_the_pin_says_unpinned_not_unreadable():
    """Provable absence and unprovable presence are different findings.

    A dict literal with constant keys can be read in full, so omitting the pin
    is a proof. Reporting that as 'cannot be read' sends the author looking for
    an unreadability problem that does not exist.
    """
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8',\n"
        "               env={'FOO': 'bar'})\n"
    )
    found = find_violations(src, "readable_absent.py")
    assert len(found) == 1, "a readable env= without the pin is still a finding"
    assert "unpinned" in found[0].reason, found[0].reason
    assert "cannot be read" not in found[0].reason, (
        f"the env IS readable; the message must not claim otherwise: {found[0].reason}"
    )


def test_a_merged_mapping_keeps_the_benefit_of_the_doubt():
    """`{**BASE}` may carry the pin, so it is 'unproven', not 'absent'."""
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8',\n"
        "               env={**BASE, 'FOO': 'bar'})\n"
    )
    found = find_violations(src, "merged.py")
    assert len(found) == 1, "an unreadable merge is still reported"
    assert "cannot be proven" in found[0].reason, found[0].reason


def test_an_env_that_cannot_be_read_is_reported():
    """A variable env may or may not carry the pin, and the failure is silent."""
    src = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, 'x.py'], text=True, encoding='utf-8', env=ENV)\n"
    )
    found = find_violations(src, "opaque_env.py")
    assert len(found) == 1, "an unreadable env= must be reported, not assumed pinned"
    assert "cannot be proven" in found[0].reason, found[0].reason


def test_the_child_rule_does_not_pre_empt_the_parent_rule():
    """No `encoding=` at all is the parent's finding, with the parent's message.

    A Python child that pins neither end must not be re-labelled as a child
    problem — `encoding=` is the fix that call needs first, and the message the
    author sees has to say so.
    """
    src = "import subprocess, sys\nsubprocess.run([sys.executable, 'x.py'], text=True)\n"
    found = find_violations(src, "neither.py")
    assert len(found) == 1, f"expected exactly one finding, got {[str(f) for f in found]}"
    assert found[0].kind == "parent", f"must stay the parent finding, got {found[0].kind}"
    assert "without encoding=" in found[0].reason, found[0].reason


def test_a_binary_python_child_is_not_a_finding():
    """No text mode, no decode — the child's codepage cannot bite."""
    src = "import subprocess, sys\nsubprocess.run([sys.executable, 'x.py'], capture_output=True)\n"
    assert find_violations(src, "binary_child.py") == [], "binary mode has no codec to get wrong"


def test_the_three_run_60_call_sites_pin_the_child():
    """The sites #962 names, asserted by name so a revert is loud.

    The guard above proves the *rule*; this proves these specific call sites
    were actually fixed — including `run_all.py`, which is the harness every
    other module runs under and so the one whose None-stdout failure looks like
    a bug in every module at once.

    Asserted by running the scanner over each file rather than by grepping for
    `PYTHONIOENCODING`: a substring search passes on a file where the pin was
    deleted but is still named in a comment or a docstring — including the
    comment this very fix added above the `env=` in `run_all.py`. The scanner
    reads the call, so only a real pin in a real key position satisfies it.
    """
    here = pathlib.Path(__file__).resolve().parent
    for name in (
        "run_all.py",
        "test_env_secret_reference_resolution.py",
        "test_powershell_command_resolution.py",
    ):
        src = (here / name).read_text(encoding="utf-8")
        unpinned = [v for v in find_violations(src, name) if v.kind == "child"]
        assert unpinned == [], (
            f"{name} spawns a Python child with the parent's encoding pinned but not "
            f"the child's; on Windows that returns proc.stdout=None (#962):\n"
            + "\n".join(f"  {v}" for v in unpinned)
        )


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
