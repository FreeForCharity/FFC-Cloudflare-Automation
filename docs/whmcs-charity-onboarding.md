# WHMCS Charity Onboarding (clients, contacts, custom fields)

This documents how charity accounts and points of contact are modeled in WHMCS, what can be
automated through the WHMCS API, and how that relates to the public domain registrant.

## Account & contact model

- **Charity = one WHMCS client.** Created with `AddClient` (`scripts/whmcs-client-add.ps1`). Holds
  the charity's primary record and any client-level custom fields.
- **Additional points of contact = WHMCS Contacts** attached to that client. Created with
  `AddContact` (`scripts/whmcs-contact-add.ps1`). Each contact has:
  - its own name / email / phone / address;
  - **email notification routing**: general, invoice, support, product, domain;
  - an **optional sub-account login** (`-SubAccount` + `-Password` + `-Permissions`, e.g.
    `managedomains,managetickets`).

Example: a charity's technical POC receives domain + support mail; a finance POC receives invoice
mail; the executive director holds a portal login.

## Public vs. private contact information

Everything stored in WHMCS (the client and its contacts) is **private**, admin-side data and is
**never published**.

The only contact data that becomes **public** is the domain **WHOIS registrant**. For FFC-owned
domains that registrant is always **Free For Charity's public org contact** — public information
only, matching FFC's public 501(c)(3) record. It is set on the registrar (Cloudflare) side and is
**not** derived from WHMCS client records. The values live in
`config/ffc-registrant-contact.example.json` (a template); copy it to
`config/ffc-registrant-contact.json` and complete the address fields before any registrar flow
consumes it.

So: collect the charity's real (private) people in WHMCS; publish only FFC's public contact to
WHOIS.

## What the WHMCS API can automate

| Capability                                           | API action                           | Script                        | Status |
| ---------------------------------------------------- | ------------------------------------ | ----------------------------- | ------ |
| Create the charity account                           | `AddClient`                          | `whmcs-client-add.ps1`        | ✅     |
| Add additional contacts (with routing / sub-account) | `AddContact`                         | `whmcs-contact-add.ps1`       | ✅     |
| Read products + per-service custom fields            | `GetProducts` / `GetClientsProducts` | `whmcs-products-export.ps1`   | ✅     |
| Create the onboarding service/order for a product    | `AddOrder`                           | _pending product enumeration_ | ⏳     |

The order step (`AddOrder`) is intentionally deferred until the live product catalog and its
custom-field ids are enumerated — those ids are install-specific and must be confirmed before we
write to them.

## Custom fields

- **Client custom fields** (Setup → Custom Client Fields) are passed to `AddClient` via
  `-CustomFieldsJson '{"<fieldId>":"value"}'`. The script encodes them as the base64 +
  PHP-serialized array WHMCS expects.
- **Product custom fields** are collected at order time and belong to `AddOrder`. Their definitions
  are not reliably returned by `GetProducts`, but they **do** appear on each existing service from
  `GetClientsProducts`. The products export now captures these (`customfields` column) and prints a
  per-product summary to the run logs, which is how we enumerate field names + ids before building
  the order step.

## Enumerating the product catalog

Run **"WHMCS - Export Products (Report)"** (`8-whmcs-export-products.yml`). The script prints a
readable catalog (pid / gid / type / module / billing / name and the discovered custom fields per
product) to the job log, and uploads CSVs as an artifact. Use this to identify the onboarding
products (e.g. pre-501(c)(3) vs. existing 501(c)(3)) and confirm which need updating.
