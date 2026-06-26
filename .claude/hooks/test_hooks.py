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
    return proc.returncode


def check(name, script, payload, expect_block):
    global PASS, FAIL
    rc = run(script, payload)
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
