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
    neither exits, throws, nor emits `::error::`. That block is the author
    saying "this failure is acceptable".

    A step that captures the code and PROPAGATES it is not in scope:

        $code = $LASTEXITCODE
        if ($code -ne 0) { exit $code }        # correct; says nothing to us
        if ($code -ne 0) { throw "..." }       # correct
        if ($code -ne 0) { Write-Output '::error::...'; exit 1 }

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
TERMINATES_RE = re.compile(r"(?:^|[\s;{])(?:exit|throw)\b|::error::", re.IGNORECASE)

EXIT_STATEMENT_RE = re.compile(r"^exit\b", re.IGNORECASE)

UNBALANCED = "unbalanced-if-block"
NO_EXIT = "no-terminal-exit"
CONDITIONAL_EXIT = "conditional-terminal-exit"


@dataclass(frozen=True)
class Finding:
    kind: str
    variable: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"line {self.line} [{self.kind}] ${self.variable}: {self.detail}"


def _strip_comments(line: str) -> str:
    """Drop a trailing `#` comment that is not inside a quoted string."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        ch = line[index]
        if quote:
            # PowerShell escapes with a backtick, and doubles a quote inside a
            # string of the same kind. Either way the next character is data.
            if ch == "`":
                out.append(ch)
                index += 1
                if index < len(line):
                    out.append(line[index])
                    index += 1
                continue
            if ch == quote:
                quote = None
            out.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        index += 1
    return "".join(out)


def _if_block(lines: list[str], start: int) -> tuple[int, str] | None:
    """Return (last_line_index, block_text) for the `if` opening at `start`.

    Brace-matched across lines, ignoring braces inside quoted strings. Returns
    None when the block never closes -- the caller reports that as a finding
    rather than skipping the step.
    """
    depth = 0
    seen_open = False
    collected: list[str] = []
    for index in range(start, len(lines)):
        stripped = _strip_comments(lines[index])
        collected.append(stripped)
        quote: str | None = None
        pos = 0
        while pos < len(stripped):
            ch = stripped[pos]
            if quote:
                if ch == "`":
                    pos += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
            elif ch == "{":
                depth += 1
                seen_open = True
            elif ch == "}":
                depth -= 1
            pos += 1
        if seen_open and depth <= 0:
            return index, "\n".join(collected)
    return None


def _last_statement(lines: list[str]) -> tuple[int, str] | None:
    """The last executable line of a body: (1-based line number, text)."""
    for index in range(len(lines) - 1, -1, -1):
        text = _strip_comments(lines[index]).strip()
        if text:
            return index + 1, text
    return None


def scan_body(text: str) -> list[Finding]:
    """Findings for one `run:` body written in PowerShell."""
    lines = text.splitlines()

    captured = [
        (index, match.group("var"))
        for index, line in enumerate(lines)
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
        for index, line in enumerate(lines):
            if not test_re.match(_strip_comments(line)):
                continue
            block = _if_block(lines, index)
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

    last = _last_statement(lines)
    if last is None:
        return findings
    line_number, statement = last

    if EXIT_STATEMENT_RE.match(statement):
        return findings

    names = ", ".join("$" + name for name in dict.fromkeys(downgraded))
    if re.search(r"\bexit\b", statement, re.IGNORECASE):
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
                    f'{workflow_name} :: {job_id} :: step {index} "{name}" :: {finding}'
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
