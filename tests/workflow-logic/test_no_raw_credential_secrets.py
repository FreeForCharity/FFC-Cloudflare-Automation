"""Guard: workflows reference OIDC identifiers, never raw credentials (#844).

Key Vault is this repo's single source of truth for credentials — "rotation is a
new KV version, no GitHub change". A credential *copied* into a GitHub secret has
to be rotated in two places, and the copy that nobody remembers is the one that
goes stale: exactly how the Cloudflare token silently broke for four months.

#844 asks for a test so that "the next raw credential pasted into GitHub fails CI
instead of going unnoticed for months". A static test cannot enumerate the
secrets *stored* in an environment — that needs an authenticated API call, and CI
would need admin scope to make it. But a stored secret is inert until a workflow
references it, so the reference is the thing worth pinning, and it is in the
tree. This sweeps every `${{ secrets.X }}` in every workflow and requires X to be
either a non-secret OIDC identifier or a named, phase-tagged piece of remaining
debt.

Two directions, both load-bearing:

  * A *new* raw credential fails `test_workflows_reference_no_unexpected_secrets`.
  * A *migrated* credential whose line is left behind fails
    `test_the_remaining_raw_credentials_list_is_exact` — so the debt list cannot
    rot into a stale overstatement of the work. (The #844 tracking comment listed
    13 workflows still on `CBM_TOKEN` when the real number was already 0; prose
    does not survive contact with a moving tree, which is why this is a test.)
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

# Only matches inside a ${{ ... }} expression, so prose ("…/environments/…/secrets")
# and PowerShell property access ($secrets.Count) are not mistaken for references.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_SECRET_REF = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")

# Non-secret values that are legitimately held as GitHub secrets.
#
# `GITHUB_TOKEN` is the ambient per-run token — not a stored credential at all.
# The `*_AZURE_*_ID` names are Entra application and tenant GUIDs: identifiers,
# not credentials. They authorize nothing on their own — the OIDC exchange is
# authorized by a federated credential on the Azure side, keyed to the workflow's
# `environment:`. Leaking one gains an attacker nothing, and rotating Key Vault
# does not touch them.
_IDENTIFIER_SUFFIXES = ("_AZURE_CLIENT_ID", "_AZURE_KV_CLIENT_ID", "_AZURE_TENANT_ID")
ALLOWED_EXACT = {"GITHUB_TOKEN"}


def _is_identifier(secret: str) -> bool:
    return secret in ALLOWED_EXACT or secret.endswith(_IDENTIFIER_SUFFIXES)


# Raw credentials still referenced by a workflow, each tagged with the #844 phase
# that clears it. EXACT — not a ceiling. Delete an entry in the same PR that
# removes its last reference.
#
# Phases 1 (whmcs-prod) and 2 (github-prod) are done: `WHMCS_API_ACCESS_KEY` and
# `CBM_TOKEN` appear nowhere below because they appear nowhere in the tree.
REMAINING_RAW_CREDENTIALS = {
    # Phase 3 — wpmudev-prod. The KV counterpart already exists
    # (`wr-all-ffc-wpmudev-ga-api-token`); the blocker is one federated credential
    # for `…:environment:wpmudev-prod`, which only an Azure admin can create.
    "FFC_WPMUDEV_GA_API_Token": {"104-domain-export-inventory.yml", "601-wpmudev-export-sites.yml"},
    # Phase 4 — m365-prod. The Exchange Online cert is NOT in Key Vault; a human
    # must upload the .pfx before any workflow change is possible. #844 is
    # explicit that no agent should handle that material.
    "FFC_EXO_CERT_PFX_BASE64": {"103-enforce-domain-standard.yml", "304-m365-dkim-enable.yml"},
    "FFC_EXO_CERT_PASSWORD": {"103-enforce-domain-standard.yml", "304-m365-dkim-enable.yml"},
    "EXO_CERT_PFX_BASE64": {"303-m365-domain-and-dkim.yml"},
    "EXO_CERT_PFX_PASSWORD": {"303-m365-domain-and-dkim.yml"},
    # Phase 4, but configuration rather than credentials — a tenant domain and an
    # organization name, neither of which authorizes anything. They are listed
    # because #844's acceptance criterion is that a migrated environment holds
    # *only* `*_AZURE_KV_*` identifiers, and these keep m365-prod from being
    # clean. They can move to workflow inputs or repo Variables instead of KV.
    "EXO_TENANT": {"303-m365-domain-and-dkim.yml"},
    "EXO_ORGANIZATION": {"303-m365-domain-and-dkim.yml"},
}

# Credentials that HAVE been migrated to Key Vault. Naming them individually
# gives a precise failure message if one is ever pasted back in, rather than the
# generic "unexpected secret" — the reintroduction of one of these is a
# regression against work that already shipped, not a new decision.
MIGRATED_TO_KEY_VAULT = {
    "CBM_TOKEN": "wr-all-cbm-github-pat / wr-all-cbm-ffc-copilot-mcp-github-pat (phase 2)",
    "WHMCS_API_ACCESS_KEY": "the whmcs-secrets-from-kv composite action (phase 1)",
    "WHMCS_API_IDENTIFIER": "the whmcs-secrets-from-kv composite action (phase 1)",
    "WHMCS_API_SECRET": "the whmcs-secrets-from-kv composite action (phase 1)",
}


def _secret_references() -> dict[str, set[str]]:
    """secret name -> workflow filenames referencing it."""
    found: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        # encoding="utf-8" is required — these files contain ✓/❌/em-dashes and
        # Python on Windows would otherwise decode as cp1252 and crash the module.
        text = path.read_text(encoding="utf-8")
        for expression in _EXPRESSION.findall(text):
            for secret in _SECRET_REF.findall(expression):
                found.setdefault(secret, set()).add(path.name)
    return found


def test_workflows_reference_no_unexpected_secrets():
    """A credential pasted into GitHub fails here instead of going unnoticed."""
    violations = []
    for secret, files in sorted(_secret_references().items()):
        if _is_identifier(secret) or secret in REMAINING_RAW_CREDENTIALS:
            continue
        where = ", ".join(sorted(files))
        if secret in MIGRATED_TO_KEY_VAULT:
            violations.append(
                f"secrets.{secret} is referenced by {where}, but it was migrated "
                f"to Key Vault ({MIGRATED_TO_KEY_VAULT[secret]}). Fetch it at run "
                "time via azure/login + `az keyvault secret show`; a GitHub copy "
                "reintroduces the two-place rotation that #844 removed."
            )
        else:
            violations.append(
                f"secrets.{secret} is referenced by {where} and is neither an OIDC "
                "identifier nor listed in REMAINING_RAW_CREDENTIALS. Key Vault is "
                "the single source of truth for credentials (#844) — store it in "
                "kv-ffc-admin-prod-cbm and fetch it over OIDC. If it is genuinely "
                "a non-secret identifier, add it to ALLOWED_EXACT with a comment "
                "saying why it authorizes nothing."
            )
    assert not violations, "\n".join(violations)


def test_the_remaining_raw_credentials_list_is_exact():
    """Migrating a credential must delete its line, so the debt list cannot rot."""
    references = _secret_references()
    violations = []
    for secret, expected_files in sorted(REMAINING_RAW_CREDENTIALS.items()):
        actual_files = references.get(secret, set())
        if not actual_files:
            violations.append(
                f"secrets.{secret} is listed as remaining debt but no workflow "
                "references it any more — delete its entry from "
                "REMAINING_RAW_CREDENTIALS and mark that #844 phase complete."
            )
        elif actual_files != expected_files:
            violations.append(
                f"secrets.{secret} is referenced by {sorted(actual_files)}, not "
                f"{sorted(expected_files)} — update REMAINING_RAW_CREDENTIALS so "
                "the remaining scope stays accurate."
            )
    assert not violations, "\n".join(violations)


def test_migrated_credentials_are_not_reintroduced():
    """Phases 1 and 2 shipped; a GitHub copy of those secrets is a regression."""
    references = _secret_references()
    reintroduced = sorted(s for s in MIGRATED_TO_KEY_VAULT if s in references)
    assert not reintroduced, (
        "these credentials live in Key Vault and must not be read from a GitHub "
        f"secret again: {reintroduced}"
    )


def test_key_vault_consumers_can_perform_the_oidc_exchange():
    """A KV fetch without `id-token: write` fails at run time, not review time.

    The whole migration rests on the OIDC exchange. A job that calls
    `az keyvault secret show` without the permission gets an opaque azure/login
    failure — and the tempting fix, under time pressure, is to paste the
    credential back into a GitHub secret, which is what this file exists to stop.
    """
    violations = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "az keyvault secret show" not in text:
            continue
        if "id-token: write" not in text:
            violations.append(
                f"{path.name} fetches from Key Vault but never declares "
                "`id-token: write` — the OIDC exchange cannot succeed."
            )
    assert not violations, "\n".join(violations)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
