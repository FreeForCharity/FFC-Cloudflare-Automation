#!/usr/bin/env python3
r"""Guard: a pwsh step that DOWNGRADES a captured `$LASTEXITCODE` must end with an explicit `exit` (#1068).

GitHub Actions appends an epilogue to every `pwsh` `run:` body:

    if ((Test-Path -LiteralPath variable:/LASTEXITCODE)) { exit $LASTEXITCODE }

So the step's exit status is whatever `$LASTEXITCODE` happens to hold when the
body ends -- not what the body decided. An author who captures a callee's exit
code and deliberately downgrades it to a warning has expressed an intent the
shell then ignores, because neither `Write-Warning` nor `Out-File` resets
`$LASTEXITCODE`.

That is #1068, in `101-domain-status.yml`'s `cloudflare` job:

    $dkimExit = $LASTEXITCODE
    ...
    if ($dkimExit -ne 0) {
      Write-Warning "m365-domain-preflight.ps1 ... exited with code $dkimExit ..."
    }
    "out_dir=$outDir" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
                                      # <-- last statement; no `exit 0`

The two log lines the run produced are the same condition reported twice -- once
as the author's tolerated warning, once as a step failure. Nothing in between
asked for the second.

WHY THIS IS A GUARD AND NOT A ONE-LINE FIX
    The *same file* already contains the correct idiom. The `m365` job's
    preflight step captures two exit codes, downgrades both to `Write-Warning`,
    and ends `exit 0`. Same workflow, same author, same intent; one step has the
    guard and the other does not. A defect whose corrected twin sits 160 lines
    away is a defect that will be reintroduced, so #1068's AC4 asks for the
    class to be covered rather than the line.

WHAT COUNTS AS A DOWNGRADE (and what deliberately does not)
    A step is in scope when it captures `$LASTEXITCODE` into a variable AND
    tolerates a non-zero value of it -- an `if ($x -ne 0) { ... }` whose block
    neither exits nor throws. That block is the author saying "this failure is
    acceptable".

    `::error::` is deliberately NOT a terminator: an annotation changes what the
    log says, not what the step returns, so a block that annotates and falls
    through is still relying on the epilogue and is still in scope. See the
    comment above `TERMINATES_RE`, and
    `test_an_error_annotation_without_an_exit_is_still_a_downgrade`.

    A step that captures the code and PROPAGATES it is not in scope:

        $code = $LASTEXITCODE
        if ($code -ne 0) { exit $code }        # correct; says nothing to us
        if ($code -ne 0) { throw "..." }       # correct
        if ($code -ne 0) { Write-Output '::error::...'; exit 1 }  # the `exit 1`,
                                                                 # not the annotation

    Only the tolerating shape is reported, because only it is contradicted by
    the epilogue. Flagging propagation would flag the majority of correct call
    sites and teach the reader to ignore this check.

THE REQUIREMENT
    The body's last executable statement -- after comments and blank lines are
    stripped -- must be an explicit `exit`. `exit 0` is the common case;
    `exit $someVar` is accepted, because an author who computed a status
    deliberately is not who this guard is aimed at.

FAIL CLOSED
    A body whose `if (...)` block cannot be delimited (unbalanced braces after
    expression masking) is a FINDING, not a skip. A conditional `exit` as the
    final statement is likewise a finding: `if ($x) { exit 1 }` leaves
    `$LASTEXITCODE` untouched on the other branch, which is precisely the state
    this guard exists to refuse. Both are reported with their own reason so the
    reader is never left guessing whether the scanner simply gave up.

Exit codes: 0 = every downgrading pwsh step ends with an explicit exit,
1 = at least one finding.
"""

from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# `powershell` is Windows PowerShell 5.1. The epilogue and `$LASTEXITCODE`
# semantics under test are identical in both hosts.
PWSH_SHELLS = frozenset({"pwsh", "powershell"})

# NOT masked: `${{ … }}` is left in the body deliberately.
#
# The sibling guard `check-pwsh-workflow-invocations.py` blanks GitHub
# expressions before parsing, because it hands the body to the PowerShell
# parser, which reads `${{` as a brace-quoted variable name. This scanner does
# not parse PowerShell -- it brace-matches and quote-tracks -- and `${{` / `}}`
# are brace-BALANCED by construction, so masking cannot change a verdict here.
#
# Measured rather than assumed, because a masking step is cheap to add and
# impossible to notice is dead: eight bodies were scanned with masking and with
# it stubbed out, including a quoted brace (`'{'`), an embedded double quote, a
# `#`, a multi-line expression, an expression inside the `if` block and one as
# the final statement. **All eight returned identical verdicts**, and a mutation
# deleting the mask survived the whole test module. It was removed rather than
# pinned by a source-shape test, which would have turned the table green while
# asserting a spelling instead of a behaviour. The expression-bearing fixtures
# in `test_pwsh_exit_downgrade.py` are kept -- they are real coverage of the
# quote-aware scanner against expression text, which is what actually protects
# this.

