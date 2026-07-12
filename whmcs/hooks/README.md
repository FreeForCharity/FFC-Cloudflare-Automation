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

## Fail-open design (why this is safe on production)

A broken checkout hook can break **all** orders, so this hook is deliberately **fail-open**:

- Every path is wrapped in `try/catch`. On **any** exception, missing/unreadable session data, or an
  unresolved field id, it returns `[]` — i.e. it **allows** the order.
- It is **read-only**: no database writes, no external/network calls.
- The worst a bug here can do is fail to catch a mis-filed order. It will **never** block a
  legitimate charity because of an internal error.

Ambiguous answers also err toward allowing: a "pre" / "not yet determined" marker beats a
full-501(c)(3) match, so a fuzzy answer is not treated as a hard 501(c)(3).

## Deploy (FTPS)

The hook is deployed to the production WHMCS hooks directory:

```
public_html/hub/includes/hooks/ffc_status_product_match.php
```

Edit the file **here**, get it reviewed via PR, run `php -l` on it, then upload it over FTPS to that
path (credentials live in the operator's secure store — never in this repo). WHMCS picks it up
immediately; no restart.

## Rollback

Delete the deployed file:

```
public_html/hub/includes/hooks/ffc_status_product_match.php
```

Removing the file removes the behavior instantly — no config change, no restart. (The canonical copy
stays here in git.)

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
