#!/usr/bin/env python3
"""Self-tests for the FFC AI-agent hooks.

Runs each hook as a subprocess with crafted stdin and asserts the exit code
(2 = blocked, 0 = allowed). Run locally or in CI:  python3 .claude/hooks/test_hooks.py
"""

import json
import os
import subprocess
import sys

HOOKS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HOOKS, "..", ".."))

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


def main():
    print("guard_bash:")
    check("force-push main", "guard_bash.py", bash("git push --force origin main"), True)
    check("force-with-lease main", "guard_bash.py", bash("git push --force-with-lease origin main"), True)
    check("curl -k", "guard_bash.py", bash("curl -k https://example.com"), True)
    check("disable node tls", "guard_bash.py", bash("NODE_TLS_REJECT_UNAUTHORIZED=0 node x.js"), True)
    check("echo secret var", "guard_bash.py", bash("echo $CLOUDFLARE_API_TOKEN"), True)
    check("rm -rf .git", "guard_bash.py", bash("rm -rf .git"), True)
    check("secret literal in cmd", "guard_bash.py", bash(f"curl -H 'Authorization: Bearer {REAL_CF}' x"), True)
    check("normal push feature", "guard_bash.py", bash("git push -u origin claude/ai-agent-hooks-security-bchbh8"), False)
    check("normal git status", "guard_bash.py", bash("git status"), False)
    check("normal gh run", "guard_bash.py", bash("gh workflow run 8-whmcs-export-products.yml --ref main"), False)
    check("rm -rf build dir", "guard_bash.py", bash("rm -rf ./node_modules"), False)
    check("curl normal", "guard_bash.py", bash("curl -sS https://api.cloudflare.com/x"), False)
    # Regressions from PR #448 review:
    check("rm -rf root", "guard_bash.py", bash("rm -rf /"), True)
    check("rm -rf bare star", "guard_bash.py", bash("rm -rf *"), True)
    check("rm -rf abs path allowed", "guard_bash.py", bash("rm -rf /tmp/foo"), False)
    check("rm -rf .git slash", "guard_bash.py", bash("rm -rf .git/"), True)
    check("force-push feature/main allowed", "guard_bash.py",
          bash("git push --force origin feature/main"), False)
    check("echo lowercase secret var", "guard_bash.py",
          bash("echo $cloudflare_api_token"), True)

    # echo-secret-var decides per STATEMENT (#1041). Judging the whole command
    # made "a secret-named variable appears somewhere" AND "an echo appears
    # somewhere" a violation -- which is the Conductor's standard idiom, since
    # audit-agentic-os-board.py and generate-agentic-os-status.py both refuse to
    # run without GH_TOKEN and are normally invoked next to an echo. Cases A and
    # B are verbatim from the issue; nothing in either can emit the token.
    check("token prefix then echo literal on next line", "guard_bash.py",
          bash('GH_TOKEN=$(gh auth token) python x.py\necho "done"'), False)
    check("token prefix then echo EXIT on next line", "guard_bash.py",
          bash('GH_TOKEN=$(gh auth token) python x.py\necho "EXIT=$?"'), False)
    check("export token then echo", "guard_bash.py",
          bash('export GH_TOKEN=$(gh auth token)\necho "starting"'), False)
    check("token prefix then echo via &&", "guard_bash.py",
          bash('GH_TOKEN=$(gh auth token) python x.py && echo "done"'), False)
    check("token prefix then echo via ;", "guard_bash.py",
          bash('GH_TOKEN=$(gh auth token) python x.py ; echo "done"'), False)
    check("real conductor idiom", "guard_bash.py",
          bash('GH_TOKEN=$(gh auth token) python scripts/audit-agentic-os-board.py > out.txt\n'
               'echo "audit written"'), False)
    # Using a secret as an ARGUMENT is not printing it. These two are what make
    # the statement/`&&` splitting load-bearing: with the whole command judged
    # as one unit, the token in the curl header arms the unrelated echo. Both
    # survive every mutation of the assignment stripper, so they test the
    # splitter and nothing else.
    check("secret in a curl header then echo on next line", "guard_bash.py",
          bash('curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" '
               'https://api.cloudflare.com/zones\necho "done"'), False)
    check("secret in a curl header then echo via &&", "guard_bash.py",
          bash('curl -H "Authorization: Bearer $GH_TOKEN" '
               'https://api.github.com/user && echo "ok"'), False)
    # AC4 in one line: the prefix form is not a leak even when the SAME
    # statement also runs a print verb, because the verb prints something else.
    check("token prefix with a print verb in the same statement", "guard_bash.py",
          bash("GH_TOKEN=$(gh auth token) printenv HOME"), False)
    check("token prefix wrapping an echo in a subshell", "guard_bash.py",
          bash("GH_TOKEN=$(gh auth token) bash -c 'echo start; python x.py'"), False)
    # The bare short names are only secrets when EXPANDED. "token" is an
    # ordinary English word and `$KEY` is an ordinary loop variable; matching
    # either unanchored blocks correct commands, which is how a guard gets
    # switched off.
    check("the word token in a message allowed", "guard_bash.py",
          bash('echo "no token found in the response"'), False)
    check("loop variable KEY allowed", "guard_bash.py",
          bash("for KEY in a b; do echo $KEY; done"), False)

    # ... and the genuine forms must all still block. The bare-name spellings
    # below were ALLOWED before this change: the old pattern required a
    # `_TOKEN`-style suffix preceded by at least one character, so `$TOKEN`
    # itself matched nothing.
    check("echo bare $TOKEN", "guard_bash.py", bash("echo $TOKEN"), True)
    check("echo quoted $TOKEN", "guard_bash.py", bash('echo "$TOKEN"'), True)
    check("echo braced ${TOKEN}", "guard_bash.py", bash("echo ${TOKEN}"), True)
    check("printf a secret", "guard_bash.py", bash("printf '%s' \"$TOKEN\""), True)
    check("echo bare lowercase $token", "guard_bash.py", bash("echo $token"), True)
    check("echo $env:SECRET", "guard_bash.py", bash("echo $env:SECRET"), True)
    # `$env:X` is a PowerShell variable READ, not the `env` command: `\benv\b`
    # treats `$` and `:` as word boundaries, so the bare name armed the rule on
    # a statement that prints nothing (Copilot's finding on #1062). The second
    # case was a false positive on `main` too.
    check("powershell env var assignment allowed", "guard_bash.py",
          bash("X=$env:PASSWORD"), False)
    check("powershell env var set in a subshell allowed", "guard_bash.py",
          bash("pwsh -c '$env:GH_TOKEN = \"x\"; python y.py'"), False)
    # ... but `main` blocked `Write-Host $env:GH_TOKEN` only via that same stray
    # `env` match, so these two keep the coverage after the accident is removed.
    # They are the discriminator for the Write-* branch.
    check("Write-Host a secret", "guard_bash.py",
          bash("Write-Host $env:GH_TOKEN"), True)
    check("Write-Output a secret", "guard_bash.py",
          bash("Write-Output $env:CLOUDFLARE_API_TOKEN"), True)
    # PowerShell's implicit output: a bare expression statement IS a print, so
    # these leak with NO verb anywhere. `main` caught them only as collateral of
    # the stray `env` match, and a list of Write-* consumers cannot reach them --
    # there is no verb to enumerate (Conductor run 92). Every other `$env:` case
    # in this file pairs the expansion with a verb or an `=`; these are the
    # spelling with neither.
    check("bare powershell expansion of a secret", "guard_bash.py",
          bash("$env:GH_TOKEN"), True)
    check("bare powershell expansion via pwsh -c", "guard_bash.py",
          bash("pwsh -c '$env:GH_TOKEN'"), True)
    check("bare powershell expansion piped to a consumer", "guard_bash.py",
          bash("$env:GH_TOKEN | Out-Host"), True)
    # An embedded pwsh script has its OWN statements: `_echo_segments` splits on
    # `;` only outside quotes, so everything after the first statement arrived
    # unjudged. Each of these is a verb-less print that `main` caught by
    # accident and this rule initially did not (Copilot, #1062).
    check("bare powershell expansion after a ; inside -c", "guard_bash.py",
          bash("pwsh -c 'true; $env:GH_TOKEN'"), True)
    check("bare powershell expansion before a ; inside -c", "guard_bash.py",
          bash("pwsh -c '$env:GH_TOKEN; true'"), True)
    check("bare powershell expansion inside a block", "guard_bash.py",
          bash("pwsh -c 'if ($x) { $env:GH_TOKEN }'"), True)
    # The discriminator for the widened delimiters: same shape, non-secret name.
    # Without it, the delimiter set could match anything and stay green.
    check("bare expansion of a non-secret after a ; allowed", "guard_bash.py",
          bash("pwsh -c 'true; $env:TEMP'"), False)
    # A grouped expression statement still prints (Copilot, #1062) ...
    check("parenthesised bare powershell expansion", "guard_bash.py",
          bash("pwsh -c '($env:GH_TOKEN)'"), True)
    check("parenthesised bare expansion with spaces", "guard_bash.py",
          bash("pwsh -c '( $env:GH_TOKEN )'"), True)
    # ... but `$(...)` is a SUBEXPRESSION whose value is substituted, so this is
    # argument passing and must stay allowed -- the `(` delimiter is excluded
    # after a `$` for exactly this case, and this is its discriminator.
    check("secret via a subexpression argument allowed", "guard_bash.py",
          bash("pwsh -c './x.ps1 -Token $($env:GH_TOKEN)'"), False)
    check("parenthesised non-secret allowed", "guard_bash.py",
          bash("pwsh -c '($env:TEMP)'"), False)
    check("bare powershell expansion after &&", "guard_bash.py",
          bash("true && $env:CLOUDFLARE_API_TOKEN"), True)
    check("braced bare powershell expansion", "guard_bash.py",
          bash("${env:GH_TOKEN}"), True)
    # The braced spelling puts a `{` between the `$` and the name, so a
    # `(?<!\$)` lookbehind alone still sees a word boundary and reinstates the
    # false positive one spelling over.
    check("braced powershell env assignment allowed", "guard_bash.py",
          bash("X=${env:PASSWORD}"), False)
    # A non-secret env var is not a leak however it is spelled, and passing a
    # token as an ARGUMENT is not printing it -- `main` blocked both by the
    # same accident.
    check("bare expansion of a non-secret allowed", "guard_bash.py",
          bash("$env:TEMP | Out-Host"), False)
    check("braced non-secret expansion allowed", "guard_bash.py",
          bash("cat ${env:HOME}/x"), False)
    check("secret passed as a pwsh argument allowed", "guard_bash.py",
          bash('pwsh -c "./x.ps1 -Token $env:GH_TOKEN"'), False)
    check("secret in a curl header, powershell spelling, allowed", "guard_bash.py",
          bash('curl -H "Authorization: Bearer $env:GH_TOKEN" https://api.github.com'), False)
    # A suffixed name that is NOT on the known-vars list -- the only case that
    # reaches the suffix pattern on its own.
    check("echo a suffixed name outside the known list", "guard_bash.py",
          bash('echo "$AZURE_CLIENT_SECRET"'), True)
    # A secret printed on a LATER line is still printed -- per-statement must
    # not become "only the first statement".
    check("secret echoed on a later line", "guard_bash.py",
          bash('python x.py\necho "$WHMCS_API_SECRET"'), True)
    # The assigned VALUE is judged too, or stripping the assignment would hide
    # the leak it was meant to excuse.
    check("secret echoed inside an assignment value", "guard_bash.py",
          bash("X=$(echo $GH_TOKEN)"), True)
    # A pipeline is one unit: printenv's output reaches grep.
    check("printenv piped to grep for a secret", "guard_bash.py",
          bash("printenv | grep GH_TOKEN"), True)
    check("env piped to grep for a secret", "guard_bash.py",
          bash("env | grep CLOUDFLARE_API_TOKEN"), True)
    # Heredoc bodies are shell here, unlike in the pipeline rule: dropping them
    # would turn a blocked command into an allowed one.
    check("secret echoed inside a heredoc body", "guard_bash.py",
          bash("bash <<EOF\necho $GH_TOKEN\nEOF"), True)
    check("workflow secrets expression", "guard_bash.py",
          bash('echo "${{ secrets.CBM_TOKEN }}"'), True)
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
    # Two discriminators, each the only case that reaches one clause. Without
    # them the WHMCS_* list and the `${{ secrets. }}` test are dead code that
    # every other case passes through the suffix pattern, and deleting either
    # would leave the suite green.
    check("echo a WHMCS var with no secret suffix", "guard_bash.py",
          bash("echo $WHMCS_API_IDENTIFIER"), True)
    check("workflow secrets expression with no secret suffix", "guard_bash.py",
          bash('echo "${{ secrets.AZURE_CLIENT_ID }}"'), True)
    # `grep -P` is unavailable in this environment's git-bash and fails by
    # matching nothing rather than by erroring visibly (run 60, 2026-07-31).
    check("grep -P", "guard_bash.py", bash("grep -P '^\\| L\\d+' docs/lessons-ledger.md"), True)
    check("grep -qP in a conditional", "guard_bash.py",
          bash('if grep -qP "\\t$N\\t" /tmp/items.txt; then echo ON; else echo MISSING; fi'), True)
    check("grep -oP", "guard_bash.py", bash("gh api x | grep -oP 'runs/\\K[0-9]+'"), True)
    check("grep --perl-regexp", "guard_bash.py", bash("grep --perl-regexp 'x' f"), True)
    check("grep -rP recursive", "guard_bash.py", bash("grep -rP 'stripCode' scripts/"), True)
    # Must NOT fire on the POSIX forms that do work here, nor on a capital P
    # that is part of the *pattern* rather than a flag.
    check("grep -E allowed", "guard_bash.py", bash("grep -E '^\\| L[0-9]+' docs/lessons-ledger.md"), False)
    check("grep -i with P-word pattern allowed", "guard_bash.py",
          bash("gh pr list | grep -i PASS"), False)
    check("grep -n literal P allowed", "guard_bash.py", bash("grep -n 'P' notes.txt"), False)
    check("pgrep not matched", "guard_bash.py", bash("pgrep -f node"), False)
    check("grep --include allowed", "guard_bash.py",
          bash("grep -r --include=*.py PATTERN scripts/"), False)
    check("curl -X POST then grep allowed", "guard_bash.py",
          bash("curl -sS https://api.example.com | grep foo"), False)
    # A leading-slash `gh api` endpoint is rewritten by MSYS path conversion
    # into a Windows filesystem path (run 61, 2026-07-31).
    check("gh api leading slash", "guard_bash.py", bash("gh api /markdown -X POST"), True)
    check("gh api leading slash with flags first", "guard_bash.py",
          bash("gh api --paginate /repos/o/r/issues"), True)
    check("gh api leading slash after -X", "guard_bash.py",
          bash("gh api -X POST /repos/o/r/issues/1/comments"), True)
    # Must NOT fire on the slash-less form, on a slash later in the path, on a
    # graphql call, or on an unrelated command that merely contains a path.
    check("gh api slash-less allowed", "guard_bash.py", bash("gh api markdown -X POST"), False)
    check("gh api nested path allowed", "guard_bash.py",
          bash("gh api repos/FreeForCharity/FFC-Cloudflare-Automation/pulls/963"), False)
    check("gh api graphql allowed", "guard_bash.py",
          bash("gh api graphql -f query='query{viewer{login}}'"), False)
    check("gh api rate_limit allowed", "guard_bash.py", bash("gh api rate_limit"), False)
    check("gh pr view with slash path allowed", "guard_bash.py",
          bash("gh pr view 963 --repo FreeForCharity/FFC-Cloudflare-Automation"), False)
    # A slash inside a flag VALUE is data, not the endpoint -- must not fire.
    check("gh api field value with slash allowed", "guard_bash.py",
          bash("gh api repos/o/r/issues -f body=/tmp/note.md"), False)

    # Rule 9: `$?` after a pipeline reads the LAST stage, not the command meant.
    # The exact shape that misreported a fail-closed probe on #965 (run 62).
    check("pipeline then $? on same line", "guard_bash.py",
          bash('python3 check.py | tail -3; echo "EXIT=$?"'), True)
    check("pipeline then $? on next line", "guard_bash.py",
          bash('python3 check.py | grep FAIL\necho "EXIT=$?"'), True)
    check("pipeline then $? into a variable", "guard_bash.py",
          bash('make build | tee log.txt\nrc=$?'), True)
    # The two CORRECT spellings must stay silent, or the rule just trains people
    # to ignore it.
    check("PIPESTATUS allowed", "guard_bash.py",
          bash('python3 check.py | tail -3; echo "EXIT=${PIPESTATUS[0]}"'), False)
    check("pipefail allowed", "guard_bash.py",
          bash('set -o pipefail\npython3 check.py | tail -3\necho "EXIT=$?"'), False)
    # `$?` with no pipeline at all is the normal, correct idiom.
    check("bare command then $? allowed", "guard_bash.py",
          bash('python3 check.py\necho "EXIT=$?"'), False)
    # `||` is not a pipeline -- it must not be mistaken for one.
    check("logical or then $? allowed", "guard_bash.py",
          bash('python3 check.py || echo failed\necho "EXIT=$?"'), False)
    # A pipeline with no `$?` anywhere is the overwhelmingly common case.
    check("pipeline without $? allowed", "guard_bash.py",
          bash('git log --oneline | head -5'), False)

    # Ledger L50 -- $? read through a pipe. The first two are verbatim the
    # commands that misreported this run and in run 72; both printed a confident
    # zero for a script that had exited 1.
    check("exit code through a pipe", "guard_bash.py",
          bash('python scripts/audit-agentic-os-board.py | tail -30; echo "EXIT=$?"'), True)
    check("exit code through a pipe, rc= form", "guard_bash.py",
          bash("check.py --strict | head -3\nrc=$?"), True)
    check("pipefail clears the rule", "guard_bash.py",
          bash('set -o pipefail\npython audit.py | tail -30; echo "EXIT=$?"'), False)
    check("$? with no pipeline allowed", "guard_bash.py",
          bash('python audit.py > out.txt; echo "EXIT=$?"'), False)
    check("|| is not a pipeline", "guard_bash.py",
          bash('python audit.py || echo failed; echo "EXIT=$?"'), False)
    check("pipe in a quoted string is not a pipeline", "guard_bash.py",
          bash('grep -E "a|b" f.txt; echo "EXIT=$?"'), False)
    check("pipeline with no $? after it allowed", "guard_bash.py",
          bash("gh pr list --json number | head -5"), False)
    # A pipeline written on a heredoc HEADER is still a pipeline. Skipping the
    # whole line made the rule miss its own target shape -- a false negative,
    # which for a guard is the expensive direction.
    check("pipeline on a heredoc header line still caught", "guard_bash.py",
          bash('python3 - <<PY | tail -3\nprint(1)\nPY\necho "EXIT=$?"'), True)
    # `$?` in SINGLE quotes is a literal and reads nothing; in double quotes the
    # shell expands it. Only the second is the L50 shape.
    check("single-quoted literal $? after a pipe allowed", "guard_bash.py",
          bash("ls | wc -l; echo '$? is a literal'"), False)
    check("double-quoted $? after a pipe still caught", "guard_bash.py",
          bash('ls | wc -l; echo "EXIT=$?"'), True)
    # `;` inside quotes is not a statement separator -- splitting there invents
    # a boundary the shell never sees.
    check("semicolon inside quotes is not a statement break", "guard_bash.py",
          bash('ls | grep -m1 x --label "a; echo $?"'), False)

    # cp1252: inline Python reading FFC data without encoding=. Both forms below
    # crashed this run on a U+274C in a board card title.
    check("inline python open() without encoding", "guard_bash.py",
          bash('python -c "import json;d=json.load(open(\'items.json\'))"'), True)
    check("python heredoc open() without encoding", "guard_bash.py",
          bash('python - <<PY\nimport json\nd=json.load(open("items.json"))\nPY'), True)
    check("inline python with encoding= allowed", "guard_bash.py",
          bash('python -c "import json;d=json.load(open(\'items.json\', encoding=\'utf-8\'))"'), False)
    check("inline python binary mode allowed", "guard_bash.py",
          bash('python -c "d=open(\'feed.json\', \'rb\').read()"'), False)
    check("open() in a non-python command allowed", "guard_bash.py",
          bash("grep -n 'open(' scripts/*.py"), False)
    check("os.open is not the builtin", "guard_bash.py",
          bash('python -c "import os;fd=os.open(\'f\', os.O_RDONLY)"'), False)
    # A nested call in the first argument is the ordinary way to write this.
    # Truncating at the first `)` hid the `encoding=` and blocked a correct
    # command -- the failure mode that gets a guard switched off.
    check("nested call before encoding= allowed", "guard_bash.py",
          bash('python -c "import os,json;d=json.load('
               'open(os.path.join(a, b), encoding=\'utf-8\'))"'), False)
    check("nested call, still missing encoding=, blocked", "guard_bash.py",
          bash('python -c "import os,json;d=json.load(open(os.path.join(a, b)))"'), True)
    # `gh api graphql --paginate` must declare $endCursor -- gh substitutes the
    # page cursor into that exact name, so any other name silently re-fetches
    # page 1 forever. The wrong-name case is the one that actually happened.
    check("graphql paginate with $cursor", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query($cursor:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$cursor){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"), True)
    check("graphql paginate with no cursor var", "guard_bash.py",
          bash("gh api graphql --paginate -f query='query{viewer{login}}'"), True)
    check("graphql paginate with $endCursor allowed", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query($endCursor:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$endCursor){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"), False)
    # A name that merely STARTS with endCursor is a different variable and gh
    # substitutes into neither -- so these must block, not ride the substring.
    # They are also the discriminators for the exact-match case above: without
    # them a bare `$endcursor in low` test passes every case in this block.
    check("graphql paginate with $endCursorX", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query($endCursorX:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$endCursorX){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"), True)
    check("graphql paginate with $endCursor_2", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query($endCursor_2:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$endCursor_2){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"), True)
    # Only --paginate needs the cursor: a single-shot graphql call is fine, and
    # REST --paginate has no query variables at all.
    check("graphql single-shot allowed", "guard_bash.py",
          bash("gh api graphql -f query='query{viewer{login}}'"), False)
    check("REST paginate allowed", "guard_bash.py",
          bash("gh api --paginate repos/FreeForCharity/FFC-Cloudflare-Automation/issues/719/comments"),
          False)
    # The name must be DECLARED by the operation, not merely present on the
    # command line. This is the false negative a whole-command scan allows: the
    # query still paginates on $cursor and would re-fetch page 1 forever, while
    # an unrelated mention downstream satisfies a substring test.
    check("$endCursor outside the query does not count", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query($cursor:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$cursor){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"
               " ; echo $endCursor"), True)
    # ... and the named-operation form is a real declaration, so it must pass.
    # Without this, tightening the regex to `query(` would silently start
    # blocking a correct command.
    check("named operation declaring $endCursor allowed", "guard_bash.py",
          bash('gh api graphql --paginate -f query='
               "'query Board($endCursor:String){organization(login:\"x\"){"
               "projectV2(number:9){items(first:100,after:$endCursor){"
               "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"), False)

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
