"""Shared helpers for FFC AI-agent security/quality hooks.

These hooks enforce the rules in .github/agents/AI_AGENT_INSTRUCTIONS.md at the
moment a tool runs, instead of relying on the agent to remember them.

Design principles:
  * Fail OPEN on anything unexpected (never block legitimate work because of a
    bug in a hook) -- every entrypoint wraps its logic in try/except and exits 0.
  * Fail CLOSED only on a *definite* match (a real-looking secret, a forbidden
    command). Ambiguity => allow.
  * No third-party dependencies. Standard library only.
"""

import re

# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------

# Tokens that are intentionally committed as documented *fake* examples. We must
# not flag these or we'd block edits to the very docs that teach the rules.
KNOWN_FAKE_TOKENS = {
    "em7chiooYdKI4T3d3Oo1j31-ekEV2FiUfZxwjv-Q",  # AI_AGENT_INSTRUCTIONS.md sample
}

# Substrings that mark a value as an obvious placeholder, not a real secret.
PLACEHOLDER_MARKERS = (
    "your-", "your_", "yourtoken", "xxxx", "example", "placeholder", "redacted",
    "changeme", "change-me", "replace", "<", ">", "${", "$(", "secrets.", "env.",
    "here", "dummy", "sample", "fake", "test-token", "abc123", "n/a", "none",
    "todo", "...", "********",
)

# High-confidence, vendor-specific secret formats.
HARD_PATTERNS = [
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("GitHub personal access token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("GitHub OAuth/app token", re.compile(r"\bgh[osur]_[A-Za-z0-9]{36}\b")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
]

# keyword = "value"  (token/secret/password/api key ...). The value charset is
# deliberately restricted to opaque-token characters (no '.', '(', '$', etc.) so
# we match real secret *literals* and not code expressions or secret *names*.
ASSIGNMENT_PATTERN = re.compile(
    r"(?P<key>(?:api[_-]?(?:key|token)|access[_-]?(?:key|token)|client[_-]?secret"
    r"|secret[_-]?(?:key|access)?|auth[_-]?token|bearer|password|passwd|apikey))"
    r"\s*[:=]\s*"
    r"(?P<quote>['\"]?)(?P<val>[A-Za-z0-9_\-+/=]{16,})(?P=quote)",
    re.IGNORECASE,
)


def _is_opaque_secret(value: str) -> bool:
    """A real token literal: not a placeholder, and mixes letters and digits
    (so plain identifiers like 'Resolve-WhmcsAccessKey' are not flagged)."""
    if value in KNOWN_FAKE_TOKENS or _looks_placeholder(value):
        return False
    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    return has_letter and has_digit

# A bare Cloudflare-style token: 40 chars of [A-Za-z0-9_-]. To avoid matching the
# many 40-char git SHAs (lowercase hex) and base64 doc blobs, we additionally
# require it to mix case + contain a digit + contain a '-' or '_', which is what
# real Cloudflare tokens look like and git SHAs never do.
CF_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_\-]{40}\b")


def _looks_placeholder(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def _is_real_cf_token(value: str) -> bool:
    if value in KNOWN_FAKE_TOKENS or _looks_placeholder(value):
        return False
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    has_digit = any(c.isdigit() for c in value)
    has_sep = ("-" in value) or ("_" in value)
    return has_upper and has_lower and has_digit and has_sep


def find_secrets(text):
    """Return a list of human-readable reasons if `text` appears to contain a
    real secret. Empty list => looks safe."""
    if not text:
        return []
    findings = []

    for label, pat in HARD_PATTERNS:
        if pat.search(text):
            findings.append(label)

    for m in ASSIGNMENT_PATTERN.finditer(text):
        if _is_opaque_secret(m.group("val")):
            findings.append(f"hardcoded value assigned to '{m.group('key')}'")

    for m in CF_TOKEN_PATTERN.finditer(text):
        if _is_real_cf_token(m.group(0)):
            findings.append("possible Cloudflare API token literal")
            break

    # De-duplicate while preserving order.
    seen = set()
    out = []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Sensitive file paths (must never be authored into the repo)
# ---------------------------------------------------------------------------

SENSITIVE_FILE_PATTERNS = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.local$"),
    re.compile(r"(^|/)\.env\..*\.local$"),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"\.pfx$"),
    re.compile(r"\.p12$"),
    re.compile(r"(^|/)secrets/"),
    re.compile(r"(^|/)id_rsa$"),
    re.compile(r"(^|/)id_ed25519$"),
]

# Explicitly-allowed example/template variants.
SENSITIVE_ALLOW = re.compile(r"(\.example$|\.sample$|\.template$|\.dist$)")


def is_sensitive_path(path):
    if not path:
        return False
    p = path.replace("\\", "/")
    if SENSITIVE_ALLOW.search(p):
        return False
    return any(pat.search(p) for pat in SENSITIVE_FILE_PATTERNS)
