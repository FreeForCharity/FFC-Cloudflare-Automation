#!/usr/bin/env python3
"""Self-tests for the FFC AI-agent hooks.

Runs each hook as a subprocess with crafted stdin and asserts the exit code
(2 = blocked, 0 = allowed). Run locally or in CI:  python3 .claude/hooks/test_hooks.py

guard_bash's cases live in RULES, a registry keyed by rule id, and two
meta-tests run over it (see the block comment above RULES for why a flat list
of cases could not hold the property that matters).
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
    return subprocess.run(
        [sys.executable, os.path.join(HOOKS, script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )


def record(name, ok, detail=""):
    """Count one assertion and print it in the same shape as check()."""
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}{'' if ok else ':'}")
    if not ok and detail:
        for line in detail.splitlines():
            print(f"         {line}")


def check(name, script, payload, expect_block):
    global PASS, FAIL
    proc = run(script, payload)
    blocked = proc.returncode == 2
    ok = blocked == expect_block
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    status = "ok  " if ok else "FAIL"
    want = "block" if expect_block else "allow"
    got = "block" if blocked else f"allow(rc={proc.returncode})"
    print(f"  [{status}] {name}: want={want} got={got}")
    return proc


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def edit(path, new=""):
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": new}}


def write(path, content=""):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


# Fabricated, CF-shaped token. Split across concatenated literals so the source
# never contains a contiguous token -- otherwise this very test file would trip
# guard_edit.py / external secret scanners (the value at runtime is unchanged).
REAL_CF = "em7XiooYdKI4T3d3" + "Oo1j31-ekEV2Fi" + "UfZxwQvzT9"


# --- the guard_bash rule registry -------------------------------------------
#
# Cases used to be a flat list of check() calls, which cannot hold the property
# that actually matters. "Does the file contain both a blocking and an allowing
# case?" is trivially true across 74 cases and will stay true forever, while a
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
#   * `signature` is a verbatim slice of the reason text the rule emits.
#     guard_bash reports every refusal through block(reason), and each site
#     passes a distinct string, so the emitted reason -- not the case's name --
#     is what attributes a block to a rule.
#   * test_rule_polarity() demands both directions PER RULE.
#   * test_block_site_coverage() derives the refusal sites from guard_bash.py's
#     own AST, so a rule added with no case turns it red without anyone
#     maintaining a count.
BLOCK, ALLOW = True, False


class Rule:
    """One guard_bash refusal rule and every case that exercises it.

    no_allow_case is an escape hatch for a rule with no sensible allowed
    command; it takes a one-line reason so the exemption is stated rather than
    silently skipped. Nothing uses it today -- every rule here has a near-miss
    that must pass.
    """

    def __init__(self, rule_id, signature, cases, no_allow_case=None):
        self.id = rule_id
        self.signature = signature
        self.cases = cases
        self.no_allow_case = no_allow_case
        self.emitted = []  # (case name, stderr) for each case that blocked

    def blocking_cases(self):
        return [c for c in self.cases if c[2] is BLOCK]

    def allowing_cases(self):
        return [c for c in self.cases if c[2] is ALLOW]


RULES = [
    Rule("tls-proxy", "Refusing to disable TLS/proxy security", [
        ("curl -k", "curl -k https://example.com", BLOCK),
        ("disable node tls", "NODE_TLS_REJECT_UNAUTHORIZED=0 node x.js", BLOCK),
        ("curl normal", "curl -sS https://api.cloudflare.com/x", ALLOW),
    ]),

    Rule("force-push-protected", "Force-push to a protected branch", [
        ("force-push main", "git push --force origin main", BLOCK),
        ("force-with-lease main", "git push --force-with-lease origin main", BLOCK),
        ("normal push feature", "git push -u origin claude/ai-agent-hooks-security-bchbh8", ALLOW),
        # 'feature/main' is not the branch 'main'.
        ("force-push feature/main allowed", "git push --force origin feature/main", ALLOW),
    ]),

    Rule("echo-secret-var", "Refusing to echo/print a secret value", [
        ("echo secret var", "echo $CLOUDFLARE_API_TOKEN", BLOCK),
        ("echo lowercase secret var", "echo $cloudflare_api_token", BLOCK),
        # Added with the registry: this rule had NO allowed case, so nothing
        # distinguished "matches $FOO_TOKEN" from "matches every echo". Both
        # below are near-misses that must stay silent -- the second names a
        # variable that CONTAINS 'KEY' without ending in the '_KEY' suffix.
        ("echo a non-secret var allowed", 'echo "Deploying $APP_NAME"', ALLOW),
        ("echo a var merely containing KEY allowed", "printf '%s\\n' \"$KEY_ROTATION_DUE\"", ALLOW),
    ]),

    Rule("secret-literal", "appears to contain a secret literal", [
        ("secret literal in cmd", f"curl -H 'Authorization: Bearer {REAL_CF}' x", BLOCK),
        # Added with the registry: no allowed case existed. Referencing the
        # credential through an env var is the CORRECT spelling of the blocked
        # command above and must not be caught with it.
        ("secret referenced via env var allowed",
         'curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user', ALLOW),
    ]),

    Rule("rm-rf-root", "destructive 'rm -rf'", [
        ("rm -rf .git", "rm -rf .git", BLOCK),
        ("rm -rf root", "rm -rf /", BLOCK),
        ("rm -rf bare star", "rm -rf *", BLOCK),
        ("rm -rf .git slash", "rm -rf .git/", BLOCK),
        ("rm -rf build dir", "rm -rf ./node_modules", ALLOW),
        ("rm -rf abs path allowed", "rm -rf /tmp/foo", ALLOW),
    ]),

    # `grep -P` is unavailable in this environment's git-bash and fails by
    # matching nothing rather than by erroring visibly (run 60, 2026-07-31).
    Rule("grep-perl-regexp", "`grep -P` (PCRE) is unavailable", [
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

    # A leading-slash `gh api` endpoint is rewritten by MSYS path conversion
    # into a Windows filesystem path (run 61, 2026-07-31).
    Rule("gh-api-leading-slash", "leading-slash endpoint is mangled", [
        ("gh api leading slash", "gh api /markdown -X POST", BLOCK),
        ("gh api leading slash with flags first", "gh api --paginate /repos/o/r/issues", BLOCK),
        ("gh api leading slash after -X", "gh api -X POST /repos/o/r/issues/1/comments", BLOCK),
        # Must NOT fire on the slash-less form, on a slash later in the path, on
        # a graphql call, or on an unrelated command that contains a path.
        ("gh api slash-less allowed", "gh api markdown -X POST", ALLOW),
        ("gh api nested path allowed",
         "gh api repos/FreeForCharity/FFC-Cloudflare-Automation/pulls/963", ALLOW),
        ("gh api graphql allowed", "gh api graphql -f query='query{viewer{login}}'", ALLOW),
        ("gh api rate_limit allowed", "gh api rate_limit", ALLOW),
        ("gh pr view with slash path allowed",
         "gh pr view 963 --repo FreeForCharity/FFC-Cloudflare-Automation", ALLOW),
        # A slash inside a flag VALUE is data, not the endpoint.
        ("gh api field value with slash allowed",
         "gh api repos/o/r/issues -f body=/tmp/note.md", ALLOW),
    ]),

    # Ledger L50 -- `$?` after a pipeline reads the LAST stage, not the command
    # meant. The first two blocking cases below are verbatim the commands that
    # misreported a fail-closed probe on #965 (run 62) and again in run 72; both
    # printed a confident zero for a script that had exited 1.
    Rule("pipeline-exit-code", "ledger L50", [
        ("pipeline then $? on same line", 'python3 check.py | tail -3; echo "EXIT=$?"', BLOCK),
        ("pipeline then $? on next line", 'python3 check.py | grep FAIL\necho "EXIT=$?"', BLOCK),
        ("pipeline then $? into a variable", "make build | tee log.txt\nrc=$?", BLOCK),
        ("exit code through a pipe",
         'python scripts/audit-agentic-os-board.py | tail -30; echo "EXIT=$?"', BLOCK),
        ("exit code through a pipe, rc= form", "check.py --strict | head -3\nrc=$?", BLOCK),
        # A pipeline written on a heredoc HEADER is still a pipeline. Skipping
        # the whole line made the rule miss its own target shape -- a false
        # negative, which for a guard is the expensive direction.
        ("pipeline on a heredoc header line still caught",
         'python3 - <<PY | tail -3\nprint(1)\nPY\necho "EXIT=$?"', BLOCK),
        # `$?` in SINGLE quotes is a literal and reads nothing; in double quotes
        # the shell expands it. Only the second is the L50 shape.
        ("double-quoted $? after a pipe still caught", 'ls | wc -l; echo "EXIT=$?"', BLOCK),
        # The two CORRECT spellings must stay silent, or the rule just trains
        # people to ignore it.
        ("PIPESTATUS allowed", 'python3 check.py | tail -3; echo "EXIT=${PIPESTATUS[0]}"', ALLOW),
        ("pipefail allowed", 'set -o pipefail\npython3 check.py | tail -3\necho "EXIT=$?"', ALLOW),
        ("pipefail clears the rule",
         'set -o pipefail\npython audit.py | tail -30; echo "EXIT=$?"', ALLOW),
        # `$?` with no pipeline at all is the normal, correct idiom.
        ("bare command then $? allowed", 'python3 check.py\necho "EXIT=$?"', ALLOW),
        ("$? with no pipeline allowed", 'python audit.py > out.txt; echo "EXIT=$?"', ALLOW),
        # `||` is not a pipeline -- it must not be mistaken for one.
        ("logical or then $? allowed", 'python3 check.py || echo failed\necho "EXIT=$?"', ALLOW),
        ("|| is not a pipeline", 'python audit.py || echo failed; echo "EXIT=$?"', ALLOW),
        # A pipeline with no `$?` anywhere is the overwhelmingly common case.
        ("pipeline without $? allowed", "git log --oneline | head -5", ALLOW),
        ("pipeline with no $? after it allowed", "gh pr list --json number | head -5", ALLOW),
        ("pipe in a quoted string is not a pipeline", 'grep -E "a|b" f.txt; echo "EXIT=$?"', ALLOW),
        ("single-quoted literal $? after a pipe allowed", "ls | wc -l; echo '$? is a literal'", ALLOW),
        # `;` inside quotes is not a statement separator -- splitting there
        # invents a boundary the shell never sees.
        ("semicolon inside quotes is not a statement break",
         'ls | grep -m1 x --label "a; echo $?"', ALLOW),
    ]),

    # cp1252: inline Python reading FFC data without encoding=. Both blocking
    # forms below crashed run 73 on a U+274C in a board card title.
    Rule("inline-python-encoding", "decodes as cp1252", [
        ("inline python open() without encoding",
         'python -c "import json;d=json.load(open(\'items.json\'))"', BLOCK),
        ("python heredoc open() without encoding",
         'python - <<PY\nimport json\nd=json.load(open("items.json"))\nPY', BLOCK),
        # A nested call in the first argument is the ordinary way to write this.
        # Truncating at the first `)` hid the `encoding=` and blocked a correct
        # command -- the failure mode that gets a guard switched off.
        ("nested call, still missing encoding=, blocked",
         'python -c "import os,json;d=json.load(open(os.path.join(a, b)))"', BLOCK),
        ("inline python with encoding= allowed",
         'python -c "import json;d=json.load(open(\'items.json\', encoding=\'utf-8\'))"', ALLOW),
        ("inline python binary mode allowed", 'python -c "d=open(\'feed.json\', \'rb\').read()"', ALLOW),
        ("open() in a non-python command allowed", "grep -n 'open(' scripts/*.py", ALLOW),
        ("os.open is not the builtin", 'python -c "import os;fd=os.open(\'f\', os.O_RDONLY)"', ALLOW),
        ("nested call before encoding= allowed",
         'python -c "import os,json;d=json.load(open(os.path.join(a, b), encoding=\'utf-8\'))"', ALLOW),
    ]),

    # `gh api graphql --paginate` must declare $endCursor -- gh substitutes the
    # page cursor into that exact name, so any other name silently re-fetches
    # page 1 forever. The wrong-name case is the one that actually happened.
    Rule("graphql-paginate-endcursor", "requires the cursor variable to be named", [
        ("graphql paginate with $cursor",
         'gh api graphql --paginate -f query='
         "'query($cursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", BLOCK),
        ("graphql paginate with no cursor var",
         "gh api graphql --paginate -f query='query{viewer{login}}'", BLOCK),
        # A name that merely STARTS with endCursor is a different variable and
        # gh substitutes into neither -- so these must block, not ride the
        # substring. They are also the discriminators for the exact-match case
        # below: without them a bare `$endcursor in low` test passes every case
        # in this rule. This is the #940 gap that motivated the registry.
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
        # The name must be DECLARED by the operation, not merely present on the
        # command line. This is the false negative a whole-command scan allows.
        ("$endCursor outside the query does not count",
         'gh api graphql --paginate -f query='
         "'query($cursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'"
         " ; echo $endCursor", BLOCK),
        ("graphql paginate with $endCursor allowed",
         'gh api graphql --paginate -f query='
         "'query($endCursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", ALLOW),
        # ... and the named-operation form is a real declaration, so it must
        # pass. Without this, tightening the regex to `query(` would silently
        # start blocking a correct command.
        ("named operation declaring $endCursor allowed",
         'gh api graphql --paginate -f query='
         "'query Board($endCursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", ALLOW),
        ("graphql single-shot allowed", "gh api graphql -f query='query{viewer{login}}'", ALLOW),
        ("REST paginate allowed",
         "gh api --paginate repos/FreeForCharity/FFC-Cloudflare-Automation/issues/719/comments",
         ALLOW),
    ]),
]

# Ordinary commands that belong to no rule: they assert the hook stays out of
# the way, so there is no rule for them to prove both directions of.
GENERAL_ALLOWS = [
    ("normal git status", "git status"),
    ("normal gh run", "gh workflow run 8-whmcs-export-products.yml --ref main"),
]


# --- deriving the refusal sites from guard_bash.py's own source -------------
#
# AC4 of #1027: this must NOT be a count. Hard-coding "there are 9 block()
# sites" or "len(RULES) == 10" goes red the moment a rule is added and teaches
# the next author to bump a constant instead of writing a case. So the sites
# come from the AST, and every one of them has to be claimed by a registered
# signature.


def _literal_fragments(node):
    """Every string literal that contributes to `node`'s value.

    Walks the expression, so an f-string (`block(f"...: {desc}.")`) and a
    concatenation (`block("..." + ", ".join(findings) + "...")`) both yield
    their literal parts. A bare Name yields nothing, which is the signal that
    the reason is produced somewhere else.
    """
    if node is None:
        return []
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def block_sites(path=GUARD_BASH):
    """Every distinct refusal message guard_bash.py can emit.

    Returns [(label, [literal fragments])]. A site whose fragments are EMPTY is
    one this function could not follow; it is returned rather than dropped so
    the caller fails loudly. Silently skipping an unreadable site would make
    the coverage check pass by not looking, which is the failure mode this
    whole issue is about.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    # `for reason in (f(cmd), g(cmd)): ... block(reason)` -- the message is
    # produced by f and g, so the block() call site is not where the rule
    # lives. Two distinct rules funnel through that one call.
    loop_sources = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            called = [c.func.id for c in ast.walk(node.iter)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
            if called:
                loop_sources[node.target.id] = called

    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "block"):
            continue
        arg = node.args[0] if node.args else None
        frags = _literal_fragments(arg)
        if frags:
            sites.append((f"guard_bash.py:{node.lineno} block(...)", frags))
            continue
        if isinstance(arg, ast.Name) and arg.id in loop_sources:
            for fname in loop_sources[arg.id]:
                fn = funcs.get(fname)
                if fn is None:
                    sites.append((f"guard_bash.py:{node.lineno} -> {fname}() not found", []))
                    continue
                for ret in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
                    if ret.value is None or (isinstance(ret.value, ast.Constant)
                                             and ret.value.value is None):
                        continue  # `return None` is "rule did not fire", not a message
                    sites.append((f"guard_bash.py:{ret.lineno} return in {fname}()",
                                  _literal_fragments(ret.value)))
            continue
        sites.append((f"guard_bash.py:{node.lineno} block({ast.dump(arg)[:40]}...)", []))
    return sites


