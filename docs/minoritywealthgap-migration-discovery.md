# minoritywealthgap.org — Wix exit discovery

Minority Wealth Gap (MWG) applied to FFC and wants off Wix and onto GitHub Pages. This is the
external-observation inventory taken **before** any onboarding workflow ran, plus what the migration
will and will not be able to carry across.

Everything below was observed from outside the site on 2026-07-25 — public DNS, RDAP, sitemaps, and
the server-rendered HTML. Nothing here required Wix credentials, and nothing here should be treated
as a substitute for reading their Wix dashboard (see [What we still need](#what-we-still-need)).

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

| Sitemap           | Count | Static-portable?                                                 |
| ----------------- | ----: | ---------------------------------------------------------------- |
| `pages`           |    12 | Yes — the real site.                                             |
| `blog-posts`      |    64 | Yes, but needs a content pipeline, not a page-by-page hand port. |
| `blog-categories` |     2 | Yes.                                                             |
| `event-pages`     |    34 | Pages yes; **registration/ticketing no** (see below).            |
| `member-profile`  |    76 | **No** — this is a logged-in Wix Members Area.                   |

The 12 real pages: `/`, `/about`, `/contact`, `/domestic-violence`, `/events`, `/news`, `/podcast`,
`/privacy-policy`, `/s-projects-side-by-side`, `/support-us`, `/terms-of-use`, `/usa-projects`.

**112 content URLs plus 76 member profiles is a materially bigger job than the 2026-05 wave sites.**
The wave's easy tier was single-digit static pages. Scope this as its own project, not a drop-in.

## The three things that do not survive a static migration

These are product decisions for MWG, not technical details to solve quietly during the port. All
three are load-bearing on a Wix Premium plan and have **no equivalent** on GitHub Pages, which
serves static files and runs no server code.

1. **Members Area — 76 member profiles.** Login, profiles, and any gated content are a Wix
   application backed by Wix's database. Static hosting cannot authenticate anyone. Either the
   feature is dropped (and the members told), or it moves to an external platform that MWG keeps
   paying for. **Ask before assuming it can go** — 76 profiles on a mature charity usually means
   something operational depends on it.
2. **Wix Events — 34 event pages.** The _pages_ port fine as history. **Registration, ticketing, and
   the registrant list do not.** If events are still being run, they need an external service
   (Eventbrite, Zeffy, Givebutter events) wired in before cutover, and the existing registrant data
   exported out of Wix first.
3. **Forms.** Every Wix form posts to Wix. After migration those endpoints are dead. Each form
   becomes a `mailto:` link or an external form service. Per the migration runbook: never ship a
   form that silently posts nowhere.

The blog (64 posts) is portable but is not a hand-port — it needs an export → Markdown → route
pipeline, or a deliberate decision to carry forward only recent posts.

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
