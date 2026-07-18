# Runbook: Google Voice metrics pull (human-in-the-loop)

The Google Voice texts that FFC charities and volunteers send arrive in a **personal** Google
account, so this step is **never automated** (no stored Google credential — see
`docs/uncaptured-comms-discovery.md`). It is run by an authorized operator in an interactive session
using their own Google sign-in, and produces a **PII-masked summary** that feeds the metrics.

## When to run

Monthly (alongside the `impact.json` refresh) and once a year for the Candid Platinum update.

## Prerequisites

- An interactive session authenticated as the operator's own Google account (the one that receives
  the Google Voice forwards). Do **not** create or store a long-lived token / refresh token for
  this.
- Read-only access is enough.

## Steps

1. **Pull** the Google Voice text forwards:
   - Gmail search: `from:txt.voice.google.com after:YYYY/MM/DD` (use a 3-year window for a backfill,
     or last-month for the recurring run). The label is `# Google Voice`; subjects look like
     `New text message from <name-or-org> (NNN) NNN-NNNN`.
   - Paginate to exhaustion to get an exact thread count — do not trust the result-count estimate.
2. **Classify** each thread:
   - **Charity / FFC** — a nonprofit, website, domain, hosting, onboarding, Candid/GuideStar, or
     volunteer coordination for FFC.
   - **Noise** — parking receipts, 2FA / verification codes, deliveries, recruiting, insurance,
     personal/family, spam. Exclude these.
3. **Extract** from charity threads: organization name and/or domain, contact phone, first-seen
   date, and intent.
4. **Reconcile** each candidate against the onboarded set:
   - GitHub `FFC-EX-*` repos (org `FreeForCharity`) → strip the prefix for the onboarded domains,
     and
   - `sites-list/sites_list.json` in `FFC-Cloudflare-Automation`.
   - A candidate with no match is an **uncaptured lead**.
5. **Produce the masked summary** (this is the only thing that leaves the session):
   - total Google Voice text threads in the window,
   - count charity/volunteer-related vs noise,
   - the list of uncaptured-lead **org names / domains** (no personal data).
6. **Hand off** the numbers:
   - Update `src/data/impact.json` in freeforcharity.org (`uncapturedLeads`, and the
     `textMessagesHandled` note) via a PR, and
   - drop the uncaptured-lead org/domain list into the onboarding pipeline.

## PII rules (hard)

- Mask using the same conventions as the existing exports: a person's name to a first initial +
  `***`, an email to `***@domain`, a phone number to its last 4 (e.g. `****1515`) — or omit it.
- Org names and **org** domains may appear unmasked; treat a **personal** domain/site/handle as PII.
- The raw, unmasked thread content stays in the interactive session only — never in the repo, an
  issue/PR body, a comment, or a commit message.

## Reference

- Design: `docs/uncaptured-comms-discovery.md`
- Tracking: #492 (Gap B), epic #490
