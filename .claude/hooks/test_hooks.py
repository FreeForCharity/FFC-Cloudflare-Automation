#!/usr/bin/env python3
"""Self-tests for the FFC AI-agent hooks.

Runs each hook as a subprocess with crafted stdin and asserts the exit code
(2 = blocked, 0 = allowed). Run locally or in CI:  python3 .claude/hooks/test_hooks.py
"""

import ast
import json
import os
import subprocess
import sys

HOOKS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HOOKS, "..", ".."))
GUARD_BASH = os.path.join(HOOKS, "guard_bash.py")

PASS, FAIL = 0, 0


def run(script, payload):
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    return proc.returncode, proc.stderr


def check(name, script, payload, expect_block):
    global PASS, FAIL
    rc, _ = run(script, payload)
    blocked = rc == 2
    ok = blocked == expect_block
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    status = "ok  " if ok else "FAIL"
    want = "block" if expect_block else "allow"
    got = "block" if blocked else f"allow(rc={rc})"
    print(f"  [{status}] {name}: want={want} got={got}")


# An advisory rule exits 0 exactly like a silent allow, so `check` above cannot
# tell "warned" from "said nothing" -- and a warning nobody can assert on is a
# warning that can rot without any test noticing. Assert on the marker instead.
WARN_MARKER = "FFC-HOOK-WARNING"


def check_warn(name, script, payload, expect_warn, tag=None):
    """Assert whether a command warns, holding the exit code at 0 either way.

    `tag` names WHICH rule must (or must not) have fired -- e.g. "[#971]".
    Without it this could only ask "did anything warn", and the two rules
    overlap on real commands: `gh api repos/o/r/issues --jq '[...]'` is an
    unpaginated list read (#971 fires, correctly) and is NOT a paginated
    array-jq (#989 must stay quiet). An untagged assertion reads that as a
    single "warn" and cannot tell a correct rule from a leaking one.
    """
    global PASS, FAIL
    rc, err = run(script, payload)
    warned = (tag in err) if tag else (WARN_MARKER in err)
    # A warn case that blocks is a failure even if it "warned" -- the whole
    # point of the tier is that the command still runs.
    ok = (warned == expect_warn) and rc == 0
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    status = "ok  " if ok else "FAIL"
    label = f"{tag} " if tag else ""
    want = f"{label}warn" if expect_warn else f"{label}silent"
    got = ("warn" if warned else "silent") + (f", rc={rc}" if rc != 0 else "")
    print(f"  [{status}] {name}: want={want} got={got}")
    # Returned so the registry can attribute a warning to its rule from the
    # emitted text, the same way check_emitting does for a block. Purely
    # additive -- #1018's spelling and behaviour above are untouched.
    return rc, err


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def edit(path, new=""):
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": new}}


def write(path, content=""):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


# `run` deliberately returns only (rc, stderr): that is the contract #1018
# introduced, and holding all the in-flight hook PRs to ONE spelling of it is
# what lets them merge in any order. `check_stderr_omits` below needs stdout as
# well, so it reaches for the whole CompletedProcess through `run_full` rather
# than widening `run`'s return and forking the contract again.
def run_full(script, payload):
    return subprocess.run(
        [sys.executable, os.path.join(HOOKS, script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )


def check_stderr_omits(name, script, payload, needle):
    """A hook must block WITHOUT quoting the payload back.

    Exit-code-only assertions cannot see this: the command is refused either
    way, and the leak is in the refusal. rc is asserted too, so a hook that
    stopped blocking cannot pass by printing nothing.
    """
    global PASS, FAIL
    proc = run_full(script, payload)
    ok = proc.returncode == 2 and needle not in proc.stderr and needle not in proc.stdout
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    detail = "blocked, payload not echoed" if ok else (
        f"rc={proc.returncode}, payload_echoed={needle in proc.stderr + proc.stdout}")
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}: {detail}")


# Fabricated, CF-shaped token. Split across concatenated literals so the source
# never contains a contiguous token -- otherwise this very test file would trip
# guard_edit.py / external secret scanners (the value at runtime is unchanged).
REAL_CF = "em7XiooYdKI4T3d3" + "Oo1j31-ekEV2Fi" + "UfZxwQvzT9"


# --- the guard_bash rule registry -------------------------------------------
#
# Cases used to be a flat list of check() calls, which cannot hold the property
# that actually matters. "Does the file contain both a blocking and an allowing
# case?" is trivially true across 149 cases and will stay true forever, while a
# SINGLE rule tested only in the direction it already passes is invisible.
#
# #940 shipped the $endCursor rule with three cases -- block / block / allow --
# all three green against `"$endcursor" not in low`, a substring test that got
# the rule wrong and allowed `$endCursorX` and `$endCursor_2`: real GraphQL
# variable names that `--paginate` substitutes into neither, i.e. the exact
# infinite-page-1 loop the rule exists to stop. The case that would have failed
# was a near-miss that must BLOCK, and nobody wrote it. That is the fourth time
# in this repo a test passed against the mutation it existed to catch (L48).
#
# So rule identity is data here, not inference from a case name:
#
#   * `signature` is a verbatim slice of the reason the rule emits -- the
#     block message, or the `[#NNN]` tag for a warn-tier rule. The EMITTED
#     REASON, not the case's name, is what attributes a firing to a rule.
#   * test_rule_polarity() demands both directions PER RULE.
#   * test_refusal_site_coverage() derives the refusal sites from
#     guard_bash.py's own AST -- across every reporting channel, block() and
#     warn() alike -- so a rule added with no case turns it red without anyone
#     maintaining a count.
#
# Every case below was lifted from the flat list by source span rather than
# retyped, and each blocking case was assigned to its rule by the reason
# guard_bash actually emitted when the case was run.
BLOCK, ALLOW, WARN, SILENT = "block", "allow", "warn", "silent"
BLOCK_TIER, WARN_TIER = "block", "warn"

# Which verdict means "the rule fired", per tier. A warn-tier rule may also
# hold ALLOW cases: #1018's "a warned command must still be allowed to run" is
# an assertion about the tier itself, not about the rule staying silent.
FIRED = {BLOCK_TIER: BLOCK, WARN_TIER: WARN}
SINK_TIER = {"block": BLOCK_TIER, "warn": WARN_TIER}


def record(name, ok, detail=""):
    """Count one assertion and print it in the same shape as check()."""
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}{'' if ok else ':'}")
    if not ok and detail:
        for line in detail.splitlines():
            print(f"         {line}")


def check_emitting(name, script, payload, expect_block):
    """`check`, plus the stderr the hook produced.

    A separate function rather than a return value bolted onto `check`, so
    `run`/`check` above stay byte-identical to the spelling #1018 introduced.
    Counting and printing stay in `record` so there is still exactly one place
    that can miscount.
    """
    rc, err = run(script, payload)
    blocked = rc == 2
    want = "block" if expect_block else "allow"
    got = "block" if blocked else f"allow(rc={rc})"
    record(f"{name}: want={want} got={got}", blocked == expect_block)
    return rc, err


