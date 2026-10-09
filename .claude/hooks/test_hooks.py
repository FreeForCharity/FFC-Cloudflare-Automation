#!/usr/bin/env python3
"""Self-tests for the FFC AI-agent hooks.

Runs each hook as a subprocess with crafted stdin and asserts the exit code
(2 = blocked, 0 = allowed). Run locally or in CI:  python3 .claude/hooks/test_hooks.py
"""

import ast
import glob
import json
import os
import re
import subprocess
import sys
import warnings

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


# A hook's contract is rc 2 (blocked) or 0 (allowed). Anything else -- a
# syntax error, a failed import, a 127 -- is a BROKEN HOOK, and `blocked =
# rc == 2` alone reads every one of those as "allowed", so an ALLOW case goes
# green against a hook that cannot run at all. That is the same shape as the
# repo's standing rule that a test asserting a non-zero exit code must also
# assert on the output: a harness that cannot start is otherwise
# indistinguishable from a rule correctly staying silent. Raised repeatedly by
# Copilot review on this PR, and correct.
VALID_RC = (0, 2)


def check(name, script, payload, expect_block):
    global PASS, FAIL
    rc, _ = run(script, payload)
    blocked = rc == 2
    ok = blocked == expect_block and rc in VALID_RC
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    status = "ok  " if ok else "FAIL"
    want = "block" if expect_block else "allow"
    got = "block" if blocked else f"allow(rc={rc})"
    print(f"  [{status}] {name}: want={want} got={got}")


# An advisory rule exits 0 exactly like a silent allow, so `check` above cannot
# tell "warned" from "said nothing" -- and a warning nobody can assert on is a
# warning that can rot without any test noticing. Assert on the marker instead.
WARN_MARKER = "FFC-HOOK-WARNING"

# Every warn reason opens with its rule's `[#NNN]` tag, so the set of tags in
# stderr is the set of warn rules that fired.
WARN_TAG_RE = re.compile(r"\[#\d+\]")


