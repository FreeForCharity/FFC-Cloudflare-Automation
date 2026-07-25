# minoritywealthgap.org — Wix exit discovery

Minority Wealth Gap (MWG) applied to FFC and wants off Wix and onto GitHub Pages. This is the
external-observation inventory taken **before** any onboarding workflow ran, plus what the migration
will and will not be able to carry across.

Everything below was observed from outside the site on 2026-07-25 — public DNS, RDAP, sitemaps, and
the server-rendered HTML. Nothing here required Wix credentials, and nothing here should be treated
as a substitute for reading their Wix dashboard (see [What we still need](#what-we-still-need)).

## Application (confirmed 2026-07-25, workflow 221 run 30172777704)

| Field      | Value                                    |
| ---------- | ---------------------------------------- |
| Client id  | **444**                                  |
| Service id | **618**                                  |
| Product    | FFC 501c3 Nonprofit / Charity Onboarding |
| Status     | **Pending**                              |
| Submitted  | 2026-07-24                               |

The application is complete: FFC ToS certified, US-establishment certified, legal status "501(c)(3)
Food, Water, or Shelter Organization", Candid/GuideStar profile
`app.candid.org/profile/14588092/minoritywealthgap-88-3800775`, board contacts for five roles, a
technical contact, footer data (Los Angeles, CA + public phone and email), and all four socials
(Facebook, Instagram, X, YouTube `@minoritywealthgap3894`). EIN, phone numbers, and personal emails
are masked in the search output by design — read them from client 444 in the WHMCS admin UI when
generating the footer config, and **never transcribe an EIN off the public website instead**.

The single order on the account is **797** (order no. 5784969874, placed 2026-07-24 13:52:36, $0.00,
"Free For Charity - Onboarding - FFC 501c3 Nonprofit / Charity Onboarding"). That is the id workflow
211 takes to accept — not the client or service id.

**EIN 88-3800775**, matching the Candid profile slug. Read it from the application (client 444), not
from the charity's website: the website is not an authority on its own legal identity, and the
footer standard treats a fabricated or transcribed EIN as a false legal claim.

One gap the application does not close:

- **No GitHub username for the technical contact.** Workflow 701 needs one to add the maintainer,
  and the application captures a LinkedIn URL instead. Ask before running 701 — a missing/invalid
  value is silently skipped and the repo is created without the maintainer.

**Domain confirmed 2026-07-25** with the charity: `minoritywealthgap.org`. Order **797 accepted**
the same day (workflow 211, `previousStatus: Pending`; WHMCS email suppressed, which is 211's
default).

### Why the domain field was blank — two independent causes

Neither is a data-entry mistake, so neither is fixed by being more careful next time.

1. **The 501c3 onboarding product does not ask for a domain.** Product pid 33's custom fields cover
   the board roster, contacts, GuideStar, timezone, legal status, mission, EIN, footer data and
   socials — there is no domain question. The **pre-501c3** product (pid 16) has exactly that pair,
   fields 5 and 6 ("Do you have a domain name?" / "What is your current/desired domain name?"), so
   the question exists in FFC's field vocabulary; it was simply never carried into pid 33.
2. **The service's `domain` column is structurally empty.** The onboarding products are service
   products, so WHMCS never prompts for a domain at order time regardless of the form.

The consequence is not cosmetic: nothing downstream can read "the domain for this charity" from
WHMCS. Workflow 221 found MWG by matching a Candid profile URL, not a recorded domain.

**Fixes, and who can make them:**

- **The recorded value** → workflow **230. WHMCS - Record Field Set** (`target=service`,
  `field=domain`): dry-run default, gated, refuses to overwrite a different value without `force`,
  and reports `previousValue`. Service 618's domain was backfilled with its single-purpose
  predecessor, 229, which 230 supersedes.
- **The intake gap** → adding a required domain question to pid 33 is a **WHMCS admin-UI change**.
  There is no API for creating product custom fields, so no workflow can do it. Until then every
  501c3 application arrives without a domain and 230 is the backfill path.

Also observed: the sweep could not read **at least 6 client records** (indexes 19, 22, 23, 24, 27,
28 — the visible tail of `unreadableIndexes`). These are the malformed-UTF-8 rows described in
[PR #868](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/pull/868), clustered in the
early client ids. They did not hide this application, but they are real corruption in WHMCS and
should be cleaned up in the admin UI.

## Current state

| Layer         | Value                                                                   | Implication for FFC                                                                 |
| ------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Registrar     | Tucows Domains Inc.                                                     | Not FFC. `clientTransferProhibited` is set — lock must be lifted before transfer.   |
| Registered    | 2022-09-23, **expires 2026-09-23**                                      | ~2 months out. Renew or transfer before it lapses; do not let this drive the plan.  |
| Authoritative | `ns8.wixdns.net`, `ns9.wixdns.net`                                      | Zone is at Wix. FFC controls no DNS today — workflow 110/102 needed first.          |
| Web           | Wix (`185.230.63.107/171/186`; `www` → `td-ccm-neg-87-45.wixdns.net`)   | Wix Premium with a connected domain.                                                |
| Mail          | Google Workspace (`aspmx.l.google.com` + 4 alts)                        | **Not M365.** Mail migration is a separate decision, not a side effect of DNS.      |
| Ads/analytics | `AW-11029382319` (Google Ads conversion) in page HTML                   | GA4/GTM not externally detectable — see [Analytics](#analytics-the-honest-picture). |
| Donations     | `avvmtgju.donorsupport.co/page/FUNSHHMBTQJ` and `givebutter.com/LQBJuF` | Both external and portable. Keep as-is through the migration.                       |
| Video         | YouTube channel `UCbslgViDOcfGW8g2D7XLnTA`                              | External, portable.                                                                 |
| Error monitor | `browser.sentry-cdn.com`                                                | Wix's own instrumentation. Disappears with Wix; nothing to migrate.                 |

Not in `sites-list/sites_list.csv`, no `FFC-EX-minoritywealthgap.org` repo, no tracking issue. This
is a brand-new onboarding, not a fleet-migration row.

## Content inventory (from Wix's own sitemaps)

| Sitemap           | Count | Static-portable?                                                            |
| ----------------- | ----: | --------------------------------------------------------------------------- |
| `pages`           |    12 | Yes — the real site.                                                        |
| `blog-posts`      |    64 | Yes, but needs a content pipeline, not a page-by-page hand port.            |
| `blog-categories` |     2 | Yes.                                                                        |
| `event-pages`     |    34 | Yes — all public; only 1 is upcoming, so registration is a 1-event problem. |
| `member-profile`  |    76 | N/A — all 404 to the public; nothing publicly visible to port.              |

The 12 real pages: `/`, `/about`, `/contact`, `/domestic-violence`, `/events`, `/news`, `/podcast`,
`/privacy-policy`, `/s-projects-side-by-side`, `/support-us`, `/terms-of-use`, `/usa-projects`.

**112 public content URLs is a materially bigger job than the 2026-05 wave sites**, whose easy tier
was single-digit static pages. Scope this as its own project, not a drop-in. The 76 member profiles
do not add to that count — they are not publicly reachable (see below).

## What actually does not survive a static migration

> **This section was rewritten on 2026-07-25 after testing every URL.** The first version reasoned
> from what the Wix _platform_ can do and called three features blockers. Fetching them as an
> anonymous visitor shows the interactive surface is far narrower. Platform capability is not
> evidence about a particular site — check the URLs.

**Members Area — 76 profiles, and all 76 return HTTP 404 to the public.** Wix publishes them in
`member-profile_p_first-chunk-sitemap.xml`, but without a members login every one is "Page Not
Found" (verified across a sample; e.g. `/minoritywealthgap/profile`, `/jkyompire/profile`). So
**nothing publicly visible is lost** by migrating — there is no public content here to capture in
the first place, and these are dead links for search engines today. The member records live in Wix's
database and need an admin export if MWG wants to keep them: a data-retention question, not a
migration blocker.

**Wix Events — 34 pages, all public (HTTP 200), and only ONE is upcoming.** Every event page renders
publicly and ports to static as-is. Registration only matters for events that have not happened:

- `events-1/cooking-camp-cohort-3-1` — 2026-08-04, registration open. That is the whole live list.

**Beware the "open registration" count.** 13 events advertise open registration, but 12 of them are
past-dated (2022–2025) — Wix leaves RSVP open on past events. Reading that number without checking
the dates turns one event into a phantom thirteen. Event dates come from each page's JSON-LD
`startDate`; the full audit is in the session's `events-audit.tsv`.

So this is not "34 events cannot come over". It is **one event needing a registration path** —
Eventbrite / Zeffy / Givebutter, or a `mailto:` if it is small — plus a Wix export of existing
registrant data if MWG wants it.

**Forms — the one that genuinely breaks.** Wix Forms markup (`wixForms`, First Name / Message /
Submit) appears on all 12 pages, i.e. a site-wide footer form, plus the contact page. Every one
posts to Wix and stops working at cutover. Replacements are routine (`mailto:` or an external form
service), but per the migration runbook: never ship a form that silently posts nowhere.

**The blog (64 posts)** is portable but is not a hand-port — it needs an export → Markdown → route
pipeline, or a deliberate decision to carry forward only recent posts.

**The real cost is the rebuild, not the features.** Wix's layout depends on its own JS runtime and
generated `comp-*` class names, so expect to rebuild presentation rather than inherit it. That is
effort to budget, not a blocker to resolve.

## Analytics — the honest picture

**What is confirmed:** a Google Ads conversion tag, `AW-11029382319`, appears in the served HTML.

**What is not:** no GA4 (`G-…`), GTM (`GTM-…`), or Universal Analytics ID is present in the HTML,
and I could not run a real browser in this session to catch runtime-injected tags (Chromium has no
network egress here — all hosts reset, and no CONNECT reaches the agent proxy). Wix injects
marketing tags client-side, often behind consent, so **the absence of a GA4 ID in the HTML is not
evidence that GA4 is absent.**

Do not guess at this. The authoritative sources are:

- **Wix dashboard → Marketing Integrations** — the actual list of connected tags.
- **The charity's Google Analytics account** — property id, data stream, and how far back the
  history goes.

That last point drives a real decision: FFC provisions GA4 + GTM via workflows **505** and **503**.
If MWG has years of GA history, provisioning a fresh property **throws away their historical data**.
For a mature charity the usual right answer is to keep their existing property and wire it into the
new site, adding FFC's GTM container alongside. Confirm before running 505.

## Sequence

MWG is an onboarding, so the onboarding chain comes first and the Wix port rides on top of it. The
migration runbook's Wave 1 preconditions (in `sites_list.csv`, zone already in FFC Cloudflare) are
**not** met yet — that is expected for a new charity, and the fix is the onboarding chain, not an
exception.

1. **Confirm the WHMCS application** (workflow 221) — establishes client id and the validated
   application fields the footer standard needs.
2. **Add the domain to FFC Cloudflare** (110 / 102). Requires moving nameservers off Wix — **this is
   the step that takes the site down if it is run before the replacement is ready.** Cloudflare can
   host the zone while records still point at Wix, so do this early and cut over later.
3. **Add `minoritywealthgap.org` to `sites_list.csv`** so the fleet tooling can see it.
4. **Decide mail.** Google Workspace today. M365 (301–305) is FFC standard, but a working mail
   system is not a thing to migrate casually alongside a website. Separate change window.
5. **Provision the repo** (701) → `FFC-EX-minoritywealthgap.org`, maintainer added.
6. **Capture + port.** Wix server-renders its content (~7.6 KB of real text on the homepage, 54
   distinct images), so a `requests`-based capture works for text and structure. Wix's layout
   depends heavily on its own JS runtime and generated `comp-*` classes — **expect to rebuild layout
   rather than inherit it.** Localize every `static.wixstatic.com` / `parastorage.com` asset; the
   runbook's zero-external-asset-hosts gate applies.
7. **Analytics** (505/503, or wire their existing GA4) — after the decision above.
8. **Footer standard.** MWG states 509(a)(2)/501(c)(3) publicly, so Level 2 is likely — but the EIN
   and legal name must come from the validated application, never from the website. Do not
   transcribe an EIN off a web page into the footer config.
9. **DNS-ready** (workflow 121 preflight → READY, CNAME PR staged and held), then cutover
   separately.

## What we still need

From MWG directly — none of this is obtainable from outside:

- **Wix dashboard access** (or an admin export): Marketing Integrations list, form destinations, the
  Members Area export, and the Wix Events registrant data.
- **Google Analytics access**: property id and history depth, so we can decide keep-vs-provision.
- **Google Ads account context** for `AW-11029382319` — is it live, and is it on a Google Ad Grant?
  (`/events-1/google-ads-for-nonprofits` suggests they are engaged with the Ad Grant program; a
  broken conversion tag after cutover would damage a live grant.)
- **Decisions on Members Area, Events registration, and the blog backlog** — the three items above.
- **Registrar credentials / transfer authorization**, and confirmation on the 2026-09-23 expiry.

## Environment note for the next session

Chromium cannot reach the network in the Claude Code web sandbox: every host returns
`ERR_CONNECTION_RESET` and no CONNECT arrives at the agent proxy, with or without an explicit
`--proxy-server`. `curl` through `HTTPS_PROXY` works fine. So browser-driven capture (the technique
that solved the WAF-blocked instituteofforgiveness clone) is unavailable here and needs a local
environment.