class Rule:
    """One guard_bash refusal rule and every case that exercises it.

    no_allow_case is an escape hatch for a rule with no sensible quiet command;
    it takes a one-line reason so the exemption is stated rather than silently
    skipped.
    """

    def __init__(self, rule_id, signature, tier, cases, no_allow_case=None, label=None):
        self.id = rule_id
        self.signature = signature
        self.tier = tier
        self.cases = cases
        self.no_allow_case = no_allow_case
        # The group header this rule's cases printed under in the flat list.
        # Kept verbatim so the re-derivation loses no string main had.
        self.label = label
        self.emitted = []  # (case name, stderr) for each case that fired

    def firing_cases(self):
        return [c for c in self.cases if c[2] == FIRED[self.tier]]

    def quiet_cases(self):
        return [c for c in self.cases if c[2] != FIRED[self.tier]]


RULES = [
    Rule("tls-proxy", 'Refusing to disable TLS/proxy security', BLOCK_TIER, [
        ("curl -k", "curl -k https://example.com", BLOCK),
        ("disable node tls", "NODE_TLS_REJECT_UNAUTHORIZED=0 node x.js", BLOCK),
        ("curl normal", "curl -sS https://api.cloudflare.com/x", ALLOW),
    ]),

    Rule("force-push-protected", 'Force-push to a protected branch', BLOCK_TIER, [
        ("force-push main", "git push --force origin main", BLOCK),
        ("force-with-lease main", "git push --force-with-lease origin main", BLOCK),
        ("normal push feature", "git push -u origin claude/ai-agent-hooks-security-bchbh8", ALLOW),
        ("force-push feature/main allowed", "git push --force origin feature/main", ALLOW),
    ]),

    Rule("echo-secret-var", 'Refusing to echo/print a secret value', BLOCK_TIER, [
        ("echo secret var", "echo $CLOUDFLARE_API_TOKEN", BLOCK),
        ("echo lowercase secret var", "echo $cloudflare_api_token", BLOCK),
        # echo-secret-var decides per STATEMENT (#1041). Judging the whole command
        # made "a secret-named variable appears somewhere" AND "an echo appears
        # somewhere" a violation -- which is the Conductor's standard idiom, since
        # audit-agentic-os-board.py and generate-agentic-os-status.py both refuse to
        # run without GH_TOKEN and are normally invoked next to an echo. Cases A and
        # B are verbatim from the issue; nothing in either can emit the token.
        ("token prefix then echo literal on next line",
         'GH_TOKEN=$(gh auth token) python x.py\necho "done"', ALLOW),
        ("token prefix then echo EXIT on next line",
         'GH_TOKEN=$(gh auth token) python x.py\necho "EXIT=$?"', ALLOW),
        ("export token then echo", 'export GH_TOKEN=$(gh auth token)\necho "starting"', ALLOW),
        ("token prefix then echo via &&",
         'GH_TOKEN=$(gh auth token) python x.py && echo "done"', ALLOW),
        ("token prefix then echo via ;",
         'GH_TOKEN=$(gh auth token) python x.py ; echo "done"', ALLOW),
        ("real conductor idiom",
         'GH_TOKEN=$(gh auth token) python scripts/audit-agentic-os-board.py > out.txt\n'
         'echo "audit written"', ALLOW),
        # Using a secret as an ARGUMENT is not printing it. These two are what make
        # the statement/`&&` splitting load-bearing: with the whole command judged
        # as one unit, the token in the curl header arms the unrelated echo. Both
        # survive every mutation of the assignment stripper, so they test the
        # splitter and nothing else.
        ("secret in a curl header then echo on next line",
         'curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" '
         'https://api.cloudflare.com/zones\necho "done"', ALLOW),
        ("secret in a curl header then echo via &&",
         'curl -H "Authorization: Bearer $GH_TOKEN" '
         'https://api.github.com/user && echo "ok"', ALLOW),
        # AC4 in one line: the prefix form is not a leak even when the SAME
        # statement also runs a print verb, because the verb prints something else.
        ("token prefix with a print verb in the same statement",
         "GH_TOKEN=$(gh auth token) printenv HOME", ALLOW),
        ("token prefix wrapping an echo in a subshell",
         "GH_TOKEN=$(gh auth token) bash -c 'echo start; python x.py'", ALLOW),
        # The bare short names are only secrets when EXPANDED. "token" is an
        # ordinary English word and `$KEY` is an ordinary loop variable; matching
        # either unanchored blocks correct commands, which is how a guard gets
        # switched off.
        ("the word token in a message allowed", 'echo "no token found in the response"', ALLOW),
        ("loop variable KEY allowed", "for KEY in a b; do echo $KEY; done", ALLOW),
        # ... and the genuine forms must all still block. The bare-name spellings
        # below were ALLOWED before this change: the old pattern required a
        # `_TOKEN`-style suffix preceded by at least one character, so `$TOKEN`
        # itself matched nothing.
        ("echo bare $TOKEN", "echo $TOKEN", BLOCK),
        ("echo quoted $TOKEN", 'echo "$TOKEN"', BLOCK),
        ("echo braced ${TOKEN}", "echo ${TOKEN}", BLOCK),
        ("printf a secret", "printf '%s' \"$TOKEN\"", BLOCK),
        ("echo bare lowercase $token", "echo $token", BLOCK),
        ("echo $env:SECRET", "echo $env:SECRET", BLOCK),
        # `$env:X` is a PowerShell variable READ, not the `env` command: `\benv\b`
        # treats `$` and `:` as word boundaries, so the bare name armed the rule on
        # a statement that prints nothing (Copilot's finding on #1062). The second
        # case was a false positive on `main` too.
        ("powershell env var assignment allowed", "X=$env:PASSWORD", ALLOW),
        ("powershell env var set in a subshell allowed",
         "pwsh -c '$env:GH_TOKEN = \"x\"; python y.py'", ALLOW),
        # ... but `main` blocked `Write-Host $env:GH_TOKEN` only via that same stray
        # `env` match, so these two keep the coverage after the accident is removed.
        # They are the discriminator for the Write-* branch.
        ("Write-Host a secret", "Write-Host $env:GH_TOKEN", BLOCK),
        ("Write-Output a secret", "Write-Output $env:CLOUDFLARE_API_TOKEN", BLOCK),
        # PowerShell's implicit output: a bare expression statement IS a print, so
        # these leak with NO verb anywhere. `main` caught them only as collateral of
        # the stray `env` match, and a list of Write-* consumers cannot reach them --
        # there is no verb to enumerate (Conductor run 92). Every other `$env:` case
        # in this file pairs the expansion with a verb or an `=`; these are the
        # spelling with neither.
        ("bare powershell expansion of a secret", "$env:GH_TOKEN", BLOCK),
        ("bare powershell expansion via pwsh -c", "pwsh -c '$env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion piped to a consumer", "$env:GH_TOKEN | Out-Host", BLOCK),
        # An embedded pwsh script has its OWN statements: `_echo_segments` splits on
        # `;` only outside quotes, so everything after the first statement arrived
        # unjudged. Each of these is a verb-less print that `main` caught by
        # accident and this rule initially did not (Copilot, #1062).
        ("bare powershell expansion after a ; inside -c", "pwsh -c 'true; $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion before a ; inside -c", "pwsh -c '$env:GH_TOKEN; true'", BLOCK),
        ("bare powershell expansion inside a block", "pwsh -c 'if ($x) { $env:GH_TOKEN }'", BLOCK),
        # The discriminator for the widened delimiters: same shape, non-secret name.
        # Without it, the delimiter set could match anything and stay green.
        ("bare expansion of a non-secret after a ; allowed", "pwsh -c 'true; $env:TEMP'", ALLOW),
        # A grouped expression statement still prints (Copilot, #1062) ...
        ("parenthesised bare powershell expansion", "pwsh -c '($env:GH_TOKEN)'", BLOCK),
        ("parenthesised bare expansion with spaces", "pwsh -c '( $env:GH_TOKEN )'", BLOCK),
        # ... but `$(...)` is a SUBEXPRESSION whose value is substituted, so this is
        # argument passing and must stay allowed -- the `(` delimiter is excluded
        # after a `$` for exactly this case, and this is its discriminator.
        ("secret via a subexpression argument allowed",
         "pwsh -c './x.ps1 -Token $($env:GH_TOKEN)'", ALLOW),
        ("parenthesised non-secret allowed", "pwsh -c '($env:TEMP)'", ALLOW),
        # `&&`/`||` are PowerShell 7 chain operators, and inside the quoted -c
        # script `_split_on_logical` never sees them; `,` continues an expression
        # into an array literal. Found by sweeping the boundary spellings rather
        # than waiting for each to arrive as a review round.
        ("bare powershell expansion after && inside -c", "pwsh -c 'true && $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion after || inside -c",
         "pwsh -c 'false || $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion before a comma", "pwsh -c '$env:GH_TOKEN,1'", BLOCK),
        # `,` is a terminator but NOT a leading delimiter, which is what keeps an
        # array of arguments allowed. This is that asymmetry's discriminator.
        ("secret in an array argument allowed",
         "pwsh -c './x.ps1 -Args $env:GH_TOKEN,$env:CLOUDFLARE_API_TOKEN'", ALLOW),
        ("bare powershell expansion after &&", "true && $env:CLOUDFLARE_API_TOKEN", BLOCK),
        ("braced bare powershell expansion", "${env:GH_TOKEN}", BLOCK),
        # The braced spelling puts a `{` between the `$` and the name, so a
        # `(?<!\$)` lookbehind alone still sees a word boundary and reinstates the
        # false positive one spelling over.
        ("braced powershell env assignment allowed", "X=${env:PASSWORD}", ALLOW),
        # A non-secret env var is not a leak however it is spelled, and passing a
        # token as an ARGUMENT is not printing it -- `main` blocked both by the
        # same accident.
        ("bare expansion of a non-secret allowed", "$env:TEMP | Out-Host", ALLOW),
        ("braced non-secret expansion allowed", "cat ${env:HOME}/x", ALLOW),
        ("secret passed as a pwsh argument allowed",
         'pwsh -c "./x.ps1 -Token $env:GH_TOKEN"', ALLOW),
        ("secret in a curl header, powershell spelling, allowed",
         'curl -H "Authorization: Bearer $env:GH_TOKEN" https://api.github.com', ALLOW),
        # A suffixed name that is NOT on the known-vars list -- the only case that
        # reaches the suffix pattern on its own.
        ("echo a suffixed name outside the known list", 'echo "$AZURE_CLIENT_SECRET"', BLOCK),
        # A secret printed on a LATER line is still printed -- per-statement must
        # not become "only the first statement".
        ("secret echoed on a later line", 'python x.py\necho "$WHMCS_API_SECRET"', BLOCK),
        # The assigned VALUE is judged too, or stripping the assignment would hide
        # the leak it was meant to excuse.
        ("secret echoed inside an assignment value", "X=$(echo $GH_TOKEN)", BLOCK),
        # A pipeline is one unit: printenv's output reaches grep.
        ("printenv piped to grep for a secret", "printenv | grep GH_TOKEN", BLOCK),
        ("env piped to grep for a secret", "env | grep CLOUDFLARE_API_TOKEN", BLOCK),
        # Heredoc bodies are shell here, unlike in the pipeline rule: dropping them
        # would turn a blocked command into an allowed one.
        ("secret echoed inside a heredoc body", "bash <<EOF\necho $GH_TOKEN\nEOF", BLOCK),
        ("workflow secrets expression", 'echo "${{ secrets.CBM_TOKEN }}"', BLOCK),
        # Two discriminators, each the only case that reaches one clause. Without
        # them the WHMCS_* list and the `${{ secrets. }}` test are dead code that
        # every other case passes through the suffix pattern, and deleting either
        # would leave the suite green.
        ("echo a WHMCS var with no secret suffix", "echo $WHMCS_API_IDENTIFIER", BLOCK),
        ("workflow secrets expression with no secret suffix",
         'echo "${{ secrets.AZURE_CLIENT_ID }}"', BLOCK),
        # Re-homed from the flat block the union merge left in main():
        # these exercise this rule and belong with it.
        # ... and the genuine forms must all still block. The bare-name spellings
        # below were ALLOWED before this change: the old pattern required a
        # `_TOKEN`-style suffix preceded by at least one character, so `$TOKEN`
        # itself matched nothing.
        ("echo bare $TOKEN", "echo $TOKEN", BLOCK),
        ("echo quoted $TOKEN", 'echo "$TOKEN"', BLOCK),
        ("echo braced ${TOKEN}", "echo ${TOKEN}", BLOCK),
        ("printf a secret", "printf '%s' \"$TOKEN\"", BLOCK),
        ("echo bare lowercase $token", "echo $token", BLOCK),
        ("echo $env:SECRET", "echo $env:SECRET", BLOCK),
        # `$env:X` is a PowerShell variable READ, not the `env` command: `\benv\b`
        # treats `$` and `:` as word boundaries, so the bare name armed the rule on
        # a statement that prints nothing (Copilot's finding on #1062). The second
        # case was a false positive on `main` too.
        ("powershell env var assignment allowed", "X=$env:PASSWORD", ALLOW),
        ("powershell env var set in a subshell allowed",
         "pwsh -c '$env:GH_TOKEN = \"x\"; python y.py'", ALLOW),
        # ... but `main` blocked `Write-Host $env:GH_TOKEN` only via that same stray
        # `env` match, so these two keep the coverage after the accident is removed.
        # They are the discriminator for the Write-* branch.
        ("Write-Host a secret", "Write-Host $env:GH_TOKEN", BLOCK),
        ("Write-Output a secret", "Write-Output $env:CLOUDFLARE_API_TOKEN", BLOCK),
        # PowerShell's implicit output: a bare expression statement IS a print, so
        # these leak with NO verb anywhere. `main` caught them only as collateral of
        # the stray `env` match, and a list of Write-* consumers cannot reach them --
        # there is no verb to enumerate (Conductor run 92). Every other `$env:` case
        # in this file pairs the expansion with a verb or an `=`; these are the
        # spelling with neither.
        ("bare powershell expansion of a secret", "$env:GH_TOKEN", BLOCK),
        ("bare powershell expansion via pwsh -c", "pwsh -c '$env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion piped to a consumer", "$env:GH_TOKEN | Out-Host", BLOCK),
        # An embedded pwsh script has its OWN statements: `_echo_segments` splits on
        # `;` only outside quotes, so everything after the first statement arrived
        # unjudged. Each of these is a verb-less print that `main` caught by
        # accident and this rule initially did not (Copilot, #1062).
        ("bare powershell expansion after a ; inside -c", "pwsh -c 'true; $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion before a ; inside -c", "pwsh -c '$env:GH_TOKEN; true'", BLOCK),
        ("bare powershell expansion inside a block", "pwsh -c 'if ($x) { $env:GH_TOKEN }'", BLOCK),
        # The discriminator for the widened delimiters: same shape, non-secret name.
        # Without it, the delimiter set could match anything and stay green.
        ("bare expansion of a non-secret after a ; allowed", "pwsh -c 'true; $env:TEMP'", ALLOW),
        # A grouped expression statement still prints (Copilot, #1062) ...
        ("parenthesised bare powershell expansion", "pwsh -c '($env:GH_TOKEN)'", BLOCK),
        ("parenthesised bare expansion with spaces", "pwsh -c '( $env:GH_TOKEN )'", BLOCK),
        # ... but `$(...)` is a SUBEXPRESSION whose value is substituted, so this is
        # argument passing and must stay allowed -- the `(` delimiter is excluded
        # after a `$` for exactly this case, and this is its discriminator.
        ("secret via a subexpression argument allowed",
         "pwsh -c './x.ps1 -Token $($env:GH_TOKEN)'", ALLOW),
        ("parenthesised non-secret allowed", "pwsh -c '($env:TEMP)'", ALLOW),
        # `&&`/`||` are PowerShell 7 chain operators, and inside the quoted -c
        # script `_split_on_logical` never sees them; `,` continues an expression
        # into an array literal. Found by sweeping the boundary spellings rather
        # than waiting for each to arrive as a review round.
        ("bare powershell expansion after && inside -c", "pwsh -c 'true && $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion after || inside -c",
         "pwsh -c 'false || $env:GH_TOKEN'", BLOCK),
        ("bare powershell expansion before a comma", "pwsh -c '$env:GH_TOKEN,1'", BLOCK),
        # `,` is a terminator but NOT a leading delimiter, which is what keeps an
        # array of arguments allowed. This is that asymmetry's discriminator.
        ("secret in an array argument allowed",
         "pwsh -c './x.ps1 -Args $env:GH_TOKEN,$env:CLOUDFLARE_API_TOKEN'", ALLOW),
        # `&&` was added as a LEADING delimiter without being added as a terminator,
        # so a bare expansion that *precedes* a chain operator went unjudged. `||`
        # kept blocking by accident -- `|` was already a terminator for pipes --
        # which is precisely what hid the asymmetry.
        ("bare powershell expansion before && inside -c",
         "pwsh -c '$env:GH_TOKEN && true'", BLOCK),
        ("bare powershell expansion as a middle chain leg",
         "pwsh -c 'true && $env:GH_TOKEN && true'", BLOCK),
        ("bare powershell expansion before || inside -c",
         "pwsh -c '$env:GH_TOKEN || true'", BLOCK),
        # The discriminator: a secret handed to a script as an argument is not a
        # print, even when the statement continues into a chain.
        ("secret argument before && allowed",
         "pwsh -c './x.ps1 -Token $env:GH_TOKEN && true'", ALLOW),
        ("bare powershell expansion after &&", "true && $env:CLOUDFLARE_API_TOKEN", BLOCK),
        ("braced bare powershell expansion", "${env:GH_TOKEN}", BLOCK),
        # The braced spelling puts a `{` between the `$` and the name, so a
        # `(?<!\$)` lookbehind alone still sees a word boundary and reinstates the
        # false positive one spelling over.
        ("braced powershell env assignment allowed", "X=${env:PASSWORD}", ALLOW),
        # A non-secret env var is not a leak however it is spelled, and passing a
        # token as an ARGUMENT is not printing it -- `main` blocked both by the
        # same accident.
        ("bare expansion of a non-secret allowed", "$env:TEMP | Out-Host", ALLOW),
        ("braced non-secret expansion allowed", "cat ${env:HOME}/x", ALLOW),
        ("secret passed as a pwsh argument allowed",
         'pwsh -c "./x.ps1 -Token $env:GH_TOKEN"', ALLOW),
        ("secret in a curl header, powershell spelling, allowed",
         'curl -H "Authorization: Bearer $env:GH_TOKEN" https://api.github.com', ALLOW),
        # A suffixed name that is NOT on the known-vars list -- the only case that
        # reaches the suffix pattern on its own.
        ("echo a suffixed name outside the known list", 'echo "$AZURE_CLIENT_SECRET"', BLOCK),
        # A secret printed on a LATER line is still printed -- per-statement must
        # not become "only the first statement".
        ("secret echoed on a later line", 'python x.py\necho "$WHMCS_API_SECRET"', BLOCK),
        # The assigned VALUE is judged too, or stripping the assignment would hide
        # the leak it was meant to excuse.
        ("secret echoed inside an assignment value", "X=$(echo $GH_TOKEN)", BLOCK),
        # A pipeline is one unit: printenv's output reaches grep.
        ("printenv piped to grep for a secret", "printenv | grep GH_TOKEN", BLOCK),
        ("env piped to grep for a secret", "env | grep CLOUDFLARE_API_TOKEN", BLOCK),
        # Heredoc bodies are shell here, unlike in the pipeline rule: dropping them
        # would turn a blocked command into an allowed one.
        ("secret echoed inside a heredoc body", "bash <<EOF\necho $GH_TOKEN\nEOF", BLOCK),
        ("workflow secrets expression", 'echo "${{ secrets.CBM_TOKEN }}"', BLOCK),
    ]),

    Rule("secret-literal", 'appears to contain a secret literal', BLOCK_TIER, [
        ("secret literal in cmd", f"curl -H 'Authorization: Bearer {REAL_CF}' x", BLOCK),
        # Added with the registry: this rule had NO quiet case, so nothing
        # distinguished "matches a credential-shaped literal" from "matches
        # every curl -H". Referencing the credential through an env var is the
        # correct spelling of the blocked command above, and must not be caught
        # with it.
        ("secret referenced via env var allowed",
         'curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user', ALLOW),
    ]),

    Rule("rm-rf-root", "destructive 'rm -rf'", BLOCK_TIER, [
        ("rm -rf .git", "rm -rf .git", BLOCK),
        ("rm -rf build dir", "rm -rf ./node_modules", ALLOW),
        # Regressions from PR #448 review:
        ("rm -rf root", "rm -rf /", BLOCK),
        ("rm -rf bare star", "rm -rf *", BLOCK),
        ("rm -rf abs path allowed", "rm -rf /tmp/foo", ALLOW),
        ("rm -rf .git slash", "rm -rf .git/", BLOCK),
    ]),

    Rule("grep-perl-regexp", '`grep -P` (PCRE) is unavailable', BLOCK_TIER, [
        # `grep -P` is unavailable in this environment's git-bash and fails by
        # matching nothing rather than by erroring visibly (run 60, 2026-07-31).
        ("grep -P", "grep -P '^\\| L\\d+' docs/lessons-ledger.md", BLOCK),
        ("grep -qP in a conditional",
         'if grep -qP "\\t$N\\t" /tmp/items.txt; then echo ON; else echo MISSING; fi', BLOCK),
        ("grep -oP", "gh api x | grep -oP 'runs/\\K[0-9]+'", BLOCK),
        ("grep --perl-regexp", "grep --perl-regexp 'x' f", BLOCK),
        ("grep -rP recursive", "grep -rP 'stripCode' scripts/", BLOCK),
        # Must NOT fire on the POSIX forms that do work here, nor on a capital P
        # that is part of the *pattern* rather than a flag.
        ("grep -E allowed", "grep -E '^\\| L[0-9]+' docs/lessons-ledger.md", ALLOW),
        ("grep -i with P-word pattern allowed", "gh pr list | grep -i PASS", ALLOW),
        ("grep -n literal P allowed", "grep -n 'P' notes.txt", ALLOW),
        ("pgrep not matched", "pgrep -f node", ALLOW),
        ("grep --include allowed", "grep -r --include=*.py PATTERN scripts/", ALLOW),
        ("curl -X POST then grep allowed", "curl -sS https://api.example.com | grep foo", ALLOW),
    ]),

    Rule("gh-api-leading-slash", 'leading-slash endpoint is mangled', BLOCK_TIER, [
        # A leading-slash `gh api` endpoint is rewritten by MSYS path conversion
        # into a Windows filesystem path (run 61, 2026-07-31).
        ("gh api leading slash", "gh api /markdown -X POST", BLOCK),
        ("gh api leading slash with flags first", "gh api --paginate /repos/o/r/issues", BLOCK),
        ("gh api leading slash after -X", "gh api -X POST /repos/o/r/issues/1/comments", BLOCK),
        # Must NOT fire on the slash-less form, on a slash later in the path, on a
        # graphql call, or on an unrelated command that merely contains a path.
        ("gh api slash-less allowed", "gh api markdown -X POST", ALLOW),
        ("gh api nested path allowed",
         "gh api repos/FreeForCharity/FFC-Cloudflare-Automation/pulls/963", ALLOW),
        ("gh api graphql allowed", "gh api graphql -f query='query{viewer{login}}'", ALLOW),
        ("gh api rate_limit allowed", "gh api rate_limit", ALLOW),
        ("gh pr view with slash path allowed",
         "gh pr view 963 --repo FreeForCharity/FFC-Cloudflare-Automation", ALLOW),
        # A slash inside a flag VALUE is data, not the endpoint -- must not fire.
        ("gh api field value with slash allowed",
         "gh api repos/o/r/issues -f body=/tmp/note.md", ALLOW),
    ]),

    Rule("pipeline-exit-code", 'ledger L50', BLOCK_TIER, [
        # Rule 9: `$?` after a pipeline reads the LAST stage, not the command meant.
        # The exact shape that misreported a fail-closed probe on #965 (run 62).
        ("pipeline then $? on same line", 'python3 check.py | tail -3; echo "EXIT=$?"', BLOCK),
        ("pipeline then $? on next line", 'python3 check.py | grep FAIL\necho "EXIT=$?"', BLOCK),
        ("pipeline then $? into a variable", 'make build | tee log.txt\nrc=$?', BLOCK),
        # The two CORRECT spellings must stay silent, or the rule just trains people
        # to ignore it.
        ("PIPESTATUS allowed", 'python3 check.py | tail -3; echo "EXIT=${PIPESTATUS[0]}"', ALLOW),
        ("pipefail allowed", 'set -o pipefail\npython3 check.py | tail -3\necho "EXIT=$?"', ALLOW),
        # `$?` with no pipeline at all is the normal, correct idiom.
        ("bare command then $? allowed", 'python3 check.py\necho "EXIT=$?"', ALLOW),
        # `||` is not a pipeline -- it must not be mistaken for one.
        ("logical or then $? allowed", 'python3 check.py || echo failed\necho "EXIT=$?"', ALLOW),
        # A pipeline with no `$?` anywhere is the overwhelmingly common case.
        ("pipeline without $? allowed", 'git log --oneline | head -5', ALLOW),
        # Ledger L50 -- $? read through a pipe. The first two are verbatim the
        # commands that misreported this run and in run 72; both printed a confident
        # zero for a script that had exited 1.
        ("exit code through a pipe",
         'python scripts/audit-agentic-os-board.py | tail -30; echo "EXIT=$?"', BLOCK),
        ("exit code through a pipe, rc= form", "check.py --strict | head -3\nrc=$?", BLOCK),
        ("pipefail clears the rule",
         'set -o pipefail\npython audit.py | tail -30; echo "EXIT=$?"', ALLOW),
        ("$? with no pipeline allowed", 'python audit.py > out.txt; echo "EXIT=$?"', ALLOW),
        ("|| is not a pipeline", 'python audit.py || echo failed; echo "EXIT=$?"', ALLOW),
        ("pipe in a quoted string is not a pipeline",
         'grep -E "a|b" f.txt; echo "EXIT=$?"', ALLOW),
        ("pipeline with no $? after it allowed", "gh pr list --json number | head -5", ALLOW),
        # A pipeline written on a heredoc HEADER is still a pipeline. Skipping the
        # whole line made the rule miss its own target shape -- a false negative,
        # which for a guard is the expensive direction.
        ("pipeline on a heredoc header line still caught",
         'python3 - <<PY | tail -3\nprint(1)\nPY\necho "EXIT=$?"', BLOCK),
        # `$?` in SINGLE quotes is a literal and reads nothing; in double quotes the
        # shell expands it. Only the second is the L50 shape.
        ("single-quoted literal $? after a pipe allowed",
         "ls | wc -l; echo '$? is a literal'", ALLOW),
        ("double-quoted $? after a pipe still caught", 'ls | wc -l; echo "EXIT=$?"', BLOCK),
        # `;` inside quotes is not a statement separator -- splitting there invents
        # a boundary the shell never sees.
        ("semicolon inside quotes is not a statement break",
         'ls | grep -m1 x --label "a; echo $?"', ALLOW),
    ]),

    Rule("inline-python-encoding", 'decodes as cp1252', BLOCK_TIER, [
        # cp1252: inline Python reading FFC data without encoding=. Both forms below
        # crashed this run on a U+274C in a board card title.
        ("inline python open() without encoding",
         'python -c "import json;d=json.load(open(\'items.json\'))"', BLOCK),
        ("python heredoc open() without encoding",
         'python - <<PY\nimport json\nd=json.load(open("items.json"))\nPY', BLOCK),
        ("inline python with encoding= allowed",
         'python -c "import json;d=json.load(open(\'items.json\', encoding=\'utf-8\'))"', ALLOW),
        ("inline python binary mode allowed",
         'python -c "d=open(\'feed.json\', \'rb\').read()"', ALLOW),
        ("open() in a non-python command allowed", "grep -n 'open(' scripts/*.py", ALLOW),
        ("os.open is not the builtin",
         'python -c "import os;fd=os.open(\'f\', os.O_RDONLY)"', ALLOW),
        # A nested call in the first argument is the ordinary way to write this.
        # Truncating at the first `)` hid the `encoding=` and blocked a correct
        # command -- the failure mode that gets a guard switched off.
        ("nested call before encoding= allowed",
         'python -c "import os,json;d=json.load('
         'open(os.path.join(a, b), encoding=\'utf-8\'))"', ALLOW),
        ("nested call, still missing encoding=, blocked",
         'python -c "import os,json;d=json.load(open(os.path.join(a, b)))"', BLOCK),
    ]),

    Rule("graphql-paginate-endcursor", 'requires the cursor variable to be named', BLOCK_TIER, [
        # `gh api graphql --paginate` must declare $endCursor -- gh substitutes the
        # page cursor into that exact name, so any other name silently re-fetches
        # page 1 forever. The wrong-name case is the one that actually happened.
        ("graphql paginate with $cursor",
         'gh api graphql --paginate -f query='
         "'query($cursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", BLOCK),
        ("graphql paginate with no cursor var",
         "gh api graphql --paginate -f query='query{viewer{login}}'", BLOCK),
        ("graphql paginate with $endCursor allowed",
         'gh api graphql --paginate -f query='
         "'query($endCursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", ALLOW),
        # A name that merely STARTS with endCursor is a different variable and gh
        # substitutes into neither -- so these must block, not ride the substring.
        # They are also the discriminators for the exact-match case above: without
        # them a bare `$endcursor in low` test passes every case in this block.
        ("graphql paginate with $endCursorX",
         'gh api graphql --paginate -f query='
         "'query($endCursorX:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursorX){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", BLOCK),
        ("graphql paginate with $endCursor_2",
         'gh api graphql --paginate -f query='
         "'query($endCursor_2:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor_2){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", BLOCK),
        # Only --paginate needs the cursor: a single-shot graphql call is fine, and
        # REST --paginate has no query variables at all.
        ("graphql single-shot allowed", "gh api graphql -f query='query{viewer{login}}'", ALLOW),
        ("REST paginate allowed",
         "gh api --paginate repos/FreeForCharity/FFC-Cloudflare-Automation/issues/719/comments", ALLOW),
        # The name must be DECLARED by the operation, not merely present on the
        # command line. This is the false negative a whole-command scan allows: the
        # query still paginates on $cursor and would re-fetch page 1 forever, while
        # an unrelated mention downstream satisfies a substring test.
        ("$endCursor outside the query does not count",
         'gh api graphql --paginate -f query='
         "'query($cursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"
         " ; echo $endCursor", BLOCK),
        # ... and the named-operation form is a real declaration, so it must pass.
        # Without this, tightening the regex to `query(` would silently start
        # blocking a correct command.
        ("named operation declaring $endCursor allowed",
         'gh api graphql --paginate -f query='
         "'query Board($endCursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", ALLOW),
    ]),

    Rule("gh-api-unpaginated-list", '[#971]', WARN_TIER, label='guard_bash / #971 unpaginated list read:', cases=[
        ("the run-64 command warns",
         "gh api 'repos/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows?per_page=100'", WARN),
        ("nested collection warns", "gh api repos/o/r/issues/719/comments", WARN),
        ("deep route ending in a collection warns",
         "gh api repos/o/r/actions/workflows/502-x.yml/runs", WARN),
        ("org repos listing warns", "gh api orgs/FreeForCharity/repos", WARN),
        # Allows.
        ("--paginate is silent", "gh api --paginate repos/o/r/actions/workflows", SILENT),
        ("explicit page= is silent",
         "gh api 'repos/o/r/actions/workflows?per_page=100&page=2'", SILENT),
        ("single-object read is silent", "gh api repos/o/r/pulls/123", SILENT),
        ("repo root is not a collection",
         "gh api repos/FreeForCharity/FFC-Cloudflare-Automation", SILENT),
        ("graphql is not a collection", "gh api graphql -f query='{viewer{login}}'", SILENT),
        ("a write is not a list read", "gh api -X DELETE repos/o/r/git/refs/heads/x", SILENT),
        ("non-gh command with the same words is silent",
         "echo 'gh api repos/o/r/issues is a list read'", SILENT),
        # The tier itself: a warned command must still be ALLOWED to run.
        ("warned list read is still allowed", "gh api repos/o/r/issues/719/comments", ALLOW),
    ]),

    Rule("gh-api-paginate-array-jq", '[#989]', WARN_TIER, label='guard_bash / #989 paginate + array-jq:', cases=[
        ("paginate + array jq",
         "gh api --paginate repos/o/r/issues/719/comments --jq '[.[]|{body}]'", WARN),
        ("paginate + array jq, -q spelling",
         "gh api --paginate repos/o/r/issues -q '[.[]|.number]'", WARN),
        # This case is the discriminator for the one above. `--q` is not a gh flag,
        # and it is what that case used to send -- passing on the `-q` SUBSTRING
        # while the real short flag went untested. Pinning silence here means the
        # `-q` case can only stay green by matching the flag itself.
        ("paginate + array jq, --q is not the -q flag",
         "gh api --paginate repos/o/r/issues --q '[.[]|.number]'", SILENT),
        ("paginate + array jq, double quotes",
         'gh api --paginate repos/o/r/pulls --jq "[.[]|.number]"', WARN),
        ("paginate + array jq, jq before endpoint",
         "gh api --paginate --jq '[.[]|.id]' repos/o/r/commits", WARN),
        # The documented-correct forms must all stay silent.
        ("paginate + STREAMING jq silent",
         "gh api --paginate repos/o/r/issues/719/comments --jq '.[] | .number'", SILENT),
        ("array jq WITHOUT paginate silent",
         "gh api repos/o/r/issues --jq '[.[]|.number]'", SILENT),
        ("paginate + slurp, no jq, silent",
         "gh api --paginate --slurp repos/o/r/issues/719/comments", SILENT),
        ("gh pr list array jq is not gh api",
         "gh pr list --json number --jq '[.[]|.number]'", SILENT),
        # The measured counter-example that decided this rule's tier: 726 reduces
        # each page to a scalar and re-joins downstream, so it is CORRECT. It still
        # warns -- an advisory tier is allowed to be noticed on correct code -- but
        # it must never be blocked, which is what this case pins.
        ("726's real usage warns but is NOT blocked",
         "gh api --paginate \"repos/$org/$repo/teams?per_page=100\" --jq '[.[] | .slug] | join(\",\")'", ALLOW),
    ]),
]


