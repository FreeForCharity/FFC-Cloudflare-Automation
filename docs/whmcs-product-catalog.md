# WHMCS Product Catalog (status-marker products)

How FFC creates and assigns WHMCS catalog products that make a charity's true infrastructure state
explicit. Two products are introduced here:

1. **Domain Registered in Cloudflare (Registrar)** — marks that a domain's **registration** is held
   at Cloudflare Registrar, not merely that Cloudflare provides DNS/nameservers for it.
2. **Hosted by GitHub Pages** — marks that the charity's site is served by **GitHub Pages**,
   distinct from the legacy WordPress hosting products.

These are **status markers**, not billable services; they default to a free payment type and are
hidden from the public order form. They are **parallel to** (not replacements for) the legacy
WordPress hosting products, so reporting can tell the cohorts apart.

## How products are created

WHMCS exposes an `AddProduct` admin API action. A non-mutating capability probe
(`scripts/whmcs-product-capability-check.ps1`, which sends an `AddProduct` call with no name and
reads the validation error) confirmed the FFC API credential **is permitted** to call `AddProduct`,
so the scripted path is the primary method. The admin UI is the documented fallback if that
permission is ever revoked.

### Scripts

| Purpose                     | API action                                           | Script                                       |
| --------------------------- | ---------------------------------------------------- | -------------------------------------------- |
| Probe AddProduct permission | `AddProduct` (invalid, non-mutating) + `GetProducts` | `scripts/whmcs-product-capability-check.ps1` |
| Create a catalog product    | `AddProduct`                                         | `scripts/whmcs-product-add.ps1`              |

`whmcs-product-add.ps1` mirrors the other write scripts: `-DryRun` previews (secrets redacted) and
skips the idempotency lookup; a live run first lists products in the target group (`GetProducts`)
and, if one already exists with the same name, makes no change and returns
`{ existing = true, pid = <id>, skipped = 'existing-product' }`. It requires `-Name` and `-GroupId`
(gid).

### Config

`config/whmcs-catalog-products.json` defines the two products. `pid` is `null` until the product is
created (backfill it from the products export afterwards). `gid` is `null` and **must be supplied**
by an admin — product **groups** are created in the WHMCS admin UI (Setup → Products/Services →
Create a New Group), not via the API.

```jsonc
{
  "products": {
    "domain_registered_cloudflare": { "pid": null, "gid": null, "name": "Domain Registered in Cloudflare (Registrar)", ... },
    "hosted_github_pages":          { "pid": null, "gid": null, "name": "Hosted by GitHub Pages", ... }
  }
}
```

### Workflow

**43. WHMCS - Product Add** (`43-whmcs-product-add.yml`) — inputs `product_key`
(`domain_registered_cloudflare` | `hosted_github_pages`), an optional `gid` override, a
`run_capability_check` toggle (read-only probe first, default on), and `dry_run` (default **true**).
It resolves name/type/description from the config and calls `whmcs-product-add.ps1`.

## Creating the products (procedure)

1. **Create the product group(s)** in the WHMCS admin UI and note each `gid`. (Decide whether both
   products share one group, e.g. "FFC Status Markers", or use existing groups.)
2. Optionally set the `gid` values in `config/whmcs-catalog-products.json`, or pass `gid` to the
   workflow.
3. Dispatch **43. WHMCS - Product Add** with `dry_run=true` to preview, then `dry_run=false` to
   create. The capability check runs first and reports `addProductPermission`.
4. Run **31. WHMCS - Export Products** and copy the new `pid` for each product into
   `config/whmcs-catalog-products.json` (commit the backfill).

## Admin-UI fallback

If the capability probe ever reports `denied`, create each product manually: WHMCS admin → Setup →
Products/Services → Products/Services → **Create a New Product** (choose the group, set Type =
_Other_, payment = _Free_, hide from order form), then run the products export and backfill `pid` as
above.

## Assigning a product to a charity

No new assignment code is needed: assign a created product to a charity through the existing
`scripts/whmcs-order-add.ps1` (`AddOrder`), passing the charity's `-ClientId` and the product's
`-ProductId` (pid). That path is idempotent — it skips if the client already has a non-terminated
service for the product (`Test-WhmcsClientHasProduct`).

## Verification

- Capability (read-only): dispatch **43** with `run_capability_check=true`, or run
  `scripts/whmcs-product-capability-check.ps1` against the APIM gateway — expect
  `addProductPermission: allowed`.
- Dry-run:
  `pwsh -File scripts/whmcs-product-add.ps1 -Name 'Hosted by GitHub Pages' -GroupId 1 -DryRun` with
  dummy creds — confirms preview JSON and `***` redaction, no write.
