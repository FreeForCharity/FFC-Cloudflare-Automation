# Domain transfer-eligibility report — Cat1-ready (eNOM → Cloudflare Registrar)

_Generated 2026-06-20 from live WHMCS data joined to the Sites Master List
(`sites-list/sites_list.csv`, `In Cloudflare = Yes`) via workflow 303 (Transfer Readiness
Preflight). Gates: `MinDaysToExpiry=15`, `PostRegLockDays=60`. Source run:
https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/27883949722._

> **Read-only.** This report initiates no transfer and spends no money. It is the gated input to the
> manual Cloudflare dashboard transfer-in for project #157.

## Definition

**Cat1-ready** = a domain that is **in WHMCS**, **in a Cloudflare zone**
(`inCloudflareZone == True`), and passes every readiness gate (`readiness == ready`): not expired
and ≥ 15 days to expiry (`expiryOk`), and ≥ 60 days since registration/last transfer per the ICANN
lock (`postRegLockOk`).

## Summary

- WHMCS domains evaluated: **304** (ready 146, blocked 129, review 29).
- In a Cloudflare zone (any readiness): **80** rows.
- **Cat1-ready (unique domains): 70** ← the transfer-in worklist.
- In-Cloudflare but blocked/review by the live gates: **5** unique domains (plus 3 duplicate WHMCS
  records of already-ready domains — see notes).

## Cat1-ready domains

|   # | domain                                    | registrar | daysToExpiry | daysSinceReg | httpHealth |
| --: | ----------------------------------------- | --------- | -----------: | -----------: | ---------- |
|   1 | 3dmc2.org                                 | enom      |         1469 |         2914 |            |
|   2 | alltypetowing.com                         | enom      |          299 |         4084 |            |
|   3 | apollonianfp.org                          | enom      |          176 |         1650 |            |
|   4 | aprilhansen.com                           | enom      |           48 |         3604 |            |
|   5 | armstrongacesbaseball.org                 | enom      |           56 |          674 |            |
|   6 | bintobetter.org                           | enom      |          163 |          202 |            |
|   7 | bringingpeople2people.org                 | enom      |          278 |         1183 |            |
|   8 | browncanyonranch.org                      | enom      |          328 |         1863 |            |
|   9 | bucktownbullsbaseball.org                 | enom      |          174 |          556 |            |
|  10 | bulldogztowing.com                        | enom      |          162 |          934 |            |
|  11 | canadatokeywestcoastalrun.org             | enom      |          284 |         3281 |            |
|  12 | cochiserobotics.org                       | enom      |          104 |         3183 |            |
|  13 | coronadonationalforestheritagesociety.org | enom      |          210 |          886 |            |
|  14 | cvrs-us.org                               | enom      |          391 |          339 |            |
|  15 | dropletsoflove.org                        | enom      |          391 |          339 |            |
|  16 | educationandempowerment.org               | enom      |           34 |         3618 |            |
|  17 | emc-professional.com                      | enom      |          364 |          366 |            |
|  18 | emc-professional.org                      | enom      |          364 |          366 |            |
|  19 | exceptionalridersprogram.com              | enom      |          645 |          307 |            |
|  20 | exceptionalridersprogram.org              | enom      |           58 |          307 |            |
|  21 | facinggiantsnc.org                        | enom      |          376 |         3276 |            |
|  22 | falloutshelterecovillage.org              | enom      |          344 |         3308 |            |
|  23 | fencingtogether.org                       | enom      |          179 |          186 |            |
|  24 | ffcadmin.org                              | enom      |          107 |         3284 |            |
|  25 | ffcdomains.com                            | enom      |           78 |         1748 |            |
|  26 | ffcdomains.org                            | enom      |           78 |         1748 |            |
|  27 | ffchosting.com                            | enom      |          202 |         4218 |            |
|  28 | ffchosting.org                            | enom      |          202 |         3292 |            |
|  29 | ffchostingmultisite.org                   | enom      |           78 |         1748 |            |
|  30 | ffcsites.org                              | enom      |          246 |         3041 |            |
|  31 | ffcworkingsite1.org                       | enom      |          135 |         3292 |            |
|  32 | ffcworkingsite2.org                       | enom      |           91 |         2831 |            |
|  33 | freedomrisingusa.org                      | enom      |          139 |          226 |            |
|  34 | freetoolsfornonprofits.org                | enom      |          126 |          239 |            |
|  35 | graftonareahistory.org                    | enom      |          387 |         3630 |            |
|  36 | harmonycenterfoundation.org               | enom      |          300 |          430 |            |
|  37 | hasbeensandgonnabees.org                  | enom      |           91 |         2834 |            |
|  38 | hunnewellscottages.com                    | enom      |          126 |         3629 |            |
|  39 | ieeeforthuachuca.org                      | enom      |          253 |         3399 |            |
|  40 | ihadrousa.com                             | enom      |           94 |         3924 |            |
|  41 | ihadrousa.org                             | enom      |           94 |         3924 |            |
|  42 | instituteofforgiveness.org                | enom      |           81 |         3206 |            |
|  43 | jwvpost619.org                            | enom      |          209 |          521 |            |
|  44 | legion-in-the-woods.org                   | enom      |           83 |          282 |            |
|  45 | letsdanceactivities.com                   | enom      |           89 |         2753 |            |
|  46 | letsdanceactivities.org                   | enom      |          210 |         2753 |            |
|  47 | lifelessonslearned.us.org                 | enom      |          109 |         3178 |            |
|  48 | makeacalendarinvite.org                   | enom      |           92 |          638 |            |
|  49 | my-missions.org                           | enom      |          258 |         3029 |            |
|  50 | nittanypost245.org                        | enom      |              |          255 |            |
|  51 | nochaos.org                               | enom      |           72 |         2485 |            |
|  52 | ohiomtawest.org                           | enom      |           81 |         3578 |            |
|  53 | paaboosterclub.org                        | enom      |          106 |         3181 |            |
|  54 | pagbooster.org                            | enom      |           75 |          655 |            |
|  55 | pantryforadaircounty.org                  | enom      |          118 |         3169 |            |
|  56 | ptuganda.org                              | enom      |          179 |         1647 |            |
|  57 | roottostem.org                            | enom      |          319 |         1872 |            |
|  58 | soulfoodwhangarei.org                     | enom      |           97 |         3190 |            |
|  59 | southamptonfriends.com                    | enom      |          334 |         2222 |            |
|  60 | southamptonfriends.org                    | enom      |          334 |         2222 |            |
|  61 | tamkeensports.org                         | enom      |          375 |          337 |            |
|  62 | technologyadoptionbarriers.org            | enom      |           35 |          330 |            |
|  63 | technologymonastery.org                   | enom      |          193 |         1633 |            |
|  64 | theafghanistanaffairs.org                 | enom      |           78 |          652 |            |
|  65 | thekccf.org                               | enom      |          264 |          461 |            |
|  66 | thelastchancesanctuary.com                | enom      |          249 |         3305 |            |
|  67 | thetrendylittlegeek.com                   | enom      |           48 |         3604 |            |
|  68 | trendylittlegeek.com                      | enom      |           48 |         3604 |            |
|  69 | wamhelp.org                               | enom      |          307 |          788 |            |
|  70 | youngfatherscare.org                      | enom      |           57 |          673 |            |

