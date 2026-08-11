# Minority Wealth Gap — duplicate accounts / multi-org application report

**Read-only WHMCS report, 2026-08-11.** Nothing was mutated: every workflow below runs on the
ungated `whmcs-prod-read` lane. This documents what the sweep found and what has to happen to
correct it. **No corrective action has been taken** — the fixes need decisions and WHMCS admin-UI
work that no workflow in this repo can perform (see [Actions](#actions)).

## How this was produced (re-runnable)

| Workflow                        | Query / input        | Run                                                                                                 | Result                                |
| ------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 221. WHMCS - Application Search | `minority`           | [31451757832](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451757832) | 4 matches / 579 services scanned      |
| 221. WHMCS - Application Search | `wealth`             | [31451794709](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451794709) | 3 matches                             |
| 221. WHMCS - Application Search | `blackelite`         | [31451970058](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451970058) | 1 match                               |
| 221. WHMCS - Application Search | `bernice`            | [31451974399](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451974399) | 4 matches — **found the 2nd account** |
| 210. WHMCS - Orders Triage      | Pending,Fraud,Active | [31451800858](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451800858) | Pending 40 / Fraud 149 / Active 541   |
| 219. WHMCS - Application Detail | `client_id=444`      | [31451999103](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31451999103) | orders 797, 803, 805                  |
| 219. WHMCS - Application Detail | `client_id=447`      | [31452184092](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31452184092) | order 801                             |

**The single-term search was not enough, and that is the reusable lesson.** Searching `minority` and
`wealth` both returned _only client 444_ and would have been reported as "one account, some
duplicate orders". The second account surfaced only when the sweep was re-run on `bernice` — the
**program** name — because that account's record carries the org name nowhere the search can see it.
When checking for duplicate accounts, sweep the program and alias names too, not just the charity's
name.

## What is actually there

Two WHMCS client accounts for the same organization, and three distinct charity identities across
four orders.

### The two accounts

| Client  | Company name (verbatim)                 | Client status | Contact | Client-level legal status                         | EIN / Candid on file |
| ------- | --------------------------------------- | ------------- | ------- | ------------------------------------------------- | -------------------- |
| **444** | `Minority Wealth Gap`                   | Active        | `D***`  | 3. Pre 501c3 Shelter Water Hunger Org             | 88-3800775 + Candid  |
| **447** | `Minority Wealth Gap ` (trailing space) | **Inactive**  | `J***`  | 9. Ongoing Nonprofit Project (not pursuing 501c3) | **both blank**       |

Both contacts are on `@minoritywealthgap.org`, so this is the same organization filed twice by two
different people — the reported "different volunteers" pattern. The trailing space in 447's company
name is why the two do not collide on an exact-name match.

Note the inconsistency inside 447: the **client** is `Inactive` while the **order and service it
owns are `Active`**. An inactive account is holding a live deliverable.

### The four orders

| Order   | Client | Service | Product                      | Status    | Placed     | Charity identity in the form         |
| ------- | ------ | ------- | ---------------------------- | --------- | ---------- | ------------------------------------ |
| **797** | 444    | 618     | FFC 501c3 Onboarding         | Active    | 2026-07-24 | Minority Wealth Gap (EIN 88-3800775) |
| **801** | 447    | 622     | Free Charity Website (Pages) | Active    | 2026-07-29 | **Bernice Women Uganda**             |
| **803** | 444    | 624     | Free Charity Website (Pages) | Active    | 2026-08-04 | **BlackElite Foundation**            |
| **805** | 444    | 626     | Free Charity Website (Pages) | **Fraud** | 2026-08-05 | **Bernice Women Uganda**             |

## The three findings, in priority order

### 1. Two organizations have accepted website orders but no onboarding application of their own

This is the substantive one; the duplicate accounts are a symptom of it.

Only **Minority Wealth Gap** has a vetted onboarding application — order 797, the pid-33 501(c)(3)
track, accepted 2026-07-25 with EIN 88-3800775 and a live Candid profile. **BlackElite Foundation**
(order 803) and **Bernice Women Uganda** (orders 801 and 805) each have a website order in `Active`
status and **no pid 16/33 onboarding application anywhere in WHMCS** — the `blackelite` and
`bernice` sweeps found no onboarding product for either. Every one of those website forms
nonetheless ticked _"I confirm our charity onboarding application has been filed"_.

The consequence if this is left alone: FFC builds and publishes sites for two organizations it has
never vetted, riding on a third organization's 501(c)(3) determination. The applications themselves
say the three are a coalition rather than one entity — 624's SEO keywords list _"Blackelite
Foundation, Uganda, Minority Wealth Gap, Bernice Women"_, and 624's own programs are titled
"Domestic Violence: Bernice Women Africa". They are also materially different charities on paper:
BlackElite founded 2023, Bernice Women founded 2015, and both operate from **Entebbe / Kajjansi,
Uganda**, while MWG's vetted application gives a **Los Angeles, CA** footer and a US EIN.

That gap — a US 501(c)(3) determination covering Uganda-based program entities — is the question
that has to be answered before any of these three sites is built. It is a judgement call about FFC's
eligibility rules, not something the data settles.

### 2. Orders 801 and 805 are the same Bernice Women application, filed twice under different accounts

Field-by-field, service 622 (client 447) and service 626 (client 444) are the same submission:
identical tagline ("Empowering Women. Restoring Dignity. Ending Violence."), identical brand color
`#7A1F5C`, identical founding year 2015, identical three programs, identical three team members and
roles, identical three testimonials, identical impact stats, identical Facebook / X / LinkedIn, and
the same Zeffy URL `…/page/FUNQGKCPGUL`. They differ only in two fields: 622 carries a logo URL that
626 lacks, and 626 carries a primary contact email that 622 lacks.

The later copy, **order 805, is already flagged `Fraud`** — almost certainly the duplicate-detection
firing on exactly this. Whichever copy is kept, one of the two must be cancelled.

### 3. The duplicate never appears in the tooling built to catch duplicates

Workflow **226 (Application Triage)**'s `reconcile-report` mode is the repo's duplicate finder, and
it cannot see this case, for two independent reasons — either one alone would be enough to hide it:

- **It groups candidates by `clientid`** (`Get-ReconciliationPlan`,
  `scripts/whmcs-application-triage.ps1`). A duplicate filed under a _different_ client account is
  structurally invisible to it. That is this case exactly.
- **It only considers `Pending` pid-16/33 onboarding orders.** All four orders here are website
  orders in `Active`/`Fraud` status; `reconcile` would skip each with `not-onboarding-order` or
  `already-active` even if handed the ids explicitly.

So "226 reports no duplicates" is not evidence that there are none. This is a real coverage gap
worth its own issue, not a footnote — cross-account duplicates are precisely the ones a human is
least likely to notice by hand, because the two rows never appear next to each other in any view.

## Actions

Nothing here is blocked on a missing credential or a broken workflow; it is blocked on decisions and
on WHMCS admin-UI operations that have no API.

**Good news on cost:** nothing downstream has been provisioned yet — there is no
`FFC-EX-minoritywealthgap.org` (or BlackElite / Bernice) repo in the org, and no row in
`sites-list/sites_list.csv` for any of the three. So this can be corrected purely inside WHMCS, with
no site to unpublish, no DNS to unwind, and no repo to archive. That will stop being true the moment
701/702 runs for any of them.

### Decisions needed first (@clarkemoyer — nobody else can make these)

1. **Are BlackElite Foundation and Bernice Women Uganda eligible in their own right?** Three
   outcomes, and they lead to different cleanups: (a) they are programs _of_ MWG and belong as
   sections of the MWG site — cancel both extra website orders; (b) they are separate charities FFC
   will serve — each must file its own onboarding application and be vetted on its own EIN before
   any website order proceeds; (c) they are not eligible — cancel and inform.
2. **Which Bernice Women copy survives**, 801 (client 447) or 805 (client 444)? This follows from
   which account is kept.
3. **Which client account is canonical?** 444 is the better record — it is Active, carries the EIN
   and Candid profile, and owns the accepted onboarding order. 447 is Inactive with every identity
   field blank. Recommend keeping **444** and retiring **447**.

### WHMCS admin-UI work (no API exists for these)

- **Merge or close client 447 into 444.** WHMCS exposes no client-merge API, so this is manual. At
  minimum, close 447 and re-home or cancel its order 801 so an `Inactive` client is not holding an
  `Active` deliverable.
- **Clear the `Fraud` flag on order 805, or cancel it.** Leaving it parked as `Fraud` is the one
  outcome to avoid — it is neither a decision nor a record of one.
- **Fix the trailing space** in 447's company name if the account is retained for any reason. It is
  what let the duplicate evade a name match, and it will do so again.

### Once decided, the workflows that can execute it

- **Cancelling the redundant website orders**: `226` reconcile cannot do it (see finding 3 — wrong
  pid, wrong status, and it would refuse them). Cancel in the WHMCS admin UI, or extend 226 first.
- **Backfilling the domain** on service 618 if MWG proceeds: workflow **230. WHMCS - Record Field
  Set** (`target=service`, `field=domain`) — gated, dry-run by default.
- **Do not run 701 / 702** for any of the three until decision 1 is settled. Provisioning a repo is
  the step that makes this expensive to reverse.

### Automation follow-ups worth filing

- **Cross-account duplicate detection.** Extend 226's `reconcile-report` to group by an identity key
  that survives a new account — contact email domain, EIN, or normalized org name — in addition to
  `clientid`. Today two accounts are two islands.
- **Website orders should assert a real onboarding application.** The "I confirm our onboarding
  application has been filed" checkbox is self-attested and was ticked by two orgs that had none. A
  read-only check — every `Active` website order maps to a pid-16/33 application for the _same_
  charity — would have caught orders 801 and 803 on the day they were placed.

## Caveats on completeness

**Every sweep reported 14 unreadable service records** (`unreadableCount: 14`, indexes 2, 6, 7, 10,
11, 13, 17, 18, 19, 22, 23, 24, 27, 28 — the malformed-UTF-8 rows described in PR #868). So this
report can say what it _found_; it cannot prove there is no third account hiding in those 14 rows.
Those records are real corruption in WHMCS and are worth repairing in the admin UI independently of
this case.

Workflow 221 matches a **contiguous case-insensitive substring** against product names and custom
field values. It found client 447 only via `bernice`; an account that shares neither the org name
nor a program name with the ones swept here would not appear. If the concern is "are there more",
the durable answer is the cross-account duplicate detection proposed above, not more hand-run
substring sweeps.
