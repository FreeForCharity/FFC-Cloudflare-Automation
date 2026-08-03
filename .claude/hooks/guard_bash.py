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

    # 2. Force-push to a protected branch. Match 'main'/'master' only as a
    #    standalone branch token, so e.g. 'feature/main' is NOT caught.
    if re.search(r"\bgit\s+push\b", low) and re.search(r"(--force\b|--force-with-lease|\s-f\b)", low):
        if re.search(r"(?<![\w./-])(main|master)(?![\w/-])", low):
            block("Force-push to a protected branch (main/master) is not allowed.")

    # 3. Printing secrets to logs. Case-insensitive so a lowercase env var
    #    (e.g. $cloudflare_api_token) can't slip past.
    secret_var = r"[A-Za-z_][A-Za-z0-9_]*(?:_TOKEN|_SECRET|_KEY|_PASSWORD|_APIKEY|_API_KEY)\b"
    known_vars = r"(CLOUDFLARE_API_TOKEN|GH_TOKEN|GITHUB_TOKEN|WHMCS_[A-Z_]+)"
    if re.search(r"\b(echo|printf|printenv|env)\b", low):
        if (re.search(secret_var, cmd, re.IGNORECASE)
                or re.search(known_vars, cmd, re.IGNORECASE)
                or "${{ secrets." in cmd):
            block("Refusing to echo/print a secret value to logs.")

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
