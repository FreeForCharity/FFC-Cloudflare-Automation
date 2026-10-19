# Runbook: Google Voice metrics pull (human-in-the-loop)

The Google Voice texts that FFC charities and volunteers send arrive in a **personal** Google
account, so this step is **never automated** (no stored Google credential — see
`docs/uncaptured-comms-discovery.md`). It is run by an authorized operator in an interactive session
using their own Google sign-in, and produces a **PII-masked summary** that feeds the metrics.

## When to run

Monthly (alongside the `impact.json` refresh) and once a year for the Candid Platinum update.

Run the **lead sweep** (steps 1 to 4 and step 7) **weekly**. Charities that text an application
question wait for an answer. On 2026-10-18 a four-week window held nine charities that no tracker
had, and one of them had placed an order three weeks earlier.

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
   - Two quick tells for a charity thread. A saved contact named `Name, (org or domain)` is already
     labelled. An **unsaved number** whose text mentions an application, order number, domain,
     website, EIN or 501(c)(3) is almost always a new charity, and is the case most likely to be
     missed.
3. **Extract** from charity threads: organization name and/or domain, contact phone, first-seen
   date, and intent.
   - Read the whole thread, not the subject line. Many applicants give the org name only in the
     second or third text.
   - When the org name never appears, save the number in Google Voice as `Name, (org)` once you
     learn it. Every later text then classifies itself.
4. **Reconcile** each candidate against the onboarded set, **by org name as well as by domain**.
   Texts usually name the org, not the domain:
   - GitHub `FFC-EX-*` repos (org `FreeForCharity`). Match the stripped domain **and** the repo
     description, which often carries the org's name (`FFC-EX-freedomrisingusa.org` is "Site for the
     State College 4th of July", which texts call "4th Fest").
   - `sites-list/sites_list.json` in `FFC-Cloudflare-Automation`. It only lists domains already in
     WHMCS, Cloudflare or a repo, so a brand-new applicant is never in it yet.
   - Hub issues, open **and** closed, titles **and** bodies. Enumerate them with paginated REST
     (`issues?state=all&per_page=100`), not `gh search`, which lags (see `CLAUDE.md`).
   - A candidate with no match is an **uncaptured lead**.
5. **Produce the masked summary** (this is the only thing that leaves the session):
   - total Google Voice text threads in the window,
   - count charity/volunteer-related vs noise,
   - the list of uncaptured-lead **org names / domains** (no personal data).
6. **Hand off** the numbers:
   - Update `src/data/impact.json` in freeforcharity.org (`uncapturedLeads`, and the
     `textMessagesHandled` note) via a PR, and
   - put each uncaptured lead on the tracker (step 7).
7. **Track each uncaptured lead** as one hub issue, then check WHMCS for it:
   - Title `[CHARITY LEAD] <Org name> (<domain>)`. Leave out the domain when it is not known yet. Do
     not use the `[WEBSITE REQUEST]` prefix, which is reserved for the provisioning form.
   - Label `website-request`. **Do not assign the issue.** Assigning it fires
     `701. Website - Provision`.
   - The body follows the PII rules below. Include the source and date ("Google Voice text,
     2026-10-15"), the org, the domain if known, a masked contact (`N***`, phone ending `7448`),
     what they asked in one or two sentences, and the next step. Leave out anything they texted that
     is a credential.
   - Then dispatch `221. WHMCS - Application Search` with `query` = a distinctive word from the org
     name or domain and `issue_number` = the new issue. It posts a masked result on the issue, which
     shows whether an application or order already exists. Dispatch these **one at a time**: 221
     uses a single concurrency group, so a third queued dispatch cancels the one waiting.
   - A number that never gave an org name does not get an issue. Reply asking for the org name and
     the domain, and pick it up next week.

## PII rules (hard)

- Mask using the same conventions as the existing exports: a person's name to a first initial +
  `***`, an email to `***@domain`, a phone number to its last 4 (e.g. `****1515`) — or omit it.
- Org names and **org** domains may appear unmasked; treat a **personal** domain/site/handle as PII.
- The raw, unmasked thread content stays in the interactive session only — never in the repo, an
  issue/PR body, a comment, or a commit message.

## Reference

- Design: `docs/uncaptured-comms-discovery.md`
- Tracking: #492 (Gap B), epic #490
