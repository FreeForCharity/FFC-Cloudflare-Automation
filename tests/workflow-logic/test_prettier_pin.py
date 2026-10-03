"""Guard: docs hand out the CI-pinned prettier, never a floating one.

Why this module exists (Conductor run 187). `722-ci.yml` checks with
`npx --yes prettier@3.8.1`. CLAUDE.md has told agents to match that pin for
months. `AGENTS.md` — the file AGENTS.md itself tells every agent to read
FIRST — contradicted it at `:403`, handing out `npx prettier --write` in the
safety-table conflict recipe. Two docs, two answers, and which one an agent
follows is decided by which file it happened to open.

The cost is measured, not hypothetical: `npx prettier@3 --check
docs/lessons-ledger.md` reported style issues on #1500's branch, which
`npx --yes prettier@3.8.1 --check` on the same bytes called clean. The check was
wrong and the branch was right. A reviewer who trusts the unpinned run bounces a
correct PR, and the author then "fixes" the formatting into something CI will
reject.

Prose could not hold this, because prose is exactly what failed: the rule was
already written down, in the other file. So it is enforced over the tracked
Markdown instead.

`test_the_scanner_flags_the_pre_fix_shape` is the load-bearing one. The tree is
clean by construction once AGENTS.md is fixed, so every other assertion here
would also pass against a scanner that returned `[]` unconditionally — the
lesson #943's guard recorded and #945's repeated.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_SPEC = importlib.util.spec_from_file_location(
    "check_prettier_pin", REPO_ROOT / "scripts" / "check-prettier-pin.py"
)
check_prettier_pin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_prettier_pin)

scan_text = check_prettier_pin.scan_text


def test_the_tracked_markdown_is_clean():
    findings = check_prettier_pin.scan_paths(check_prettier_pin._tracked_markdown())
    assert not findings, (
        "a doc hands out an unpinned prettier; CI pins prettier@3.8.1 and the "
        "Markdown reflow differs between minors:\n  " + "\n  ".join(findings)
    )


def test_the_scanner_flags_the_pre_fix_shape():
    """The exact line that was in AGENTS.md:403 before this fix."""
    pre_fix = (
        "  after its numeric neighbor, then `npx prettier --write` the file and re-run\n"
    )
    findings = scan_text(pre_fix, "AGENTS.md")
    assert len(findings) == 1, findings
    assert "npx prettier --write" in findings[0], findings


def test_a_bare_major_is_still_unpinned():
    """`prettier@3` is what produced the false red — a major that floats."""
    assert scan_text("run `npx --yes prettier@3 --check docs/x.md` first")


def test_the_pinned_form_is_accepted():
    assert scan_text("`npx --yes prettier@3.8.1 --write docs/x.md`") == []
    assert scan_text("`npx prettier@3.8.1 --check .`") == []


def test_naming_the_anti_pattern_is_not_using_it():
    """Negative polarity. CLAUDE.md warns about `npx prettier` by name, and a
    guard that fired on the warning would make the warning unwritable."""
    warning = (
        "- **Format with the CI-pinned prettier.** `722-ci.yml` checks with "
        "`npx --yes prettier@3.8.1`; plain\n"
        "  `npx prettier` fetches the latest version, whose Markdown reflow differs.\n"
    )
    assert scan_text(warning, "CLAUDE.md") == []


def test_prose_outside_a_code_span_is_not_a_command():
    assert scan_text("We used to tell people to npx prettier --write and that was wrong.") == []


def test_a_fenced_block_is_scanned():
    """The canonical copy-paste recipe lives in a fenced block, so a span-only
    scanner could not see the one line most likely to regress. Measured on
    `CLAUDE.md:64` before this was fixed: the whole tree scanned clean with
    that recipe unpinned."""
    doc = (
        "Run CI's own command:\n"
        "\n"
        "```bash\n"
        '( cd "$(git rev-parse --show-toplevel)" && npx prettier --check . --ignore-unknown )\n'
        "```\n"
    )
    findings = scan_text(doc, "CLAUDE.md")
    assert len(findings) == 1, f"expected the fenced recipe to be flagged, got {findings}"
    assert "CLAUDE.md:4" in findings[0], findings
    assert "npx prettier --check" in findings[0], findings


def test_a_pinned_fenced_recipe_is_accepted():
    """The real tree's fenced recipe. Scanning fence bodies must not turn the
    repo's own correct instructions into a finding."""
    doc = (
        "```bash\n"
        '( cd "$(git rev-parse --show-toplevel)" && '
        "npx --yes prettier@3.8.1 --check . --ignore-unknown )\n"
        "```\n"
    )
    assert scan_text(doc, "CLAUDE.md") == []


def test_a_tilde_fence_counts_and_a_closed_fence_restores_span_scanning():
    """A `~~~` fence is a fence, and prose after a fence closes is back to
    span-only — otherwise one stray fence turns the rest of the file into
    raw-line scanning and the prose rule silently stops applying."""
    assert scan_text("~~~\nnpx prettier --write x.md\n~~~\n") != []
    after = (
        "```bash\n"
        "echo hello\n"
        "```\n"
        "We used to tell people to npx prettier --write and that was wrong.\n"
    )
    assert scan_text(after) == [], "prose after a closed fence is not a command"


def test_an_indented_continuation_is_not_treated_as_code():
    """Deliberate scope boundary: a 4-space indented line is NOT code here.

    In Markdown an indented block is a code block, but it is also how a wrapped
    bullet continuation renders, and `CLAUDE.md:41` is exactly that. So the
    fence is the only unambiguous code delimiter and indentation is left alone.

    The fixture must carry an UNPINNED invocation with an action flag and no
    backticks — the one shape that scanning indented lines raw would flag. An
    earlier draft of this test used a pinned command, which passes whether or
    not the boundary holds and so asserted nothing."""
    wrapped = (
        "  - **Never do this.** The advice we used to give was\n"
        "    to run npx prettier --write over the tree, and it caused\n"
        "    local-pass/CI-fail loops.\n"
    )
    assert scan_text(wrapped, "CLAUDE.md") == []


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-prettier-pin.py" in ci, (
        "check-prettier-pin.py must run in 722-ci.yml, or the rule is documentation "
        "— which is the thing that already failed here"
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
