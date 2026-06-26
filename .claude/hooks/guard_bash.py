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

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never let a hook bug block legitimate work.
        sys.exit(0)
