# WHMCS Hooks (version-controlled mirror)

This folder holds FFC's custom WHMCS **hooks** under source control. It is the source of truth; the
files are deployed to production WHMCS by hand over FTPS (see **Deploy** below).

Refs: [#697](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/697),
[#678](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/678).

## What is a WHMCS hook?

A hook is a small PHP file that WHMCS auto-discovers and runs when a specific event ("action point")
fires. Drop a file that calls `add_hook('<ActionPoint>', <priority>, <callback>)` into the hooks
directory and WHMCS runs it — no registration, no restart. Remove the file and the behavior is gone.
Hooks run inside the live WHMCS request, so a misbehaving checkout hook can affect **every** order.

## Hooks in this folder

### `ffc_status_product_match.php`

**Rule.** When an applicant checks out, look at the onboarding product(s) in their cart and the
answer they gave to the "What is the legal status of your organization?" custom field, and block the
order (with a friendly steer) if the two contradict each other:

| Cart product                       | pid | Answer says…                      | Result                                  |
| ---------------------------------- | --- | --------------------------------- | --------------------------------------- |
| Pre-501(c)(3) Charity Onboarding   | 16  | already an approved 501(c)(3)     | **blocked** → use the 501(c)(3) product |
| 501(c)(3) Charity Onboarding       | 33  | not yet / pre-501(c)(3) / pending | **blocked** → use the Pre-501(c)(3) one |
| either, answer matches the product |     | consistent                        | allowed                                 |

The action point is `ShoppingCartValidateCheckout`, which fires server-side during checkout
completion, before the order and invoice are created. Returning a non-empty array of error strings
blocks checkout and shows those strings to the applicant; returning `[]` allows the order.

**The legal-status field id is resolved by name, not hardcoded.** On pid 16 the field happens to be
id 3 and on pid 33 it is id 106, but the hook looks the id up at runtime via a `Capsule` query on
`tblcustomfields` (`relid = <pid>`, `fieldname LIKE '%legal status%'`) and caches it per pid, so it
keeps working if the fields are ever renumbered.

### `ffc_promote_charity_record.php`

**Rule.** WHMCS is FFC's charity CRM: each charity's reusable profile lives once at the **client**
level (Custom Client Fields), and orders reference it. The **order is the charity's certified
submission and the human-review gate** — an admin reviews the answers before accepting. **Acceptance
is the trigger:** this hook then copies the certified answers into the matching client custom fields,
so the CRM only ever holds reviewed data and nothing is re-entered.

It fires for two order kinds:

- **Onboarding** (pid 16 pre-501c3 / pid 33 full-501c3) → footer / identity data (EIN, mission,
  Candid ×2, public phone/email, city & state, Facebook/LinkedIn/Instagram/X/YouTube).
- **Website** (pid 40) → site body / SEO / integration data (tagline, description, short
  description, SEO keywords, brand color, founding year, alternate names, Zeffy/Idealist/Microsoft-
  Form URLs).

Action point `AcceptOrder`. From `$vars['orderid']` it finds the service(s) (`tblhosting` where
`orderid = <id>`, `packageid IN (16,33,40)`), reads their product custom-field values
(`tblcustomfieldsvalues`, `relid = <service id>`), and writes the allowlisted ones into the client's
fields (resolved by name, `type='client'`). Also copies the account **Company Name** → **Legal
organization name** (best-effort — `companyname` is often empty, so this may be a no-op).

- **Copy-if-empty (idempotent, self-service-safe).** A client field is written **only when empty**,
  so re-accepting an order changes nothing and a value the charity later edits in their portal is
  never clobbered.
- **PII is never copied.** The onboarding form also collects board members' and the primary/technical
  contacts' individual LinkedIn / phone / email. Those are **excluded**; only the explicit public
  allowlist (EIN, mission, Candid ×2, public phone/email, city & state, and the charity's Facebook /
  LinkedIn / Instagram / X / YouTube) is ever promoted.
- **Fail-safe.** Every path is wrapped in `try/catch`; on any error it does nothing and returns — it
  never throws, so it can never disrupt order acceptance. Worst case: a field isn't pre-filled and is
  filled by hand.

## Fail-open / fail-safe design (why this is safe on production)

A broken checkout hook can break **all** orders, so this hook is deliberately **fail-open**:

- Every path is wrapped in `try/catch`. On **any** exception, missing/unreadable session data, or an
  unresolved field id, it returns `[]` — i.e. it **allows** the order.
- It is **read-only**: no database writes, no external/network calls.
- The worst a bug here can do is fail to catch a mis-filed order. It will **never** block a
  legitimate charity because of an internal error.

Ambiguous answers also err toward allowing: a "pre" / "not yet determined" marker beats a
full-501(c)(3) match, so a fuzzy answer is not treated as a hard 501(c)(3).

## Deploy (FTPS)

The hooks are deployed to the production WHMCS hooks directory:

```
public_html/hub/includes/hooks/ffc_status_product_match.php
public_html/hub/includes/hooks/ffc_promote_charity_record.php
```

Edit the file **here**, get it reviewed via PR, run `php -l` on it, then upload it over FTPS to that
path (credentials live in the operator's secure store — never in this repo). WHMCS picks it up
immediately; no restart.

## Rollback

Delete the deployed file (per hook):

```
public_html/hub/includes/hooks/ffc_status_product_match.php
public_html/hub/includes/hooks/ffc_promote_charity_record.php
```

Removing a file removes that behavior instantly — no config change, no restart. (The canonical copies
stay here in git.) `ffc_promote_charity_record.php` is also idempotent (copy-if-empty), so removing
it only stops future pre-fills; it never has to be "undone."

## Manual test plan

Use a **$0 test order** (do not complete a real paid order):

1. Add **Pre-501(c)(3) Charity Onboarding** (pid 16) to the cart and answer the legal-status
   question with the approved **"501(c)(3) Nonprofit"** option → checkout should be **blocked** with
   the message steering you to the 501(c)(3) product.
2. Repeat with the **"pre-501(c)(3) Nonprofit"** (not-yet-determined) answer → checkout should
   **succeed**.
3. (Mirror) Add **501(c)(3) Charity Onboarding** (pid 33) and answer **"pre-501(c)(3)"** →
   **blocked** toward the Pre product; answer **"501(c)(3)"** → **succeeds**.

If anything blocks unexpectedly, delete the deployed file (see **Rollback**) — the fail-open design
means removing it can only ever relax validation, never tighten it.

### `ffc_promote_charity_record.php`

Use a **$0 test order** on an onboarding product for a test client whose client custom fields start
empty:

1. Place a **501(c)(3) Charity Onboarding** ($0, pid 33) order for the test client, filling EIN,
   mission, public phone/email, city & state, and the socials.
2. **Accept** the order (admin → Accept Order, or the API/triage runner).
3. Open the client's profile → **Custom Fields**: EIN, Brief mission, Candid links, Public phone,
   Public email, City & state, and the Facebook/LinkedIn/Instagram/X/YouTube fields should now be
   **pre-filled** from the order.
4. Confirm **no PII leaked**: the board/primary/technical contacts' individual phone/email/LinkedIn
   must **not** appear in any client field.
5. Edit one client field by hand, then re-accept (or accept a second onboarding order) → the
   hand-edited value must be **preserved** (copy-if-empty).

Verify `php -l ffc_promote_charity_record.php` is clean before uploading.
