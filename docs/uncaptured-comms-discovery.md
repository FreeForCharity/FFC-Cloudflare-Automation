# Uncaptured communications discovery (email + text)

Design for **Gap B** of the metrics epic (#490), tracked in #492: surface Free For Charity charity
communications that are **not yet in WHMCS / the sites-list**, so we feed the onboarding pipeline
and stop undercounting reach.

This is a design + interface spec. It deliberately ships **no runnable workflow yet** because it
introduces a new credential domain (Google) into an otherwise Azure/Cloudflare/WHMCS-centric repo —
see "Auth" below for the decision needed before implementation.

## Why

WHMCS is not a complete source of truth (legacy WordPress sites aren't all in it, and "charity
partner" isn't a defined field — #491). On top of that, many charity inquiries arrive by **email and
text message and never become WHMCS records at all**. A live look at the inbox confirms real,
actionable signal today (see the masked evidence at the bottom): an already-onboarded charity asking
about its site and Candid, plus prospective charities with no record in the system.

## Sources & auth

| Source                                                                                 | What it yields                          | Auth (proposed, KV-backed per repo convention)                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Microsoft 365** shared mailboxes (`contact@`, `support@`, `info@freeforcharity.org`) | Inbound org name, sender domain, intent | Reuse OIDC → Key Vault (as WHMCS/Zeffy do); add a Graph app registration with `Mail.Read` **plus an Exchange Online application access policy** to restrict it to those mailboxes (Graph application permissions are tenant-wide by default — the access policy is what actually scopes them)                                                                                                                                                                                                               |
| **Google Voice → Gmail** texts                                                         | Org/person, phone, intent               | Texts forward into a Gmail mailbox under label `# Google Voice`, sender `*@txt.voice.google.com`. These arrive in a **consumer** Gmail inbox, so access needs an **OAuth client + one-time user consent + stored refresh token** (least-privilege **read-only** `gmail.readonly` scope) in a new scoped KV secret — a service account would only work with Google Workspace + domain-wide delegation, not a consumer mailbox. _Design decision to confirm, since the repo has no Google integration today._ |
| **Gmail contacts**                                                                     | Known charity contacts/orgs             | Same Google credential, **read-only** People API `contacts.readonly` scope                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Onboarding form** submissions                                                        | Structured charity intent               | Normalize to the same shape (wherever they currently land)                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

> **Decision needed (blocks implementation):** where the Google credential lives. Recommended: a
> `*-ffc-google-*` scoped secret in `kv-ffc-admin-prod-cbm`, loaded by a new
> `google-secrets-from-kv` composite action mirroring `whmcs-secrets-from-kv` — keeping Key Vault as
> the single source of truth.

## Pipeline

1. **Pull** recent messages from each source over a configurable window (`-SinceDays`, default e.g.
   365; the inbox supports looking back further for a one-time backfill).
2. **Classify** charity-signal vs noise. Drop parking receipts, 2FA / verification codes, and
   personal messages. Extract `org`, `domain`, `phone`, `firstSeen`, `intent`.
3. **Reconcile** each candidate domain/org against:
   - `sites-list/sites_list.json` (the reconciled domain inventory), and
   - WHMCS `GetClients` (via the existing APIM path). A candidate with no match is an **uncaptured
     lead**.
4. **Emit**:
   - `artifacts/discovery/pipeline.csv` — PII-masked, `retention-days: 7`.
   - Aggregate counts to the job step summary (no per-person rows).
   - An `uncapturedLeads` count + `textMessagesHandled` count for the freeforcharity.org
     `impact.json` derivation (#493).

## Privacy (hard requirements)

Follow the same rule as the Zeffy exports:

- **Mask PII using the same conventions as the existing exports** (don't invent a new scheme): a
  person's name to a first initial + `***`, an email to `***@domain`, a phone number to its last 4
  (e.g. `****1515`) — or omit the field entirely, as the Zeffy exports do. Apply this in any
  committed/persisted artifact, issue, PR, comment, or step summary.
- As a **project policy**, organization names and **org** domains may appear unmasked (the
  sites-list already publishes every org domain). A domain can still be personal data when it
  identifies an individual, so treat any **personal** domain or identifier (a personal site, an
  individual's name/handle, a sole-proprietor domain) as PII and mask it like name/email/phone.
- Raw, unmasked candidate lists live only in the retention-capped Actions artifact — never in the
  repo, an issue/PR body, or a commit message.

## Proposed workflow (to implement once auth is decided)

`47-discover-uncaptured-comms.yml` — `workflow_dispatch` only, `windows-latest`, `pwsh`, validates
that the required secrets resolved, calls `scripts/discover-uncaptured-comms.ps1`, writes counts to
the step summary, and uploads the masked `pipeline.csv` artifact (`retention-days: 7`,
`if-no-files-found: error`). One source per script helper, mirroring the existing numbered export
workflows.

## Live evidence (PII-masked, 2026-06-30)

Three real Google Voice texts, illustrating each disposition:

- **Already onboarded** — `theafghanistanaffairs.org` (has an `FFC-EX` repo), POC `****8351`, asking
  about finalizing the site and _"For Candid, do I need to do anything?"_ → reconciles to an
  existing site; not a new lead, but shows support volume the system doesn't currently count.
- **Uncaptured lead** — `LoveMustWin.org`, POC `****1515`: _"get everything in one spot for my
  website … I submitted a ticket."_ → a charity actively engaging with no clean record in the
  metrics inventory.
- **Uncaptured prospect** — POC `****1706`: a prospective senior-community initiative weighing
  whether to become a nonprofit → top-of-funnel lead invisible to WHMCS today.

Noise correctly excluded in the same window: parking receipts, a Zeffy verification code.

Refs #490, #492