GENERAL_ALLOWS = [
    ("normal git status", "git status"),
    ("normal gh run", "gh workflow run 8-whmcs-export-products.yml --ref main"),
]


# --- deriving the refusal sites from guard_bash.py's own source -------------
#
# This must NOT be a count. Hard-coding "there are N block() sites", or
# asserting len(RULES), goes red the moment a rule is added and teaches the
# next author to bump a constant instead of writing a case. So the sites come
# from the AST, and every one has to be claimed by a registered signature.


def _literal_fragments(node):
    """Every string literal that contributes to `node`'s value."""
    if node is None:
        return []
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _owners(tree):
    """node id -> the innermost FunctionDef containing it (None at module level)."""
    owner = {}

    def descend(node, current):
        for child in ast.iter_child_nodes(node):
            owner[id(child)] = current
            descend(child, child if isinstance(child, ast.FunctionDef) else current)

    descend(tree, None)
    return owner


def _call_names(node):
    """Names of direct (non-attribute) function calls inside an expression."""
    return [c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]


def _reason_bindings(tree, owner):
    """Every local binding of a name to something that produces reasons.

    Keyed by **(enclosing function, name)**, not by bare name, and recording
    every binding's line so the nearest preceding one wins.

    A flat name-keyed map resolves `sink(<Name>)` by SPELLING: any site whose
    argument happens to be called `reason` takes the branch and expands to
    whichever functions some other `for reason in (...)` loop mentioned --
    confidently, with the wrong functions, instead of falling through to the
    loud unresolved path. That was measured on a tree carrying an ast.Assign
    binding beside the existing ast.For one: the loop's helpers were derived
    TWICE each, the assigned helper's messages not at all, and zero sites came
    back empty, so the "fails loudly" promise never fired. Both binding forms
    are modelled here for that reason.
    """
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.iter
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name, value = node.targets[0].id, node.value
        else:
            continue
        called = _call_names(value)
        if not called:
            continue
        fn = owner.get(id(node))
        bindings.setdefault((fn.name if fn else None, name), []).append((node.lineno, called))
    return bindings


