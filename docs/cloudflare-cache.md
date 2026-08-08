# Cloudflare cache: purge and rules audit

Automation for the Cloudflare edge cache on FFC zones — purging it, and auditing the rules that
decide what gets cached in the first place.

| Piece                                    | What                                                                 |
| ---------------------------------------- | -------------------------------------------------------------------- |
| `scripts/cloudflare-cache-purge.ps1`     | Purge by URL or wholesale, with optional post-purge verification     |
| `scripts/cloudflare-cache-rules-get.ps1` | Read Cache Rules + zone cache settings, report error-caching posture |
| Workflow **124**                         | Cache Purge (Admin) — write, `dry_run` defaults true                 |
| Workflow **125**                         | Cache Rules Audit — read-only                                        |

## Why this exists: the 2026-08-07 freeforcharity.org incident

The site's Site Quality monitor emailed four homepage SVGs that "cannot be retrieved", all HTTP 429.
The files existed and the origin served them correctly the whole time. The 429s were coming from
Cloudflare's cache.

**The chain.**

1. **The origin issued a 429.** LiteSpeed at the cPanel origin (`x-turbo-charged-by: LiteSpeed` on
   the bad responses) throttled a burst of parallel asset fetches. Transient, and by itself
   harmless.
2. **The origin stamped a one-year TTL on that error.** `public/.htaccess` in
   `FFC-IN-freeforcharity.org` sets `Cache-Control: public, max-age=31536000, immutable` in a
   `<FilesMatch>` keyed on **file extension**, with no status-code condition. LiteSpeed applied it
   to the 429 too.
3. **Cloudflare cached it.** Cloudflare does not normally cache a 429 — but it does when the
   response carries an explicit `Cache-Control`. The error was frozen at the edge with
   `max-age=31536000`.

Result: `cf-cache-status: HIT`, `age: 58393`, `content-length: 0`. Four SVGs **and a 40 KB Next.js
JS chunk** served as empty 429s to every visitor routed through an affected PoP, for about 16 hours,
with a completely healthy origin. The JS chunk is the part that made it an outage rather than a
cosmetic bug.

The diagnostic that separates this from a real origin failure is one request:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://example.org/path        # 429  <- edge
curl -s -o /dev/null -w '%{http_code}\n' https://example.org/path?cb=1   # 200  <- origin is fine
```

A cache-buster query string forces an origin fetch. If the plain URL fails and the busted one
succeeds, the origin is healthy and the edge is holding a bad object. Confirm by reading
`cf-cache-status` and `age` on the failing response.

## Purging

Targeted purge of the specific URLs, with verification:

```bash
pwsh -File scripts/cloudflare-cache-purge.ps1 -Domain freeforcharity.org \
  -Url 'https://freeforcharity.org/Svgs/FFC-Consulting.svg' \
  -Url 'https://freeforcharity.org/_next/static/chunks/1gzgy07de-6wt.js' \
  -Verify
```

Via workflow 124, paste the URLs into the `urls` input (newline or comma separated) and run once
with `dry_run=true` to see the payload, then again with `dry_run=false`.

**Prefer `-Url` over `-All`.** Every purged object refetches from the origin on next request, so
purging a busy zone wholesale produces exactly the origin burst that caused the incident. `-All`
exists for the case where the poisoned set is unknown, and requires its own switch.

**`-Verify` is the part that proves it worked.** The purge API returning 200 does not mean the edge
stopped serving the bad object. `-Verify` re-requests each purged URL and reports its status and
`cf-cache-status`; a URL still answering 429 with `HIT` was not purged and the script exits
non-zero.

Only `files` and `purge_everything` are implemented. Cloudflare's `prefixes` / `hosts` / `tags`
selectors are Enterprise-only and return HTTP 400 on every other plan, so they are deliberately
absent rather than present-and-broken.

## Auditing the rules

```bash
pwsh -File scripts/cloudflare-cache-rules-get.ps1 -Domain freeforcharity.org
```

Reads the `http_request_cache_settings` phase entrypoint (the zone's Cache Rules) and the
`cache_level` / `browser_cache_ttl` / `always_online` zone settings, then reports findings. A 404 on
the entrypoint is not an error — it means the zone has no Cache Rules and is on Cloudflare defaults.

The finding that matters here is **`no-error-status-ttl`**: no rule caps edge TTL for 4xx/5xx, so an
origin `Cache-Control` on an error response is honoured for its full `max-age`. That is the
zone-side precondition for the incident above. The related `respect-origin-without-status-cap`
finding names the rules that hand the origin full control of edge TTL.

Findings are advisory. `-FailOnFinding` (workflow input `fail_on_finding`) turns severity `warn` and
above into a non-zero exit for use as a gate.

## Fixing it properly

Purging clears the symptom. Two changes prevent recurrence, and they are independent — do both:

**Origin side** (`FFC-IN-freeforcharity.org`, `public/.htaccess`): stop error responses inheriting
the immutable header. Scope the directive by status rather than by file extension alone:

```apache
<FilesMatch "\.(js|css|woff2|ttf|webp|svg|png|jpg|jpeg|gif|ico)$">
  Header set Cache-Control "public, max-age=31536000, immutable" "expr=%{REQUEST_STATUS} == 200"
  Header always set Cache-Control "no-store" "expr=%{REQUEST_STATUS} >= 400"
</FilesMatch>
```

LiteSpeed's `expr` support is partial, so verify this against a real 429 after deploying rather than
assuming it took.

**Cloudflare side**: a Cache Rule with an `edge_ttl.status_code_ttl` entry covering 400–599 set to
no-store. This does not depend on the origin behaving, which is why it is worth having even after
the `.htaccess` fix lands. Workflow 125 reports whether such a rule is present.

**The remaining root cause** is why LiteSpeed throttled at all. The most likely reason is that the
origin is not restoring real visitor IPs from Cloudflare: without `mod_cloudflare` /
`CF-Connecting-IP` restoration, every visitor appears to LiteSpeed as one of ~20 Cloudflare egress
IPs, so a per-IP throttle trips on ordinary traffic. That is a WHM-side check (LiteSpeed →
Per-Client Throttling) and is not automated here.

## Credentials

Both scripts resolve the zone through `Resolve-CfZone` in `scripts/cloudflare-api-common.ps1`, which
probes `CLOUDFLARE_API_TOKEN_FFC` then `CLOUDFLARE_API_TOKEN_CM`. In CI those come from Azure Key
Vault via the `cloudflare-tokens-from-kv` composite action — 125 uses `scope: read`, 124's apply job
uses `scope: write`. Purging requires the **Cache Purge** token permission, which is separate from
Zone:Read; a token that resolves the zone can still be refused the purge, and that surfaces as a
failed batch in the verdict rather than a crash.