# `$name = $LASTEXITCODE`, optionally followed by a comment.
CAPTURE_RE = re.compile(
    r"^\s*\$(?P<var>[A-Za-z_]\w*)\s*=\s*\$LASTEXITCODE\s*(?:#.*)?$",
    re.IGNORECASE,
)

# Terminators that make an `if` block a PROPAGATION rather than a downgrade.
#
# `::error::` is deliberately NOT one of them. An annotation does not change the
# step's exit status -- only `exit` and `throw` do -- so a block that annotates
# and then falls through is still relying on the runner epilogue, which is the
# whole subject of this guard. It was in this set in the first revision, and it
# only ever matched because the scanner was reading INSIDE string literals; once
# literals are blanked (see `_scan_line`) an `::error::` written the way anyone
# actually writes it, `Write-Output "::error::…"`, is invisible here anyway.
TERMINATES_RE = re.compile(r"(?:^|[\s;{])(?:exit|throw)\b", re.IGNORECASE)

EXIT_STATEMENT_RE = re.compile(r"^exit\b", re.IGNORECASE)

UNBALANCED = "unbalanced-if-block"
NO_EXIT = "no-terminal-exit"
CONDITIONAL_EXIT = "conditional-terminal-exit"
UNTERMINATED_HERE = "unterminated-here-string"


@dataclass(frozen=True)
class Finding:
    kind: str
    variable: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"line {self.line} [{self.kind}] ${self.variable}: {self.detail}"


def _scan_line(line: str) -> tuple[str, str, str | None]:
    """Split one line into `(code, visible, here_open)`; the first two are the
    same length, and `here_open` is the quote character of a here-string this
    line OPENS (`"` or `'`), else None. Cross-line state belongs to the caller
    (`_scan_lines`), so this stays line-local.

    `visible` is the line with any trailing `#` comment removed and string
    literals left intact -- what a human should be shown quoted back at them.

    `code` is that same span with the **contents of every string literal
    blanked to spaces, the delimiters kept**. Every syntactic question this
    guard asks is asked of `code`, so a brace, a `#`, or the word `exit` inside
    a literal cannot be read as syntax -- while a line holding nothing but a
    literal still reads as executable, because its quotes survive. Blanking the
    delimiters too is the one thing that cannot be done here, and is its own
    entry in the list below. The first revision asked these questions of the raw
    text and was wrong three ways, each a SILENT one (Copilot, #1347):

      * `Write-Warning "tolerated exit $code"` matched `TERMINATES_RE`, so a
        real downgrade was classified as propagation and the step went
        unchecked -- a false negative in a guard, which is the failure class
        this whole PR is about;
      * a doubled quote (`""` / `''`, PowerShell's escape for a literal quote
        in both string kinds) was read as close-then-reopen. That inverts the
        parity for the rest of the line, so a later `#` looked like a comment
        and a later `}` like syntax;
      * a backtick was treated as an escape inside SINGLE-quoted strings, where
        PowerShell gives it no special meaning -- so `'a`'` consumed the real
        closing quote.

    HERE-STRINGS were once listed here as a "known limit" whose "failure
    direction is a brace that stops being counted, which surfaces as
    `unbalanced-if-block` -- a reported finding, not a silent pass." **That was
    measured and is false** (Copilot, #1347). A here-string whose text merely
    contains the word `exit` made `TERMINATES_RE` match inside the literal, so a
    real downgrade was filed as propagation and `scan_body` returned `[]`: a
    SILENT PASS, in the permissive direction, in the exact class this guard
    exists to catch -- the same defect as the single-line-literal one, one
    string kind over. The limit note would have told the next reader not to
    bother looking, which is worse than not mentioning it.

    So here-strings are now tracked: `_scan_lines` blanks their contents, and a
    here-string that never closes is REPORTED rather than swallowed (blanking to
    end-of-body would hide the `$LASTEXITCODE` capture and return `[]` again --
    trading one silent pass for another).
    """
    code: list[str] = []
    visible: list[str] = []
    here_open: str | None = None
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote is None:
            if ch == "#":
                break
            visible.append(ch)
            # The DELIMITERS stay in `code`; only a literal's CONTENTS are
            # blanked. Blanking the quotes too made a line holding nothing but a
            # string literal come out empty, so `_last_statement` skipped it and
            # named an earlier line as the body's last statement -- contradicting
            # its own docstring, changing the quoted detail, and misclassifying
            # `conditional-terminal-exit` where `no-terminal-exit` was correct
            # (Copilot, #1347). Keeping them costs nothing: a quote is not a
            # brace, not a `#`, and not a terminator keyword, so none of the
            # questions asked of `code` can be answered differently by it.
            # A here-string OPENER (`@"` / `@'`) must be the last token on its
            # line, and is only an opener in code position -- which is why this
            # is detected here, inside the quote-tracking loop, rather than by a
            # regex over the raw line. `Write-Output "user@"` also ends in `@"`,
            # and a regex cannot tell the two apart; the lexer can, because by
            # the time it reaches that `@` it is already inside a literal.
            if (
                ch == "@"
                and i + 1 < n
                and line[i + 1] in ("'", '"')
                and line[i + 2 :].strip() == ""
            ):
                here_open = line[i + 1]
                visible.extend(line[i + 1 :])
                code.extend(" " * len(line[i + 1 :]))
                break
            code.append(ch)
            if ch in ("'", '"'):
                quote = ch
            i += 1
            continue

        # Inside a string literal.
        if quote == '"' and ch == "`" and i + 1 < n:
            # Backtick escapes the next character -- double-quoted strings only.
            visible.extend((ch, line[i + 1]))
            code.extend("  ")
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and line[i + 1] == quote:
                # A doubled quote is an escaped literal quote in BOTH string
                # kinds; the literal does not end here.
                visible.extend((ch, line[i + 1]))
                code.extend("  ")
                i += 2
                continue
            quote = None
            visible.append(ch)
            code.append(ch)  # closing delimiter is kept -- see the note above
            i += 1
            continue
        visible.append(ch)
        code.append(" ")
        i += 1
    return "".join(code), "".join(visible), here_open


