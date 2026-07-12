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

## `_new` files: corrected gated-journey versions (PENDING)

Files ending in `_new` are the **gated-journey-corrected** versions produced for
[#678](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/678). They reorder the
journey to be website-first (site validated on its GitHub Pages address **before** the domain order)
and are **PENDING application in the WHMCS admin** — production still sends the old versions until
each `_new` body is pasted in per
[`../docs/apply-email-templates.md`](../docs/apply-email-templates.md).

| Current (live)                 | Corrected (pending)                |
| ------------------------------ | ---------------------------------- |
| `tmpl_115.html`                | `tmpl_115_new.html`                |
| `tmpl_116.html`                | `tmpl_116_new.html`                |
| `welcome_A_domain_pid39.html`  | `welcome_A_domain_pid39_new.html`  |
| `welcome_C_website_pid40.html` | `welcome_C_website_pid40_new.html` |
| `welcome_B_email_product.html` | (no correction pending)            |
| `welcome_B1_m365.html`         | (no correction pending)            |
| `welcome_B2_google.html`       | (no correction pending)            |

## Template ID / product mapping

### Onboarding acceptance emails (sent when an application product activates)

| WHMCS template ID | File                                  | Product                                                   |
| ----------------- | ------------------------------------- | --------------------------------------------------------- |
| **115**           | `tmpl_115.html` / `tmpl_115_new.html` | pid **16** — FFC Pre-501c3 Nonprofit / Charity Onboarding |
| **116**           | `tmpl_116.html` / `tmpl_116_new.html` | pid **33** — FFC 501c3 Nonprofit / Charity Onboarding     |

(Product ids match `config/whmcs-onboarding-products.json` at the repo root.)

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
