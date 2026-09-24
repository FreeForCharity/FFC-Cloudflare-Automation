#!/usr/bin/env python3
"""PreToolUse hook for Bash.

Blocks (exit 2) commands that violate this repo's security rules:
  * Disabling TLS verification or tampering with the agent proxy
    (the environment README forbids this outright).
  * Force-pushing to a protected branch (main/master).
  * Printing secrets to logs (echo/printenv of *_TOKEN/*_SECRET/*_KEY/...).
  * A real-looking secret literal pasted directly into the command.
  * Irreversible destructive removals of the repo/home root.

Everything else is allowed. Any internal error => allow (exit 0).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def block(reason):
    sys.stderr.write(
        "BLOCKED by FFC security hook (.claude/hooks/guard_bash.py):\n"
        f"{reason}\n"
        "See .github/agents/AI_AGENT_INSTRUCTIONS.md.\n"
    )
    sys.exit(2)


# Warnings, unlike blocks, do not stop the command. They accumulate and are
# flushed once at the end so that a warning never short-circuits a later
# `block()` -- an advisory rule must not be able to let a security rule through
# by exiting first.
WARNINGS = []

WARN_MARKER = "FFC-HOOK-WARNING"


def warn(reason):
    WARNINGS.append(reason)


def finish():
    """Emit any accumulated warnings and allow the command."""
    if WARNINGS:
        sys.stderr.write(f"{WARN_MARKER} (.claude/hooks/guard_bash.py):\n")
        for reason in WARNINGS:
            sys.stderr.write(f"  {reason}\n")
    sys.exit(0)


def _strip_quoted(text):
    """Blank out single/double-quoted spans, preserving length.

    Used only where a *shell operator* is being looked for, so that `echo "a|b"`
    does not read as a pipeline. Never use it to look for `$?`, which most often
    appears inside double quotes (`echo "EXIT=$?"`) -- that is the case worth
    catching, not the case worth ignoring.
    """
    out = list(text)
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            else:
                out[i] = " "
        elif ch in "'\"":
            quote = ch
            out[i] = " "
    return "".join(out)


def _strip_single_quoted(text):
    """Blank out single-quoted spans only, preserving length.

    The counterpart to `_strip_quoted`, and the right tool for `$?`: inside
    DOUBLE quotes the shell still expands it (`echo "EXIT=$?"` is the exact
    shape ledger L50 is about), while inside SINGLE quotes it is a literal that
    reads nothing -- `echo '$?'` prints two characters. Blanking both would
    discard the case worth catching; blanking neither blocks a correct command.
    """
    out = list(text)
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
                if ch == "'":
                    out[i] = " "
            elif quote == "'":
                out[i] = " "
        elif ch in "'\"":
            quote = ch
            if ch == "'":
                out[i] = " "
    return "".join(out)


def _split_statements(line):
    """Split one line on TOP-LEVEL `;` separators.

    A bare `line.split(";")` also splits the semicolons inside
    `python -c "import x; print(y)"`, tearing one statement into two whose
    quoting no longer balances. That both invents statement boundaries where
    the shell sees none and lets a `$?` inside a quoted argument read as a
    separate statement. `_strip_quoted` preserves length, so offsets into the
    blanked copy index the original.

    Quoting is not the only span a `;` can hide in, and this is the SAME defect
    `_pipe_stages` and `_split_on_logical` were each fixed for -- at the
    outermost of the three splitters, which runs before either of them. A `;`
    inside `$(...)`, `${...}` or backticks separates two commands whose
    combined *output* is one word of this line; the outer command continues
    past the closing paren. Tearing there puts a force-push's verb and flag in
    one statement and its refspec in the next, and rule 2 -- which requires all
    three in one piece -- goes silent. Measured at f28b310, with both other
    splitters already fixed, each a real force-push to `main` that was ALLOWED:

        git push --force $(cd /repo; git remote) main
        git push --force `cd /repo; git remote` main
        git push --force origin $(cd /repo; cat b.txt):main

    All three BLOCK on `main`, where rule 2 judged the whole command rather
    than each segment, so they are a regression this stack introduced rather
    than pre-existing holes. `_top_level_ops` is shared rather than copied for
    the reason its own docstring gives: a second copy is how these splitters
    came to disagree in the first place.
    """
    bare = _strip_quoted(line)
    parts = []
    start = 0
    for i, oplen in _top_level_ops(bare, (";",)):
        parts.append(line[start:i])
        start = i + oplen
    parts.append(line[start:])
    return parts


def _statements(cmd):
    """Split a command into ordered statements, skipping heredoc bodies.

    Heredoc payloads are skipped rather than parsed: a Python or jq body is not
    shell, and a `|` inside one is not a pipeline. Including them produced the
    only false positive found while developing this rule.

    The heredoc *header* is shell and is kept. Skipping the whole line meant a
    pipeline written there -- `python - <<PY | tail; echo "EXIT=$?"` -- was
    never analysed, so the rule missed its own target shape rather than
    over-reporting it.
    """
    stmts = []
    lines = cmd.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        for part in _split_statements(line):
            if part.strip():
                stmts.append(part)
        i += 1
        if m:
            terminator = m.group(1)
            while i < len(lines) and lines[i].strip() != terminator:
                i += 1
            i += 1
    return stmts


# The three span kinds `_top_level_ops` tracks, and why they are not one thing:
# bare `(`/`{` nest inside a command substitution and are literal inside a
# parameter expansion, and a backtick span is opaque to both.
CMD, PARAM, TICK = "cmd", "param", "tick"


def _top_level_ops(bare, ops):
    """Yield `(index, length)` for each operator in `ops` that is TOP LEVEL.

    `bare` must already be `_strip_quoted`, so the only spans left to skip are
    the ones quoting cannot express: command substitutions `$(...)`, parameter
    expansions `${...}`, backticks, and a backslash escape.

    An operator inside one of those is not a separator of THIS command. A
    substitution is a command of its own whose *output* becomes a single word
    here, and the outer command continues past the closing paren -- so
    splitting there tears one command's words across two computed pieces. For
    a rule that requires several conditions in the same piece that fails
    PERMISSIVELY, which is the direction a guard must never fail in.

    Bare `(`/`{` are tracked only once a COMMAND substitution is open, so
    `$( (a) && b )` does not close its span early while an ordinary
    `$(a) && b` still splits. They are deliberately NOT tracked inside a
    parameter expansion: `${x:-foo(}` is valid bash -- the default-value word
    is literal text and its `(` need not balance -- so pushing a closer for it
    made the `}` that really ends the expansion pair with the `(` instead, and
    `closers` then never emptied. Every later operator on the line was
    invisible, which for rule 2 is the #1309 false positive returning by
    another door. A nested `$(` or `${` inside either kind is still caught by
    the `$` branch above, so nothing is lost by not tracking bare ones here.
    Copilot review on #1336.

    `ops` is matched longest-first by the caller's ordering, so `|&` wins over
    `|`.

    A backtick span is tracked on the SAME stack as the other two kinds. The
    two side-flag spellings that came before it were each wrong, in opposite
    directions, and -- measured against the same standard -- **neither was a
    reachable bypass**:

    - A flag toggled on EVERY backtick lets an odd backtick inside a
      substitution invert the parity for everything after it, so the opening
      backtick of a later, genuine span reads as a close and its contents are
      scanned as top level. The `|` in a backticked remote then splits a real
      force-push into two stages. 21 such splits were measured -- but the
      injected prefix carrying the odd backtick is **bash-invalid**, and a
      sweep of 132 bash-VALID vectors of this shape found **0** the old code
      allowed. The reason is that this scanner's state is local to one CALL --
      one statement -- and any statement a shell will actually execute has
      balanced backticks, so the parity it sees is even and cannot invert. An
      odd backtick is only tolerated where it is never executed as part of the
      same statement: a quoted heredoc body holds literal text, but the body's
      own lines are statements too, so an unpaired backtick and a force-push
      cannot share one runnable statement. Measured on that shape as well --
      `bash <<'EOF'` with the odd backtick and the push in one body blocks on
      both the old and new code.

      So this was a real parsing defect that mis-split input no shell would
      run, not a permissive hole.

      Two corrections earned here, both from Copilot on #1336 and both worth
      keeping because the wrong versions were plausible. The first write-up
      said "21 real force-pushes allowed", which skipped the `bash -n` filter
      the bullet below already applied. The second explained the 0 by claiming
      heredoc bodies are skipped by `_split_statements` -- **wrong**: rule 2's
      segments come from `_echo_segments`, which INCLUDES bodies deliberately
      and fail-closed (`bash <<EOF` really does execute a push in its body, as
      `force_push_violation` documents). The 0 was measured; that explanation
      of it was invented, and an invented mechanism next to a measured number
      is how a reader ends up trusting the wrong one.
    - Toggling it only at TOP LEVEL fixes that but leaves backticks inside
      `$(...)` untracked, so an unquoted `)` inside them can match the outer
      substitution's closer and empty the stack early. Measured over 72
      bash-valid vectors of that shape: **0 bypasses** -- after the premature
      close the next backtick turns suppression back on, which saves it by
      accident -- but **1 false positive**,
      `git push origin $(echo `printf a)b`) | grep -f p.txt main`, blocked
      when it should not be. That is #1309's defect, not a permissive tear.

    So the stack is not here to close a measured hole; it is here because
    "accidentally safe" is not a property worth depending on in a guard, and
    because it removes that false positive.

    While a backtick span is open nothing else may open, close, or be an
    operator, so an unterminated backtick swallows the rest of the line. That
    costs 5 extra blocks on the probe corpus and every one of them is a command
    **bash itself refuses** -- `bash -n` on the injected prefix says
    `unexpected EOF while looking for matching ``'` -- so nothing a shell would
    run changes verdict. Verified against the balanced control, which stays
    valid and still splits. Under-splitting also fails toward BLOCK, the only
    direction a guard may fail in.

    Copilot review on #1336, three rounds. The severity on this last one was
    higher than measurement supports, and two of my own attempts to measure it
    were wrong first -- see the ledger row.

    Extracted from `_pipe_stages`, which had this scanner inline, because
    `_split_on_logical` needs exactly the same span model and a second copy
    would be one more place for the two to drift apart (#1309).
    """
    # One stack for all three span kinds -- a backtick span lives here too,
    # not in a side flag. Each entry is `(closing_char, kind)` where kind is
    # CMD, PARAM or TICK; the kind is what the `closers[-1][1]` tests below
    # branch on, because bare `(`/`{` nest inside CMD, are literal inside
    # PARAM, and are just characters inside TICK.
    closers = []
    i = 0
    n = len(bare)
    while i < n:
        ch = bare[i]
        if ch == "\\":
            # An escaped character is data, never an operator -- `\|` included.
            i += 2
            continue
        if closers and closers[-1][1] == TICK:
            # Inside a backtick span only its own backtick ends it. Nothing
            # else may open a span, close an outer one, or be an operator.
            if ch == "`":
                closers.pop()
            i += 1
            continue
        if ch == "`":
            closers.append(("`", TICK))
            i += 1
            continue
        if ch == "$" and bare[i + 1 : i + 2] in ("(", "{"):
            closers.append((")", CMD) if bare[i + 1] == "(" else ("}", PARAM))
            i += 2
            continue
        if closers and closers[-1][1] == CMD and ch in "({":
            closers.append((")" if ch == "(" else "}", CMD))
            i += 1
            continue
        if closers and ch == closers[-1][0]:
            closers.pop()
            i += 1
            continue
        if not closers:
            for op in ops:
                if bare.startswith(op, i):
                    yield i, len(op)
                    i += len(op)
                    break
            else:
                i += 1
            continue
        i += 1


def _split_on_logical(stmt):
    """Split one statement on `&&` / `||` outside quotes and substitutions.

    A pipeline (`|`) is deliberately NOT split: `printenv | grep GH_TOKEN`
    prints a secret and must stay one unit, whereas
    `python x.py && echo done` is two independent commands. `&` is left alone
    too -- splitting it would tear `echo >&2 $TOKEN` into a half holding the
    verb and a half holding the variable, which is the one direction a guard
    must never fail in.

    Only a TOP-LEVEL `&&` / `||` is a boundary, for the reason `_top_level_ops`
    states, and this is the same defect `_pipe_stages` was fixed for one level
    down. Once rule 2 decides per segment, an `&&` inside a substitution tears
    a single `git push` line in two -- verb and flag in one segment, refspec in
    the next -- and the rule sees no segment carrying all three. Measured on
    this branch before the fix, each a real force-push to `main` that the guard
    ALLOWED even with `_pipe_stages` already fixed:

        git push --force $(cd /repo && git remote) main
        git push --force $(test -d .git && echo origin) main
        git push --force `cd /repo && git remote` main
        git push --force $(cd /repo && (echo origin | cat)) main
        git push --force $(cd /repo || echo origin) main
        git push --force origin $(cd /repo && cat b.txt):main

    All six BLOCK on `main`, where the rule judged the whole command, so they
    are a regression this stack introduced rather than pre-existing holes.
    """
    bare = _strip_quoted(stmt)
    parts = []
    start = 0
    for i, oplen in _top_level_ops(bare, ("&&", "||")):
        parts.append(stmt[start:i])
        start = i + oplen
    parts.append(stmt[start:])
    return parts


def _echo_segments(cmd):
    """Every shell segment of `cmd`, heredoc bodies INCLUDED.

    Deliberately not `_statements()`: that skips heredoc payloads because a
    Python body is not shell, which is right for the pipeline rule and wrong
    here -- `bash <<EOF` ... `echo $GH_TOKEN` ... `EOF` prints a secret, and
    dropping the body would turn a blocked command into an allowed one.
    """
    for line in cmd.splitlines():
        for part in _split_statements(line):
            for seg in _split_on_logical(part):
                if seg.strip():
                    yield seg


def _skip_word(text, i):
    """Index just past the shell word starting at `i`.

    Tracks quotes and `$(`/`(` nesting so a command substitution containing
    spaces -- `$(gh auth token)` -- is one word rather than three.
    """
    quote = None
    depth = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if text.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch.isspace() and depth == 0:
            break
        i += 1
    return i


def _strip_assignments(segment):
    """Split a segment into (command part, [assigned values]).

    `VAR=$(...)` -- including the `VAR=$(...) cmd` prefix form that hands a
    token to one command without exporting it -- is never a leak on its own,
    so the assignment is removed before the segment is judged. The assigned
    VALUES are returned rather than discarded, rather than trusting the
    stripper: `X=$(echo $GH_TOKEN)` still has to be judged, and it is judged
    as its own chunk.
    """
    text = segment
    i = 0
    values = []
    while i < len(text) and text[i].isspace():
        i += 1
    while True:
        kw = re.match(r"(?:export|local|readonly|declare|typeset)\s+", text[i:])
        if kw:
            i += kw.end()
            continue
        assign = re.match(r"[A-Za-z_][A-Za-z0-9_]*\+?=", text[i:])
        if not assign:
            break
        i += assign.end()
        start = i
        i = _skip_word(text, i)
        values.append(text[start:i])
        while i < len(text) and text[i] in " \t":
            i += 1
    return text[i:], values


# A print verb anywhere in the segment. Kept broad on purpose: narrowing it to
# "the segment's command word" would miss `foo && echo $TOKEN` shapes that the
# splitter has not separated.
#
# `env` must NOT match after a `$`: PowerShell spells a variable READ
# `$env:PASSWORD`, and `\benv\b` treats `$` and `:` as word boundaries, so the
# bare name `$env:PASSWORD` read as "the env command plus a secret" and blocked
# an assignment that prints nothing (Copilot, #1062).
#
# Removing that accident costs real coverage unless it is replaced, because
# `Write-Host $env:GH_TOKEN` was blocked on `main` ONLY by the same stray match
# -- `Write-Host` was never a listed verb. This repo is PowerShell-first, so
# the Write-* stream cmdlets are now named explicitly and the coverage is
# deliberate rather than incidental.
# The second lookbehind is the braced spelling: `${env:PASSWORD}` puts a `{`
# between the `$` and the name, so a `(?<!\$)` alone still sees a word boundary
# and the accident survives one spelling over -- reinstating the very false
# positive above for `X=${env:PASSWORD}` (Conductor run 92).
PRINT_VERB_RE = re.compile(
    r"\b(?:echo|printf|printenv)\b"
    r"|(?<!\$)(?<!\$\{)\benv\b"
    r"|\bWrite-(?:Host|Output|Information|Verbose|Debug|Warning|Error)\b",
    re.IGNORECASE)

# A conventionally-secret-suffixed identifier (FFC_CLOUDFLARE_API_TOKEN,
# WHMCS_API_SECRET, ...). Matched with or without a leading `$`, because
# `printenv GH_TOKEN` and `env | grep GH_TOKEN` name the variable bare.
SECRET_SUFFIXED_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*_(?:TOKEN|SECRET|KEY|PASSWORD|APIKEY|API_KEY)\b",
    re.IGNORECASE)

# The bare short names -- `$TOKEN`, `${SECRET}`, `$env:PASSWORD`. These are
# required to appear as an EXPANSION: unlike `GH_TOKEN`, the bare word "token"
# occurs constantly in ordinary prose (`echo "no token found"`), so matching it
# unanchored would block correct commands. `KEY` is excluded even here -- `for
# KEY in ...; do echo $KEY; done` is a normal loop, not a leak.
BARE_SECRET_NAMES = r"(?:TOKEN|SECRET|PASSWORD|APIKEY|API_KEY)"
BARE_SECRET_REF_RE = re.compile(
    rf"\$\{{?(?:env:)?{BARE_SECRET_NAMES}\b", re.IGNORECASE)
BARE_SECRET_NAME_RE = re.compile(rf"\A{BARE_SECRET_NAMES}\Z", re.IGNORECASE)

KNOWN_SECRET_VARS_RE = re.compile(
    r"\b(?:CLOUDFLARE_API_TOKEN|GH_TOKEN|GITHUB_TOKEN|WHMCS_[A-Z_]+)\b", re.IGNORECASE)


# PowerShell's implicit output: a bare expression statement IS a print, so
# `pwsh -c '$env:GH_TOKEN'` writes the token to the log with no verb anywhere.
# `main` caught this only as collateral of the stray `\benv\b` match, and
# replacing that accident with a list of Write-* CONSUMERS could not cover it:
# there is no verb to enumerate (Conductor run 92). Terminators are `|`, a
# closing quote, or end-of-chunk; `=` is deliberately absent, which is what
# keeps every assignment form allowed. The leading `['"]` alternative matters
# more than the `^` one -- through the Bash tool the realistic spelling is
# `pwsh -c '$env:GH_TOKEN'`, where the statement starts after the `-c` quote.
# The delimiters are PowerShell STATEMENT boundaries, not just the chunk edge.
# `_echo_segments` splits on `;` only OUTSIDE quotes, so an embedded script --
# `pwsh -c 'true; $env:GH_TOKEN'` -- arrives here as one chunk with its own
# internal statements. Anchoring on "start, or right after the -c quote" missed
# every statement but the first, which is a verb-less leak `main` caught by
# accident (Copilot, #1062). A space is deliberately NOT a delimiter: that is
# what keeps `./x.ps1 -Token $env:GH_TOKEN` an argument rather than a print.
# `=` is absent from the terminators for the same reason, so assignments stay
# allowed.
# A leading `(` is a grouped expression statement -- `pwsh -c '($env:GH_TOKEN)'`
# still prints -- but ONLY when it is not preceded by `$`. `$(...)` is a
# subexpression whose value is substituted, so `./x.ps1 -Token $($env:GH_TOKEN)`
# is argument passing and must stay allowed, exactly like the unparenthesised
# form (Copilot, #1062).
# Swept the remaining statement-boundary spellings rather than waiting for them
# to arrive one review round at a time: `&&` and `||` are PowerShell 7 pipeline
# chain operators and, inside the quoted `-c` script, `_split_on_logical` never
# sees them; `,` terminates an expression that continues into an array literal.
# A newline needs no entry (`_echo_segments` splits on lines first) and a
# `foreach (...) { ... }` body is already reached by `{`.
#
# `,` is a TERMINATOR only, never a leading delimiter -- that asymmetry is what
# keeps the array-argument form `./x.ps1 -Args $env:A,$env:B` allowed, since
# both elements are then preceded by a space or a comma rather than by a
# statement boundary.
PS_IMPLICIT_OUTPUT_RE = re.compile(
    r"""(?:\A|['";{&|]|(?<!\$)\()\s*\$\{?env:([A-Za-z_][A-Za-z0-9_]*)\}?"""
    r"""\s*(?:\||&|;|,|\}|\)|['"]|\Z)""",
    re.IGNORECASE)


def _ps_implicit_secret_output(chunk):
    """The env var a bare PowerShell expansion would print, if it is a secret.

    The name is re-tested against the SAME patterns the rest of the rule uses
    rather than re-enumerated here -- a third copy of the secret-name list is a
    place for the three to drift apart, and drift in this direction is silent.
    """
    for match in PS_IMPLICIT_OUTPUT_RE.finditer(chunk):
        name = match.group(1)
        if (SECRET_SUFFIXED_RE.search(name)
                or KNOWN_SECRET_VARS_RE.search(name)
                or BARE_SECRET_NAME_RE.match(name)):
            return name
    return None


def _safe_name(name):
    """A variable name fit to appear in a refusal, or None if it is not.

    THE single place a user-derived name becomes message text. It exists as a
    function rather than two inline copies because the copies diverged: the
    cap-and-re-scan was written for `_secret_label()` in one round and the
    PowerShell branch was added in the next with only the cap, reintroducing
    the identical leak one message over (Copilot, #1062). A new refusal must
    call this rather than slice a name itself.

    The re-scan is not belt-and-braces, it is load-bearing, and the cap is why:
    `ghp_` + 36 chars is exactly 40, so a variable named `<token>_KEY`
    truncates onto the token's end and RESTORES the word boundary the `_KEY`
    suffix had suppressed -- turning a harmless name into a well-formed
    credential at the moment it is printed.
    """
    name = name[:40]
    if not name or common.find_secrets(name):
        return None
    return name


def _secret_label(chunk):
    """Name WHAT armed the rule, never the text that did it.

    Quoting the offending statement was diagnostic and wrong: rule 3 runs
    before the secret-literal rule, so `echo "MY_API_KEY=<real value>"` is
    judged here -- and the message went to this hook's stderr, which lands in
    the transcript. A guard that exists to keep secrets out of logs must not
    print one while refusing to (Copilot, #1062).

    A variable NAME is safe to report where its value is not: the whole rule
    is premised on the value living in the variable. The regexes match
    identifier characters only, so a name cannot smuggle a quoted payload --
    but it is still capped and re-scanned with the shared secret detector, so
    the claim "this message cannot carry a credential" is enforced rather than
    argued.
    """
    for pattern in (KNOWN_SECRET_VARS_RE, SECRET_SUFFIXED_RE, BARE_SECRET_REF_RE):
        match = pattern.search(chunk)
        if not match:
            continue
        name = match.group(0).lstrip("$").lstrip("{")
        if name.lower().startswith("env:"):
            name = name[4:]
        safe = _safe_name(name)
        if safe is None:
            break
        return f"`{safe}`"
    if "${{ secrets." in chunk:
        return "a `${{ secrets.* }}` expression"
    return "a secret-named variable"


def _names_a_secret(chunk):
    return bool(
        SECRET_SUFFIXED_RE.search(chunk)
        or BARE_SECRET_REF_RE.search(chunk)
        or KNOWN_SECRET_VARS_RE.search(chunk)
        or "${{ secrets." in chunk
    )


# Long force flags are matched case-insensitively -- `--force-with-lease` is
# covered by `--force\b`, since the `-` that follows `force` is a word boundary.
FORCE_LONG_RE = re.compile(r"--force\b", re.IGNORECASE)
# The SHORT flag is matched case-SENSITIVELY, and that is the whole point of
# this half of #1309: `-F` is not a git-push flag, but it is `gh api -F`,
# `git commit -F`, `grep -F` and `sort -f`. Under the old lowercased match a
# `-F` anywhere in the command supplied the "force" half of the rule.
#
# `[A-Za-z]*` on both sides because git's option parser BUNDLES short options:
# `git push -fq origin main` and `-qf` both force-push (measured -- `-qZ` is
# rejected as an unknown switch, so the parser really is splitting the
# cluster), while `-f\b` sees neither. That hole predates #1309 -- the original
# `\s-f\b` missed it too -- and it is a bypass of a protected-branch rule, so
# it is fixed here rather than deferred. The `f` inside the cluster stays
# lowercase-only, which is what keeps `-F` and `-qF` out.
FORCE_SHORT_RE = re.compile(r"(?<!\S)-[A-Za-z]*f[A-Za-z]*\b")
PROTECTED_BRANCH_RE = re.compile(r"(?<![\w./-])(main|master)(?![\w/-])", re.IGNORECASE)
# git accepts its GLOBAL options BEFORE the subcommand, so the verb is not
# always the word right after `git` (#1311). `git -c protocol.version=2 push
# --force origin main`, `git --no-pager push ...` and `git -C /repo push ...` are
# all working force-push spellings -- measured, each one parses its options and
# gets as far as the remote lookup -- and `\bgit\s+push\b` saw none of them.
# `git -c` in particular is what tooling and CI snippets emit routinely, so an
# agent could reach this without trying to.
#
# Only OPTION-SHAPED words may sit between `git` and `push`, plus the single
# argument word that `-c` and `-C` take separately. That is what keeps the
# widening safe: in a real git command line the SUBCOMMAND is the first
# non-option word, so `commit`, `log`, or an unquoted message word ends the
# scan before a stray `push` can be read as the verb.
#
# The lookahead on the second alternative is load-bearing. Without it, a failed
# match backtracks so that `-c` is read as a bare option and its ARGUMENT is
# read as the verb: `git -c push.default=simple config --list` matched, because
# `push.default=simple` begins with `push` followed by a word boundary. Barring
# an option that owns a separate argument from alt 2 fixes that structurally,
# which is why `push\b` itself is left alone -- tightening the verb's trailing
# boundary would have been a LOOSENING of a block rule, and the property worth
# keeping is that this pattern is a strict superset of the one it replaces: no
# command that was blocked before can become allowed here.
#
# Deliberately NOT matched against `_strip_quoted(stage)`, though #1311 raised
# it as the way to make a permissive verb match safe. Blanking quoted spans
# would take `bash -c "git push --force origin main"` -- which really does
# rewrite main -- from blocked to allowed, and a verb rule must not fail open
# to buy a false-positive fix. The option-shaped restriction above buys the
# same safety without touching what the rule can see.
#
# `-c`/`-C` are not the only options whose value is a SEPARATE word, and the
# long ones were missed on the first pass (Conductor run 170 on #1312, three
# live bypasses). Measured on git 2.43.0 -- each runs the subcommand with the
# value taken as its own argument, against a `--bogus-opt x` control that
# exits 129:
#
#   git --work-tree <dir> status     -> 0, "On branch master"
#   git --namespace x status         -> 0, "On branch master"
#   git --config-env a.b=HOME status -> 0, "On branch master"
#   git --git-dir <path> status      -> 0, "On branch master"
#
# `--exec-path` is deliberately absent: bare, it PRINTS the exec path and
# exits without running the subcommand at all, so it can never precede a push.
# `--super-prefix` is present and is the one entry not confirmed here -- this
# git rejects it (129, like the bogus control), because it was removed as an
# internal-only option. It is kept because older gits accept it and listing it
# only widens what may sit before the verb.
#
# `git.exe` is the same rule reached from the other end: `\bgit\s` wants
# whitespace right after `git`, and `git.exe push --force origin main` is a
# working spelling on a Windows host -- which is where the Conductor runs.
GIT_SEPARATE_ARG_OPT = r"(?:-[cC]|--(?:git-dir|work-tree|namespace|config-env|super-prefix))"
GIT_GLOBAL_OPT = (
    rf"(?:{GIT_SEPARATE_ARG_OPT}\s+\S+|(?!{GIT_SEPARATE_ARG_OPT}\s)--?[A-Za-z]\S*)"
)
GIT_PUSH_RE = re.compile(
    rf"\bgit(?:\.exe)?\s+(?:{GIT_GLOBAL_OPT}\s+)*push\b", re.IGNORECASE
)


def _pipe_stages(stmt):
    """Split one segment on `|` outside quotes, substitutions and escapes.

    The counterpart to `_split_on_logical`, which deliberately leaves `|`
    alone because a secret can cross a pipe. Rule 2's three conditions cannot:
    a `git push` is force-pushing to `main` only if the verb, the flag and the
    refspec are arguments of the SAME command. `|&` is bash's
    "pipe stdout and stderr", so the `&` is consumed with the bar rather than
    left to start the next stage.

    Only a TOP-LEVEL `|` is a stage boundary, and getting that wrong fails
    permissively rather than restrictively -- which is why the skips below are
    the load-bearing half of this function rather than polish. A `|` inside a
    command substitution belongs to a different command whose output becomes
    one WORD of this one, so the outer command continues past the closing
    paren: splitting there tears a single `git push` line in two, leaving the
    verb and the flag in one computed "stage" and the refspec in the next, and
    rule 2 then sees no stage carrying all three. Measured on this branch
    before the fix, each a real force-push to `main` that the guard ALLOWED:

        git push --force $(git remote | head -1) main
        git push --force origin $(cat b.txt | tr -d '\\n'):main
        git push --force `git remote | head -1` main
        git push --force origin \\| main

    All four BLOCK on `main`, where the rule judged the whole segment, so
    these were a regression introduced with stage splitting rather than
    pre-existing holes. Copilot on #1310.

    So `|` is a boundary only outside `$(...)`, `${...}`, backticks and a
    backslash escape. Bare `(`/`{` are tracked only once a substitution is
    open, so `$( (a) | b )` keeps its inner paren from closing the span early
    while an ordinary `$(a) | b` still splits.
    """
    bare = _strip_quoted(stmt)
    parts = []
    start = 0
    # `|&` first so the longest match wins and the `&` is consumed with the bar.
    for i, oplen in _top_level_ops(bare, ("|&", "|")):
        parts.append(stmt[start:i])
        start = i + oplen
    parts.append(stmt[start:])
    return parts


def force_push_violation(cmd):
    """`git push --force origin main` -- history rewritten on a protected branch.

    The rule decides per SEGMENT (#1309). Judging the whole command made "a
    push appears somewhere", "a force flag appears somewhere" and "the word
    main appears somewhere" a violation, which is the agent's standard idiom:
    a commit whose message mentions `main`, or a `gh api ... -F body=...`
    posted right after a push, each supplied one of the three halves. Conductor
    run 168 was blocked three times pushing a one-line fix to a feature branch,
    and the pressure that creates is to route around the hook.

    Segments come from `_echo_segments`, which INCLUDES heredoc bodies. That is
    deliberate and is the fail-closed choice: `bash <<EOF` ... `git push
    --force origin main` ... `EOF` really does rewrite main, so dropping the
    body would turn a blocked command into an allowed one. Including it costs
    nothing on the #1309 false positive, whose heredoc body carries the word
    `main` but no push verb.

    `_echo_segments` keeps a pipeline whole, which rule 3 needs (`printenv |
    grep GH_TOKEN` prints a secret across the pipe) and this rule must not
    have: a later stage supplies flags and words the push never saw, so
    `git push origin feature-x | grep -f patterns.txt main` armed all three
    halves. This rule therefore splits the segment again on `|` and requires
    the three inside ONE stage. Copilot on #1310.

    That narrowing is safe only for a boundary that is really a boundary, and
    an earlier revision of this PR claimed here that it "cannot open a bypass
    -- a real force-push carries its own verb, flag and refspec in its own
    stage". It does; the claim was still wrong, because a `|` inside a command
    substitution is not a stage boundary at all, and splitting on it cut that
    single command's own words across two stages. Four real force-pushes to
    `main` were ALLOWED as a result, all four of which `main` blocks. The
    rows, and what `_pipe_stages` now skips to restore them, are in its
    docstring. A stage-scoping rule is only as safe as its notion of a stage.
    """
    for seg in _echo_segments(cmd):
        for stage in _pipe_stages(seg):
            if not GIT_PUSH_RE.search(stage):
                continue
            if not (FORCE_LONG_RE.search(stage) or FORCE_SHORT_RE.search(stage)):
                continue
            if PROTECTED_BRANCH_RE.search(stage):
                return "Force-push to a protected branch (main/master) is not allowed."
    return None


def echo_secret_violation(cmd):
    """`echo $GH_TOKEN` -- a secret printed into the log.

    The rule decides per STATEMENT. Judging the whole command made
    `GH_TOKEN=$(gh auth token) python x.py` + a later `echo "done"` a
    violation, because a secret-named variable appeared somewhere and an
    `echo` appeared somewhere else -- and that pair is the Conductor's
    standard idiom, since both `scripts/audit-agentic-os-board.py` and
    `scripts/generate-agentic-os-status.py` refuse to run without `GH_TOKEN`
    and are normally invoked next to an `echo` (#1041).

    Nothing in that command can emit the token: `echo "done"` prints a string
    literal, and a `VAR=$(...)` assignment is not a print. The rule now asks
    whether THIS statement prints THAT variable.
    """
    for index, segment in enumerate(_echo_segments(cmd), start=1):
        command_part, assigned = _strip_assignments(segment)
        # Command part ONLY -- never an assignment value. `_strip_assignments`
        # hands back the value as its own chunk, so `X=$env:PASSWORD` arrives
        # here as the chunk `$env:PASSWORD`, and testing the values too would
        # re-block the assignment this rule was just fixed to allow. (Found by
        # the run-92 patch failing `powershell env var assignment allowed`.)
        implicit = _ps_implicit_secret_output(command_part)
        if implicit:
            safe = _safe_name(implicit)
            named = f"`{safe}`" if safe else "a secret-named variable"
            return (
                "Refusing to echo/print a secret value to logs.\n"
                f"  statement {index}: a bare PowerShell expansion of {named}\n"
                "In PowerShell a bare expression statement IS output, so this "
                "writes the value to the log with no print verb involved.\n"
                "Assign it, pass it as an argument, or let the command read the "
                "variable itself."
            )
        for chunk in [command_part] + assigned:
            verb = PRINT_VERB_RE.search(chunk)
            if verb and _names_a_secret(chunk):
                return (
                    "Refusing to echo/print a secret value to logs.\n"
                    f"  statement {index}: `{verb.group(0)}` together with "
                    f"{_secret_label(chunk)}\n"
                    "The offending text is deliberately NOT quoted here -- this "
                    "hook's own stderr lands in the transcript, so echoing a "
                    "pasted literal back would be the leak it exists to stop.\n"
                    "Reference the value through an env var the command reads "
                    "itself. `VAR=$(...) cmd` is fine -- it is printing it that "
                    "is not."
                )
    return None


def pipeline_exit_code_violation(cmd):
    """`cmd | filter; echo $?` reports the FILTER's status, not the command's.

    Ledger L50. `scripts/audit-agentic-os-board.py | tail -45; echo "EXIT=$?"`
    printed `EXIT=0` while the script had exited 1 with six real findings, and
    the same shape recurred twice in run 73 -- once while verifying that a guard
    fails closed, which reported a confident `exit=0` for a script that had in
    fact exited 1. A check whose failure mode is to print the answer you were
    hoping for is worse than no check.

    `set -o pipefail` makes the idiom correct, so its presence anywhere in the
    command clears the rule.
    """
    if re.search(r"\bset\s+[-a-z]*o\s+pipefail\b|\bset\s+-o\s+pipefail\b", cmd):
        return None
    stmts = _statements(cmd)
    for prev, nxt in zip(stmts, stmts[1:]):
        bare = _strip_quoted(prev)
        # A real pipeline: a single `|` that is not `||` and not `|&`.
        if not re.search(r"(?<!\|)\|(?![|&])", bare):
            continue
        if "$?" in _strip_single_quoted(nxt):
            return (
                "Reading `$?` straight after a pipeline reports the LAST command's "
                "status, not the one you care about (ledger L50).\n"
                f"  pipeline: {prev.strip()[:120]}\n"
                f"  then:     {nxt.strip()[:120]}\n"
                "Redirect to a file and read `$?` before piping, or add "
                "`set -o pipefail`. This has silently turned a failing audit into "
                "a green one more than once."
            )
    return None



LABEL_FLAG_RE = re.compile(r"--(?:add|remove)-label\b")
GH_TOKEN_RE = re.compile(r"^(?:\S*/)?gh$")


def _invokes_gh_edit(bare):
    """True if a statement invokes `gh issue edit` / `gh pr edit`.

    Token-based rather than the adjacent `gh\\s+(?:issue|pr)\\s+edit` this
    replaced, because `gh` strips leading global flags before it resolves the
    subcommand -- so `gh --repo O/R issue edit --add-label x` is a working
    command that the adjacent form never matches. A guard whose bypass is a
    flag the caller was already likely to pass is not a guard.

    Skipping flag tokens is NOT enough, and that near-miss is the reason this
    is a function: `--repo` takes a separated value, so its argument (`O/R`)
    stands between `gh` and `issue` as a bare word. A "first non-flag token
    must be issue|pr" test reproduces the hole it was written to close.

    So the subject and the verb are matched as *ordered tokens* anywhere after
    a `gh`. That is permissive alone, and safe only in combination: the caller
    also requires `--add-label`/`--remove-label` in the same statement, and no
    other `gh` subcommand carries those flags (`issue|pr create` and `list`
    spell it `--label`, which is why they stay allowed). Quoted spans are
    already blanked by the caller, so the flag named inside a `--body` message
    is not a match.
    """
    toks = bare.split()
    for i, tok in enumerate(toks):
        if not GH_TOKEN_RE.match(tok):
            continue
        rest = toks[i + 1 :]
        for j, sub in enumerate(rest):
            if sub in ("issue", "pr") and "edit" in rest[j + 1 :]:
                return True
    return False


def gh_edit_label_violation(cmd):
    """`gh issue|pr edit --add-label/--remove-label` rewrites the WHOLE label set.

    Ledger L193. The flags read the item's current labels, compute a new set and
    PUT it back, so two label edits seconds apart silently undo one another. On
    #788 the Conductor added `agent-ready` at 19:37:09Z and removed `claimed` at
    19:37:34Z; the second command read a label set that predated the first and
    wrote it back minus `claimed`, taking `agent-ready` with it. The timeline
    records BOTH removals at the same instant, from a command whose only argument
    was `--remove-label claimed`. Each command reported success, so the loss is
    invisible from either one -- it was caught only because a downstream feed's
    count failed to move.

    The additive/subtractive endpoints cannot rewrite the set, so they cannot
    lose a concurrent edit. Other `gh ... edit` flags (--title, --body,
    --add-assignee, ...) are deliberately NOT matched: this rule is about the
    label set specifically, because that is the field a run mutates twice.
    """
    for stmt in _statements(cmd):
        bare = _strip_quoted(stmt)
        if _invokes_gh_edit(bare) and LABEL_FLAG_RE.search(bare):
            return (
                "`gh issue/pr edit --add-label/--remove-label` is a read-modify-write "
                "of the ENTIRE label set (ledger L193), so it silently discards a "
                "label edit made moments earlier -- including your own.\n"
                "  command: " + stmt.strip()[:140] + "\n"
                "Use the endpoints that cannot rewrite the set, then verify by "
                "re-reading the issue or PR:\n"
                "  add:    gh api --method POST   repos/OWNER/REPO/issues/N/labels "
                "-f 'labels[]=NAME'\n"
                "  remove: gh api --method DELETE repos/OWNER/REPO/issues/N/labels/NAME\n"
                "`/issues/N/labels` is correct for a PR too -- a PR IS an issue to the "
                "labels API, so N is the PR number and no /pulls/ form exists."
            )
    return None

CALL_SPAN = 240


def _call_args(text, start):
    """The argument text of a call whose `(` has just been consumed at `start`.

    Scans to the *matching* `)`, tracking nesting and quoted spans, and returns
    None when it cannot find one inside `CALL_SPAN` characters.

    Truncating at the first `)` instead -- the earlier form -- reads
    `open(os.path.join(a, b), encoding="utf-8")` as `os.path.join(a, b`, which
    contains no `encoding=`, and so blocked a correct command. A nested call in
    the first argument is the ordinary way to write this, not an edge case.
    """
    depth = 1
    quote = None
    escaped = False
    out = []
    for ch in text[start:start + CALL_SPAN]:
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return "".join(out)
        out.append(ch)
    return None


def inline_python_encoding_violation(cmd):
    """`open(path)` in an inline Python script decodes as cp1252 on this host.

    CLAUDE.md has said so since 2026-07-25, and it still cost run 73 two calls:
    FFC board titles and PR bodies routinely carry em dashes, arrows and the
    U+274C cross, so `json.load(open(f))` dies with
    `'charmap' codec can't decode byte 0x9d`. Repo scripts already pin UTF-8;
    ad-hoc `python -c` / heredoc scripts are the surface that does not, and they
    read exactly the data that breaks it.

    Only inline Python is inspected -- a checked-in file is covered by
    scripts/check-subprocess-encoding.py and by review.
    """
    if not re.search(r"\bpython[0-9.]*\s+(-c\b|-\s*<<|-\s*$)", cmd, re.MULTILINE):
        return None
    for m in re.finditer(r"(?<![\w.])open\s*\(|\bio\.open\s*\(", cmd):
        args = _call_args(cmd, m.end())
        # An unbalanced or over-long call is one this rule cannot read. It says
        # nothing rather than guessing: unlike the security rules above, this is
        # an ergonomics guard against a local cp1252 crash, and the module's
        # standing contract is that an internal uncertainty allows the command.
        if args is None:
            continue
        if "encoding" in args:
            continue
        # Binary mode needs no encoding, and asking for one is an error.
        if re.search(r"['\"][rwxa]\+?b\+?['\"]", args):
            continue
        return (
            "Inline Python `open(...)` without `encoding=` decodes as cp1252 on "
            "this Windows host and dies on FFC data (em dashes, arrows, the "
            "U+274C in alert titles):\n"
            f"  open({args.strip()[:120]})\n"
            "Write `open(path, encoding=\"utf-8\")`. If you are also printing what "
            "you read, set `PYTHONIOENCODING=utf-8` -- the decode error names a "
            "byte offset, the encode error names a codepoint. See CLAUDE.md."
        )
    return None


# Path segments that name a COLLECTION on the GitHub REST API. The check is on
# the LAST segment of the path, which is what distinguishes a list read from a
# single-object read no matter how deep the route is:
#
#   repos/O/R/actions/workflows            -> "workflows"  collection
#   repos/O/R/actions/workflows/5.yml/runs -> "runs"       collection
#   repos/O/R/pulls/123                    -> "123"        single object
#   repos/O/R                              -> "<repo>"     single object
#
# Deliberately not exhaustive: it covers the list endpoints this repo's agents
# actually call. A collection missing from this set is a missed warning, which
# is the safe direction for an advisory rule.
COLLECTION_SEGMENTS = {
    "workflows",
    "runs",
    "issues",
    "pulls",
    "comments",
    "commits",
    "branches",
    "repos",
    "jobs",
}


def _blank_quoted(cmd):
    """`cmd` with the CONTENTS of quoted spans replaced by spaces.

    Length-preserving, so an offset into the result indexes the original
    string. Used to ask "is this command really invoking `gh api`?" without
    matching the same words quoted inside `echo '...'` or a heredoc payload --
    the false-positive class that has now bitten three separate text-scanning
    guards in this repo. The quote characters themselves are kept so argument
    parsing can still see where a token began.
    """
    out = list(cmd)
    quote = None
    for i, ch in enumerate(cmd):
        if quote is None:
            if ch in "'\"":
                quote = ch
        elif ch == quote:
            quote = None
        else:
            out[i] = " "
    return "".join(out)


def _gh_api_endpoint(cmd):
    """The API path/URL argument of a `gh api` call, or None.

    Takes the first token after `api` that looks like a route and is not a flag
    or a flag's value. Returns the path with any scheme/host and query string
    removed, plus the raw query, so the caller can inspect both.
    """
    m = re.search(r"\bgh\s+api\b", _blank_quoted(cmd))
    if not m:
        return None, None

    # Flags that consume the following token, so it is never the endpoint.
    valued = {"-H", "--header", "-F", "--field", "-f", "--raw-field", "-X",
              "--method", "-q", "--jq", "-t", "--template", "--input",
              "--cache", "--hostname"}

    tokens = cmd[m.end():].split()
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in valued:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        if tok.startswith("|") or tok.startswith(">") or tok.startswith("&"):
            break
        candidate = tok.strip("'\"")
        if not candidate or "/" not in candidate.split("?")[0]:
            # `gh api graphql` and `gh api rate_limit` have no slash and are not
            # collections; stop rather than scanning into the rest of the line.
            return None, None
        path, _, query = candidate.partition("?")
        path = re.sub(r"^https?://[^/]+/", "", path)
        return path, query
    return None, None


def _jq_expression(cmd):
    """The expression passed to `--jq`/`-q`, unquoted, or None.

    The `(?<![\\w-])` guard is what makes the `-q` alternative mean the FLAG.
    Without it `-q` also matches inside any longer flag ending in those two
    characters -- `--q` being the one that bit: the test case named for the
    short spelling was sending `--q`, which `gh` does not accept, and it
    passed anyway on the substring. A rule that matches a flag nobody can
    type cannot tell you the real flag is covered.
    """
    m = re.search(r"(?<![\w-])(?:--jq|-q)\s+(?:'([^']*)'|\"([^\"]*)\"|(\S+))", cmd)
    if not m:
        return None
    return next((g for g in m.groups() if g is not None), None)


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd.strip():
        sys.exit(0)

    low = cmd.lower()

    # 1. TLS / proxy tampering (explicitly forbidden by the environment).
    tls_violations = [
        (r"\bcurl\b[^\n|;&]*\s(-k|--insecure)\b", "curl with TLS verification disabled (-k/--insecure)"),
        (r"node_tls_reject_unauthorized\s*=\s*0", "NODE_TLS_REJECT_UNAUTHORIZED=0"),
        (r"pythonhttpsverify\s*=\s*0", "PYTHONHTTPSVERIFY=0"),
        (r"git\s+config\s+http\.sslverify\s+false", "git http.sslVerify false"),
        (r"\bunset\s+https_proxy\b", "unsetting HTTPS_PROXY"),
        (r"--no-check-certificate", "wget --no-check-certificate"),
        (r"-skipcertificatecheck", "PowerShell -SkipCertificateCheck"),
    ]
    for pat, desc in tls_violations:
        if re.search(pat, low):
            block(f"Refusing to disable TLS/proxy security: {desc}.")

    # 2. Force-push to a protected branch, decided per segment (see
    #    force_push_violation). Match 'main'/'master' only as a standalone
    #    branch token, so e.g. 'feature/main' is NOT caught.
    reason = force_push_violation(cmd)
    if reason:
        block(reason)

    # 3. Printing secrets to logs, decided per statement (see
    #    echo_secret_violation). Case-insensitive so a lowercase env var
    #    (e.g. $cloudflare_api_token) can't slip past.
    reason = echo_secret_violation(cmd)
    if reason:
        block(reason)

    # 4. A real-looking secret literal pasted into the command.
    findings = common.find_secrets(cmd)
    if findings:
        block("Command appears to contain a secret literal: " + ", ".join(findings)
              + ". Reference it via an env var / GitHub secret instead.")

    # 5b. Two correctness rules, promoted from prose because prose did not hold.
    #     Both are already written down in CLAUDE.md (ledger L50 for the exit
    #     code, the "Reading gh --format json" section for the encoding), and
    #     both were violated by the Conductor itself, repeatedly, *after* being
    #     documented -- twice each in run 73 alone. A rule that costs a run every
    #     time it is rediscovered belongs in a hook, not in a file someone is
    #     expected to have remembered.
    for reason in (pipeline_exit_code_violation(cmd), inline_python_encoding_violation(cmd),
                   gh_edit_label_violation(cmd)):
        if reason:
            block(reason)

    # 5. Irreversible destructive removals. Only block when an rm -rf targets a
    #    root/home/.git path or a bare wildcard -- NOT ordinary paths like /tmp/x.
    if re.search(r"\brm\b", low) and re.search(r"-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r", low):
        dangerous = "--no-preserve-root" in low
        roots = {"", "/*", "~", "~/", "~/*", "$home", "$home/", "$home/*", "*"}
        for tok in cmd.split():
            if tok.startswith("-"):
                continue
            t = tok.lower()
            stripped = t.rstrip("/")  # "/" and "//" -> "" (root)
            if stripped == "" or t in roots or stripped == ".git" or stripped.endswith("/.git"):
                dangerous = True
                break
        if dangerous:
            block("Refusing a destructive 'rm -rf' targeting a root/home/.git path.")

    # 6. `gh api graphql --paginate` whose query does not declare $endCursor.
    #    gh substitutes the page cursor into a variable named EXACTLY $endCursor;
    #    with any other name the `after:` argument stays null, so every page is
    #    page 1 and the loop runs until the RATE LIMITER stops it -- there is no
    #    other termination condition. It fails SILENTLY and looks like success:
    #    a large output of well-formed, entirely duplicate rows. A 2026-07-30
    #    conductor run wrote 2,454,201 rows / 98 MB (24,542 re-fetches of page 1)
    #    that deduplicated to 107, and drained the shared 5,000-point GraphQL
    #    budget to ZERO over ~6.5 hours -- starving every other agent session on
    #    the account until the hourly reset. This is a broken command, not merely
    #    a wasteful one: it can never return page 2.
    #    See AGENTS.md "GitHub API rate budget".
    if re.search(r"\bgh\s+api\b", low) and "graphql" in low and "--paginate" in low:
        # The name must match EXACTLY, so a plain substring test is not enough:
        # `$endCursorX` and `$endCursor_2` contain `$endcursor` but are different
        # GraphQL variables, and gh substitutes into neither -- the precise
        # silent-infinite-loop this rule exists to catch. A GraphQL variable name
        # is [_A-Za-z][_0-9A-Za-z]*, so the exact name is the one not followed by
        # another name character. `low` is already lowercased.
        #
        # It must also be declared IN THE OPERATION'S VARIABLE LIST, not merely
        # present somewhere on the command line. Scanning the whole command lets
        # an unrelated mention satisfy the check -- `... -f query='query($cursor:
        # String){...}' ; echo $endCursor` would pass while the query still
        # paginates on `$cursor`, i.e. the exact command this rule exists to
        # stop. gh substitutes into the operation's declared variable, so that
        # declaration is the only place the name counts. The optional name between
        # `query` and `(` covers the named form `query Foo($endCursor:String)`.
        declares_cursor = re.search(
            r"query\s*(?:[a-z_][0-9a-z_]*\s*)?\([^)]*\$endcursor(?![0-9a-z_])", low
        )
        if not declares_cursor:
            block(
                "`gh api graphql --paginate` requires the cursor variable to be named "
                "exactly `$endCursor` (and the query to request `pageInfo{hasNextPage "
                "endCursor}`). This query declares no `$endCursor`, so gh cannot advance "
                "the cursor: it will re-fetch page 1 until the shared GraphQL budget "
                "absorbs it, and the duplicate output looks like a successful full sweep."
            )

    # 7. `grep -P` (PCRE) is not available in the Windows git-bash this repo is
    #    driven from: it exits non-zero with "grep: -P supports only unibyte and
    #    UTF-8 locales" and matches NOTHING. That is not a loud failure -- the
    #    error goes to stderr while the exit status silently makes every
    #    `if grep -qP ...` take the else branch and every `grep -P ... || echo
    #    MISSING` report MISSING. A 2026-07-31 conductor run used `grep -qP` to
    #    ask which of 10 open PRs were on the public board and was told all ten
    #    were absent; every one was in fact present with a status already set.
    #    Acting on that would have re-added ten duplicate board items.
    #    Use `grep -E` (POSIX ERE) or awk instead.
    #    (Rule 6 is the `--paginate`/`$endCursor` guard, landed in #940. This
    #    branch also carried a rule 9 for `$?` read through a pipeline; it was
    #    dropped before merge because #1007 landed the same check as
    #    pipeline_exit_code_violation(), which strips quotes and splits
    #    statements. Superset verified, not assumed: all 8 of rule 9's own
    #    assertions hold against the function, and they are kept in
    #    test_hooks.py so the coverage survives the implementation.)
    if re.search(r"(?<![\w-])grep\b[^\n|;&]*?\s-(?:-perl-regexp\b|[A-Za-z]*P[A-Za-z]*\b)", cmd):
        block(
            "`grep -P` (PCRE) is unavailable in this environment's git-bash: it matches "
            "nothing and exits non-zero, so conditionals silently take the negative branch "
            "and you get confident, wrong answers rather than an error. Use `grep -E` for "
            "extended regex, or awk for field-wise matching."
        )

    # 8. `gh api /<path>` with a LEADING SLASH is rewritten by MSYS path
    #    conversion before gh ever sees it: `gh api /markdown` becomes
    #    `gh api "C:/Program Files/Git/markdown"` and fails with
    #    `invalid API endpoint`. Same argument-mangling class as the
    #    `origin\main;...` corruption in ledger L42, but on a gh endpoint
    #    rather than a git ref, and the error text blames the endpoint rather
    #    than the shell -- which is what makes it cost time. Every `gh api`
    #    example in AGENTS.md is already slash-less; this keeps it that way.
    #    Hit on 2026-07-31 (run 61) rendering a table through `gh api
    #    /markdown` to settle a review question.
    #    Drop the leading slash: `gh api markdown`, `gh api rate_limit`.
    #    Matched as "a whitespace-led /path token anywhere in the `gh api`
    #    invocation" rather than by enumerating flags first: the endpoint can
    #    follow a flag that takes a separate value (`gh api -X POST /repos/...`),
    #    which a flags-then-endpoint pattern misses. The `(?<=\s)` keeps it off
    #    an embedded value like `-f path=/x`, where the slash is data.
    if re.search(r"(?<![\w-])gh\s+api\b[^\n|;&]*?(?<=\s)/[A-Za-z]", cmd):
        block(
            "`gh api` with a leading-slash endpoint is mangled by MSYS path conversion in "
            "this environment's git-bash -- `gh api /markdown` is rewritten to a filesystem "
            "path and fails with `invalid API endpoint`, blaming the endpoint rather than "
            "the shell. Drop the leading slash: `gh api markdown`."
        )

    # 9. `gh api --paginate` with an ARRAY-BUILDING `--jq` (#989).
    #
    #    --paginate runs the jq expression once PER PAGE and concatenates the
    #    outputs. A streaming filter (`.[] | ...`) concatenates cleanly and is
    #    the form AGENTS.md teaches. An array-building filter (`[...]`) emits
    #    one array per page -- `[...][...][...]` -- which is not valid JSON.
    #    `gh` exits 0 and says nothing; the failure surfaces later as a parse
    #    error at a byte offset in the middle of page 2, nowhere near the
    #    command that caused it.
    #
    #    WARNS rather than blocks, and the reason is a measurement, not caution.
    #    #989 (following #927) requires checking false positives against real
    #    usage before wiring a rule. Doing that found a legitimate committed
    #    counter-example -- `726-repo-rulesets-drift-audit.yml:205`:
    #
    #      gh api --paginate ".../teams?per_page=100" --jq '[.[] | .slug] | join(",")'
    #      team_grants=$(printf '%s' "$team_grants" | paste -sd, -)
    #
    #    That expression reduces each page to a STRING, and the very next line
    #    re-joins the per-page lines on purpose. The author knew about the
    #    per-page behaviour and compensated downstream, so the command is
    #    correct. A block would have called correct, deliberate code an error.
    #    The defect #989 describes is real, but it is "the output is not valid
    #    JSON *if you consume it as JSON*" -- which the command string alone
    #    cannot tell you.
    bare = _blank_quoted(cmd)
    if re.search(r"\bgh\s+api\b", bare) and "--paginate" in bare:
        expr = _jq_expression(cmd)
        if expr is not None and expr.lstrip().startswith("["):
            warn(
                "[#989] `gh api --paginate` with an array-building --jq runs the filter ONCE PER "
                "PAGE and concatenates the results, so this emits one array per page -- "
                "`[...][...]` -- which is not valid JSON. gh exits 0 either way, and the "
                "failure surfaces later as a parse error mid-page-2, far from its cause.\n"
                "  Streaming filter instead:  --jq '.[] | ...'\n"
                "  Whole set as one array (--slurp cannot be combined with --jq):\n"
                "    gh api --paginate --slurp <endpoint> > pages.json   # array OF PAGES, flatten it\n"
                "  Ignore this if you reduce each page to a scalar and recombine downstream, "
                "the way 726 does."
            )

    # 10. Unpaginated `gh api` list read (#971) -- WARN, do not block.
    #
    #    `per_page=100` is the maximum, not a guarantee, and the default is 30.
    #    A truncated list looks exactly like a complete one, so an ABSENCE check
    #    over it returns a confident, wrong "not found". Run 64 nearly reported
    #    two incident-tracked workflows as deleted this way; run 73 hit the same
    #    thing at the 30 default.
    #
    #    Advisory, because a bounded single-page read is legitimate when you
    #    want the newest N and are making no completeness claim. Blocking that
    #    would be wrong, and a guard people switch off guards nothing.
    if "--paginate" not in cmd:
        path, query = _gh_api_endpoint(cmd)
        if path:
            method = re.search(r"(?:-X|--method)\s+(\w+)", cmd)
            is_read = not method or method.group(1).upper() == "GET"
            explicit_page = bool(re.search(r"(?:^|&)page=", query or ""))
            last = path.rstrip("/").split("/")[-1].lower()
            if is_read and not explicit_page and last in COLLECTION_SEGMENTS:
                warn(
                    f"[#971] `gh api {path}` is a LIST read without --paginate. per_page=100 is the "
                    "maximum, not a guarantee (the default is 30), and a truncated list is "
                    "indistinguishable from a complete one -- so any conclusion of the form "
                    "\"X is missing\" / \"nothing is pending\" / \"zero failures\" drawn from it "
                    "may be false.\n"
                    "  Add --paginate, or compare .total_count against what you actually got.\n"
                    "  Fine to ignore if you only want the newest N and are claiming nothing "
                    "about the rest."
                )

    finish()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never let a hook bug block legitimate work.
        sys.exit(0)