def _resolve_reason(bindings, owner, call):
    """Which functions could have produced this `sink(<Name>)` argument.

    The nearest binding of that name, in the same function, at or above the
    call. None when there is none -- the caller then reports an unreadable
    site rather than guessing, which is the whole point.
    """
    if not (call.args and isinstance(call.args[0], ast.Name)):
        return None
    fn = owner.get(id(call))
    key = (fn.name if fn else None, call.args[0].id)
    prior = [(ln, names) for ln, names in bindings.get(key, []) if ln <= call.lineno]
    if not prior:
        return None
    return max(prior, key=lambda pair: pair[0])[1]


def _returns_literals(fn):
    """Does this function return string literals (i.e. produce messages)?"""
    if fn is None:
        return False
    return any(_literal_fragments(n.value)
               for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None)


def _reason_sinks(tree, funcs, bindings, owner):
    """The functions guard_bash.py uses to report a reason to the agent.

    DISCOVERED, never hard-coded. `block` is not special -- the rule is "any
    module-level function that main() hands a human-readable message to is
    reporting that message to the agent", which is a property of the call, not
    of the name. Anchoring on the literal name `block` was a real blind
    channel, not a hypothetical one: #1018 added `warn(reason)` for the #971
    and #989 rules, and a name-anchored walker derived the same sites, all
    claimed, and reported green while two rules had no case at all.
    """
    main_fn = funcs.get("main")
    if main_fn is None:
        return set()
    sinks = set()
    for node in ast.walk(main_fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in funcs or not node.args:
            continue
        if _literal_fragments(node.args[0]):
            sinks.add(node.func.id)
            continue
        # A name argument counts only when it resolves to a function that
        # really does return message literals -- otherwise any helper taking a
        # local variable would be mistaken for a reporting channel.
        resolved = _resolve_reason(bindings, owner, node) or []
        if any(_returns_literals(funcs.get(n)) for n in resolved):
            sinks.add(node.func.id)
    return sinks


def refusal_sites(path=GUARD_BASH):
    """Every distinct refusal message guard_bash.py can emit, on every channel.

    Returns [(label, [literal fragments], sink name)]. A site whose fragments
    are EMPTY is one this function could not follow; it is returned rather than
    dropped so the caller fails loudly. Silently skipping an unreadable site
    would make the coverage check pass by not looking, which is the failure
    mode this whole file is about.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    owner = _owners(tree)
    bindings = _reason_bindings(tree, owner)
    sinks = _reason_sinks(tree, funcs, bindings, owner)
    if not sinks:
        # No channel found at all means this resolver no longer understands the
        # file. Reporting "0 sites, all covered" would be a green that means
        # nothing, so hand back one unresolvable site instead.
        return [("guard_bash.py: no reason-reporting function found at all", [], None)]

    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in sinks):
            continue
        sink = node.func.id
        arg = node.args[0] if node.args else None
        frags = _literal_fragments(arg)
        if frags:
            sites.append((f"guard_bash.py:{node.lineno} {sink}(...)", frags, sink))
            continue
        resolved = _resolve_reason(bindings, owner, node)
        if resolved:
            for fname in resolved:
                fn = funcs.get(fname)
                if fn is None:
                    sites.append((f"guard_bash.py:{node.lineno} -> {fname}() not found", [], sink))
                    continue
                for ret in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
                    if ret.value is None or (isinstance(ret.value, ast.Constant)
                                             and ret.value.value is None):
                        continue  # `return None` is "rule did not fire", not a message
                    sites.append((f"guard_bash.py:{ret.lineno} return in {fname}()",
                                  _literal_fragments(ret.value), sink))
            continue
        sites.append((f"guard_bash.py:{node.lineno} "
                      f"{sink}({ast.dump(arg)[:40] if arg else ''}...)", [], sink))
    return sites


# --- meta-tests over the registry -------------------------------------------


def test_rule_polarity():
    """Every rule needs a case in BOTH directions.

    A rule exercised only where it already passes is green and worthless --
    #940's $endCursor rule was block/block/allow and all three passed against
    an implementation that got the rule wrong.
    """
    problems = []
    for rule in RULES:
        if not rule.firing_cases():
            problems.append(f"rule '{rule.id}' has NO case that must {FIRED[rule.tier].upper()} "
                            f"-- add a command this rule is supposed to catch")
        if not rule.quiet_cases() and not rule.no_allow_case:
            problems.append(f"rule '{rule.id}' has NO case that must stay QUIET "
                            f"-- add a near-miss that must pass, or set "
                            f"no_allow_case='<why>' on the Rule")
    record("every rule has both a firing and a quiet case", not problems, "\n".join(problems))


def test_rule_attribution():
    """A case must fire for ITS OWN rule's reason.

    Matched on the reason guard_bash emits, never on the case's name -- a name
    is a claim about which rule fired, and it is exactly the claim that goes
    stale. Without this, a case can quietly start being caught by an unrelated
    rule and stay green while its own rule rots.
    """
    problems = []
    for rule in RULES:
        for name, err in rule.emitted:
            if rule.signature not in err:
                problems.append(
                    f"rule '{rule.id}' case '{name}' fired, but not for this rule: "
                    f"expected reason containing {rule.signature!r}\n"
                    f"  emitted: {' '.join(err.split())[:160]}")
    record("every firing case fires for its own rule's reason", not problems, "\n".join(problems))


def test_refusal_site_coverage():
    """Every refusal guard_bash.py can emit has a case.

    Derived from the AST, not from a count: adding a rule to guard_bash.py with
    no registered case turns this red and names the line. The reporting
    channels are discovered too, so this holds for a rule added on a NEW
    channel and not only for block().
    """
    sites = refusal_sites()
    problems = []

    for label, frags, _sink in sites:
        if not frags:
            problems.append(f"{label}: cannot read this refusal's message, so it cannot be "
                            f"shown to be tested -- teach refusal_sites() this shape")

    for label, frags, sink in sites:
        if not frags:
            continue
        claimants = [r for r in RULES if any(r.signature in f for f in frags)]
        if not claimants:
            problems.append(f"{label}: no registered rule claims this refusal\n"
                            f"  emits: {frags[0][:110]!r}\n"
                            f"  add a Rule to RULES whose signature is a slice of that text")
            continue
        want = SINK_TIER.get(sink)
        for r in claimants:
            if want and r.tier != want:
                problems.append(f"{label}: claimed by rule '{r.id}', which is a {r.tier}-tier "
                                f"rule, but this site reports through {sink}() -- its cases "
                                f"would assert the wrong verdict")

    for rule in RULES:
        if not any(rule.signature in f for _, frags, _ in sites for f in frags):
            problems.append(
                f"rule '{rule.id}' signature {rule.signature!r} matches no refusal site "
                f"in guard_bash.py. Three ways this happens, and they need OPPOSITE fixes:\n"
                f"    (a) the rule was removed          -> delete the Rule\n"
                f"    (b) its message was reworded      -> update the signature\n"
                f"    (c) the rule MOVED to a shape refusal_sites() cannot follow\n"
                f"        -> fix the resolver. Check the message really changed before\n"
                f"           touching the signature: editing it to chase this error\n"
                f"           produces a green that has stopped checking anything.")

    record(f"every refusal site in guard_bash.py is covered ({len(sites)} sites derived)",
           not problems, "\n".join(problems))


def run_rule_cases():
    for rule in RULES:
        if rule.label:
            print(rule.label)
        for name, cmd, verdict in rule.cases:
            if verdict in (BLOCK, ALLOW):
                rc, err = check_emitting(f"{rule.id} / {name}", "guard_bash.py",
                                         bash(cmd), verdict == BLOCK)
                if rc == 2:
                    rule.emitted.append((name, err))
            else:
                rc, err = check_warn(f"{rule.id} / {name}", "guard_bash.py", bash(cmd),
                                     verdict == WARN, tag=rule.signature)
                if verdict == WARN and rule.signature in err:
                    rule.emitted.append((name, err))


def main():
    print("guard_bash (by rule):")
    run_rule_cases()

    print("guard_bash (general, rule-independent):")
    for name, cmd in GENERAL_ALLOWS:
        check(name, "guard_bash.py", bash(cmd), False)


    print("guard_bash (refusal messages must not echo the payload):")
    # The refusal must not quote the offending statement back. This rule runs
    # BEFORE the secret-literal rule, so a pasted value reaches it first, and
    # the hook's stderr lands in the transcript -- quoting it would be the leak
    # the rule exists to stop. An exit-code assertion cannot see this: the
    # command is refused either way.
    check_stderr_omits("block message does not echo a pasted literal", "guard_bash.py",
                       bash(f'echo "MY_API_KEY={REAL_CF}"'), REAL_CF)
    check_stderr_omits("block message does not echo a heredoc literal", "guard_bash.py",
                       bash(f'bash <<EOF\necho "WHMCS_API_SECRET={REAL_CF}"\nEOF'), REAL_CF)
    # ... and the reported NAME is re-scanned, not merely capped. A variable
    # named `<token>_KEY` matches the suffix pattern, and the 40-char cap lands
    # exactly on the token's end -- restoring the word boundary that the `_KEY`
    # suffix had suppressed, so the truncated name IS a well-formed credential.
    # This is the only case that reaches the find_secrets() fallback.
    pat = "ghp_" + "b" * 36
    check_stderr_omits("block message does not echo a token-shaped var name", "guard_bash.py",
                       bash(f"echo ${pat}_KEY"), pat)
    # The SAME assertion against the PowerShell implicit-output message, which
    # is a second refusal site and reintroduced the identical leak by slicing
    # the name itself instead of routing through `_safe_name()`. One case per
    # message, or the next refusal added repeats it a third time.
    check_stderr_omits("powershell block message does not echo a token-shaped var name",
                       "guard_bash.py", bash(f"$env:{pat}_KEY"), pat)

    print("guard_bash meta-tests (over the rule registry):")
    test_rule_polarity()
    test_rule_attribution()
    test_refusal_site_coverage()

    print("guard_edit:")
    check("write .env", "guard_edit.py", write(".env", "X=1"), True)
    check("write key.pem", "guard_edit.py", write("certs/key.pem", "x"), True)
    check("write under secrets/", "guard_edit.py", write("secrets/foo.txt", "x"), True)
    pk = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIabc\n"  # split so this file stays clean
    check("edit private key content", "guard_edit.py", edit("docs/x.md", pk), True)
    check("edit ghp token", "guard_edit.py", edit("a.md", "token=ghp_" + "a" * 36), True)
    check("edit cf token assignment", "guard_edit.py",
          edit("a.ps1", f'$token = "{REAL_CF}"'), True)
    check("allow .env.example", "guard_edit.py", write(".env.example", "TOKEN=your-token-here"), False)
    check("allow placeholder", "guard_edit.py", edit("a.md", 'api_token = "your-api-token-here"'), False)
    check("allow secrets ref", "guard_edit.py",
          edit("w.yml", "TOKEN: ${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}"), False)
    check("allow git sha", "guard_edit.py", edit("a.md", "commit abc1234def5678901234567890123456789012ab"), False)
    check("allow documented fake token", "guard_edit.py",
          edit("a.md", "em7chiooYdKI4T3d3Oo1j31-ekEV2FiUfZxwjv-Q"), False)
    check("allow normal ps1", "guard_edit.py", write("scripts/x.ps1", "Write-Host 'hi'"), False)

    print("post_edit / scan_prompt / session_start (must not block):")
    check("post_edit normal", "post_edit.py", write("scripts/x.ps1", ""), False)
    check("scan_prompt with secret", "scan_prompt.py", {"prompt": f"here is ghp_{'a'*36}"}, False)
    check("session_start", "session_start.py", {}, False)

    test_git_precommit()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


def test_git_precommit():
    """Integration test: stage files in a throwaway git repo and run the shared
    pre-commit scanner, asserting it blocks (rc=1) / allows (rc=0)."""
    global PASS, FAIL
    import shutil
    import tempfile

    print("git pre-commit (.githooks/scan_staged.py):")
    scan_src = os.path.join(REPO, ".githooks", "scan_staged.py")
    common_src = os.path.join(HOOKS, "common.py")
    if not (os.path.exists(scan_src) and os.path.exists(common_src)):
        print("  [skip] scanner or common.py not found")
        return

    def run_in_repo(setup):
        d = tempfile.mkdtemp()
        try:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

            def g(*a):
                subprocess.run(["git", *a], cwd=d, env=env, capture_output=True)

            g("init", "-q")
            os.makedirs(os.path.join(d, ".claude", "hooks"))
            os.makedirs(os.path.join(d, ".githooks"))
            shutil.copy(common_src, os.path.join(d, ".claude", "hooks", "common.py"))
            shutil.copy(scan_src, os.path.join(d, ".githooks", "scan_staged.py"))
            setup(d, g)
            proc = subprocess.run(
                [sys.executable, os.path.join(d, ".githooks", "scan_staged.py")],
                cwd=d, env=env, capture_output=True, text=True)
            return proc.returncode
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def stage(d, g, path, content):
        full = os.path.join(d, path)
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
        g("add", path)

    cases = [
        ("clean file allowed", lambda d, g: stage(d, g, "ok.md", "# hello world"), False),
        ("staged secret blocked",
         lambda d, g: stage(d, g, "bad.md", "token=ghp_" + "a" * 36), True),
        ("staged .env blocked", lambda d, g: stage(d, g, ".env", "X=1"), True),
        ("placeholder allowed",
         lambda d, g: stage(d, g, "doc.md", 'api_token = "your-api-token-here"'), False),
    ]
    for name, setup, expect_block in cases:
        rc = run_in_repo(setup)
        blocked = rc == 1
        ok = blocked == expect_block
        PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
        print(f"  [{'ok  ' if ok else 'FAIL'}] {name}: "
              f"want={'block' if expect_block else 'allow'} "
              f"got={'block' if blocked else f'allow(rc={rc})'}")


if __name__ == "__main__":
    main()
