# WHMCS Email Templates (Onboarding Journey)

HTML bodies of the custom FFC onboarding/welcome emails. WHMCS stores these in its **database**
(admin area, Setup → Email Templates), so this folder is a version-controlled mirror: edit here,
review via PR, then apply in the admin per
[`../docs/apply-email-templates.md`](../docs/apply-email-templates.md).

## Discount code — read this first

These templates contain/reference the **live FFC discount code** that charities use to bring their
domain order to $0. Per operator decision (2026-07-12), the code appearing in this public **repo**
is explicitly accepted — the hard rule is that it must **never appear on a public website**. The
code lives in **emails only**.

- Do **not** copy these files, or excerpts of them, into any website repo, GitHub Pages content, or
  public page.
- The FFC websites' banned-phrase guards do **not** cover the code itself, so no automation will
  catch a leak — this warning is the control.

**No committed template actually contains the code.** The `_new` acceptance templates
(`tmpl_115_new` / `tmpl_116_new`) reference "the discount code from this email" and carry an
explicit `{DISCOUNT_CODE — ...}` placeholder line instead — when applying them in the admin, the
code line must be copied over from the **current live** template (or delivered by WHMCS promotion
settings) per [`../docs/apply-email-templates.md`](../docs/apply-email-templates.md). **If the live
template has no code and no promotion mechanism supplies one, that is a production bug to escalate**
— the public onboarding journey promises the code arrives in this email.

## `_new` files: corrected gated-journey versions

Files ending in `_new` are the **gated-journey-corrected** versions produced for
[#678](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/678). They reorder the
journey to be website-first (site validated on its GitHub Pages address **before** the domain
order). Their status differs by template:

| Old file (historical)          | Corrected file                     | Status                                                                |
| ------------------------------ | ---------------------------------- | --------------------------------------------------------------------- |
| `tmpl_115.html`                | `tmpl_115_new.html`                | **Live**; re-mirrored from the 2026-11-13 export; old file is history |
| `tmpl_116.html`                | `tmpl_116_new.html`                | **Live**; re-mirrored from the 2026-11-13 export; old file is history |
| `welcome_A_domain_pid39.html`  | `welcome_A_domain_pid39_new.html`  | Pending application in the WHMCS admin                                |
| `welcome_C_website_pid40.html` | `welcome_C_website_pid40_new.html` | Pending application in the WHMCS admin                                |
| `welcome_B_email_product.html` | (no correction pending)            | No correction pending                                                 |
| `welcome_B1_m365.html`         | (no correction pending)            | No correction pending                                                 |
| `welcome_B2_google.html`       | (no correction pending)            | No correction pending                                                 |

`tmpl_115.html` and `tmpl_116.html` are the **pre-2026-10-14** bodies (they still link the pid 39
domain order) and each carries a `HISTORICAL` comment on its first line. Do not apply them. A
pending `_new` body goes live only once it is pasted in per
[`../docs/apply-email-templates.md`](../docs/apply-email-templates.md).

## 2026-10-14: build-first onboarding (applied live)

The application forms (pid 16 and 33) now collect the website content, a required domain, a required
organization legal name and the technical contact's GitHub username, so the website product (pid 40)
is no longer a separate step. These changes were made **directly in the WHMCS admin** on 2026-10-14
(Clarke's direction) and are mirrored here:

- **115 / 116** (`tmpl_115_new.html` / `tmpl_116_new.html`): "What happens next: we build your
  website for you" (build, preview link, approve, then order the domain named in the application,
  with the coupon), plus a "Your website maintainer" section linking the FFCadmin site-owner pages
  (accept-invitation, training, common-edits). The pid 40 order link is gone. Live is these bodies
  with the real coupon in place of the `{DISCOUNT_CODE …}` placeholder.
- **16 Order Confirmation** (stock template; mirrored since 2026-11-13, see below): a "what happens
  next" paragraph for applicants and a maintainer prep list (GitHub signup, ffcadmin.org/site-owner
  and /site-owner/training).
- **7 / 23 / 8 Support Ticket Opened / Opened by Admin / Reply** (stock; 7 mirrored since
  2026-11-13, see below): one "Maintaining your charity's website?" line linking the site-owner
  guides, training track and common edits.

## 2026-11-13: live export mirrored

The live bodies of templates **16, 115, 116 and 7** were mirrored from the read-only WHMCS export of
2026-11-13 (`whmcs-live-forms-2026-11-13.md`, which also carries template 122 and both products'
fields). They include the **"What happens next"** block (review time, the GitHub invitation, the
preview link in a support ticket, the domain with the coupon, charity email, how to get help).

