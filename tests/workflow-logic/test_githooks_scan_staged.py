"""Tests for the shared pre-commit secret scanner, .githooks/scan_staged.py (#996).

The scanner fails open by design: `except Exception: sys.exit(0)`, so a scanner
bug can never wedge a commit. That trade is sound. What made #996 a security
defect is that the fail-open was *silent*, so a total failure and a clean scan
produced the identical observable result -- the same shape as #722's large-blob
guard going green for the wrong reason.

The failure was real, not theoretical: `_git()` decoded git's output with the
LOCALE codec, and on the Windows Conductor host that is cp1252. A staged line
carrying a character whose UTF-8 bytes cp1252 leaves undefined raised
UnicodeDecodeError inside the diff read -- before find_secrets() was ever called
-- and the commit succeeded.

It is a narrower set than #996 claimed, and the bottom of this file pins it:
cp1252 rejects only 0x81/0x8D/0x8F/0x90/0x9D, so `”` (U+201D) and `❌` (U+274C)
abort the read while the **em dash does not**. Measured, not assumed.

So the tests here are mostly about the direction that produces a **falsely clean**
scan:

  * a legacy, non-UTF-8 locale is FORCED (`LC_ALL=C` with UTF-8 mode and PEP 538
    coercion off => ASCII), because CI is ubuntu-latest with a UTF-8 locale and a
    test that merely runs the scanner passes against a completely unfixed tree.
    ASCII is not cp1252, but the defect class is identical: an 8-bit locale codec
    decoding UTF-8 bytes from git. `test_the_forced_locale_is_actually_legacy`
    fails loudly if the forcing ever stops working -- a harness that silently
    stops reproducing the bug would turn every case below into decoration;
  * a **real secret** on a line that also carries non-ASCII is still detected and
    still exits 1. This is the criterion that matters -- "no exception" alone is
    satisfied by a scanner that decodes fine and detects nothing;
  * the fail-open is preserved (exit 0) **and** now prints a distinguishable
    "SECRET SCAN DID NOT RUN" line, pinned in both halves;
  * the notice itself survives a non-ASCII exception message under the forced
    locale. Anything raised *inside* the fail-open handler would escape it and
    wedge the commit -- the one outcome that handler exists to prevent. (Today
    `sys.stderr` defaults to errors="backslashreplace", so this holds without
    special-casing; the test pins that it keeps holding.)
  * the clean case stays silent, so the notice keeps meaning something.

No network. Each case builds a throwaway git repo, copies in the real scanner
plus a `common.py` fixture, stages content, and runs the scanner as a subprocess.

Run: python3 tests/workflow-logic/test_githooks_scan_staged.py
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / ".githooks" / "scan_staged.py"
REAL_COMMON = REPO_ROOT / ".claude" / "hooks" / "common.py"

# Fabricated, GitHub-PAT-shaped. Split so this file never contains a contiguous
# token and cannot trip the very scanner under test.
FAKE_PAT = "gh" + "p_" + "a" * 36

EM_DASH = "—"
CURLY = "“" + "”"
EMOJI = "\U0001f6a8"
NON_ASCII_LINE = f"A line {EM_DASH} with {CURLY}curly quotes{CURLY} and an {EMOJI} emoji"

# A legacy 8-bit locale. PEP 540 turns UTF-8 mode ON automatically for the C
# locale and PEP 538 coerces it to C.UTF-8, so BOTH must be disabled or Python
# quietly repairs the very condition under test.
LEGACY_ENV = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}


def _env(extra=None):
    # Inherit the whole environment and override -- a scrubbed env dict is what
    # broke the harness in #943 (and bash in test_722 before it).
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    env.update(extra or {})
    return env


def run_scanner(files, common_src=None, legacy_locale=True, args=(), init_repo=True):
    """Stage `files` ({path: content}) in a throwaway repo and run the scanner.

    Returns (returncode, stderr).

    The scanner's stderr is captured as BYTES and decoded here, deliberately:
    under the forced legacy locale the child's stderr genuinely is not UTF-8
    (Python writes it with errors="backslashreplace"), so pinning the child to
    PYTHONIOENCODING=utf-8 would remove part of what is under test. Decoding
    with errors="replace" is the honest read of a stream whose codec we chose
    to leave legacy.
    """
    env = _env(LEGACY_ENV if legacy_locale else {})
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, ".claude", "hooks"))
        os.makedirs(os.path.join(d, ".githooks"))
        shutil.copy(str(common_src or REAL_COMMON), os.path.join(d, ".claude", "hooks", "common.py"))
        shutil.copy(str(SCANNER), os.path.join(d, ".githooks", "scan_staged.py"))
        if init_repo:
            # check=True on BOTH setup calls: a failed init or add leaves
            # nothing staged, the scanner finds no files and exits 0, and
            # every allow-case below would go green on a broken harness --
            # the system under test made indistinguishable from its setup.
            subprocess.run(["git", "init", "-q"], cwd=d, env=env,
                           capture_output=True, check=True)
        for path, content in files.items():
            full = os.path.join(d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(path) else None
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
            if init_repo:
                subprocess.run(["git", "add", path], cwd=d, env=env,
                               capture_output=True, check=True)
        proc = subprocess.run(
            [sys.executable, os.path.join(d, ".githooks", "scan_staged.py"), *args],
            cwd=d,
            env=env,
            capture_output=True,  # bytes -- see the docstring
        )
        return proc.returncode, (proc.stderr or b"").decode("utf-8", "replace")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _common_that_raises(tmpdir, message):
    """A common.py fixture whose find_secrets() blows up -- the 'unexpected
    exception' path, forced without breaking the real shared module."""
    path = os.path.join(tmpdir, "common_raises.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "def is_sensitive_path(p):\n"
            "    return False\n\n\n"
            "def find_secrets(text):\n"
            f"    raise RuntimeError({message!r})\n"
        )
    return path


