"""Unit tests for the tracked-filename guard (#1165).

The tree is clean by construction -- 857 tracked paths, none outside printable
ASCII -- so almost every test that could be written here would also pass against
a `scan()` that returned `[]` unconditionally. Two do not, and they are the
load-bearing pair:

  * `test_the_guard_reads_an_unprintable_name_undecoded` builds a real
    repository holding the U+F022 file from the incident and requires the guard
    to name it. This is #1165's AC2: it goes red the moment `-z` is dropped from
    `tracked_paths`, because `git` then hands back the printable ASCII escape
    `"\\357\\200\\242\\357\\200\\242"` and a codepoint scan reads it as clean.

  * `test_default_git_output_quotes_the_name_into_printable_ascii` measures the
    trap itself, in the same repository, so the reason for the choice is a
    result rather than a comment. Without it the first test looks like an
    arbitrary preference between two spellings of the same command.

Everything else pins the rule's edges, the fail-closed path, and the CI wiring.

Run: python3 tests/workflow-logic/test_tracked_filenames.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "check-tracked-filenames.py"

_spec = importlib.util.spec_from_file_location("check_tracked_filenames", GUARD)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(guard)

# The exact name from the incident: U+F022 twice, `ef 80 a2 ef 80 a2` on disk.
INCIDENT_NAME = "\uf022\uf022"


def _scratch_repo(tmpdir: str, filename: str) -> pathlib.Path:
    """A real git repository with `filename` staged, so `ls-files` reports it.

    Staged rather than committed on purpose: the index is what `git ls-files`
    reads, and staged-but-uncommitted is the state the original mistake passed
    through (`git add -A` beside the edited files).
    """
    root = pathlib.Path(tmpdir)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    # A repo-local identity, so the test never depends on the host's git config.
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / filename).write_bytes(b"")
    subprocess.run(["git", "add", "--", filename], cwd=root, check=True)
    return root


def _main(argv: list[str]) -> tuple[int, str]:
    """`guard.main`, with its report captured rather than printed.

    Captured because the report is deliberately written as `::error::`, and a
    workflow-command line on stdout becomes a GitHub annotation whether or not
    the test passed -- so an uncaptured call would decorate a green CI run with
    the very errors these tests exist to provoke. The text is returned instead
    of discarded, so it stays available to assert on.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = guard.main(argv)
    return code, buffer.getvalue()


# --- the load-bearing pair --------------------------------------------------


def test_the_guard_reads_an_unprintable_name_undecoded():
    """AC2. Drop `-z` from `tracked_paths` and this goes red.

    The guard must see the two U+F022 codepoints, not git's printable escape of
    them. This is the whole check: a scan that reads the escape finds nothing
    and passes on a tree holding the very file it exists to catch.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _scratch_repo(tmpdir, INCIDENT_NAME)
        paths = guard.tracked_paths(root)

        assert INCIDENT_NAME in paths, (
            f"tracked_paths did not return the raw name; got {paths!r}. If this "
            f"fails after an edit to tracked_paths, `-z` is what you removed."
        )
        findings = guard.scan(paths, allowed=())
        assert len(findings) == 1, findings
        assert "private-use character U+F022" in findings[0], findings[0]


def test_default_git_output_quotes_the_name_into_printable_ascii():
    """The trap, measured. Without `-z`, git hands back printable ASCII.

    Every character of `"\\357\\200\\242\\357\\200\\242"` is inside the band the
    rule considers clean, which is exactly why the naive form of this guard
    passes on a dirty tree.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _scratch_repo(tmpdir, INCIDENT_NAME)
        proc = subprocess.run(
            ["git", "ls-files"],  # deliberately no -z: this is the trap
            cwd=root,
            capture_output=True,
            check=True,
        )
        quoted = proc.stdout.decode("utf-8", "surrogateescape")

        assert INCIDENT_NAME not in quoted, (
            "git returned the raw name without -z; the premise of this guard's "
            "choice of flag has changed and its docstring is now wrong"
        )
        assert "\\357\\200\\242" in quoted, quoted
        assert guard.scan([quoted.strip()], allowed=()) == [], (
            "the quoted form must scan CLEAN -- that is the failure this guard "
            "is designed around, and if it ever reports, this test is measuring "
            "something else"
        )


# --- the rule's edges -------------------------------------------------------


def test_ordinary_ascii_paths_are_clean():
    paths = [
        "scripts/check-tracked-filenames.py",
        ".github/workflows/722-ci.yml",
        "docs/lessons-ledger.md",
        "a b/c-d_e.f~g",
    ]
    assert guard.scan(paths, allowed=()) == []