| WHMCS template ID             | Mirror file                                                    |
| ----------------------------- | -------------------------------------------------------------- |
| **16** Order Confirmation     | `tmpl_16_order_confirmation.html` (stock template, FFC-edited) |
| **115** pre-501(c)(3) welcome | `tmpl_115_new.html`                                            |
| **116** 501(c)(3) welcome     | `tmpl_116_new.html`                                            |
| **7** Support Ticket Opened   | `tmpl_7_support_ticket_opened.html` (stock auto-responder)     |

How the mirrors differ from the database, on purpose:

- The export gives the bodies as **text**. The wording is copied verbatim; the HTML markup (lists,
  bold headings, links) is reconstructed, so paste wording from here, not markup.
- **Coupon code:** the live 115/116 bodies contain the real code. The mirrors keep the
  `{DISCOUNT_CODE …}` placeholder (see "Discount code" above).
- **Emoji:** WHMCS stores the maintainer heading's emoji in 115/116 as `????` (lost in the
  database). The mirrors keep the intended emoji entity; re-entering it in the admin is a fix to
  make there.
- **116 sentence, fix pending Clarke's sign-off.** Live 116 still reads "Once your domain points at
  your website, because the nonprofit email programs require an active website to qualify, help you
  set up your charity email (Microsoft 365 or Google Workspace)." The mirror carries the corrected
  sentence, "Once your domain points at your website, we'll help you set up your charity email on
  Microsoft 365 or Google Workspace (the nonprofit email programs require an active website to
  qualify).", with an HTML comment quoting the live text. Apply it in the admin once Clarke signs
  off, then delete the comment.
- Templates 23 / 8 (ticket opened by admin / reply) and 122 (pid 40 welcome) are not mirrored.

## Template ID / product mapping

### Onboarding acceptance emails (sent when an application product activates)

| WHMCS template ID | File                                                   | Product                                                   |
| ----------------- | ------------------------------------------------------ | --------------------------------------------------------- |
| **115**           | `tmpl_115_new.html` (old: `tmpl_115.html`, historical) | pid **16** — FFC Pre-501c3 Nonprofit / Charity Onboarding |
| **116**           | `tmpl_116_new.html` (old: `tmpl_116.html`, historical) | pid **33** — FFC 501c3 Nonprofit / Charity Onboarding     |

(Product ids match `config/whmcs-onboarding-products.json` at the repo root.)

### Reconcile / cleanup emails

| WHMCS template ID | File                          | Sent by                                                   |
| ----------------- | ----------------------------- | --------------------------------------------------------- |
| **123**           | `123_application_refile.html` | `scripts/whmcs-application-triage.ps1` (`reconcile` mode) |

**Template 123 — "FFC - Application Re-file / Duplicate Cleanup".** Sent to the applicant when the
Application Triage runner (`reconcile` mode) cancels a duplicate or wrong-track pending onboarding
order so they can re-submit on the correct form. This body carries a `{$prior_answers}` placeholder:
the runner **injects** it at send time with a paste-ready `Question: Answer` list built from _that
application&rsquo;s own submitted onboarding field values_ (blank/placeholder fields skipped), then
sends the composed HTML via the WHMCS `SendEmail` API as a custom message. `{$prior_answers}` is
**not** a native WHMCS merge field — only this runner resolves it; every other merge field
(`{$client_first_name}`, `{$whmcs_link}`, `{$signature}`, …) is left intact for WHMCS. The runner
prefers the **live** WHMCS template (id 123) fetched via `GetEmailTemplates` and falls back to this
versioned file when the live template is unavailable. Email is sent **before** the order is
cancelled — a failed send aborts the cancel, so an order is never cancelled silently.

### Product welcome emails (sent on product/service activation)

| File                                    | Product                                                 |
| --------------------------------------- | ------------------------------------------------------- |
| `welcome_A_domain_pid39.html` / `_new`  | pid **39** — free .org domain registration              |
| `welcome_B_email_product.html`          | generic charity email product (provider not yet chosen) |
| `welcome_B1_m365.html`                  | Microsoft 365 nonprofit email                           |
| `welcome_B2_google.html`                | Google Workspace nonprofit email                        |
| `welcome_C_website_pid40.html` / `_new` | pid **40** — free charity website (GitHub Pages)        |

## Editing notes

- Bodies use WHMCS merge fields (`{$client_first_name}`, `{$whmcs_url}`, `{$signature}`, ...). Keep
  them intact.
- Some files carry placeholder tokens (`{TRANSFER_PID}`, `{M365_PID}`, `{GOOGLE_PID}`) where the
  product id was not final at authoring time — resolve these to real pids when applying in the
  admin.