def warn_tags(err):
    return set(WARN_TAG_RE.findall(err))


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
    record(f"{name}: want={want} got={got}", blocked == expect_block and rc in VALID_RC)
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
        ("force-push -f main", "git push -f origin main", BLOCK),
        ("force-with-lease master", "git push --force-with-lease origin master", BLOCK),
        ("commit then force-push main via &&", "git commit -m x && git push -f origin main", BLOCK),
        # git's option parser bundles short options, so these force-push too.
        # Measured: `-fq`/`-qf` reach the remote lookup, `-qZ` is rejected as an
        # unknown switch -- the cluster really is being split. Copilot on #1310.
        ("force-push main via bundled -fq", "git push -fq origin main", BLOCK),
        ("force-push main via bundled -qf", "git push -qf origin main", BLOCK),
        # Heredoc bodies are analysed, not skipped: `bash <<EOF` really does run
        # what is inside one, so this must stay the direction the rule fails in.
        ("force-push main inside a heredoc body",
         "bash <<'EOF'\ngit push --force origin main\nEOF", BLOCK),
        ("normal push feature", "git push -u origin claude/ai-agent-hooks-security-bchbh8", ALLOW),
        ("force-push feature/main allowed", "git push --force origin feature/main", ALLOW),
        # force-push-protected decides per SEGMENT (#1309). Judging the whole
        # command made "a push appears somewhere" AND "a force flag appears
        # somewhere" AND "the word main appears somewhere" a violation, which
        # blocked Conductor run 168 three times on an ordinary feature-branch
        # push. Cases A and B are verbatim from the issue; neither can rewrite
        # a protected branch. Note `-F` is not a git-push flag at all -- it is
        # `git commit -F`, `gh api -F` and `grep -F`, which is why the short
        # flag is now matched case-sensitively.
        ("commit -F file then push feature",
         "git commit -q -F msg.txt; git push -q origin feature-x", ALLOW),
        # Widening the short flag to a bundled cluster must not undo that: the
        # `f` inside the cluster is lowercase-only, so `-qF` stays clear.
        ("commit -qF file naming main then push feature",
         "git commit -qF main-notes.txt; git push -q origin feature-x", ALLOW),
        ("heredoc commit message naming main then push feature",
         "git commit -q -F - <<'EOF'\nfix: only for CI runs on main\nEOF\n"
         "git push -q origin feature-x", ALLOW),
        ("push feature then gh api -f body naming main",
         "git push -q origin feature-x; "
         "gh api repos/o/r/pulls/1/comments -f body='... main ...'", ALLOW),
        ("push feature then echo main via &&", "git push origin feature-x && echo main", ALLOW),
        # A later pipeline stage supplies flags and words the push never saw.
        # `_echo_segments` keeps a pipeline whole (rule 3 needs that), so rule 2
        # splits on `|` itself. The lowercase row is Copilot's on #1310 -- the
        # uppercase one is cleared by the case-sensitive flag match as well.
        ("push feature piped through grep -F main",
         "git push origin feature-x | grep -F main", ALLOW),
        ("push feature piped through grep -f naming main",
         "git push origin feature-x | grep -f patterns.txt main", ALLOW),
        # Splitting on `|` must not open a bypass: a real force-push carries
        # its verb, flag and refspec in its own stage, wherever it sits.
        ("force-push main as the last pipeline stage",
         "echo x | git push --force origin main", BLOCK),
        ("force-push main as the first pipeline stage",
         "git push --force origin main | tee push.log", BLOCK),
        # ...but only where the `|` is really a stage boundary. A `|` inside a
        # command substitution belongs to a DIFFERENT command whose output is
        # one word of this one, so splitting there cut a single force-push in
        # two -- verb and flag in one computed stage, refspec in the next --
        # and all four of these were ALLOWED at 46adfe3 while `main` blocked
        # every one. A permissive miss, so they are the rows that matter.
        # Copilot on #1310.
        ("force-push main with a pipe inside $()",
         "git push --force $(git remote | head -1) main", BLOCK),
        ("force-push main with a pipe inside $() in the refspec",
         "git push --force origin $(cat b.txt | tr -d '\\n'):main", BLOCK),
        ("force-push main with a pipe inside backticks",
         "git push --force `git remote | head -1` main", BLOCK),
        # The escaped pipe carries a REFSPEC, not a bare `\|` argument. The
        # first spelling of this row was `git push --force origin \| main`,
        # which is bash-valid but **git-invalid**: `|` is not a local ref, so
        # git aborts the whole push on `error: src refspec | does not match
        # any` and `main` never moves -- measured against a local bare remote,
        # twice, including after a fresh commit. A row labelled "real
        # force-push" that no git will execute pins nothing, which is the same
        # empty-vector class as the `git -c a=b` and `--config-env a=B` rows
        # earlier in this file. Copilot on #1336, third instance.
        #
        # `feat|x` is a legal branch name (`git check-ref-format --branch`
        # rc=0 -- `|` is absent from git's forbidden set), so this command is
        # executable AND protected-targeting: measured, it prints
        # `+ 822891d...3c8627e feat|x -> main (forced update)` and the remote's
        # main really is overwritten. The control that makes that mean
        # something is the same refspec WITHOUT --force, which git refuses
        # (`! [rejected] ... (non-fast-forward)`) -- so the force flag is
        # load-bearing here rather than decorative.
        #
        # Discrimination is unchanged by the repair: neutering the splitter's
        # backslash escape on a copy of the whole hooks directory (anchor
        # asserted present, mutant compiled before its exit code was read)
        # flips this row BLOCK -> ALLOW, the permissive direction, and moves
        # none of the three controls beside it.
        ("force-push main with an escaped pipe inside the refspec",
         "git push --force origin feat\\|x:main", BLOCK),
        # `|&` is bash's "pipe stdout and stderr", and `_pipe_stages` matches it
        # ahead of a bare `|` so the `&` is consumed with the bar rather than
        # left to start the next stage. The file had ZERO `|&` cases before
        # these six, which is the gap Copilot reported on #1336 -- an operator
        # the splitter names explicitly and no case exercised.
        #
        # What these rows do NOT establish, measured rather than assumed: that
        # the `"|&"` entry in that ops tuple is load-bearing for THIS rule. `|`
        # is a PREFIX of `|&`, so an ops list of `("|",)` breaks the line at the
        # same index and differs only by a leading `&` on the next stage --
        # which none of rule 2's three conditions look at. A mutant dropping
        # `"|&"` agrees with the real guard on all six verdicts below. So they
        # pin the boundary (and would catch a rewrite that stopped splitting
        # there, or split only on a bare `|` followed by a non-`&`), and they
        # do not discriminate the token. Every one is `bash -n` valid.
        ("force-push main as the first |& stage",
         "git push --force origin main |& tee push.log", BLOCK),
        ("force-push main as the last |& stage",
         "echo x |& git push --force origin main", BLOCK),
        ("push feature |& grep -f naming main",
         "git push origin feature-x |& grep -f patterns.txt main", ALLOW),
        ("push feature |& grep -F main",
         "git push origin feature-x |& grep -F main", ALLOW),
        ("push feature |& tee, nothing protected named",
         "git push origin feature-x |& tee push.log", ALLOW),
        # ...and a `|&` inside a substitution is no more a boundary than a bare
        # `|` is, for the same reason: the substitution's output is one WORD of
        # this command, so the force-push keeps all three conditions together.
        ("force-push main with a |& inside $()",
         "git push --force $(git remote |& head -1) main", BLOCK),
        # An ODD backtick inside a substitution used to toggle the scanner's
        # backtick flag and carry it out past the closing paren, INVERTING the
        # parity for the rest of the line. The opening backtick of the later,
        # genuine span then read as a close, so its `|` and `&&` were scanned
        # as top level and tore a real force-push into two stages. 21 of these
        # were ALLOWED before the `not closers` guard.
        #
        # These three vectors are deliberately **bash-invalid** -- `bash -n`
        # rejects the unbalanced backtick with `unexpected EOF while looking
        # for matching ``'` -- so they pin the PARSER, not a reachable bypass.
        # A sweep of 132 bash-valid vectors of this shape found 0 the old code
        # allowed, because bash makes unquoted backticks pair. Kept anyway: a
        # guard must fail closed on malformed input too, and these are the only
        # rows that exercise the inversion at all. Do not read the 21 as a
        # severity figure -- the first version of this comment invited exactly
        # that, and Copilot caught it on #1336.
        ("force-push main after an odd backtick leaked out of $()",
         "echo $(echo ` ) ; git push --force `git remote | head -1` main", BLOCK),
        ("force-push main after an odd backtick, && in the later span",
         "echo $(echo ` ) ; git push -f `cd /repo && git remote` main", BLOCK),
        ("force-push main after an odd backtick, ; in the later span",
         "echo $(echo ` ) ; git -C /repo push --force `cd /repo; git remote` main",
         BLOCK),
        # ...and the ALLOW half, which is what stops the lazy fix of never
        # toggling the flag at all. A TOP-LEVEL backtick span is still a real
        # span, so an operator inside one is still not a boundary, and a
        # feature push followed by an unrelated command naming `main` must
        # stay allowed either side of it.
        ("push feature, then && a command naming main, after a closed $()",
         "echo $(echo hi) ; git push origin feature-x && grep -f patterns.txt main",
         ALLOW),
        ("push feature through a top-level backtick span containing a pipe",
         "git push origin `git branch --show-current | tr -d x`", ALLOW),
        ("push feature after a substitution holding an EVEN backtick pair",
         "echo $(echo `date`) ; git push origin feature-x | grep -f patterns.txt main",
         ALLOW),
        # A bare `(` inside a PARAMETER expansion is literal text -- `${x:-foo(}`
        # is valid bash and its paren need not balance. Tracking it as a nested
        # span made the `}` that really ends the expansion pair with the `(`,
        # so `closers` never emptied and every later operator on the line went
        # invisible. That is #1309's false positive returning by another door,
        # so these are the rows that matter. Copilot on #1336.
        ("push feature, then && a command naming main, after ${} with a bare (",
         "echo ${x:-foo(} ; git push origin feature-x && grep -f patterns.txt main",
         ALLOW),
        ("push feature piped to grep naming main, after ${} with a bare {",
         "echo ${x:-foo{} ; git push origin feature-x | grep -f patterns.txt main",
         ALLOW),
        # ...and the BLOCK half, which stops the lazy fix of never tracking
        # bare grouping at all. Inside a COMMAND substitution `( )` really is
        # syntactic, so `$( (a) && b )` must not close its span early and the
        # force-push it wraps must still be caught.
        ("force-push main after a ${} carrying a bare (",
         "echo ${x:-foo(} ; git push --force origin main", BLOCK),
        ("force-push main with a grouped subshell inside $()",
         "git push --force $( (echo origin) && cat r.txt ) main", BLOCK),
        # A `)` inside BACKTICKS inside `$(...)`. bash accepts this unquoted
        # (checked with `bash -n`) and `_strip_quoted` leaves the paren intact,
        # so the scanner really does see it. While backticks inside a
        # substitution went untracked, that `)` matched the outer `$(`'s closer
        # and emptied the stack early. It produced no bypass -- the next
        # backtick turned suppression back on -- but it did block the benign
        # pipeline below. Both polarities pinned so neither the premature close
        # nor the fix for it can regress unseen. Copilot on #1336.
        ("push feature, ) inside backticks inside $(), then pipe to grep main",
         "git push origin $(echo `printf a)b`) | grep -f p.txt main", ALLOW),
        ("push feature, ) inside backticks and an operator inside $()",
         "git push origin $(echo `printf a)b` && true) feature-x", ALLOW),
        ("force-push main, ) inside backticks and an operator inside $()",
         "git push --force origin $(echo `printf a)b` && true) main", BLOCK),
        ("force-push main, ) inside backticks supplying the remote",
         "git push --force $(echo `printf a)b` ; true) main", BLOCK),
        # Same defect one level UP, in `_split_on_logical`, which tears the
        # statement into segments before `_pipe_stages` ever runs. An `&&` or
        # `||` inside a substitution is not a segment boundary either, and all
        # six of these were ALLOWED at 533b1ea -- with `_pipe_stages` already
        # fixed -- while `main` blocked every one. Permissive, so they matter.
        ("force-push main with && inside $()",
         "git push --force $(cd /repo && git remote) main", BLOCK),
        ("force-push main with && inside $() guarding a test",
         "git push --force $(test -d .git && echo origin) main", BLOCK),
        ("force-push main with && inside backticks",
         "git push --force `cd /repo && git remote` main", BLOCK),
        ("force-push main with && and a nested pipeline inside $()",
         "git push --force $(cd /repo && (echo origin | cat)) main", BLOCK),
        ("force-push main with || inside $()",
         "git push --force $(cd /repo || echo origin) main", BLOCK),
        ("force-push main with && inside $() in the refspec",
         "git push --force origin $(cd /repo && cat b.txt):main", BLOCK),
        # Same defect one level up AGAIN, in `_split_statements` -- the
        # outermost of the three splitters, which runs before the other two.
        # A `;` inside a substitution is not a statement boundary, and all
        # three of these were ALLOWED at f28b310, with `_pipe_stages` AND
        # `_split_on_logical` both already fixed, while `main` blocked every
        # one. Permissive, so they are the rows that matter.
        ("force-push main with ; inside $()",
         "git push --force $(cd /repo; git remote) main", BLOCK),
        ("force-push main with ; inside backticks",
         "git push --force `cd /repo; git remote` main", BLOCK),
        ("force-push main with ; inside $() in the refspec",
         "git push --force origin $(cd /repo; cat b.txt):main", BLOCK),
        # The ALLOW half for `;`, which stops the lazy fix of simply not
        # splitting on it. A TOP-LEVEL `;` is still a real statement boundary,
        # so a feature push followed by an unrelated command naming `main`
        # must stay allowed.
        ("push feature, then ; a command naming main",
         "git push origin feature-x; grep -f patterns.txt main", ALLOW),
        ("push feature through a substitution containing ;",
         "git push origin $(cd /repo; git branch --show-current)", ALLOW),
        # ...and the ALLOW half, which is what stops the lazy fix of simply not
        # splitting on `&&`. A TOP-LEVEL `&&` is still a real boundary, so a
        # feature-branch push followed by an unrelated command naming `main`
        # must stay allowed -- that is #1309, the false positive this whole
        # stack exists to remove.
        ("push feature, then && a command naming main",
         "git push origin feature-x && grep -f patterns.txt main", ALLOW),
        ("push feature through a substitution containing &&",
         "git push origin $(cd /repo && git branch --show-current)", ALLOW),
        # The opposite error -- a substitution that swallows the rest of the
        # line -- would re-break the false positive the stage split exists for.
        # `$(a) | b` must still split; only an UNCLOSED span may run on.
        ("push feature through a substitution, then grep -f naming main",
         "git push origin $(git branch --show-current) | grep -f patterns.txt main", ALLOW),
        ("push feature after a substitution containing its own pipeline",
         "echo $( (git log --oneline) | head -1 ) | git push -q origin feature-x", ALLOW),
        # git's GLOBAL options may precede the subcommand, so the verb is not
        # always the word after `git` (#1311). Each of these is a working
        # force-push spelling that the old `\bgit\s+push\b` never saw -- the
        # `-c` form especially, which is what tooling and CI snippets emit.
        ("force-push main via git -c", "git -c protocol.version=2 push --force origin main", BLOCK),
        ("force-push main via git --no-pager", "git --no-pager push --force origin main", BLOCK),
        ("force-push main via git -C", "git -C /repo push --force origin main", BLOCK),
        ("force-push master via git -c and -C",
         "git -c core.pager=cat -C /repo push -f origin master", BLOCK),
        # `-c`/`-C` are not the only options taking a SEPARATE value word, and
        # the long ones were missed on the first pass (Conductor run 170 on
        # #1312). Each verified against git 2.43.0 as a running command, with
        # a `--bogus-opt x` control exiting 129.
        ("force-push main via git --work-tree",
         "git --work-tree /repo push -f origin main", BLOCK),
        ("force-push main via git --namespace",
         "git --namespace x push --force origin main", BLOCK),
        # `--config-env` takes `section.key=ENVVAR`, and BOTH halves have to be
        # real: this row read `a=B` until Copilot caught it on #1336. Measured,
        # the two failure causes are separable and only one is about the key --
        # `a=B` reports the missing env var `B` first, a VALID key with a
        # missing var (`core.pager=PAGER_ENV`) fails identically, and exporting
        # `B` does not help, while `a=HOME` still gives
        # `error: key does not contain a section: a`. So the key is the real
        # defect. `a.b=HOME` is the spelling `guard_bash.py` documents as
        # measured-working, and it is what this row uses now.
        ("force-push main via git --config-env",
         "git --config-env a.b=HOME push --force origin main", BLOCK),
        # `--git-dir <path>` was already blocked, but only by accident: the
        # path ends `.git push`, and `\bgit\s+push\b` matched INSIDE it. Pin
        # it now that the rule itself covers the form, so a future narrowing
        # cannot be hidden by that coincidence.
        ("force-push main via git --git-dir with a separate arg",
         "git --git-dir /repo/.git push --force origin main", BLOCK),
        # `\bgit\s` wants whitespace right after `git`; the Conductor runs on
        # Windows, where `git.exe push` is an ordinary spelling.
        ("force-push main via git.exe", "git.exe push --force origin main", BLOCK),
        # `--exec-path` splits three ways, and only one of them can push.
        # Measured on git 2.43.0 in a repo with no remote, against a
        # `git push origin main` control:
        #
        #   bare, then the verb        -> 0, prints /usr/lib/git-core, NO push
        #   bare + a separate path     -> 0, prints it too; the word is ignored
        #   `=<path>`, then the verb   -> 1, "src refspec main does not match
        #                                 any" -- identical to the control, so
        #                                 push really ran
        #
        # The control is what carries that last row: a repo with no remote was
        # supposed to make a real push fail by naming `origin`, but git rejects
        # the refspec first, so the error looks nothing like a push until you
        # see the plain `git push` control produce the same line.
        #
        # So the ALLOW row is the fix (`--exec-path` now takes the value slot,
        # so `push` stops being the verb) and the `=` row must stay BLOCK
        # because it is a real force-push. The separate-path row is the one to
        # read carefully: it was ALLOWED before this change, because the generic
        # option alternative matched `--exec-path` and `/usr/lib/git-core` is
        # not option-shaped, so the scan stopped there and never reached `push`.
        # It BLOCKS now -- a NEW over-block, taken on purpose, since an older
        # git that consumed the path and ran on would make that a real
        # force-push. Stated because the first draft of this comment said it
        # "stays BLOCK", and the mutation below is what proved otherwise.
        #
        # Discrimination, measured: dropping `exec-path` from
        # GIT_SEPARATE_ARG_OPT on a copy flips exactly two of these rows, in
        # OPPOSITE directions -- the ALLOW row reddens (`want=allow got=block`,
        # the false positive returning) and the separate-path row loosens
        # (`want=block got=allow`). The `=` row does not move, because it
        # matches through the generic alternative either way. A pair that fails
        # both ways is what makes this entry's behaviour pinned rather than
        # merely covered.
        # Copilot on #1336; its finding was right and its stated mechanism was
        # not -- git does not take `push` as the option's value, it exits before
        # reading it.
        ("bare git --exec-path cannot push, so not a force-push",
         "git --exec-path push --force origin main", ALLOW),
        ("force-push main via git --exec-path=<path>",
         "git --exec-path=/usr/lib/git-core push --force origin main", BLOCK),
        ("git --exec-path with a separate path, over-blocked on purpose",
         "git --exec-path /usr/lib/git-core push --force origin main", BLOCK),
        # ...and the long options must not arm the rule either. The second is
        # run 170's row: `log` is not option-shaped, so it ends the scan and
        # the `push` after `--grep` is never read as the verb.
        ("normal push feature via git --work-tree",
         "git --work-tree /repo push --force origin feature-x", ALLOW),
        ("git log --grep push naming main", "git log --grep push main", ALLOW),
        # ...and the widening must not arm the rule off a word that is not the
        # verb. The first is the ordinary reason to write `git -c` at all; the
        # second is `-c`'s ARGUMENT beginning with `push`, which an earlier
        # draft read as the subcommand by backtracking; the third proves a
        # quoted `push` still cannot supply it.
        #
        # The key must be `section.key=value`. This row read `-c a=b` until
        # Copilot caught it on #1336: `git -c a=b status` exits 128 with
        # `error: key does not contain a section: a`, so the row was asserting
        # that the guard leaves alone a command git itself refuses -- which says
        # nothing about REAL `git -c` usage, the whole point of the case. Note
        # `git -c a=b --version` exits 0, because `--version` answers before
        # config is parsed; check such a key with a subcommand that reads it.
        ("normal push feature via git -c",
         "git -c core.pager=cat push --force origin feature-x", ALLOW),
        ("git -c push.default then an unrelated main and force",
         "git -c push.default=simple config --list && echo main --force", ALLOW),
        ("commit message naming push, force and main",
         'git commit --amend -m "ready to push --force origin main"', ALLOW),
        # Keeps the case-sensitive short flag pinned now that pipe splitting
        # clears the `grep -F` row on its own: prose inside a heredoc body is
        # analysed (bodies are deliberately not skipped), and this line holds
        # all three halves in ONE stage. Only `-F != -f` clears it.
        ("heredoc prose naming git push -F and main",
         "gh pr create -F - <<'EOF'\nwe force-push with git push -F only on main\nEOF", ALLOW),
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
        # From the flat block the union merge left in main(): these four
        # were the only cases there that the rule did not already hold.
        # The other 39 were verbatim duplicates of cases above and were
        # dropped rather than re-homed -- two copies of one case is waste
        # and an edit hazard, and no guard here can see it (the corpus
        # check compares SETS, and both copies passed).
        ("bare powershell expansion before && inside -c",
         "pwsh -c '$env:GH_TOKEN && true'", BLOCK),
        ("bare powershell expansion as a middle chain leg",
         "pwsh -c 'true && $env:GH_TOKEN && true'", BLOCK),
        ("bare powershell expansion before || inside -c",
         "pwsh -c '$env:GH_TOKEN || true'", BLOCK),
        ("secret argument before && allowed",
         "pwsh -c './x.ps1 -Token $env:GH_TOKEN && true'", ALLOW),
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
        # A redirect TARGET is a shell path, not the endpoint -- must not fire.
        # Conductor run 161 was blocked three times on exactly this shape.
        ("gh api redirect to absolute path allowed",
         "gh api repos/o/r/contents/x > /c/tmp/z.yaml", ALLOW),
        ("gh api stderr redirect to absolute path allowed",
         "gh api repos/o/r/pulls 2> /tmp/err.txt", ALLOW),
        ("gh api stdin from absolute path allowed",
         "gh api graphql --input < /c/tmp/q.json", ALLOW),
        # ...but a leading-slash endpoint BEFORE the redirect still blocks.
        ("gh api leading slash then redirect",
         "gh api /markdown > /tmp/out.html", BLOCK),
        # A stop character INSIDE QUOTES is jq/header data, not a shell operator.
        # A quote-blind span ends on it and never reaches the endpoint that
        # follows, so these are the bypasses the `<>` stop would otherwise open.
        # Measured on the quote-blind span: the first three were all ALLOWED.
        ("gh api jq gt then leading slash endpoint",
         "gh api --jq '.a > 1' /markdown", BLOCK),
        ("gh api jq lt then leading slash endpoint",
         "gh api --jq '.a < 1' /markdown", BLOCK),
        # `|` has been a stop character since rule 8 was written, so this one
        # is an OLDER bypass than the `<>` pair -- allowed before either change.
        ("gh api jq pipe then leading slash endpoint",
         "gh api --jq '.workflow_runs[] | select(.id > 5)' /repos/o/r/actions/runs", BLOCK),
        # Pins behaviour that was already correct: a quoted span with no stop
        # character in it never truncated the match.
        ("gh api quoted header then leading slash endpoint",
         "gh api -H 'Accept: application/vnd.github+json' /markdown", BLOCK),
        # ...and quoting a stop character must not start blocking a correct
        # call. This is the case that catches the obvious wrong fix.
        ("gh api jq comparison without endpoint allowed",
         "gh api repos/o/r/issues --jq '.[] | select(.number > 5)'", ALLOW),
        # An ESCAPED quote is a literal character, not the start of a quoted
        # span. An escape-blind stripper reads it as an unterminated quote and
        # blanks the rest of the command -- endpoint included -- so the call
        # sails through with nothing left to object to. Found in review of
        # #1313 (Conductor run 171); these three block on `main` and regressed
        # when the span first became quote-aware.
        ("gh api escaped single quote then endpoint",
         "gh api -f body=it\\'s /markdown", BLOCK),
        ("gh api escaped double quote then endpoint",
         'gh api -f body=a\\"b /markdown', BLOCK),
        # The realistic one: `\"` inside a double-quoted span does not close it.
        ("gh api escaped double quote inside double quotes then endpoint",
         'gh api -f body="a\\" > x" /markdown', BLOCK),
        # Controls for the opposite error -- an over-eager stripper. Neither of
        # these involves an escape, and both were already correct.
        ("gh api double-quoted jq then leading slash endpoint",
         'gh api --jq ".a > 1" /markdown', BLOCK),
        ("gh api apostrophe inside double quotes then endpoint",
         "gh api -f body=\"it's\" /markdown", BLOCK),
        # Reading quoted text as data opens exactly one hole that is NOT data:
        # a `-c` payload, whose quotes are how the command is passed. Reported
        # in review of #1313 and reproduced before fixing -- all four of these
        # block on `main` and were ALLOWED once the span became quote-aware.
        # The nested shell is MSYS bash too, so the inner call is mangled the
        # same way; blocking is right on the merits, not only for the guard.
        ("bash -c double-quoted endpoint",
         'bash -c "gh api /markdown"', BLOCK),
        ("bash -c single-quoted endpoint",
         "bash -c 'gh api /markdown'", BLOCK),
        ("sh -c single-quoted endpoint",
         "sh -c 'gh api /markdown'", BLOCK),
        # A cluster CONTAINING `c` takes the next word, which is how the shell
        # reads it -- `-lc` must not slip past a scan looking only for `-c`.
        ("bash -lc clustered flag then endpoint",
         'bash -lc "gh api /markdown"', BLOCK),
        # Nesting is scanned too. The first fix stopped at one level and this
        # shape was going to be pinned as an accepted limitation; a limitation
        # that can be written in one line is a bypass with a docstring.
        ("nested bash -c endpoint",
         'bash -c "bash -c \'gh api /markdown\'"', BLOCK),
        # Controls for the opposite error. The whole point of the quote-aware
        # span is that quoted text which is DATA stays allowed, so unwrapping
        # `-c` payloads must not drag those back into blocking.
        ("bash -c slash-less endpoint allowed",
         'bash -c "gh api markdown"', ALLOW),
        ("the endpoint quoted as prose in a comment body allowed",
         "gh issue comment 1 -f body='do not write gh api /markdown'", ALLOW),
        # An ESCAPED leading slash is still a leading slash. Verified with a
        # `gh` shim on PATH rather than by reading the grammar: all three of
        # these reach gh as `/markdown`, so MSYS mangles them exactly as the
        # bare form does. Allowed on `main` too -- the lookbehind wants
        # whitespace before the `/` and a backslash is not whitespace -- so
        # this one is older than the quote-aware span, not a regression of it.
        ("escaped slash unquoted",
         r"gh api \/markdown", BLOCK),
        ("escaped slash inside a -c payload",
         r"bash -c 'gh api \/markdown'", BLOCK),
        ("escaped slash inside a double-quoted -c payload",
         r'bash -c "gh api \/markdown"', BLOCK),
        # ...and the over-block direction, which is why the escape rule is
        # state-aware. Inside double quotes bash PRESERVES the backslash, so
        # the shim shows gh receiving a literal `\/markdown` as field data --
        # not an endpoint. Blanking only the backslash regardless of state
        # would expose a `/` after a blank and block a correct call.
        ("escaped slash as double-quoted field data allowed",
         r'gh api repos/o/r/issues -f body="see \/markdown"', ALLOW),
        ("escaped slash as single-quoted field data allowed",
         r"gh api repos/o/r/issues -f body='see \/markdown'", ALLOW),
        # Two `-c` spellings the first version of the payload scanner missed,
        # both of which block on `main` and so were regressions of it.
        ("bash --norc -c endpoint",
         "bash --norc -c 'gh api /markdown'", BLOCK),
        ("bash --noprofile --norc -c endpoint",
         "bash --noprofile --norc -c 'gh api /markdown'", BLOCK),
        # `$'...'` is a quoting form, so the `$` comes off before the pair.
        ("bash -c ANSI-C quoted endpoint",
         "bash -c $'gh api /markdown'", BLOCK),
        ("sh -c ANSI-C quoted endpoint",
         "sh -c $'gh api /markdown'", BLOCK),
        # The shell is often not the FIRST word of its command. A simple
        # command is `[assignments] [redirections] word...`, and something may
        # exec the shell instead of being it. Reported on #1313; all four block
        # on `main` and all four reach gh with `/markdown` via the shim, so all
        # four were regressions of the command-position anchor.
        ("assignment before the shell",
         "VAR=1 bash -c 'gh api /markdown'", BLOCK),
        ("two assignments before the shell",
         "A=1 B=2 bash -c 'gh api /markdown'", BLOCK),
        ("redirection before the shell",
         "> /tmp/out bash -c 'gh api /markdown'", BLOCK),
        ("env(1) execs the shell",
         "env VAR=1 bash -c 'gh api /markdown'", BLOCK),
        # These two were NOT reported. They are here because the fix is a
        # quoted-ness test rather than a list of prefixes: had it been a list,
        # `env` would have been fixed and these would have been the next
        # round's finding. They pass without being enumerated anywhere.
        ("nohup execs the shell",
         "nohup bash -c 'gh api /markdown'", BLOCK),
        ("timeout execs the shell",
         "timeout 5 bash -c 'gh api /markdown'", BLOCK),
        # A shell is usually not spelled `bash` on this host. The first
        # wrapper scanner matched a bare name only, so every path-qualified
        # spelling walked past it -- reported on #1313, and `/bin/bash -c` is
        # confirmed by the `gh` shim to reach gh with `/markdown`.
        ("absolute path shell",
         '/bin/bash -c "gh api /markdown"', BLOCK),
        ("quoted absolute Windows path shell",
         '"/c/Program Files/Git/bin/bash.exe" -c "gh api /markdown"', BLOCK),
        ("Windows path shell with an escaped space",
         r'/c/Program\ Files/Git/bin/bash.exe -c "gh api /markdown"', BLOCK),
        ("bare bash.exe",
         'bash.exe -c "gh api /markdown"', BLOCK),
        # A word also starts after a SEPARATOR, and dropping the space after
        # one hid the wrapper from a scanner that only looked after
        # whitespace. Copilot reported the `;` form on #1313 round 10; the
        # other five are the same shape and were not reported. All six reach
        # gh with `/markdown` under the shim, and `main` blocks every one, so
        # these are regression pins rather than new coverage.
        ("semicolon with no space",
         'echo hi;bash -c "gh api /markdown"', BLOCK),
        ("pipe with no space",
         'echo hi|bash -c "gh api /markdown"', BLOCK),
        ("and-and with no space",
         'echo hi&&bash -c "gh api /markdown"', BLOCK),
        ("or-or with no space",
         'false||bash -c "gh api /markdown"', BLOCK),
        # The subshell form needed a second fix, in `_skip_word`: a `)` with
        # nothing open used to be absorbed into the word, so the payload came
        # out as `"gh api /markdown")` -- no longer a matched quote pair, so
        # the unwrap declined it and the endpoint stayed inside a blanked span.
        ("subshell around the wrapper",
         '(bash -c "gh api /markdown")', BLOCK),
        ("bare newline separator",
         'echo hi\nbash -c "gh api /markdown"', BLOCK),
        # ...and the discrimination that keeps that from being a blanket
        # "anything after a `;`" rule. An ESCAPED separator is a literal
        # character: `hi;bash` is one argument to `echo`, no shell is invoked,
        # and the shim confirms gh is never reached. `main` blocks this one, so
        # the row also records a false positive this PR removes.
        ("an escaped separator is data, not a boundary",
         'echo hi\\;bash -c "gh api /markdown"', ALLOW),
        # A backslash-NEWLINE is a line continuation, not an escaped operator:
        # the shell deletes it and runs one command. Rule 8's span stops at a
        # newline, so leaving the newline in the blanked copy hid everything
        # after it. `main` has this hole too -- the only finding on this branch
        # that is not a regression of its own making (#1313 round 11), which is
        # why these rows say `main` was never a safe fallback for it either.
        ("line continuation before the endpoint",
         "gh api \\\n/markdown", BLOCK),
        ("line continuation with a flag between",
         "gh api --paginate \\\n/repos/o/r/issues", BLOCK),
        ("line continuation inside a nested shell",
         "bash -c 'gh api \\\n/markdown'", BLOCK),
        # ...and the two ways that must NOT start blocking. A continued line
        # whose endpoint has no leading slash is an ordinary read call, and a
        # BARE newline really is a statement boundary -- `/bin/true` on its own
        # line is a command, not an endpoint.
        ("line continuation, slash-less endpoint allowed",
         "gh api \\\nrepos/o/r/issues", ALLOW),
        # A QUOTED endpoint was invisible to the span regex on every revision,
        # `main` included: `(?<=\s)` wants whitespace before the slash and finds
        # a quote. Hit live in Conductor run 190 -- two `gh api` calls apart, one
        # worked and one failed `invalid API endpoint`, and rule 8 said nothing
        # about either. All six pre-existing rows in this rule were unquoted,
        # which is why eleven rounds of review never surfaced it.
        ("double-quoted leading-slash endpoint",
         'gh api "/repos/o/r/actions/runs/1/pending_deployments"', BLOCK),
        ("single-quoted leading-slash endpoint",
         "gh api '/repos/o/r/actions/runs/1/pending_deployments'", BLOCK),
        ("quoted leading-slash endpoint with flags before it",
         'gh api -X POST "/repos/o/r/issues/1/comments"', BLOCK),
        # ...and the carve-out that keeps the fix from becoming a false positive.
        # A `?` ANYWHERE in the argument suppresses MSYS path conversion -- even
        # a trailing one with nothing after it -- so these commands genuinely
        # work and must stay allowed. That measurement is INHERITED from
        # Conductor run 190 (argv[1] printed through git-bash); this suite's host
        # is Linux and cannot observe the rewrite, so these rows pin the
        # behaviour the measurement implies rather than the measurement itself.
        ("quoted endpoint with a query string allowed",
         'gh api "/repos/o/r/actions/runs?status=waiting"', ALLOW),
        ("single-quoted endpoint with a query string allowed",
         "gh api '/repos/o/r/actions/runs?status=waiting'", ALLOW),
        ("quoted endpoint with a bare trailing ? allowed",
         'gh api "/repos/o/r/actions/runs?"', ALLOW),
        # Controls for the word-reading half: a quoted FULL URL is not a path, a
        # quoted slash-less route was never mangled, and a slashed path inside a
        # field VALUE is data. Without these, "the endpoint is the first non-flag
        # word" could be satisfied by any quoted token.
        ("quoted full URL allowed",
         'gh api "https://api.github.com/repos/o/r/issues"', ALLOW),
        ("quoted slash-less endpoint allowed",
         'gh api "repos/o/r/issues"', ALLOW),
        ("a slashed path in a quoted field value allowed",
         'gh api repos/o/r/issues -f body="see /markdown"', ALLOW),
        # The other quadrant, and `main` gets it wrong in the opposite direction:
        # a BARE endpoint carrying a `?` is NOT mangled, and `main` blocks it.
        # `gh api /repos/<o>/<r>/actions/runs?status=waiting` is how one lists
        # pending gates -- an ordinary read, refused with advice that does not
        # describe a real failure for that argument. Same harm as the run-161
        # over-block in point 1 of #1313: a guard that fires on correct commands
        # teaches its users to route around it.
        ("bare endpoint with a query string allowed",
         "gh api /repos/o/r/actions/runs?status=waiting", ALLOW),
        ("bare endpoint with a bare trailing ? allowed",
         "gh api /repos/o/r/actions/runs?", ALLOW),
        # The regex fallback has to survive, and this is the row that proves it:
        # the word reader used to tokenize on raw whitespace, so a quoted flag
        # operand containing a space (`-H 'Accept: application/json'`) made it
        # return `application/json` and never reach the endpoint. It is already a
        # BLOCK row above; this is its `?` sibling, which must NOT be exempted by
        # a question mark sitting in an OPERAND rather than in the endpoint.
        ("a ? in a flag operand does not exempt a mangled endpoint",
         "gh api -H 'Accept: application/vnd?x' /markdown", BLOCK),
        # ...and the hole that the "fallback has to survive" framing left open,
        # because it needed BOTH halves to be defeated at once. Round 13,
        # Copilot. The spaced operand derails the word reader as described above,
        # AND quoting the endpoint blanks it out of the text the regex reads, so
        # neither layer sees it. All four one-condition spellings block, which is
        # why twelve rounds of review walked past this. The fix is to split on
        # UNQUOTED whitespace; these three were ALLOWED before it.
        ("gh api spaced header operand then QUOTED leading slash endpoint",
         "gh api -H 'Accept: application/vnd.github+json' \"/markdown\"", BLOCK),
        ("gh api spaced --header operand then quoted leading slash endpoint",
         "gh api --header \"X-Thing: a b\" '/repos/o/r'", BLOCK),
        ("gh api spaced field operand then quoted leading slash endpoint",
         "gh api -X POST -f 'name=a b' \"/repos/o/r/issues\"", BLOCK),
        # The same cause ran in the over-block direction too, and these two are
        # the controls that prove the fix is about tokenization rather than about
        # loosening the rule: a slashed path inside a flag's OPERAND is data, and
        # the whitespace split handed its tail to the loop as the endpoint. Both
        # BLOCKED before the fix -- false positives on correct commands.
        ("a slashed path in a header value is not the endpoint",
         "gh api -H \"X: /markdown\" rate_limit", ALLOW),
        ("a slashed path in a field value is not the endpoint",
         "gh api -f 'body=see /markdown for this' repos/o/r/issues", ALLOW),
        # Round 14, Copilot. The rows above pin the escapes that would HIDE a
        # real endpoint; this is the opposite direction, and it was live: the
        # anchor search ran on an escape-BLIND blanker, so the `\"` here read as
        # closing the span and exposed the prose after it. The shell sees one
        # argument to `echo` and never invokes `gh`. BLOCKED before the fix.
        ("an escaped quote in prose does not expose an embedded gh api",
         'echo "x\\" gh api /markdown"', ALLOW),
        # The discriminator for it: the same prose WITHOUT the escape was always
        # allowed, so only a row carrying the escape can tell the two scanners
        # apart. Without this pair the fix is untestable.
        ("unescaped prose naming gh api stays allowed",
         'echo "x gh api /markdown"', ALLOW),
        # ...and the escape must not start hiding a real endpoint either, which
        # is the direction a careless fix breaks. `_strip_quoted` is in fact
        # STRICTER here than the blanker it replaced: the escape-blind scan took
        # the literal `'` as opening an unterminated span and blanked the
        # endpoint away entirely.
        ("an escaped quote before a real endpoint still blocks",
         "gh api -f body=it\\'s /markdown", BLOCK),
        # Round 15, Copilot. Round 14 made the ANCHOR escape-aware; round 13's
        # TOKENIZER was still escape-blind, so a `\"` inside a flag operand
        # closed the span and split the words wrongly -- and with the endpoint
        # also quoted, the regex fallback was blanked out again. Third round
        # running where the hole needed two conditions at once. ALLOWED before.
        ("escaped quote in a header operand then a quoted endpoint",
         'gh api -H "Accept: a\\" b" "/markdown"', BLOCK),
        ("escaped quote in a field operand then a quoted endpoint",
         'gh api -f "body=a\\" b" "/markdown"', BLOCK),
        # The three one-condition controls that all blocked before the fix, and
        # so could not have caught it. Without these the row above pins a
        # verdict rather than the discrimination that produces it.
        ("same escaped operand with an UNQUOTED endpoint was already blocked",
         'gh api -H "Accept: a\\" b" /markdown', BLOCK),
        ("same quoted endpoint with an UNESCAPED operand was already blocked",
         "gh api -H 'Accept: a' \"/markdown\"", BLOCK),
        # An escaped BACKSLASH is not an escaped quote: the span really does
        # close here, so a tokenizer that swallowed `\\` as an escape of the
        # following `"` would read the rest of the line as quoted and lose the
        # endpoint. This is the row that keeps the fix from over-consuming.
        ("an escaped backslash still closes the span, so the endpoint is found",
         'gh api -H "Accept: a\\\\" "/markdown"', BLOCK),
        # ...and the over-block direction for the same shape: an escaped quote
        # in an operand must not make a SLASH-LESS endpoint start blocking.
        ("escaped operand with a slash-less endpoint stays allowed",
         'gh api -H "Accept: a\\" b" repos/o/r', ALLOW),
        # The escape rule has three branches and the rows above only exercised
        # one. Mutation review caught that: disabling the UNQUOTED branch and
        # making single quotes honour escapes both left the suite green, because
        # rule 8's regex fallback covered for the reader. These two rows quote
        # the endpoint, which blanks the fallback and leaves the tokenizer as the
        # only thing that can decide -- so each branch now has to be right.
        #
        # Unquoted `\'` is one word to the shell. An escape-blind reader opens a
        # span on that `'` and swallows the endpoint.
        ("unquoted escaped quote in an operand, with a quoted endpoint",
         "gh api -f body=a\\'b \"/markdown\"", BLOCK),
        # ...and the opposite branch: inside SINGLE quotes bash treats `\` as a
        # literal, so this `'` really does close. A reader that honoured the
        # escape there would run the span on and lose the endpoint.
        ("a backslash before a closing single quote does not extend the span",
         "gh api -f 'body=a\\' \"/markdown\"", BLOCK),
        # Round 16, Copilot. bash allows a redirection anywhere in a simple
        # command, so these really do call `gh api /markdown`. The reader treated
        # `>` as an early stop and lost the endpoint after it; `<` was worse,
        # falling through to the candidate test and returning "no endpoint". The
        # regex fallback misses them structurally, not by luck -- its span is
        # `[^\n|;&<>]*?`, which halts at the very `>` that moved the endpoint out
        # of reach. All seven ALLOWED before the fix.
        ("redirection before the endpoint, spaced",
         "gh api > /tmp/out /markdown", BLOCK),
        ("input redirection before the endpoint",
         "gh api < /tmp/in /markdown", BLOCK),
        ("fd-numbered redirection before the endpoint",
         "gh api 2> /tmp/err /markdown", BLOCK),
        ("redirection with a GLUED target before the endpoint",
         "gh api >/tmp/out /markdown", BLOCK),
        ("appending redirection before the endpoint",
         "gh api >> /tmp/out /markdown", BLOCK),
        ("fd duplication before the endpoint consumes no operand",
         "gh api 2>&1 /markdown", BLOCK),
        ("both-streams redirection before the endpoint",
         "gh api &> /tmp/out /markdown", BLOCK),
        # The redirect TARGET is a path and must never be read as the endpoint --
        # the direction a fix that merely skipped the operator would break.
        ("a redirect target is not the endpoint",
         "gh api > /tmp/out markdown", ALLOW),
        ("an input redirect target is not the endpoint",
         "gh api < /tmp/in markdown", ALLOW),
        # ...and a pipeline or a second statement must still STOP the scan, which
        # is what the surviving `|`/`&` stop is for. `&&` must not match the
        # redirection pattern.
        ("a pipeline after a slash-less endpoint stays allowed",
         "gh api markdown | tail -3", ALLOW),
        ("a second statement's endpoint is still reached",
         "gh api markdown && gh api /markdown", BLOCK),
        ("a bare newline stays a statement boundary",
         "gh api markdown\n/bin/true", ALLOW),
        # ...and the over-block direction, which the same review raised as a
        # caution. A shell name inside a quoted ARGUMENT is prose carried as
        # data, not an invocation.
        #
        # The next two are the ones that pin the COMMAND-POSITION anchor, and
        # they exist because mutation review caught the first version of this
        # block claiming more than it showed. Dropping the anchor left the
        # simpler rows below green: their inner payload is quoted with a
        # MISMATCHED pair (`"..."` inside `'...'`), so the unwrap declines and
        # the payload stays blanked whatever the anchor does. They were
        # protected by an accident, not by the design they were cited for.
        # Here the inner pair matches, so the unwrap succeeds and only the
        # anchor stands between prose and a false block.
        # The TRAILING text after the inner payload is load-bearing and is the
        # detail two rounds of reasoning got wrong: without it the outer span's
        # closing quote is glued onto the payload by `_skip_word`, the unwrap
        # sees a mismatched pair and declines, and the row goes green whatever
        # the anchor does. With it the inner pair is clean, the unwrap
        # succeeds, and only the anchor stands between prose and a false block.
        # Chosen by running candidates against the mutants rather than by
        # reading the code.
        ("a matched-quote payload inside a field value allowed",
         'gh api repos/o/r/issues -f body="bash -c \'gh api /markdown\' and more"',
         ALLOW),
        # Same shape with a separator inside the quoted span: separators are
        # read off the blanked copy, so a `;` or `|` in DATA must not start a
        # command. This row kills the raw-separator mutant; the one above does
        # not, so both are needed.
        ("a quoted separator does not start a command",
         'gh api repos/o/r/issues -f body="a; bash -c \'gh api /markdown\' b"',
         ALLOW),
        ("a quoted pipe does not start a command",
         'gh api repos/o/r/issues -f body="a | bash -c \'gh api /markdown\' b"',
         ALLOW),
        ("a shell name inside a quoted field value allowed",
         "gh api repos/o/r/issues -f body='bash -c \"gh api /markdown\"'", ALLOW),
        ("a shell name inside a double-quoted field value allowed",
         'gh api repos/o/r/issues -f body="run bash -c to reproduce /markdown"',
         ALLOW),
        ("advice about the wrapper echoed allowed",
         "echo 'never run bash -c \"gh api /markdown\"'", ALLOW),
        # `--` ends the options, and the two sides of it behave oppositely.
        # Both verified with a `gh` shim, because "what does the shell do with
        # `--`" is exactly the kind of claim this PR has twice got wrong by
        # reading the grammar instead of running it.
        #
        # BEFORE a `-c`, `--` means the next word is a SCRIPT PATH. The shell
        # tries to open a file named `-c`, fails, and never reaches gh -- so
        # blocking these was a false positive on commands that execute
        # nothing. Reported by review on #1313.
        # A GLUED `-c` payload does not run, so blocking it would be a false
        # positive. Reported on #1313 as a bypass; the premise is wrong for
        # bash and sh, and these rows exist so it is not re-reported a third
        # time. `-c` is not a getopt-style option -- its argument must be the
        # NEXT word -- so `-c'...'` arrives as the single word `-cgh api /...`
        # and the shell reads `g`, `h`, ... as option letters. Measured:
        #
        #   bash -c'echo RAN'      -> rc=1  bash: - : invalid option
        #   sh   -c'echo RAN'      -> rc=2  sh: 0: Illegal option -h
        #   bash -c 'echo RAN'     -> rc=0  RAN            (the control)
        #
        # A `gh` shim on PATH confirms the negative directly: gh is never
        # reached by any glued form, and is reached by the separate-word one.
        ("glued -c payload does not run, so it is allowed",
         "bash -c'gh api /markdown'", ALLOW),
        ("glued -c payload with escaped spaces allowed",
         r"bash -cgh\ api\ /markdown", ALLOW),
        ("sh glued -c payload allowed",
         "sh -c'gh api /markdown'", ALLOW),
        ("end-of-options before -c allowed",
         "bash -- -c 'gh api /markdown'", ALLOW),
        ("end-of-options with a long option allowed",
         "bash --norc -- -c 'gh api /markdown'", ALLOW),
        ("sh end-of-options before -c allowed",
         "sh -- -c 'gh api /markdown'", ALLOW),
        # AFTER a `-c`, `--` is just a separator and the payload still runs --
        # the shim shows gh receiving `/markdown`. The first fix for the rows
        # above yielded the bare `--` as the payload and let this through, so
        # the same review round that reported a false positive also had a
        # bypass hiding behind it. Not reported; found by probing both sides.
        ("end-of-options after -c still blocks",
         "bash -c -- 'gh api /markdown'", BLOCK),
        # A command substitution's contents are EXECUTED, so blanking them as
        # a quoted span hides a real command. `main` caught these for free by
        # matching the raw string; making the span quote-aware regressed them,
        # and all three are confirmed with a `gh` shim to reach gh with
        # `/markdown`. Reported on #1313.
        ("substitution generates the -c payload",
         'bash -c "$(printf \'gh api /markdown\')"', BLOCK),
        ("backtick substitution generates the -c payload",
         'bash -c "`printf \'gh api /markdown\'`"', BLOCK),
        ("split literal inside the substitution",
         'bash -c "$(printf \'gh api /mark\'\'down\')"', BLOCK),
        # ...and a substitution outside any `-c`, same principle.
        ("endpoint inside a bare substitution",
         'echo "$(gh api /markdown)"', BLOCK),
        # Controls. A substitution is only a candidate for what it CONTAINS --
        # a dynamic payload with no endpoint in its source stays allowed, which
        # is what keeps `bash -c "$(generate)"` from becoming unusable.
        ("benign substitution allowed",
         'echo "$(gh api rate_limit)"', ALLOW),
        ("dynamic -c payload with no endpoint allowed",
         'bash -c "$(cat scripts/deploy.sh)"', ALLOW),
        # SINGLE quotes neutralize a substitution and DOUBLE quotes do not, so
        # scanning every `$(` span blocked prose that bash only ever prints.
        # Reported on #1313 after the round above introduced it. All four are
        # confirmed with the `gh` shim NOT to reach gh, and their expanding
        # counterparts above are confirmed to reach it -- the pair is what
        # makes this a distinction rather than a loosening.
        ("substitution inside single quotes is literal",
         "echo '$(gh api /markdown)'", ALLOW),
        ("backtick inside single quotes is literal",
         "echo '`gh api /markdown`'", ALLOW),
        ("escaped dollar neutralizes the substitution",
         'echo "\\$(gh api /markdown)"', ALLOW),
        ("escaped backtick neutralizes the substitution",
         'echo "\\`gh api /markdown\\`"', ALLOW),
        # ...and the unquoted form, which does expand, must still block.
        ("unquoted substitution still blocks",
         "echo $(gh api /markdown)", BLOCK),
        # REGRESSION PINS, not discriminators -- said plainly because a green
        # row that proves nothing is how a table stops meaning anything. Both
        # survive every mutation tried against the payload scanner, including
        # deleting the option-word `break` they were written for: neither
        # command contains a leading-slash endpoint anywhere, so no scanning
        # mistake can reach them. They are kept to pin the shapes against a
        # FUTURE over-eager change, and they do not evidence this one.
        ("bash running a script file allowed",
         "bash scripts/deploy.sh && gh api rate_limit", ALLOW),
        ("unrelated -c flag allowed",
         "sort -c /tmp/list.txt", ALLOW),
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
        # The three trigger tokens have to land in the SAME shell segment. Scanned
        # over the whole command they fire on a command carrying no GraphQL query
        # at all, and the one that supplies the `graphql` token is the budget read
        # AGENTS.md requires every run -- so batching it with any REST --paginate
        # sweep was refused, under a message about a query that does not exist.
        # Measured on conductor run 230; this is the case that motivated the fix.
        ("REST paginate beside a graphql budget read allowed",
         "gh api rate_limit --jq '.resources.graphql.remaining' ; "
         "gh api 'repos/o/r/issues/719/comments?per_page=100' --paginate "
         "--jq '.[].created_at'", ALLOW),
        # Per-segment scanning must not become an escape hatch: a real offender in
        # the SECOND statement is still the command this rule exists to stop.
        ("offender in the second statement still blocks",
         "gh api rate_limit --jq '.resources.core.remaining' ; "
         "gh api graphql --paginate -f query='query($cursor:String){viewer{login}}'", BLOCK),
        # Statement splitting is line-based, so a command written across backslash
        # continuations is joined first. Without that join the trigger tokens and
        # the declaration land in different segments and a CORRECT query is
        # refused -- a false positive introduced by the fix for the one above.
        ("continuation-split query declaring $endCursor allowed",
         "gh api graphql --paginate \\\n"
         "  -f query='query($endCursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$endCursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", ALLOW),
        # ...and the join must not let a misnamed cursor through on the same shape.
        ("continuation-split query with $cursor blocks",
         "gh api graphql --paginate \\\n"
         "  -f query='query($cursor:String){organization(login:\"x\"){"
         "projectV2(number:9){items(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor} nodes{id}}}}}'", BLOCK),
        # Segmentation is quote-aware. A `;` inside a quoted ARGUMENT is not a
        # statement boundary, and the position matters: it has to fall between
        # `--paginate` and the declaration for the case to discriminate. A naive
        # `cmd.split(";")` tears the declaration off the trigger here and blocks a
        # correct command -- with the `;` inside the query instead, the
        # declaration rides along in the first piece and the case passes under a
        # naive splitter too, proving nothing. Measured both ways on run 230.
        ("a quoted semicolon before the query is not a boundary",
         "gh api graphql --paginate -f q='label:a;b' -f query="
         "'query($endCursor:String){search(query:$q,type:ISSUE,"
         "first:100,after:$endCursor){pageInfo{hasNextPage endCursor}}}'", ALLOW),
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

    Rule("gh-api-classic-branch-protection", '[#1518]', WARN_TIER,
         label='guard_bash / #1518 classic branch-protection read:', cases=[
        ("the canonical call warns",
         "gh api repos/o/r/branches/main/protection", WARN),
        # The two commands run 196 actually issued, whose 404s were one sentence
        # from being filed as "neither template repo protects main". Both repos
        # carry three active rulesets.
        ("run 196's Footer-Only read warns",
         "gh api repos/FreeForCharity/FFC-IN-Footer_Only_Template/branches/main/protection", WARN),
        ("run 196's Single-Page read warns",
         "gh api repos/FreeForCharity/FFC-IN-FFC_Single_Page_Template/branches/main/protection", WARN),
        # The defect is the ENDPOINT, not the branch name -- a rule keyed on
        # `main` would miss every release branch and still read as covered.
        ("a non-main branch warns too",
         "gh api repos/o/r/branches/release%2F1.x/protection", WARN),
        ("a sub-resource of it warns",
         "gh api repos/o/r/branches/main/protection/required_status_checks", WARN),
        ("the --jq spelling still warns",
         "gh api repos/o/r/branches/main/protection --jq '.required_status_checks'", WARN),
        # The CORRECT calls must never be warned at. A guard that nags at the
        # remedy it just prescribed teaches people to ignore it.
        ("the rulesets listing is silent", "gh api repos/o/r/rulesets", SILENT),
        ("a single ruleset read is silent", "gh api repos/o/r/rulesets/16769005", SILENT),
        ("org-level rulesets are silent", "gh api orgs/FreeForCharity/rulesets", SILENT),
        # Declared overlap, not tolerated: the plural `branches` listing is a
        # genuine unpaginated list read, so #971 fires and is right to, while
        # this rule must stay quiet -- `protection` is not a COLLECTION_SEGMENT,
        # which is what keeps the two rules disjoint.
        ("the branches listing warns under #971, not this rule",
         "gh api repos/o/r/branches", SILENT, ("[#971]",)),
        ("a single branch read is silent", "gh api repos/o/r/branches/main", SILENT),
        ("non-gh command with the same words is silent",
         "echo 'repos/o/r/branches/main/protection returns 404'", SILENT),
        # The tier itself: asking the classic question is allowed, just annotated.
        ("warned protection read is still allowed",
         "gh api repos/o/r/branches/main/protection", ALLOW),
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
        # The one DELIBERATE overlap, declared rather than tolerated: this is an
        # unpaginated list read, so #971 fires and is right to; it is not a
        # paginated array-jq, so #989 must stay quiet. check_warn's own
        # docstring names this exact command as the reason `tag` exists. The
        # fourth element is what keeps "no unexpected warning" strict for the
        # other silent cases while letting this one warn under #971.
        ("array jq WITHOUT paginate silent",
         "gh api repos/o/r/issues --jq '[.[]|.number]'", SILENT, ("[#971]",)),
        ("paginate + slurp, no jq, silent",
         "gh api --paginate --slurp repos/o/r/issues/719/comments", SILENT),
        ("gh pr list array jq is not gh api",
         "gh pr list --json number --jq '[.[]|.number]'", SILENT),
        # Round 14. This rule's anchor shared rule 8's escape-blind blanker, so
        # the same `\"` that exposed an endpoint there exposed `gh api` here and
        # this advisory fired on prose. The review named only rule 8; this site
        # was found by grepping that helper's callers, which is the whole reason
        # the helper is now deleted rather than left unused. WARNED before the fix.
        ("escaped quote in prose does not trigger the paginate advisory",
         'echo "x\\" gh api --paginate --jq \'[.[] | .slug]\'"', SILENT),
        ("unescaped prose with the same words was already silent",
         'echo "x gh api --paginate --jq \'[.[] | .slug]\'"', SILENT),
        # The measured counter-example that decided this rule's tier: 726 reduces
        # each page to a scalar and re-joins downstream, so it is CORRECT. It still
        # warns -- an advisory tier is allowed to be noticed on correct code -- but
        # it must never be blocked, which is what this case pins.
        ("726's real usage warns but is NOT blocked",
         "gh api --paginate \"repos/$org/$repo/teams?per_page=100\" --jq '[.[] | .slug] | join(\",\")'", ALLOW),
    ]),

    # #1127 / ledger L193. Registered as a Rule rather than as flat check()
    # calls: test_refusal_site_coverage() derives every refusal from
    # guard_bash.py's AST and requires a registered signature to claim it, so
    # cases written outside the table leave gh_edit_label_violation() uncovered
    # and the suite red. The signature is a slice of the readable literal in
    # that refusal -- the message is concatenated around the offending
    # statement, so only the constant fragments are matchable.
    Rule("gh-edit-label", 'read-modify-write of the ENTIRE label set', BLOCK_TIER, [
        # Both subcommands, both flags. The defect is the whole-set PUT, so
        # removing is exactly as lossy as adding (#788 lost an add to a remove).
        ("issue edit --remove-label",
         "gh issue edit 788 --repo FreeForCharity/FFC-Cloudflare-Automation "
         "--remove-label claimed", BLOCK),
        ("issue edit --add-label", "gh issue edit 788 --add-label agent-ready", BLOCK),
        ("pr edit --add-label", "gh pr edit 1125 --add-label security", BLOCK),
        ("label flag anywhere in the statement",
         "gh issue edit 788 --body-file x.md --remove-label blocked --repo O/R", BLOCK),
        # Blocked through the bypass the first pattern had: `gh` resolves its
        # subcommand only AFTER stripping leading global flags, so an adjacent
        # `gh (issue|pr) edit` match is defeated by `--repo` -- a flag the
        # Conductor passes as a matter of course, which made the hole the
        # DEFAULT path.
        ("global --repo before the subcommand",
         "gh --repo O/R issue edit 788 --add-label agent-ready", BLOCK),
        ("global -R before the subcommand",
         "gh -R O/R pr edit 1125 --remove-label claimed", BLOCK),
        # And the near-miss inside the fix: `--repo` takes a SEPARATED value, so
        # `O/R` sits between `gh` and `issue` as a bare word. A rule that merely
        # skipped flag tokens would read `O/R` as the subcommand and let this pass.
        ("gh by path, behind an env prefix",
         "MSYS_NO_PATHCONV=1 /usr/bin/gh --repo O/R issue edit 788 --add-label x", BLOCK),
        # Allowed: the additive/subtractive endpoints this rule points people at,
        # and every other reading of the words "edit" and "label". The last two
        # bound the ordered-token match -- they are blocked cases above with only
        # the label flag removed, so they fail if the rule ever widens to any
        # `gh issue edit`.
        ("additive labels endpoint allowed",
         "gh api --method POST repos/O/R/issues/788/labels -f 'labels[]=agent-ready'", ALLOW),
        ("subtractive labels endpoint allowed",
         "gh api --method DELETE repos/O/R/issues/788/labels/claimed", ALLOW),
        ("non-label issue edit allowed", "gh issue edit 788 --title 'a new title'", ALLOW),
        ("issue create --label allowed",
         "gh issue create --label agentic-os --title x --body y", ALLOW),
        ("issue list --label allowed", "gh issue list --label agent-ready --state open", ALLOW),
        ("the flag name inside a quoted message allowed",
         "gh issue comment 788 --body 'do not use gh issue edit --remove-label here'", ALLOW),
        ("global flag + issue edit without a label flag allowed",
         "gh --repo O/R issue edit 788 --title 'a new title'", ALLOW),
    ], label="guard_bash / L193 gh edit label read-modify-write:"),
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


