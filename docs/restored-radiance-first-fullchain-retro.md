# Retro: first full-chain charity onboarding (restoredradiancefoundation.org, 2026-07-07)

The first end-to-end, agent-driven run of the whole onboarding chain — from a domain name to a live,
analytics-wired charity website — using **restoredradiancefoundation.org** (Restored Radiance
Foundation). This is the "what happened, what we learned, what we fixed" record. Operational
specifics live in the linked docs; this is the narrative and the lessons.

## What was accomplished (the chain)

| Phase           | Result                                                                                                                                           |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Domain purchase | `restoredradiancefoundation.org` registered via Cloudflare Registrar (**113**), $8.50, auto-renew on, FFC account                                |
| Zone + DNS      | Zone auto-created; GitHub Pages apex + `www` enforced (**701**)                                                                                  |
| Website repo    | `FFC-EX-restoredradiancefoundation.org` from the FFC template, Pages enabled, maintainer added (**701**)                                         |
| Site live       | https://restoredradiancefoundation.org (rebrand + analytics + real-501c3 content)                                                                |
| GA4             | Property `restoredradiancefoundation.org - GA4` (`G-7ZB8DM7LEF`) under **FFC Supported Sites** (`accounts/400204327`) — via new workflow **505** |
| GTM             | Container `GTM-WKKRTBK8`, GA4 tag seeded (**503**), published, serving on the live site                                                          |

## The big lesson: identify the application by DOMAIN, not by masked name

The chain's first step is "check WHMCS for the application." That step **initially matched the wrong
charity**, and the reason is instructive:

- The onboarding application has **no dedicated "Organization Name" field**. The org name is only
  embedded in the **mission text** (a product custom field). Client-level `companyname` is always
  empty.
- The masked triage tables (209/210) show the **applicant's personal first name** (e.g. `A***`), not
  the org. Searching for `R***` (from "Restored Radiance") matched two unrelated charities whose
  applicants happened to have R-names (Desert Princess Community Foundation, South Texas Watchmen) —
  the real applicant's first name starts with `A`.
- The application answers (org name, desired domain, mission, EIN) are **product custom fields** on
  the onboarding service, readable only via `GetClientsProducts` (which returns field **names**),
  not `GetClientsDetails` (client fields, no names).

**Resolution:** the real application was found from the **order number** the charity texted
(`1572214305` → WHMCS client **419**), then confirmed by a direct API read. To make this a
one-command lookup in future, we built **workflow 221 (WHMCS Application Search)** — sweep
`GetClientsProducts`, match a domain/org substring, return the client id + readable application.

> Correct onboarding order going forward: **domain → 221 (find the application by domain) → confirm
> client id → run the chain from the confirmed application**, not from a domain string alone.

## Automation built or hardened during this run

| Item                                                                                                                 | Why                                                                                  |
| -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **219** WHMCS Application Detail (+ `GetClientsProducts`)                                                            | Read one client's full application (org name/mission/desired domain) with PII masked |
| **221** WHMCS Application Search                                                                                     | The missing domain/org → client-id lookup                                            |
| **505** Google GA4 Property Provision                                                                                | Productionized the GA-property step of onboarding                                    |
| **503/505** issue comment-back, **503** `tagmanager.edit.containerversions` scope fix                                | Publish + agent-readable ids                                                         |
| **whmcs-prod-read** environment                                                                                      | Ungate read-only WHMCS workflows (reads shouldn't sit at an approval gate)           |
| Docs: GA account name `FFC Supported Sites`, DWD canonical scope list, CLAUDE.md sandbox-dispatch + globaladmin path | Correct defaults + fewer round-trips                                                 |

## Findings handed to the operator

- **M365 broken by a typo** — the `github-oidc-m365-prod` federated credential subject had a
  trailing hyphen (`FFC-Cloudflare-Automation-`), so every M365 job failed `AADSTS700213`.
  **✅ Fixed & verified 2026-07-07 (issue #625)** — 101/301/302 M365 jobs green. Repair recipe in
  [azure-oidc-federated-credentials.md](azure-oidc-federated-credentials.md).
- **Intake gap** — the onboarding form has no discrete "Organization Name" question, so
  `companyname` is never populated. Recommended: add an Organization Name field (product custom
  field on the onboarding product) so it flows to `companyname` and every downstream step.
- **DWD scopes** — consolidated canonical list documented so new Google features don't each require
  an Admin-console round-trip (see [google-api.md](google-api.md)).
- **Two more real pending applications** surfaced while searching: Desert Princess Community
  Foundation (`dpfoundation.org`) and South Texas Watchmen (`southtexaswatchmen.org`).

## Azure IAM + GitHub settings — ✅ applied 2026-07-07 (issue #625)

The agent can read Azure and query WHMCS directly (via `az` device-auth in the sandbox), but **Azure
AD IAM writes are blocked by the harness** in auto-mode. These were applied in an interactive session
(admin `clarkemoyer@freeforcharity.org`, device-code login after an MFA refresh):

1. ✅ Fixed the `m365-prod` credential typo on the Graph CLI app. The optional kv-reader
   `m365-prod` credential was **not needed** — 301's second login runs under `cloudflare-prod-read`.
2. ✅ Added the `whmcs-prod-read` federated credential on the kv-reader app + both repo Variables,
   created the ungated `whmcs-prod-read` environment, and confirmed KV RBAC access.

Verified end-to-end: M365 jobs **101/301/302** green (no `AADSTS700213`); WHMCS reads
**202/201/209** run ungated and green; gate audit **730** green. Recipes remain in
[azure-oidc-federated-credentials.md](azure-oidc-federated-credentials.md) for future repairs.