def _closes_here_string(line: str, quote: str) -> bool:
    """Does `line` close a here-string opened with `quote`?

    Three candidate rules, and the middle one is chosen deliberately:

      * `line.startswith(quote + "@")` -- too loose. A content line may begin
        with those two characters (`"@notaterminator`), and closing there hands
        the rest of the literal back to the code view: a false NEGATIVE, silent
        (Copilot, #1347).
      * the delimiter ALONE on the line, bar trailing whitespace -- too tight,
        and it breaks a legal idiom this repo could easily write:

            $x = @"
            text
            "@ | Out-File -FilePath $env:GITHUB_OUTPUT -Append

        Refusing to close there runs the literal to end-of-body, hides the
        `$LASTEXITCODE` capture, and reports `unterminated-here-string` against
        correct code -- a false POSITIVE, which round 3 of this PR argued is the
        worse direction, because it lands on whoever wrote the idiom properly and
        teaches them to stop believing the guard.
      * **chosen:** the delimiter at column 0, followed by end-of-line or by a
        character that cannot continue an identifier. So `"@`, `"@ | …`, `"@;`,
        `"@)` and `"@ -replace …` all close, and `"@notaterminator` does not.

    Stated rather than implied: there is **no PowerShell host on this sandbox's
    PATH**, so this rule is derived from the language grammar (a here-string ends
    at a newline followed by the delimiter) plus the false-positive/false-negative
    asymmetry above -- it is NOT measured against a real parser. CI has a host;
    if the two ever disagree, the divergence is confined to a line beginning
    `"@` immediately followed by an identifier character, which is a PowerShell
    syntax error under either reading.
    """
    token = quote + "@"
    if not line.startswith(token):
        return False
    rest = line[len(token) :]
    return rest == "" or not (rest[0].isalnum() or rest[0] == "_")


