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

| Capability                                           | API action                           | Script                      | Status |
| ---------------------------------------------------- | ------------------------------------ | --------------------------- | ------ |
| Create the charity account                           | `AddClient`                          | `whmcs-client-add.ps1`      | ✅     |
| Add additional contacts (with routing / sub-account) | `AddContact`                         | `whmcs-contact-add.ps1`     | ✅     |
| Read products + per-service custom fields            | `GetProducts` / `GetClientsProducts` | `whmcs-products-export.ps1` | ✅     |
| Create the onboarding service/order for a product    | `AddOrder`                           | `whmcs-order-add.ps1`       | ✅     |
| Orchestrate client + contacts + order                | (composes the above)                 | `whmcs-charity-onboard.ps1` | ✅     |

### End-to-end onboarding

`scripts/whmcs-charity-onboard.ps1` takes an intake JSON
(`examples/whmcs/onboard-501c3.example.json`) and runs AddClient → AddContact (one per roster
person, with notification routing / sub-account logins) → AddOrder. The `product` key resolves to a
`pid` via `config/whmcs-onboarding-products.json`. Always run with `-DryRun` first; it previews
every call without writing. The dispatch workflow **"204. WHMCS - Charity Onboard"** runs it
(dry-run by default).

### Onboarding products (enumerated)

| Key        | pid | Product                                      | Notes                                                  |
| ---------- | --- | -------------------------------------------- | ------------------------------------------------------ |
| `pre501c3` | 16  | FFC Pre-501c3 Nonprofit / Charity Onboarding | light intake (status, mission, domain, EIN, GuideStar) |
| `501c3`    | 33  | FFC 501c3 Nonprofit / Charity Onboarding     | full board + primary + technical contact roster        |

`config/whmcs-onboarding-products.json` holds each product's custom-field ids. (Note: pid 35 "Online
Impacts" is a **separate funnel**, not a duplicate — leave it as is.)

### Idempotency (safe re-runs)

The write scripts dedupe against live WHMCS so re-running onboarding does not create duplicates:

- **Client** — `AddClient` reuses an existing client with the same email (`GetClients`); pass
  `-FailIfExists` to error instead. Output includes `existing: true/false`.
- **Contacts** — `AddContact` skips a contact whose email already exists on the client
  (`GetContacts`); each result carries `existing`.
- **Order** — `AddOrder` skips when the client already has a non-terminated service for that product
  (`GetClientsProducts`); pass `-AllowDuplicate` to override (output shows
  `skipped: existing-service`).

These checks — and the `existing` / `skipped` output fields they produce — appear on **live
execution only**. Under `-DryRun` the dedupe is skipped and those fields are absent (dry-run output
keeps its original shape: client `{…, request}`, contacts `[{contactid, email}]`, order
`{…, request}`).

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

Run **"WHMCS - Export Products (Report)"** (`202-whmcs-export-products.yml`). The script prints a
readable catalog (pid / gid / type / module / billing / name and the discovered custom fields per
product) to the job log, and uploads CSVs as an artifact. Use this to identify the onboarding
products (e.g. pre-501(c)(3) vs. existing 501(c)(3)) and confirm which need updating.
