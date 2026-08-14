# Mindful Moves — application intake review (client 432)

Pre-approval review of the **Mindful Moves** pre-501(c)(3) onboarding application, triggered by the
applicant following up by SMS on the status of a 2026-07-07 submission.

**Status: not approved — four decisions are open, and one of them is a hard blocker.** Nothing has
been provisioned. No domain was registered, no zone created, no repo provisioned, no WHMCS record
written. This document is the decision package; the actionable tracker is the linked issue.

Personal details follow [`pii-classification.md`](./pii-classification.md): the applicant is a
natural person who filed an application, so their name is rendered as an initial and their phone
number is withheld. Organization name, mission, legal status and EIN are `public` by that policy and
are printed in full.

## The application (verified, not guessed)

Located with **221. WHMCS - Application Search** (`query=Mindful`,
[run 31839363031](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/31839363031))
— the domain/org sweep, not the masked triage tables, per the standing rule that the triage tables
show the applicant's personal first name and will match the wrong charity. **583 client-products
scanned, exactly 1 match**, so the identification is unambiguous.

| Field              | Value                                        |
| ------------------ | -------------------------------------------- |
| WHMCS client id    | **432**                                      |
| Service id         | **583**                                      |
| Product            | FFC Pre-501c3 Nonprofit / Charity Onboarding |
| Order status       | **Pending**                                  |
| Registered         | **2026-07-07**                               |
| Domain on record   | _(empty)_                                    |
| Legal status       | pre-501c(3) Nonprofit                        |
| EIN                | 42-3645005 _(unverified — see decision 4)_   |
| Desired domain     | `https://MindfulMoves.org`                   |
| Has a domain?      | No                                           |
| Has web hosting?   | No                                           |
| Current host       | `https://none.com` _(placeholder)_           |
| GuideStar URL      | `https://none.org` _(placeholder)_           |
| TechSoup approved? | No                                           |
| Best time to call  | M–F after 4 PM EST                           |

**Mission (as submitted):** _"Mindful Moves' mission is to address teenagers' unhealthy
relationships with and lack of education concerning drugs and alcohol. Our website will host
information on the risks of using substances, how to use Narcan, and where to find addiction
recovery centers. A professor at Johns Hopkins, who teaches about harm reduction, will approve all
information on the website."_

The `Pending` status and the 2026-07-07 date corroborate the applicant's account: the application
was submitted and never worked. This is a backlog miss, not a rejection.

### Context supplied by SMS, not present in the application

Three facts arrived over text that the intake form never asked for, and each one changes the
onboarding plan:

1. The website is a **Girl Scout Gold Award project**.
2. Gold Award rules bar the applicant from **spending their own money** on it — hence "free domain",
   and hence no ability to buy a builder subscription.
3. They **do not want site design help** — building the site is the graded project. The asks are
   narrow and specific: **a free domain** and **access to a website builder**.

That third point is the useful one. It shrinks the request well below a standard onboarding: no
rebrand PR, no clone, no design work.

## Decision 1 (BLOCKER) — `mindfulmoves.org` is not available

The requested domain is **already registered by a third party**. Verified against the .org registry
RDAP (Public Interest Registry) on 2026-08-14:

| Property    | Value                                            |
| ----------- | ------------------------------------------------ |
| Registrar   | GoDaddy.com, LLC                                 |
| Registered  | 2025-04-06                                       |
| Transferred | 2025-07-16                                       |
| Expires     | 2027-04-06                                       |
| Nameservers | `launch1.spaceship.net`, `launch2.spaceship.net` |
| Status      | `client delete/renew/transfer/update prohibited` |

The Spaceship `launch*` nameservers are a parking configuration — the domain is held, not operated.
It is very likely listed for aftermarket resale.

This is a blocker rather than a detail because **"FFC registers you a free domain" and "FFC buys a
parked domain on the aftermarket" are different offers with different price tags.** FFC's
free-domain path is a registrar purchase at cost (roughly $10–20/yr for a `.org` via **113. Domain -
Registrar Search / Check / Register**). An aftermarket parked `.org` commonly runs three to four
figures. It cannot be quietly folded into the same workflow, and the applicant explicitly cannot
contribute funds.

**Alternatives checked the same way (RDAP, 2026-08-14):**

| Domain                    | Status        |
| ------------------------- | ------------- |
| `mindfulmovesproject.org` | **available** |
| `themindfulmoves.org`     | **available** |
| `mindfulmovesteens.org`   | **available** |
| `mindfulmovesmd.org`      | **available** |
| `mindfulmoves.info`       | **available** |
| `mindfulmoves.us`         | **available** |
| `mindfulmoves.org`        | registered    |
| `mindful-moves.org`       | registered    |
| `mindfulmoves.net`        | registered    |

