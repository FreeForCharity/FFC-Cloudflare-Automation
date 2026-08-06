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
    """Split one line on `;` separators that are outside quotes.

    A bare `line.split(";")` also splits the semicolons inside
    `python -c "import x; print(y)"`, tearing one statement into two whose
    quoting no longer balances. That both invents statement boundaries where
    the shell sees none and lets a `$?` inside a quoted argument read as a
    separate statement. `_strip_quoted` preserves length, so offsets into the
    blanked copy index the original.
    """
    bare = _strip_quoted(line)
    parts = []
    start = 0
    for i, ch in enumerate(bare):
        if ch == ";":
            parts.append(line[start:i])
            start = i + 1
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


def _split_on_logical(stmt):
    """Split one statement on `&&` / `||` outside quotes.

    A pipeline (`|`) is deliberately NOT split: `printenv | grep GH_TOKEN`
    prints a secret and must stay one unit, whereas
    `python x.py && echo done` is two independent commands. `&` is left alone
    too -- splitting it would tear `echo >&2 $TOKEN` into a half holding the
    verb and a half holding the variable, which is the one direction a guard
    must never fail in.
    """
    bare = _strip_quoted(stmt)
    parts = []
    start = 0
    i = 0
    while i < len(bare):
        if bare.startswith("&&", i) or bare.startswith("||", i):
            parts.append(stmt[start:i])
            i += 2
            start = i
            continue
        i += 1
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
PS_IMPLICIT_OUTPUT_RE = re.compile(
    r"""(?:\A|['"])\s*\$\{?env:([A-Za-z_][A-Za-z0-9_]*)\}?\s*(?:\||['"]|\Z)""",
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

    # 2. Force-push to a protected branch. Match 'main'/'master' only as a
    #    standalone branch token, so e.g. 'feature/main' is NOT caught.
    if re.search(r"\bgit\s+push\b", low) and re.search(r"(--force\b|--force-with-lease|\s-f\b)", low):
        if re.search(r"(?<![\w./-])(main|master)(?![\w/-])", low):
            block("Force-push to a protected branch (main/master) is not allowed.")

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
    for reason in (pipeline_exit_code_violation(cmd), inline_python_encoding_violation(cmd)):
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

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never let a hook bug block legitimate work.
        sys.exit(0)