def test_strip_quoted_matches_a_bash_accurate_scanner():
    """`_strip_quoted`'s escape rule is broader than bash's -- pin that it costs
    nothing, rather than asserting it in a docstring.

    Inside double quotes bash escapes only `\\`, `"`, `$`, backtick and newline
    and PRESERVES the backslash before anything else (measured: `"a\\zb"` prints
    `a\\zb` in bash and dash). The scanner treats every `\\x` there as an escape,
    which is simpler and, for the one question its callers ask -- where the
    operators and the endpoint are -- indistinguishable, because inside a
    double-quoted span every character is blanked anyway and the only character
    whose escaping could move the span's END is `"`, which bash escapes too.

    That argument is exactly the kind that stops being true after a refactor,
    so it is a test: compare against a bash-accurate reference over every
    string the shell metacharacters can form. Copilot raised the docstring as
    misleading on #1313 and was right about bash; this is what makes the reply
    checkable by the next reader instead of quotable.
    """
    import importlib.util
    import itertools
    import re

    spec = importlib.util.spec_from_file_location("_gb_for_test", GUARD_BASH)
    gb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gb)

    bs, dq, sq = chr(92), '"', "'"
    dq_escapable = {bs, dq, "$", "`", chr(10)}

    def bash_accurate(text):
        out, quote, i, n = list(text), None, 0, len(text)
        while i < n:
            ch = text[i]
            if quote == sq:
                if ch == sq:
                    quote = None
                else:
                    out[i] = " "
            elif ch == bs and i + 1 < n and (quote is None or text[i + 1] in dq_escapable):
                out[i] = out[i + 1] = " "
                i += 2
                continue
            elif quote == dq:
                if ch == quote:
                    quote = None
                else:
                    out[i] = " "
            elif ch in sq + dq:
                quote = ch
                out[i] = " "
            i += 1
        return "".join(out)

    # Every character that can reach `_strip_quoted` and change where a span
    # ends or where an operator is found: the quotes and the escape, the five
    # shell operators rule 8 stops at (`|`, `;`, `&`, `<`, `>`), `$` and the
    # backtick (substitution), `/` (the leading slash rule 8 is ABOUT), and one
    # ordinary letter to stand for inert text. `/` and `<` were missing here
    # while the docstring claimed them (#1313 review), which is the drift the
    # count check below now makes impossible.
    # The NEWLINE joined this list in round 11 by the same argument that put
    # `/` and `<` in it: a backslash-newline is a line CONTINUATION, the shell
    # deletes it, and rule 8's span stops at a newline -- so it is precisely a
    # character whose escaping moves where a span ends. Without it here, the
    # corpus could not express the bypass at all.
    alphabet = ["a", bs, dq, sq, "|", ";", "&", "$", "`", "/", "<", ">", chr(10)]
    max_length = 5

    # The docstring states this measurement as a number, and a number in prose
    # drifts from the test that is supposed to back it -- which is exactly what
    # happened: it claimed 177,155 strings over 11 characters to length 5 while
    # this test enumerated 11,110 over 10 characters to length 4, so the
    # "measured exhaustively" sentence was backed by 6% of the corpus it named.
    # Deriving the claim from the corpus means neither side can move alone.
    corpus_size = sum(len(alphabet) ** n for n in range(1, max_length + 1))
    claimed = re.search(r"to\s+length\s+(\d+)\s*--\s*([\d,]+)\s+strings",
                        gb._strip_quoted.__doc__ or "", re.S)
    record("the docstring's corpus claim matches the corpus this test walks",
           bool(claimed)
           and int(claimed.group(1)) == max_length
           and int(claimed.group(2).replace(",", "")) == corpus_size,
           f"docstring says {claimed.groups() if claimed else None}, "
           f"test walks length {max_length} / {corpus_size:,} strings")

    # The two scanners are NO LONGER expected to be identical, and comparing
    # them for equality is what this test used to do. Unquoted, the shipped
    # scanner deliberately reveals an escaped ORDINARY character, because that
    # character is data the command receives -- `gh api \/markdown` reaches gh
    # as `/markdown`, so hiding it hid a live endpoint (#1313 review).
    #
    # Asserting the new rule by re-implementing it here would make the test
    # agree with the code by construction. So `bash_accurate` stays a model of
    # the SHELL, and the two properties below are stated independently of how
    # the scanner is written:
    #
    #   1. at every operator and quote position the two agree on blanked-ness
    #      -- that is what this function exists to get right, and no escape
    #      policy may change it;
    #   2. the shipped scanner never blanks MORE than bash-accurate does, so
    #      the divergence can only ever reveal, never hide.
    #
    # Together these fail for any divergence except the intended one.
    syntax = set(sq + dq + "|;&<>`" + bs)
    disagree_on_syntax = []
    hides_more = []
    walked = 0
    for length in range(1, max_length + 1):
        for combo in itertools.product(alphabet, repeat=length):
            s = "".join(combo)
            walked += 1
            got, ref = gb._strip_quoted(s), bash_accurate(s)
            for idx, src in enumerate(s):
                got_blank, ref_blank = got[idx] == " ", ref[idx] == " "
                if src in syntax and got_blank != ref_blank:
                    disagree_on_syntax.append((s, idx))
                    break
                if got_blank and not ref_blank:
                    hides_more.append((s, idx))
                    break
            if len(disagree_on_syntax) >= 5 or len(hides_more) >= 5:
                break
        if disagree_on_syntax or hides_more:
            break

    record("_strip_quoted agrees with bash at every operator and quote",
           not disagree_on_syntax,
           "\n".join(f"{s!r} at {i}: scanner={gb._strip_quoted(s)!r} "
                     f"bash-accurate={bash_accurate(s)!r}"
                     for s, i in disagree_on_syntax))
    record("_strip_quoted never hides a character bash-accurate keeps",
           not hides_more,
           "\n".join(f"{s!r} at {i}: scanner={gb._strip_quoted(s)!r} "
                     f"bash-accurate={bash_accurate(s)!r}" for s, i in hides_more))
    # A corpus that silently shrinks is the failure this test had; assert the
    # walk actually completed rather than inferring it from the absence of
    # diffs, which an empty corpus also produces.
    record("the equivalence walk covered the whole corpus",
           bool(disagree_on_syntax or hides_more) or walked == corpus_size,
           f"walked {walked:,} of {corpus_size:,} strings")