**Recommendation:** offer `mindfulmovesproject.org` or `themindfulmoves.org`. Both keep the brand,
both are `.org` (which matches the nonprofit framing and FFC's standard), and either can go through
113 at cost today. `mindfulmovesteens.org` is arguably the best audience fit if the applicant wants
the teen focus in the name. Confirm the choice with the applicant before registering — the domain is
the public identity of their graded project, so this is their call, not ours.

## Decision 2 — "access to a website builder" does not map onto the current FFC stack

The applicant asked for a builder they can use themselves. FFC's standard website offering is an
**FFC-EX-`<domain>` repo built from the Next.js/static template and served by GitHub Pages** —
edited as code, through pull requests. That is not a website builder in the sense meant here, and
handing a high-schooler a static-site repo and calling it a builder would be answering a different
question than the one asked.

The legacy WordPress option (WPMU DEV) _is_ a real builder, but FFC is actively migrating **off** it
(epic #702), so putting a brand-new site onto it adds to the very backlog that is being drained.

The options, honestly stated:

| Option                                            | Fits "builder"? | Cost to applicant | Notes                                                                                           |
| ------------------------------------------------- | --------------- | ----------------- | ----------------------------------------------------------------------------------------------- |
| FFC-EX repo + GitHub Pages (standard)             | No — it's code  | Free              | Editable in the GitHub web UI; real learning value, but a genuine ramp for a solo teen builder  |
| WPMU DEV WordPress + page builder                 | Yes             | Free to them      | Cuts against the #702 migration; adds a site to the legacy estate                               |
| They build on a free tier elsewhere; FFC does DNS | Yes             | Free              | FFC registers the domain and points DNS only. Smallest FFC surface, and matches "I'll build it" |

**Recommendation: the third.** It is the closest match to what was actually asked for, it costs FFC
one domain registration and a DNS record, and it leaves the build — the graded part — entirely with
the applicant. FFC can still provision the FFC-EX repo later if they want to migrate in.

## Decision 3 — the two required attestations are blank, for a benign reason

Both certification fields came back empty:

- `I certify our organization agrees to the Free For Charity Terms of Service` — **blank**
- `I certify our organization is legally established and operating in the United States` — **blank**

**This is not a refusal.** Issue #670 made both attestations mandatory on the onboarding forms and
closed **2026-07-11** — four days _after_ this 2026-07-07 submission. The applicant was never shown
the fields. The social-page and AI-usage fields #670 added are blank for the same reason, as are the
footer and team-member fields.

They still have to be collected before approval; they just shouldn't be read as a red flag against
this applicant.

## Decision 4 — legal-entity status needs a human judgment

These three facts sit awkwardly together and should be resolved by a person, not by automation:

- The application asserts **pre-501(c)(3)** status and supplies **EIN 42-3645005**.
- The GuideStar field is the placeholder `https://none.org`, so there is no Candid profile to check
  against. (EIN verification via **801. Candid - Charity Check** is not available — the Candid
  scaffolding is inert pending the one-time key/environment setup in `candid-api-and-mcp.md`, and
  the Candid MCP server is unauthenticated in this session.) **The EIN is therefore unverified.**
- A **Girl Scout Gold Award project** is normally an individual's service project carried out under
  a troop/council, **not** a separately incorporated nonprofit — which is exactly what the blank
  "legally established and operating in the United States" attestation asks about.

There is a plausible innocent explanation for all of it (an EIN can be issued well before exemption,
and some Gold Award projects do incorporate). There is also a plausible reading in which the entity
does not exist yet. Either way, FFC's pre-501(c)(3) product exists precisely to serve organizations
in this state, so this is a question to ask, not grounds to decline.

**Related and unresolved:** the Gold Award is a high-school award, so the applicant is likely a
minor. Someone needs to decide who is competent to accept the FFC Terms of Service on the
organization's behalf — the applicant, a parent/guardian, or a troop/council officer. That question
is upstream of decision 3 and is not answerable from the record.

## Recommended next actions

Nothing here is safe to automate past the point of asking. In order:

1. **Reply to the applicant** (they have waited five weeks; they follow up politely and deserve a
   real answer). Tell them: `mindfulmoves.org` is taken by a third party and FFC cannot get it for
   free; offer the available alternatives; ask which they want. Best time to reach them is M–F after
   4 PM EST — SMS is already a working channel.
2. **Ask the builder question directly** — "do you want to build on a free platform of your choice
   with our domain pointed at it, or learn the GitHub-based site we normally provision?" Their
   answer picks the path in decision 2.
3. **Collect the ToS + US-establishment attestations**, from whoever is competent to give them, and
   settle the entity question in decision 4 at the same time.
4. **Only then** run the chain: **113** (register the chosen domain, gated on
   `cloudflare-prod-write`) → **103** (`dry_run=true` first) → optionally **701** if they choose the
   FFC-EX path → **204** to convert client 432's Pending order.

Steps 1–3 are conversations, not workflows. Step 4 is the standard chain and needs approvals at
`cloudflare-prod-write` and `github-prod`.

## What was run to produce this

All read-only. No gated environment was approved and no write was made.

| Check                         | How                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------- |
| Application lookup            | 221. WHMCS - Application Search, `whmcs-prod-read` (ungated), run 31839363031 |
| Requested-domain availability | Public Interest Registry RDAP, `rdap.publicinterestregistry.org`              |
| Alternative-domain sweep      | RDAP, 404 = available                                                         |
| Attestation-gap explanation   | Issue #670 close date vs. application date                                    |

One caveat on the search, recorded because it bounds the claim: 221 reported `unreadableCount: 14` —
fourteen client records could not be JSON-encoded by WHMCS because of malformed UTF-8 in stored
values, and were skipped. The single Mindful Moves match is solid, but "exactly one match" is a
statement about the 569 records that were readable, not all 583.