## In Cloudflare but NOT ready (held back by the live gates)

These are the domains the first-cut presence-only report would have counted as Cat1 but which the
live expiry/lock gates exclude:

| domain                       | readiness | registrar | daysToExpiry | reason                         |
| ---------------------------- | --------- | --------- | -----------: | ------------------------------ |
| bboc4vets.org                | blocked   | enom      |         -390 | Domain expired (390 days ago). |
| freeforcharity.org           | blocked   | enom      |         -444 | Domain expired (444 days ago). |
| postpartumcarefoundation.com | blocked   | enom      |          -30 | Domain expired (30 days ago).  |
| postpartumcarefoundation.org | blocked   | enom      |          -30 | Domain expired (30 days ago).  |
| technologymonastery.us       | blocked   | enom      |         -537 | Domain expired (537 days ago). |

## First-cut (66 Cat-1 candidates) vs. live gates

The first-cut report in #436 counted **66 Cat-1 candidates** from the Master List on presence/status
only (In WHMCS + Active + In Cloudflare), with no expiry or 60-day-lock gates applied. This live,
gated run resolves that set:

- **70 unique domains pass** all live gates and form the transfer-in worklist above.
- **5 are newly excluded** by the live gates — all of them because the domain is **expired**
  (negative days to expiry), which must be renewed before any transfer. None were blocked by the
  60-day post-registration lock in this run.
- **3 apparent extras** (armstrongacesbaseball.org, freedomrisingusa.org, legion-in-the-woods.org)
  are **duplicate WHMCS records** of domains already in the ready list (a second order with no
  registrar/expiry populated); they are not separate domains. See #433 / data-hygiene follow-up.

### Expired in-Cloudflare domains to renew (then re-run)

- `bboc4vets.org` — Domain expired (390 days ago).
- `freeforcharity.org` — Domain expired (444 days ago).
- `postpartumcarefoundation.com` — Domain expired (30 days ago).
- `postpartumcarefoundation.org` — Domain expired (30 days ago).
- `technologymonastery.us` — Domain expired (537 days ago).

## Reproduce

Run **workflow 303 — Domain Transfer Readiness Preflight** (`workflow_dispatch`,
`min_days_to_expiry=15`, `post_reg_lock_days=60`, optional `issue_number`), approve the `whmcs-prod`
deployment, then download the `domain-transfer-preflight` artifact. The Cat1-ready list is
`cat1_ready_in_cloudflare.csv` / `.md`; the full classification is `domain_transfer_preflight.csv`.