def _scan_lines(lines: list[str]) -> tuple[list[str], list[str], int | None]:
    """`_scan_line` over a whole body, carrying here-string state across lines.

    Returns `(code_lines, visible_lines, unterminated_at)`, where the last is the
    1-based line number of a here-string opener that never closed, or None.

    PowerShell requires a here-string's closing delimiter to be the FIRST thing
    on its line, so that is what is matched -- deliberately not `lstrip()`ed. A
    permissive match would end the literal early and hand its remaining text back
    to the code view, which is precisely the false negative this function exists
    to close; requiring column 0 is both what the language says and the safe
    direction if the two ever disagree.
    """
    code_lines: list[str] = []
    visible_lines: list[str] = []
    here: str | None = None
    opened_at: int | None = None

    for index, line in enumerate(lines):
        if here is not None:
            if not _closes_here_string(line, here):
                # Content: the whole line is literal text, never code.
                code_lines.append(" " * len(line))
                visible_lines.append(line)
                continue

            # The TERMINATOR line. Only the delimiter is literal; anything after
            # it is real code and is scanned. Blanking the whole line -- which
            # this did at first -- hid it, and that was not a cosmetic gap: on
            #
            #     $msg = @"
            #     prose
            #     "@ ; exit 0
            #
            # `_last_statement` named `$msg = @"` and the guard reported
            # `no-terminal-exit` against a body that ends in `exit 0`. A false
            # POSITIVE -- the direction `_closes_here_string` above cites to
            # justify closing on `"@ | …` in the first place, so accepting such a
            # terminator and then discarding its tail was self-contradictory
            # (Copilot, #1347).
            token = here + "@"
            tail_code, tail_visible, tail_opens = _scan_line(line[len(token) :])
            pad = " " * len(token)
            code_lines.append(pad + tail_code)
            visible_lines.append(pad + tail_visible)
            # The tail may open a FURTHER here-string (`"@ + @"`), so state is
            # taken from the tail's own scan rather than simply cleared.
            here = tail_opens
            opened_at = index + 1 if tail_opens is not None else None
            continue
        code, visible, opens = _scan_line(line)
        code_lines.append(code)
        visible_lines.append(visible)
        if opens is not None:
            here = opens
            opened_at = index + 1

    return code_lines, visible_lines, opened_at


def _if_block(code_lines: list[str], start: int) -> tuple[int, str] | None:
    """Return (last_line_index, block_code) for the `if` opening at `start`.

    Brace-matched across lines over the string-blanked `code` view, so a brace
    inside a literal is already a space by the time it gets here. Returns None
    when the block never closes -- the caller reports that as a finding rather
    than skipping the step.
    """
    depth = 0
    seen_open = False
    collected: list[str] = []
    for index in range(start, len(code_lines)):
        line = code_lines[index]
        collected.append(line)
        for ch in line:
            if ch == "{":
                depth += 1
                seen_open = True
            elif ch == "}" and seen_open:
                # `and seen_open` is load-bearing, not defensive. The scan can
                # START on a `} elseif (...) {` header -- `test_re` matches that
                # spelling on purpose -- and that leading `}` closes the PREVIOUS
                # block, not this one. Counting it drove depth to -1, so the
                # trailing `{` brought it back to 0 and the block was declared
                # closed on its own header line: the body was never examined, an
                # `exit` inside it never seen, and a propagating block reported
                # as a downgrade (Copilot HIGH, #1347).
                depth -= 1
        if seen_open and depth <= 0:
            return index, "\n".join(collected)
    return None


def _last_statement(
    code_lines: list[str], visible_lines: list[str]
) -> tuple[int, str, str] | None:
    """The last executable line: (1-based line number, code, visible).

    "Executable" is decided on `code`, so a line holding only a string literal
    still counts while a line holding only a comment does not.
    """
    for index in range(len(code_lines) - 1, -1, -1):
        if code_lines[index].strip():
            return index + 1, code_lines[index].strip(), visible_lines[index].strip()
    return None


