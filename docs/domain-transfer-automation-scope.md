# Domain Transfer Automation — Scope

- **Branch:** `claude/domain-transfer-automation-pb8abt`
- **Parent project:**
  [#157 — Transition eNOM domains to Cloudflare Registrar](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/157)
- **Implementation:** see [domain-transfer-automation.md](domain-transfer-automation.md) for usage;
  the in-scope items below are now built (workflows 14/16/25).

## Purpose

This document pins the scope of the **domain transfer automation** so it does not duplicate adjacent
work already in flight. It covers the move of existing eNOM-registered domains to **Cloudflare
Registrar** under the Free For Charity account — distinct from buying brand-new domains and distinct
from building the transition inventory.

## The hard constraint: transfers are dashboard-only (today)

The Cloudflare **Registrar API (beta)** can search, check availability/pricing, and **register new**
domains. It **cannot initiate an inbound registrar transfer**. Verified against the official API
reference and the Registrar API guide ("Transfers are not yet available through the API"; renewals
and contact updates are likewise not yet available).

The Cloudflare **dashboard**, by contrast, has a full transfer-in flow, which is why eligible eNOM
domains already show as transferable there. So the transfer itself has exactly **one unavoidable
manual step** (dashboard transfer-in + auth/EPP code entry). Everything around that step is
automatable, and that surrounding work is what this branch owns.

## EPP / auth-code retrievability (open risk)

Whether the transfer auth code is returned in a **copy-pasteable** form is not guaranteed and must
be probed before relying on it:

- **WHMCS `DomainRequestEPP`** returns an `eppcode` field **only if the registrar module supports
  inline return**. Otherwise it returns success with no code and the code is emailed directly to the
  registrant.
- **eNOM** has a documented path to **email** the EPP key; inline retrieval needs direct eNOM
  reseller API credentials, which today live inside the WHMCS registrar-module config.

Therefore the preflight includes a **read-only EPP-retrieval probe** that reports, per registrar,
whether the code comes back inline or email-only. No transfer is initiated by the probe.

## In scope (this branch)

The transfer-execution/readiness layer, sitting between the inventory (#325) and the manual
dashboard transfer-in:

1. **Read-only transfer-readiness preflight** — per domain: registrar-lock state, 60-day
   post-registration/transfer lock window, expiry buffer, WHOIS-privacy state, nameservers already
   pointed at Cloudflare. Runs standalone, and consumes #325's
   `enom_cloudflare_transition_inventory.csv` when it is available.
2. **EPP-retrieval probe** — determines inline vs email-only auth-code delivery; read-only,
   initiates nothing.
3. **Per-domain dashboard runbook** — the exact manual transfer-in steps and where the auth code
   will appear, generated per domain.
4. **Post-transfer verification** — confirms registrar is now Cloudflare, nameservers are correct,
   and the site is still Live (reuses existing status workflows).

## Out of scope (owned elsewhere — do not duplicate)

| Concern                                                   | Owner                                                                                              | Notes                                                                                  |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Inventory / Cat1–Cat3 categorization / sub-issue creation | PR #325 (`enom-cloudflare-transition-inventory.ps1`)                                               | Produces the domain list this branch consumes.                                         |
| Buying brand-new domains via Registrar API                | Merged: #428 (`cloudflare-domain-register.ps1`, `cloudflare-registrar-access-check.ps1`, wf 20/21) | New registrations, not transfers. Now on `main`.                                       |
| Duplicate of #428                                         | PR #356                                                                                            | Same script, older Copilot version — superseded by the merged #428, recommend closing. |
| API-initiated transfer                                    | Cloudflare                                                                                         | Not supported by the API; revisit when the beta adds it.                               |
| Nameserver update in WHMCS                                | `whmcs-domain-nameservers-update.ps1` + the Add-Domain workflow (display 02)                       | Reused, not reimplemented.                                                             |

## Sequence (end to end)

```
#325 inventory  ->  [this branch] preflight + EPP probe  ->  per-domain runbook
                ->  (manual) Cloudflare dashboard transfer-in  ->  [this branch] verification
```
