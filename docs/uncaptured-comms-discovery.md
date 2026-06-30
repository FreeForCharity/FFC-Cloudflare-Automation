# Uncaptured communications discovery (email + text)

Design for **Gap B** of the metrics epic (#490), tracked in #492: surface Free For Charity charity
communications that are **not yet in WHMCS / the sites-list**, so we feed the onboarding pipeline
and stop undercounting reach.

**Two-track by design:**

- **Microsoft 365** mailboxes are org-owned and **can be automated** (OIDC → Key Vault → Graph, like
  the other exports in this repo).
- **Google Voice texts come from a personal Google account and must NOT be fully authorized for
  automation.** That pull is **human-in-the-loop**: an authorized operator (whoever is updating the
  metrics) runs it in an interactive session using their own Google sign-in, and hands a PII-masked
  summary to the pipeline. **No Google refresh token is ever stored in Key Vault or CI.**

## Why

WHMCS is not a complete source of truth (legacy WordPress sites aren't all in it, and "charity
partner" isn't a defined field — #491; note the new gid-6 catalog products give a usable partner
signal). On top of that, many charity inquiries arrive by **email and text message and never become
WHMCS records at all**. A live look at the inbox confirms real, actionable signal today (see the
masked evidence at the bottom): an already-onboarded charity asking about its site and Candid, plus
prospective charities with no record in the system.

## Sources & auth

| Source                                                                                 | Track                 | Auth                                                                                                                                                                                                                                                                                               |
| -------------------------------------------------------------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Microsoft 365** shared mailboxes (`contact@`, `support@`, `info@freeforcharity.org`) | **Automated**         | Reuse OIDC → Key Vault (as WHMCS/Zeffy do); add a Graph app registration with `Mail.Read` **plus an Exchange Online application access policy** to restrict it to those mailboxes (Graph application permissions are tenant-wide by default — the policy is what scopes them)                      |
| **Google Voice → Gmail** texts (personal account)                                      | **Human-in-the-loop** | Pulled in an authorized **interactive session** by the operator using their own Google sign-in (label `# Google Voice`, sender `*@txt.voice.google.com`). **Never** a stored automation credential / refresh token in Key Vault or CI. The session emits a masked summary that feeds the pipeline. |
| **Gmail contacts** (personal account)                                                  | **Human-in-the-loop** | Same interactive session / operator sign-in; not automated.                                                                                                                                                                                                                                        |
| **Onboarding form** submissions                                                        | **Automated**         | Normalize to the same shape (wherever they currently land).                                                                                                                                                                                                                                        |

> **Principle — no stored Google credential.** Putting a personal Google account's long-lived token
> into CI is out of scope. The automated workflow covers **only the org-owned sources** (M365 +
> onboarding forms) and the reconciliation; the personal Google Voice/Gmail pull is run by a human
> in an authorized session and merged in as a masked summary.

## Pipeline

1. **Pull** recent messages over a configurable window (`-SinceDays`; the inbox supports looking
   back further for a one-time backfill). M365 is pulled by the workflow; Google Voice is pulled by
   the operator in an interactive session.
2. **Classify** charity-signal vs noise. Drop parking receipts, 2FA / verification codes, and
   personal messages. Extract `org`, `domain`, `phone`, `firstSeen`, `intent`.
3. **Reconcile** each candidate domain/org against:
   - `sites-list/sites_list.json` (the reconciled domain inventory) — this is what the automated
     script reconciles against today, and
   - _(planned)_ WHMCS `GetClients` / gid-6 product holders via the existing APIM path. A candidate
     with no match in the available sources is an **uncaptured lead**.
4. **Emit**:
   - `artifacts/discovery/pipeline.csv` — PII-masked, `retention-days: 7`.
   - Aggregate counts to the job step summary (no per-person rows).
   - An `uncapturedLeads` count + `textMessagesHandled` count for the freeforcharity.org
     `impact.json` derivation (#493). The Google Voice contribution is the masked count the operator
     produced interactively.

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
- Raw, unmasked candidate lists live only in the retention-capped Actions artifact (M365 track) or
  the operator's interactive session (Google Voice track) — never in the repo, an issue/PR body, or
  a commit message.

## Components to build

- **Automated (this repo):** `47-discover-uncaptured-comms.yml` — `workflow_dispatch` only,
  `windows-latest`, `pwsh` — covering **only the M365 mailboxes + onboarding forms** and the
  reconciliation against `sites-list/sites_list.json` (WHMCS reconciliation planned). Validates
  secrets resolved, writes counts to the step summary, uploads the masked `pipeline.csv`
  (`retention-days: 7`, `if-no-files-found: error`).
- **Human-in-the-loop (runbook, not a workflow):** an authorized operator runs the Google
  Voice/Gmail discovery in an interactive session, producing a masked summary (counts + candidate
  org/domain list) that is merged into the derivation. This is deliberately NOT a CI job — no
  personal Google token is stored.

## Live evidence (PII-masked, 2026-06-30)

Pulled in an authorized interactive session (the human-in-the-loop model in action). A deeper scan
confirmed **≥ 3,650 Google Voice text threads** over the period (a floor — pagination was halted
before the full 3-year boundary; Gmail's own ~201 estimate is wrong), **~45% charity-related**, i.e.
**≥ ~1,640 charity/volunteer threads**. Three texts illustrate each disposition:

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