# --------------------------------------------------------------------------
# The harness itself: if this stops reproducing the bug, nothing below means
# anything, so it is a test rather than a silent precondition.
# --------------------------------------------------------------------------


def _preferred_encoding_under_legacy_env():
    """What codec a child process defaults to under LEGACY_ENV.

    The child prints an ASCII codec name, but WE still decode it as UTF-8
    explicitly: leaving `text=True` bare here would decode the probe's answer
    with the locale codec -- the exact defect this module exists to lock down,
    reintroduced in the test that measures it. `test_subprocess_encoding.py`
    catches that repo-wide, and did.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
        # Spelled out rather than routed through _env() so both repo guards can
        # read it: os.environ is visibly inherited (#943 -- a scrubbed env dict
        # breaks children on Windows) and the child's encode is visibly pinned
        # alongside our decode (#962). PYTHONIOENCODING moves only the child's
        # std streams; it does not touch locale.getpreferredencoding(), which
        # is the value being probed, so the answer is still the forced codec.
        env={**os.environ, **LEGACY_ENV, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stdout or "").strip()


def test_the_forced_locale_is_actually_legacy():
    reported = _preferred_encoding_under_legacy_env()
    enc = reported.lower().replace("-", "").replace("_", "")
    assert enc and "utf8" not in enc, (
        "LEGACY_ENV no longer forces a non-UTF-8 default encoding "
        f"(got {reported!r}); every case in this module would then "
        "pass against a completely unfixed scanner"
    )


def test_the_fixture_content_is_undecodable_in_that_locale():
    codec = _preferred_encoding_under_legacy_env()
    try:
        NON_ASCII_LINE.encode("utf-8").decode(codec)
    except (UnicodeDecodeError, LookupError):
        return
    raise AssertionError(
        f"the fixture line decodes cleanly as {codec!r}, so it does not reproduce "
        "the cp1252 crash this module exists to lock down"
    )


# --------------------------------------------------------------------------
# AC1 / AC2: non-ASCII content is scanned, and detection still works on it.
# --------------------------------------------------------------------------


def test_non_ascii_diff_is_scanned_not_crashed_through():
    rc, err = run_scanner({"doc.md": f"# notes\n{NON_ASCII_LINE}\n"})
    assert rc == 0, f"clean non-ASCII file must be allowed, got rc={rc}\n{err}"
    assert "DID NOT RUN" not in err, f"the scan aborted instead of running:\n{err}"
    assert "Traceback" not in err, f"unexpected traceback:\n{err}"


def test_secret_on_a_non_ascii_line_is_still_detected():
    rc, err = run_scanner({"doc.md": f"{NON_ASCII_LINE}\ntoken = \"{FAKE_PAT}\"\n"})
    assert rc == 1, (
        "a real secret staged alongside non-ASCII prose must BLOCK the commit "
        f"(got rc={rc}); this is the fail-open #996 reported\n{err}"
    )
    assert "BLOCKED" in err, f"the block must say so on stderr:\n{err}"


def test_secret_on_the_same_line_as_a_non_ascii_char_is_detected():
    rc, err = run_scanner({"doc.md": f"api_key = \"{FAKE_PAT}\"  # note {EM_DASH} see above\n"})
    assert rc == 1, f"secret + em dash on ONE line must block, got rc={rc}\n{err}"


def test_sensitive_path_with_non_ascii_content_still_blocks():
    rc, err = run_scanner({".env": f"# {EM_DASH} config\nX=1\n"})
    assert rc == 1, f".env must block regardless of content encoding, got rc={rc}\n{err}"


def test_detection_is_unchanged_under_a_utf8_locale():
    rc, err = run_scanner({"doc.md": f"token = \"{FAKE_PAT}\"\n"}, legacy_locale=False)
    assert rc == 1, f"the plain UTF-8 case must still block, got rc={rc}\n{err}"


# --------------------------------------------------------------------------
# AC4: the fail-open survives, and is loud.
# --------------------------------------------------------------------------


def test_unexpected_exception_still_exits_zero_but_says_it_did_not_run():
    tmp = tempfile.mkdtemp()
    try:
        stub = _common_that_raises(tmp, "boom")
        rc, err = run_scanner({"doc.md": "hello\n"}, common_src=stub)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 0, f"fail-open must be preserved: a scanner bug must not wedge commits (rc={rc})"
    assert "SECRET SCAN DID NOT RUN" in err, (
        "a crashed scan must be distinguishable from a clean one on stderr; "
        f"got:\n{err!r}"
    )
    assert "not a passing scan" in err.lower(), f"the notice must not read as a pass:\n{err}"
    assert "boom" in err, f"the notice must carry the cause:\n{err}"


def test_the_failure_notice_survives_a_non_ascii_exception_message():
    tmp = tempfile.mkdtemp()
    try:
        stub = _common_that_raises(tmp, f"boom {EM_DASH} {EMOJI}")
        rc, err = run_scanner({"doc.md": "hello\n"}, common_src=stub)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 0, (
        "an encode error inside the fail-open handler would escape it and wedge "
        f"the commit -- exactly what failing open exists to prevent (rc={rc})\n{err}"
    )
    assert "SECRET SCAN DID NOT RUN" in err, f"the notice must still be printed:\n{err!r}"


def test_missing_shared_module_is_reported_not_swallowed():
    tmp = tempfile.mkdtemp()
    try:
        broken = os.path.join(tmp, "common_broken.py")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("raise ImportError('no detection logic here')\n")
        rc, err = run_scanner({"doc.md": "hello\n"}, common_src=broken)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 0, f"an unimportable common.py must not block commits (rc={rc})"
    assert "SECRET SCAN DID NOT RUN" in err, (
        f"an unscanned commit must say so, whatever the reason:\n{err!r}"
    )


# --------------------------------------------------------------------------
# AC5: the clean path is unchanged, so the notice keeps meaning something.
# --------------------------------------------------------------------------


def test_a_failing_git_is_reported_not_read_as_nothing_staged():
    """The non-exception silent fail-open (Copilot review on #1000).

    `_git()` used to return `.stdout` whatever git's exit code was, so a failed
    `diff --cached` yielded "" -- indistinguishable from "nothing is staged".
    The scan then completed over an empty file list and exited 0 through the
    SUCCESS path, never reaching the handler that prints the notice. A staged
    secret went unscanned, silently, with no crash anywhere.

    Driven by running outside a work tree, which is the real shape of it: git
    exits non-zero and prints to stderr, and the old code read that as clean.
    """
    rc, err = run_scanner(
        {"doc.md": f'token = "{FAKE_PAT}"\n'}, init_repo=False, legacy_locale=False
    )
    assert rc == 0, f"a git failure must still fail OPEN, not wedge the commit (rc={rc})"
    assert "SECRET SCAN DID NOT RUN" in err, (
        "git failed, so nothing was scanned -- that must not be reported as a "
        f"clean scan. Got stderr:\n{err!r}"
    )


def test_clean_commit_is_silent():
    rc, err = run_scanner({"ok.md": "# hello world\n"})
    assert rc == 0, f"clean commit must be allowed, got rc={rc}\n{err}"
    assert err.strip() == "", f"a clean scan must stay silent, got:\n{err!r}"


def test_placeholder_is_still_allowed():
    rc, err = run_scanner({"doc.md": f'api_token = "your-api-token-here"  # {EM_DASH} sample\n'})
    assert rc == 0, f"documented placeholders must not block, got rc={rc}\n{err}"


def test_manual_path_mode_reads_the_worktree():
    rc, err = run_scanner(
        {"doc.md": f"{NON_ASCII_LINE}\ntoken = \"{FAKE_PAT}\"\n"}, args=("doc.md",)
    )
    assert rc == 1, f"explicit-path mode must detect the same secret, got rc={rc}\n{err}"


# --------------------------------------------------------------------------
# Which characters actually trigger this, pinned as executable fact.
#
# #996, this PR's first draft, and ledger L07 all said "an em dash crashes the
# cp1252 read". Measured on the Windows Conductor host, that is FALSE: cp1252
# leaves only five byte values undefined, and an em dash's UTF-8 bytes are not
# among them -- it decodes to mojibake and the scan completes, so a secret on an
# em-dash line was always caught. The characters that really abort the read are
# the ones whose UTF-8 encoding contains 0x81/0x8D/0x8F/0x90/0x9D -- in this
# repo's prose that is U+201D (the RIGHT curly double quote) and U+274C (the
# cross mark), both carrying 0x9D. L07's original crash was the cross mark, not
# the em dashes it blamed.
#
# This matters beyond pedantry: "every commit with an em dash was unscanned"
# implies the scanner was dead on nearly every commit this repo has ever taken,
# when in fact it worked on most of them and died on a narrow class. Anyone
# auditing which past commits went unscanned would get a wildly wrong answer,
# and anyone "confirming the bug" by committing an em dash on Windows would
# watch it block correctly and conclude the defect did not exist.
# --------------------------------------------------------------------------

CP1252_UNDEFINED = {0x81, 0x8D, 0x8F, 0x90, 0x9D}


def _decodes_as_cp1252(raw: bytes) -> bool:
    """True if an 8-bit cp1252 read would survive these bytes.

    Decodes with the codec by name rather than relying on the host's locale, so
    this measures the same thing on the Windows host and on ubuntu CI.
    """
    try:
        raw.decode("cp1252")
        return True
    except UnicodeDecodeError:
        return False

# (label, char, does an 8-bit cp1252 read abort on it?)
TRIGGER_TABLE = [
    ("em dash U+2014", "—", False),
    ("en dash U+2013", "–", False),
    ("left curly dq U+201C", "“", False),
    ("right curly sq U+2019", "’", False),
    ("ellipsis U+2026", "…", False),
    ("bullet U+2022", "•", False),
    ("check mark U+2713", "✓", False),
    ("siren emoji U+1F6A8", "\U0001f6a8", False),
    ("right curly dq U+201D", "”", True),
    ("cross mark U+274C", "❌", True),
]


def test_the_cp1252_undefined_byte_set_is_exactly_five():
    measured = {i for i in range(256) if not _decodes_as_cp1252(bytes([i]))}
    assert measured == CP1252_UNDEFINED, (
        "the premise of every claim in this file is which bytes cp1252 rejects; "
        f"expected {sorted(CP1252_UNDEFINED)}, measured {sorted(measured)}"
    )


def test_the_em_dash_does_not_trigger_the_crash_but_u201d_does():
    wrong = []
    for label, char, should_abort in TRIGGER_TABLE:
        aborts = not _decodes_as_cp1252(char.encode("utf-8"))
        if aborts != should_abort:
            wrong.append(f"{label}: expected abort={should_abort}, measured {aborts}")
    assert not wrong, (
        "the documented trigger set no longer matches reality -- fix the prose in "
        ".githooks/scan_staged.py, .githooks/README.md and docs/lessons-ledger.md "
        "rather than this table:\n  " + "\n  ".join(wrong)
    )


def test_the_shared_fixture_really_contains_a_triggering_character():
    """NON_ASCII_LINE reproduces on a real cp1252 host only because of U+201D.

    Drop the curly quotes from it and every non-ASCII case above still passes
    under the forced-ASCII CI locale while quietly ceasing to reproduce the bug
    on the host that actually has it.
    """
    triggering = [c for c in NON_ASCII_LINE if not _decodes_as_cp1252(c.encode("utf-8"))]
    assert triggering, (
        "NON_ASCII_LINE contains no character that aborts a cp1252 read, so it "
        "cannot reproduce #996 on the Windows host -- only under forced ASCII"
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
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