# --- meta-tests over the registry -------------------------------------------


def test_rule_polarity():
    """AC2: every rule needs a case in BOTH directions.

    A rule exercised only where it already passes is green and worthless --
    #940's $endCursor rule was block/block/allow and all three passed against
    an implementation that got the rule wrong.
    """
    problems = []
    for rule in RULES:
        if not rule.blocking_cases():
            problems.append(f"rule '{rule.id}' has NO case that must BLOCK "
                            f"-- add a command this rule is supposed to refuse")
        if not rule.allowing_cases() and not rule.no_allow_case:
            problems.append(f"rule '{rule.id}' has NO case that must be ALLOWED "
                            f"-- add a near-miss that must pass, or set "
                            f"no_allow_case='<why>' on the Rule")
    record("every rule has both a blocking and an allowing case",
           not problems, "\n".join(problems))


def test_block_attribution():
    """AC3 (runtime half): a case must block for ITS OWN rule's reason.

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
                    f"rule '{rule.id}' case '{name}' blocked, but not for this rule: "
                    f"expected reason containing {rule.signature!r}\n"
                    f"  emitted: {' '.join(err.split())[:160]}")
    record("every blocking case blocks for its own rule's reason",
           not problems, "\n".join(problems))


def test_block_site_coverage():
    """AC3 (source half): every refusal guard_bash.py can emit has a case.

    Derived from the AST, not from a count (AC4): adding a rule to
    guard_bash.py with no registered case turns this red and names the line.
    """
    sites = block_sites()
    problems = []

    unresolved = [label for label, frags in sites if not frags]
    for label in unresolved:
        problems.append(f"{label}: cannot read this refusal's message, so it cannot be "
                        f"shown to be tested -- teach block_sites() this shape")

    for label, frags in sites:
        if not frags:
            continue
        if not any(r.signature in f for r in RULES for f in frags):
            problems.append(f"{label}: no registered rule claims this refusal\n"
                            f"  emits: {frags[0][:110]!r}\n"
                            f"  add a Rule to RULES whose signature is a slice of that text")

    for rule in RULES:
        if not any(rule.signature in f for _, frags in sites for f in frags):
            problems.append(f"rule '{rule.id}' signature {rule.signature!r} matches no "
                            f"block() site in guard_bash.py -- the rule was removed or "
                            f"its message was reworded")

    record(f"every block() site in guard_bash.py is covered ({len(sites)} sites derived)",
           not problems, "\n".join(problems))


def main():
    print("guard_bash (by rule):")
    for rule in RULES:
        for name, cmd, expect in rule.cases:
            proc = check(f"{rule.id} / {name}", "guard_bash.py", bash(cmd), expect)
            if proc.returncode == 2:
                rule.emitted.append((name, proc.stderr))

    print("guard_bash (general, rule-independent):")
    for name, cmd in GENERAL_ALLOWS:
        check(name, "guard_bash.py", bash(cmd), ALLOW)

    print("guard_bash meta-tests (over the rule registry):")
    test_rule_polarity()
    test_block_attribution()
    test_block_site_coverage()

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