def test_no_hook_module_has_an_invalid_escape_sequence():
    r"""A hook must not prepend a compiler warning to its own refusal.

    Hook diagnostics reach the agent on STDERR, which is where an invalid
    escape sequence surfaces too -- so the warning arrives in front of the
    `BLOCKED by ...` explanation it is supposed to be reading. A guard whose
    job is to explain itself should not open with noise, and a reader who sees
    a warning above a block has one more reason to distrust the block.

    Found by the Conductor (run 175) on #1313: a docstring here illustrated a
    Windows path as `/c/Program\ Files/...`, and `\ ` is not a valid escape in
    a non-raw string. Prose examples containing Windows paths are exactly what
    keeps reintroducing this, so the assertion is mechanical.

    ⚠️ The obvious form of this check is version-dependent and would have
    passed on the host that needed it. Python >= 3.12 raises SyntaxWarning for
    an invalid escape; <= 3.11 raises DeprecationWarning. Measured here on
    3.11.15, the defect reported as DeprecationWarning, so a
    `py_compile(..., doraise=True)` under `-W error::SyntaxWarning` -- the
    natural spelling -- was GREEN on this interpreter while the warning was
    live. Match on the message instead of the category, and this holds on both.

    Every hook module is scanned rather than just `guard_bash.py`: the cause is
    prose, and prose is in all of them.
    """
    offenders = []
    for path in sorted(glob.glob(os.path.join(HOOKS, "*.py"))):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(src, path, "exec")
            except SyntaxError as exc:  # a broken module is a louder failure
                offenders.append(f"{os.path.basename(path)}: SyntaxError {exc}")
                continue
        for entry in caught:
            if "invalid escape sequence" in str(entry.message):
                offenders.append(
                    f"{os.path.basename(path)}:{entry.lineno} "
                    f"{entry.category.__name__}: {entry.message} "
                    "-- make the string raw, or double the backslash")
    record("no hook module compiles with an invalid escape sequence",
           not offenders, "\n".join(offenders))


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
        for name, cmd, verdict, *rest in rule.cases:
            expected_tags = rest[0] if rest else ()
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
                if verdict == SILENT:
                    # `tag` makes check_warn's negative mean "THIS rule stayed
                    # quiet", which cannot see a DIFFERENT warn rule that has
                    # started firing here (Copilot's finding on #1039, and it
                    # is real). Asserting no warning AT ALL is the obvious fix
                    # and is wrong: the two rules deliberately overlap, and
                    # `gh api repos/o/r/issues --jq '[...]'` IS an unpaginated
                    # list read while NOT being a paginated array-jq -- so the
                    # blunt form reds a case that is correct by design (it did,
                    # when measured). Each case therefore declares the OTHER
                    # tags it legitimately provokes, and anything beyond that
                    # set is an unexpected leak.
                    unexpected = warn_tags(err) - set(expected_tags)
                    record(f"{rule.id} / {name}: no unexpected warning"
                           + (f" (expected {' '.join(expected_tags)})" if expected_tags else ""),
                           not unexpected,
                           f"also warned under {' '.join(sorted(unexpected))} -- either a rule "
                           f"has started leaking onto this command, or the overlap is legitimate "
                           f"and belongs in the case's expected-tag list")


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
    test_strip_quoted_matches_a_bash_accurate_scanner()
    test_rule_attribution()
    test_refusal_site_coverage()
    test_no_hook_module_has_an_invalid_escape_sequence()

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
