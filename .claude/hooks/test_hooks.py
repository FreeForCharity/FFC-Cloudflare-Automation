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


REAL_CF = "em7XiooYdKI4T3d3Oo1j31-ekEV2FiUfZxwQvzT9"  # fabricated, CF-shaped


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
    check("edit private key content", "guard_edit.py",
          edit("docs/x.md", "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n"), True)
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

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
