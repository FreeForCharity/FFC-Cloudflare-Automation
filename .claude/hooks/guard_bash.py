#!/usr/bin/env python3
"""PreToolUse hook for Bash.

Blocks (exit 2) commands that violate this repo's security rules:
  * Disabling TLS verification or tampering with the agent proxy
    (the environment README forbids this outright).
  * Force-pushing to a protected branch (main/master).
  * Printing secrets to logs (echo/printenv of *_TOKEN/*_SECRET/*_KEY/...).
  * A real-looking secret literal pasted directly into the command.
  * Irreversible destructive removals of the repo/home root.

Warns (exit 0, message on stderr) about:
  * An unpaginated `gh api` LIST read (#971), whose truncation is invisible and
    makes any absence check over it unsound.
  * `gh api --paginate` with an array-building `--jq` (#989), which runs the
    filter once per page and emits one array per page.

Everything else is allowed. Any internal error => allow (exit 0).

Blocks are for commands that are *unsafe* -- a leaked secret or a destroyed
tree cannot be undone. Warnings are for commands whose OUTPUT is unsound to
draw a conclusion from. Both #971 and #989 are the second kind: each has a
legitimate form that the command string alone cannot distinguish from the
broken one (see the measured counter-example at rule 6), and a guard that
blocks correct work gets switched off -- at which point it guards nothing.
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

    # 6. `gh api --paginate` with an ARRAY-BUILDING `--jq` (#989).
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

    # 7. Unpaginated `gh api` list read (#971) -- WARN, do not block.
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