def test_a_control_character_is_a_finding():
    findings = guard.scan(["docs/note\tname.md"], allowed=())
    assert len(findings) == 1, findings
    assert "control character U+0009" in findings[0], findings[0]


def test_a_newline_in_a_name_is_a_finding():
    """The case `-z` exists for twice over: a newline both hides the path from a
    line-splitting reader and is itself outside the printable band."""
    findings = guard.scan(["docs/two\nlines.md"], allowed=())
    assert len(findings) == 1, findings
    assert "control character U+000A" in findings[0], findings[0]


def test_del_is_a_finding():
    findings = guard.scan(["docs/del\x7f.md"], allowed=())
    assert "control character U+007F" in findings[0], findings[0]


def test_an_ordinary_non_ascii_character_is_a_finding_and_is_labelled_as_such():
    """The broad rule, and the label that distinguishes it from the incident.

    An acute-accented `e` in a path is a different judgement call from a U+F022, and the report has
    to let a reader tell them apart -- one is plausibly deliberate.
    """
    findings = guard.scan(["docs/caf\u00e9.md"], allowed=())
    assert len(findings) == 1, findings
    assert "non-ASCII character U+00E9" in findings[0], findings[0]
    assert "private-use" not in findings[0], findings[0]


def test_an_undecodable_byte_is_reported_as_such():
    """A filename need not be valid UTF-8. Surrogateescape keeps the byte, and
    the report says which byte rather than dying on the decode."""
    findings = guard.scan(["docs/bad\udcff.md"], allowed=())
    assert len(findings) == 1, findings
    assert "undecodable byte 0xFF" in findings[0], findings[0]


def test_every_offending_character_is_reported_not_just_the_first():
    """AC3, at the character level as well as the path level."""
    findings = guard.scan([INCIDENT_NAME], allowed=())
    assert len(findings) == 1, findings
    assert findings[0].count("private-use character U+F022") == 2, findings[0]


def test_every_offending_path_is_reported_not_just_the_first():
    findings = guard.scan(["a\uf022", "b\uf022", "c\uf022"], allowed=())
    assert len(findings) == 3, findings


def test_the_finding_survives_a_terminal_that_cannot_render_the_name():
    """The name is printed through `ascii()`, so the report is copy-pasteable.

    The incident hid for a week behind exactly this: the name renders as `""`,
    as an octal escape, or as nothing, depending on the tool.
    """
    findings = guard.scan([INCIDENT_NAME], allowed=())
    assert INCIDENT_NAME not in findings[0], findings[0]
    assert "\\uf022" in findings[0], findings[0]


# --- the allowlist ----------------------------------------------------------


def test_the_allowlist_is_empty_on_this_tree():
    """Not a count assertion about the rule -- a statement that the broad rule
    was chosen because it costs nothing here (#1165 AC5)."""
    assert guard.ALLOWED_NON_ASCII_PATHS == (), (
        "an entry here is an explicit exception and belongs in the PR that adds it"
    )


def test_an_allowlisted_path_is_not_a_finding():
    assert guard.scan([INCIDENT_NAME], allowed=(INCIDENT_NAME,)) == []


def test_a_stale_allowlist_entry_is_an_error():
    """The freeze rule every sibling guard applies: an exception nobody prunes
    stops being a list of known exceptions."""
    errors = guard.stale_allowances(["README.md"], allowed=("docs/gone\uf022.md",))
    assert len(errors) == 1, errors
    assert "not tracked" in errors[0], errors[0]


def test_a_live_allowlist_entry_is_not_stale():
    assert guard.stale_allowances([INCIDENT_NAME], allowed=(INCIDENT_NAME,)) == []


# --- fail closed ------------------------------------------------------------


def test_a_directory_that_is_not_a_repository_fails_closed():
    """An enumeration that did not complete is not a clean tree."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            guard.tracked_paths(tmpdir)
        except guard.GitUnavailable as exc:
            assert "ls-files" in str(exc), str(exc)
            return
        raise AssertionError("tracked_paths returned instead of raising outside a repo")


def test_main_exits_non_zero_when_it_cannot_enumerate():
    """The fail-closed path has to reach the exit code, not just the exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        code, report = _main(["--repo", tmpdir])
        assert code == 1
        assert "could not enumerate tracked paths" in report, report


