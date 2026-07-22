# WHMCS MCP server — implementation design (read-only v1)

Design spec for a custom **WHMCS MCP server** so Claude sessions can do WHMCS application lookups
conversationally instead of round-tripping through GitHub Actions workflows (219/221) and reading
job logs. Tracks issue
[#770](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/770).

This document resolves the design decisions and scopes the build into tranches. It commits **no
architecture on its own** — the "Decisions needing sign-off" section lists what a maintainer should
confirm before Tranche A code lands. The same scaffold is the template for the Zeffy MCP server
([#769](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/769)).

## Two surfaces, different jobs

Like Candid (`docs/candid-api-and-mcp.md`), WHMCS gets two integration surfaces used for different
things. This spec adds the **interactive** one; the **audited** one already exists and stays.

| Surface                                | For                                                           | Auth                                                 |
| -------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------- |
| **WHMCS MCP server** (interactive)     | Claude sessions: application/client lookups during onboarding | Local: WHMCS creds + APIM key from Key Vault via env |
| **WHMCS Actions workflows** (219, 221) | Logged artifacts a workflow or audit depends on               | OIDC → Key Vault → APIM (`whmcs-secrets-from-kv`)    |

Rule of thumb (mirrors the Candid doc): use the MCP server to _look things up while you work_; when
a decision, audit, or downstream workflow depends on the result, run the Actions path so the answer
is a committed artifact.

## Architecture

A **stdio MCP server** (Node, `@modelcontextprotocol/sdk`) that reuses the repo's proven WHMCS API
path verbatim — the same APIM gateway, the same Key-Vault-mastered credentials, the same host
allowlist. It adds **no new credential store** and **no new network path**; it is a conversational
front-end over the exact request `scripts/whmcs-application-search.ps1` already makes.

```
Claude session (local/desktop/VS Code)
  └─ stdio ──► whmcs MCP server (Node)
       reads WHMCS_API_URL / WHMCS_API_IDENTIFIER / WHMCS_API_SECRET / WHMCS_APIM_SUBSCRIPTION_KEY
       from the environment (populated from Key Vault — never hard-coded)
       └─ POST https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php
            Ocp-Apim-Subscription-Key: <whmcs-ops key>
            └─ APIM (egress 20.231.116.111) ──► Cloudflare ──► WHMCS
```

### Credentials (Key Vault stays master — never a copy)

The server reads credentials **from the environment only**, using the exact variable names the
PowerShell helpers already resolve (`scripts/whmcs-api-common.ps1`): `WHMCS_API_URL`,
`WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET`, and `WHMCS_APIM_SUBSCRIPTION_KEY`. An operator running
the server locally populates those from Key Vault the same way the sandbox does today
(`az keyvault secret show` on the `read-all-ffc-whmcs-*` +
`read-all-ffc-apim-whmcs-subscription-key` secrets — see `CLAUDE.md` › "Azure CLI from the
sandbox"). The server never writes, caches, or logs a credential, and there is **no GitHub-secret or
on-disk copy** — this preserves the architectural invariant that Key Vault is the single source of
truth (`docs/whmcs-apim-routing.md`).

### Host allowlist (ported from `Invoke-WhmcsApi`)

The credential is attached to every request, so the server MUST refuse to send it anywhere but the
known WHMCS hosts. Port the allowlist from `scripts/whmcs-api-common.ps1` exactly:

- Allowed hosts: `apim-ffc-gateway-prod.azure-api.net`, `freeforcharity.org`.
- Scheme must be `https`.
- A configured `WHMCS_API_URL` that resolves to any other host is a hard error — never a silent
  fallback that could exfiltrate the credential.

Reuse the same transient-retry posture too (the WHMCS origin's Imunify360 bot-protection
intermittently challenges non-allowlisted IPs; the APIM path avoids it, but a bounded exponential
backoff on `429`, `502`, `503`, and `504` responses (and on request timeouts) keeps parity with the
scripts).

## Tool surface (read-only v1)

Every tool maps to a WHMCS API action the repo already calls, and returns **PII-masked** output (see
below). No tool performs a write.

| MCP tool                   | WHMCS action(s)                                                   | Mirrors                                         | Purpose                                                                                                          |
| -------------------------- | ----------------------------------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `whmcs_application_search` | `GetClientsProducts` (swept)                                      | `scripts/whmcs-application-search.ps1` (wf 221) | Find the onboarding application(s) matching a domain or org name; the flagship use case.                         |
| `whmcs_application_detail` | `GetClientsDetails` + `GetOrders` + `GetClientsProducts` (one id) | `scripts/whmcs-application-detail.ps1`          | Read one application's readable fields (mission, desired domain, legal status).                                  |
| `whmcs_client_products`    | `GetClientsProducts`                                              | wf 219                                          | List a client's services with product custom fields (names + masked values).                                     |
| `whmcs_client_lookup`      | `GetClients`                                                      | `Find-WhmcsClientIdByEmail`                     | Resolve a client id by exact email match (masked contact fields); the mirrored helper does not do domain lookup. |
| `whmcs_products_catalog`   | `GetProducts`                                                     | `scripts/whmcs-products-export.ps1`             | Non-PII product/custom-field catalog for field-name discovery.                                                   |

**Identify by domain, not by masked name.** The triage tables (209/210) show the applicant's
personal first name, not the org; the org name is only inside the mission text. So
`whmcs_application_search` scans product name + custom-field values (HTML-stripped), exactly as the
221 script does — matching on a guessed name-initial finds the wrong charity.

## PII masking (server-side, non-negotiable)

Apply the masking from `scripts/whmcs-application-search.ps1` verbatim, before any value leaves the
server — the MCP surface must never be less protective than the 209/210 conventions:

- **Personal names** (`first`/`last`/`your`/`contact`/`poc` name fields, but NOT
  org/charity/company/nonprofit/foundation names) → first initial + `***`.
- **EIN / tax id** → `***` (fully redacted).
- **Email** → `***@domain`.
- **Phone** → `***`.
- **HTML** (WHMCS wraps URL answers in `<a href>…</a>`) → stripped for both matching and display.

Org name, desired domain, mission text, product name, service status, and registration date are the
_signal_ of an onboarding application and are returned unmasked. Masking is applied by field-name
heuristic (see `Format-MaskedField`); the server should keep that heuristic and its allow/deny word
lists in one shared module so `#769`'s Zeffy server can reuse it.

## Decisions needing maintainer sign-off (before Tranche A)

These are the ambiguities that keep this at "design" rather than "build"; a scheduled worker should
not choose them unilaterally:

1. **Language / SDK** — Node + `@modelcontextprotocol/sdk` (proposed, matches the ecosystem and the
   repo's existing Node tooling) vs Python. _Recommendation: Node._
2. **Transport** — local **stdio** (proposed: the server needs Key-Vault-sourced creds + the APIM
   key, so it runs where those are available — a developer/admin context, not a public endpoint) vs
   a hosted remote connector like Candid's. _Recommendation: stdio for v1;_ revisit remote only if
   sessions need it without local creds.
3. **Location in repo** — `mcp/whmcs/` (proposed) with its own `package.json`, kept out of the root
   CI build so it does not perturb Validate Repository.
4. **`.mcp.json` registration** — whether to register the stdio server in the repo `.mcp.json` (with
   an env-var-driven command) or leave it operator-configured. Note the sandbox caveat below.
5. **Read-only scope confirmation** — v1 is read-only (issue says so); confirm no write tools
   (`AddClient`/`AddOrder`/…) until a separate, gated design.

> **Sandbox note (same as Candid):** Claude Code on the web only exposes MCP servers configured as
> org connectors, so a repo-level stdio entry mainly benefits **local/desktop/VS Code** sessions
> cloned from this repo. The Actions workflows (219/221) remain the path that works from the web
> sandbox.

## Delivery tranches

- [ ] **Tranche A — scaffold + flagship tool (sandbox-doable, no gate).** `mcp/whmcs/` package,
      `@modelcontextprotocol/sdk` stdio server, shared APIM client (host allowlist + retry) and the
      shared masking module, `whmcs_application_search`, and unit tests that mock the APIM `fetch`
      (no live creds). Verifiable in CI on plain Node.
- [ ] **Tranche B — remaining read tools.** `whmcs_application_detail`, `whmcs_client_products`,
      `whmcs_client_lookup`, `whmcs_products_catalog`, each with masked-output tests.
- [ ] **Tranche C — live smoke (needs a credentialed actor).** Run the server against live APIM via
      `az` device-auth (KV creds), confirm `whmcs_application_search` returns a known application
      (e.g. the client-419 fixture from prior retros) with correct masking. Not sandbox-doable —
      needs the human device-code step.
- [ ] **Tranche D — deferred: write tools.** Explicitly out of scope for this issue; any write
      surface (`AddClient`/`AddOrder`/ticket replies) is a separate design with its own approval
      gate.

Each unchecked tranche becomes (or is tracked by) a follow-up issue so `#770` closes when v1 (A–C)
is live.

## Non-goals

- **No writes** in v1 (see Tranche D).
- **No donor / full-PII exposure** — the server never returns more than the 209/210-masked view.
- **Not a replacement for the audited Actions path** — anything a workflow or audit depends on still
  runs 219/221 so the result is a committed artifact.

## Acceptance criteria (for the build that follows this spec)

- Server reads creds only from the environment; no credential is written, cached, logged, or copied
  into GitHub/on-disk (Key Vault stays master).
- Host allowlist + `https`-only enforced exactly as `Invoke-WhmcsApi` does; a non-allowlisted
  `WHMCS_API_URL` is a hard error.
- Every read tool returns 209/210-masked output; masking logic is shared (reusable by `#769`).
- Tranche A ships with unit tests that pass on plain Node in CI (mocked APIM; no live creds).
- `docs/whmcs-mcp-server-design.md` (this file) referenced from the build PR.

## References

- Issue [#770](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/770) (this
  server), [#769](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/769) (Zeffy
  MCP, same scaffold),
  [#724](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/724) (AI-Management
  migration this was migrated from).
- `scripts/whmcs-api-common.ps1` — credential resolution, `Invoke-WhmcsApi` (host allowlist +
  retry), the WHMCS actions.
- `scripts/whmcs-application-search.ps1` — the flagship read + the masking rules to port.
- `docs/whmcs-apim-routing.md` — the APIM/Key-Vault credential path and security model.
- `docs/candid-api-and-mcp.md` — the two-surface pattern this mirrors.