def scan_body(text: str) -> list[Finding]:
    """Findings for one `run:` body written in PowerShell."""
    code_lines, visible_lines, unterminated_at = _scan_lines(text.splitlines())

    if unterminated_at is not None:
        # Fail closed, and BEFORE the `if not captured` return below -- that is
        # the whole point. An unclosed here-string blanks everything after it,
        # including any `$LASTEXITCODE` capture, so falling through would return
        # `[]` and call an unreadable body clean.
        return [
            Finding(
                UNTERMINATED_HERE,
                "(body)",
                unterminated_at,
                "a here-string opens here and never closes (PowerShell wants the "
                "closing `\"@` / `'@` as the FIRST thing on a line) -- everything "
                "after it is unreadable to this guard, so it is reported rather "
                "than scanned as if it were empty",
            )
        ]

    captured = [
        (index, match.group("var"))
        for index, line in enumerate(code_lines)
        if (match := CAPTURE_RE.match(line))
    ]
    if not captured:
        return []

    findings: list[Finding] = []
    downgraded: list[str] = []

    for _, var in captured:
        # `if ($var -ne 0)` / `if (0 -ne $var)` -- the tolerate-or-propagate fork.
        test_re = re.compile(
            r"^\s*(?:\}\s*)?(?:else)?if\s*\(.*(?:"
            rf"\${re.escape(var)}\s+-ne\s+0|0\s+-ne\s+\${re.escape(var)}"
            r").*\)",
            re.IGNORECASE,
        )
        for index, line in enumerate(code_lines):
            if not test_re.match(line):
                continue
            block = _if_block(code_lines, index)
            if block is None:
                findings.append(
                    Finding(
                        UNBALANCED,
                        var,
                        index + 1,
                        "the `if` block guarding this exit code never closes "
                        "(unbalanced braces) -- reported rather than skipped, "
                        "because a block this guard cannot read is its blind spot",
                    )
                )
                continue
            if not TERMINATES_RE.search(block[1]):
                downgraded.append(var)

    if not downgraded:
        return findings

    last = _last_statement(code_lines, visible_lines)
    if last is None:
        return findings
    line_number, statement_code, statement = last

    if EXIT_STATEMENT_RE.match(statement_code):
        return findings

    names = ", ".join("$" + name for name in dict.fromkeys(downgraded))
    if re.search(r"\bexit\b", statement_code, re.IGNORECASE):
        findings.append(
            Finding(
                CONDITIONAL_EXIT,
                downgraded[0],
                line_number,
                f"{names} is downgraded to a warning, and the body's last statement "
                f"({statement!r}) only exits CONDITIONALLY. On the other branch "
                "`$LASTEXITCODE` still holds the tolerated failure and the runner's "
                "epilogue fails the step anyway. End with an unconditional `exit`.",
            )
        )
    else:
        findings.append(
            Finding(
                NO_EXIT,
                downgraded[0],
                line_number,
                f"{names} is downgraded to a warning, but the body ends with "
                f"{statement!r}, which does not reset `$LASTEXITCODE`. The runner "
                "appends `exit $LASTEXITCODE`, so the step fails with the very code "
                "the author chose to tolerate (#1068). End the body with `exit 0`.",
            )
        )
    return findings


def _default_shell(container: dict) -> str | None:
    defaults = container.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    shell = run.get("shell")
    return shell if isinstance(shell, str) else None


def scan_workflow(workflow_text: str, workflow_name: str) -> list[str]:
    """Rendered findings for every PowerShell `run:` body in one workflow."""
    doc = yaml.safe_load(workflow_text)
    if not isinstance(doc, dict):
        return []

    workflow_shell = _default_shell(doc)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    reported: list[str] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_shell = _default_shell(job) or workflow_shell
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            if (step.get("shell") or job_shell) not in PWSH_SHELLS:
                continue
            name = step.get("name") or "(unnamed)"
            for finding in scan_body(step["run"]):
                reported.append(
                    # 1-based, to match the `line N` this same string already
                    # carries and the way `steps:` is counted in the Actions UI.
                    # It is the position in the job's FULL steps list, not among
                    # the pwsh ones, so it stays usable as a YAML coordinate.
                    f'{workflow_name} :: {job_id} :: step {index + 1} "{name}" :: {finding}'
                )
    return reported


def main() -> int:
    if not WORKFLOWS.is_dir():
        print(f"::error::workflow directory not found: {WORKFLOWS}")
        return 1

    findings: list[str] = []
    scanned = 0
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"::error::cannot read {path.name}: {exc}")
            return 1
        try:
            findings.extend(scan_workflow(text, path.name))
        except yaml.YAMLError as exc:
            print(f"::error::cannot parse {path.name}: {exc}")
            return 1
        scanned += 1

    if findings:
        print(
            "::error::A pwsh step downgrades a captured $LASTEXITCODE to a warning "
            "but does not end with an explicit `exit`. The runner appends "
            "`exit $LASTEXITCODE`, so the step fails with the code the author "
            "deliberately tolerated (#1068)."
        )
        for line in findings:
            print(f"  {line}")
        print(
            "\nFix: end the body with `exit 0` (see 101-domain-status.yml's m365 "
            "preflight step, which already does), and fail explicitly on the exit "
            "codes you do NOT intend to tolerate."
        )
        return 1

    print(f"OK: {scanned} workflow(s) scanned; no downgraded exit code ends without an explicit exit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