def test_main_exits_non_zero_on_a_repo_holding_the_incident_file():
    """AC1, end to end through `main` rather than through `scan`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _scratch_repo(tmpdir, INCIDENT_NAME)
        code, report = _main(["--repo", str(root)])
        assert code == 1
        assert "private-use character U+F022" in report, report


def test_main_exits_zero_on_this_repository():
    """AC1's other half. Also the check that the broad rule is actually clean
    here, rather than clean by assertion."""
    code, report = _main(["--repo", str(REPO_ROOT)])
    assert code == 0, report


# --- wiring -----------------------------------------------------------------


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-tracked-filenames.py" in ci, (
        "check-tracked-filenames.py must run in 722-ci.yml, or the rule is documentation"
    )


def test_the_ci_step_is_not_gated_to_one_event():
    """AC4. `722` is a required check and the merge group is where required
    checks are evaluated for a queued PR, so a step that skipped on
    `merge_group` could be satisfied without ever having scanned -- the same
    false clean the large-blob guard's own comment warns about.

    Each delimiter is asserted present BEFORE it is used to slice. An
    `IndexError` out of a bare `split(...)[1]` would not be caught by this
    module's runner, which handles `AssertionError` only, so a renamed step
    would abort the module partway and take every later test with it -- a
    clean-looking list of PASSes with the failures simply never run (L82,
    `run_all.py`'s truncation guard). Raised by Copilot on #1267.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")

    step_name = "Validate tracked filenames are printable ASCII"
    invocation = "scripts/check-tracked-filenames.py"
    assert step_name in ci, f"722-ci.yml no longer has a step named {step_name!r}"

    block = ci.split(step_name, 1)[1]
    assert invocation in block, (
        f"the {step_name!r} step no longer invokes {invocation} -- the step and "
        f"its command have drifted apart, so this test can no longer read the "
        f"step body it is asserting about"
    )
    block = block.split(invocation, 1)[0]

    assert "if:" not in block, (
        "the tracked-filename step must carry no event gate, so it runs on "
        f"pull_request, merge_group and push alike; found:\n{block}"
    )


def test_ci_still_triggers_on_merge_group():
    """The other half of AC4: an ungated step is only everywhere if the workflow
    itself still listens on the merge group."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "jobs:" in ci, "722-ci.yml has no `jobs:` key; this file is not what it was"
    header = ci.split("jobs:", 1)[0]
    assert "merge_group:" in header, header


def test_the_failure_report_is_pure_ascii():
    """The printed report must be ASCII, so it cannot die on any console.

    `print()` on a Windows console encodes with the console codepage, and a
    character it cannot represent raises `UnicodeEncodeError` -- a correct
    finding becomes a traceback on the host most likely to have produced the
    file (L35, #945).

    THE OBVIOUS WEAKER ASSERTION DOES NOT WORK, and finding that out is why this
    test is worth its lines. `report.encode("cp1252")` was the first version. It
    passes on an em dash (cp1252 0x97) and on an accented letter (0xe9), so it
    could not tell the guard's original text from its current text at all -- and
    a mutation putting an em dash back **survived** it. cp1252 fails only on
    characters outside its 256, an arrow or an emoji among them, which is
    precisely the class an author is least likely to type by accident. ASCII is
    therefore the band to assert: it is the only one safe on every console, and
    it is the one a reviewer can check by eye.

    Measured against the real report rather than read off the source.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _scratch_repo(tmpdir, INCIDENT_NAME)
        code, report = _main(["--repo", str(root)])
        assert code == 1, report

    offenders = sorted({hex(ord(char)) for char in report if not 0x20 <= ord(char) <= 0x7E})
    # A report is multi-line, so the newline is expected and is not an offender.
    offenders = [point for point in offenders if point not in ("0xa", "0xd", "0x9")]
    assert not offenders, (
        f"the failure report contains non-ASCII character(s) {offenders}; keep "
        f"the text in `main()` to ASCII so it cannot fail to encode on any "
        f"console (L35, #945)"
    )


def test_this_module_holds_no_invisible_character_of_its_own():
    """The file that tests a printable-ASCII rule must itself be printable ASCII.

    Not vanity: writing about an unprintable character is a way of shipping one.
    Three artifacts in this PR's own authoring picked up real `U+F022`
    characters, because a `\\u` escape typed into a source file, a PR body and
    an API call was *resolved* on the way rather than written literally --
    nothing objects, and in a terminal the character and the escape render
    identically. Copilot flagged the two in this module; this test is what stops
    the next one, and it fails loudly with the codepoint and the line, because
    the diff will not show it.
    """
    text = pathlib.Path(__file__).read_text(encoding="utf-8")
    offenders = [
        (n, hex(ord(char)), line)
        for n, line in enumerate(text.split("\n"), 1)
        for char in line
        if not (0x20 <= ord(char) <= 0x7E)
    ]
    assert not offenders, (
        "this module contains a character outside printable ASCII; write it as "
        f"an escape instead so a reader can see it: {offenders[:5]}"
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
