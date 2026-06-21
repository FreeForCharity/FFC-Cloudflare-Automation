# Registry-truth transfer worklist (RDAP — authoritative)

_Generated 2026-06-21 by `scripts/domain-registry-probe.ps1` over the 84 in-Cloudflare zones
(`In Cloudflare = Yes` in the Sites Master List). Each domain's **registrar of record**, **real
expiry**, and **transfer-lock** state come from the registry over RDAP — not WHMCS, whose
expiry/status is frequently stale._

> FFC holds domains in only two registrar accounts: **Cloudflare, Inc.** and **eNom, LLC**. A
> registrar of record outside those two means the domain is not in an FFC account (re-registered by
> someone else, dropped, or DNS-only for a third party). Being resolvable or in our Cloudflare DNS
> does **not** imply ownership.

## Summary

- **ENOM_READY** (61): eNom — unlocked, not expired → transfer to Cloudflare now
- **ENOM_LOCKED** (13): eNom — `clientTransferProhibited` → unlock at eNom first, then transfer
- **OTHER** (3): Registrar is **not** FFC (not Cloudflare/eNom) → investigate / not ours
- **UNREGISTERED** (1): No registry record → dropped / available → investigate
- **UNKNOWN** (1): No RDAP server for the TLD (e.g. `.us.org`) → manual WHOIS
- **AT_CLOUDFLARE** (5): Already at Cloudflare (migration complete)

**Headline:** 0 domains are actually expired at the registry — every WHMCS "Expired"/"Cancelled"
flag on this set was stale. The real eNom→Cloudflare worklist is **61 ready + 13 locked = 74 domains
still at eNom**.

## ENOM_READY (61)

_eNom — unlocked, not expired → transfer to Cloudflare now_

| domain                                    | registrar | registryExpiry | locked | siteHealth  | whmcsStatus |
| ----------------------------------------- | --------- | -------------- | ------ | ----------- | ----------- |
| alltypetowing.com                         | eNom, LLC | 2027-04-15     | False  | Live        | Active      |
| bintobetter.org                           | eNom, LLC | 2026-11-30     | False  | Live        | Active      |
| bulldogztowing.com                        | eNom, LLC | 2026-11-29     | False  | Live        | Active      |
| canadatokeywestcoastalrun.org             | eNom, LLC | 2027-03-31     | False  | Live        | Active      |
| coronadonationalforestheritagesociety.org | eNom, LLC | 2027-01-16     | False  | Live        | Active      |
| educationandempowerment.org               | eNom, LLC | 2026-07-24     | False  | Live        | Active      |
| emc-professional.com                      | eNom, LLC | 2027-06-19     | False  | Live        | Active      |
| exceptionalridersprogram.org              | eNom, LLC | 2026-08-17     | False  | Live        | Active      |
| fencingtogether.org                       | eNom, LLC | 2026-12-16     | False  | Live        | Active      |
| ffcadmin.org                              | eNom, LLC | 2026-10-05     | False  | Live        | Active      |
| ffcworkingsite1.org                       | eNom, LLC | 2026-11-02     | False  | Live        | Active      |
| ffcworkingsite2.org                       | eNom, LLC | 2026-09-19     | False  | Live        | Active      |
| freedomrisingusa.org                      | eNom, LLC | 2026-11-06     | False  | Live        | Active      |
| hunnewellscottages.com                    | eNom, LLC | 2026-10-24     | False  | Live        | Active      |
| instituteofforgiveness.org                | eNom, LLC | 2026-09-09     | False  | Live        | Active      |
| jwvpost619.org                            | eNom, LLC | 2027-01-15     | False  | Live        | Active      |
| legion-in-the-woods.org                   | eNom, LLC | 2026-09-11     | False  | Live        | Active      |
| letsdanceactivities.org                   | eNom, LLC | 2027-01-16     | False  | Live        | Active      |
| my-missions.org                           | eNom, LLC | 2027-03-05     | False  | Live        | Active      |
| nochaos.org                               | eNom, LLC | 2026-08-31     | False  | Live        | Active      |
| pagbooster.org                            | eNom, LLC | 2026-09-03     | False  | Live        | Active      |
| ptuganda.org                              | eNom, LLC | 2026-12-16     | False  | Live        | Active      |
| southamptonfriends.org                    | eNom, LLC | 2027-05-20     | False  | Live        | Active      |
| tamkeensports.org                         | eNom, LLC | 2027-06-30     | False  | Live        | Active      |
| technologyadoptionbarriers.org            | eNom, LLC | 2026-07-25     | False  | Live        | Active      |
| technologymonastery.org                   | eNom, LLC | 2026-12-30     | False  | Live        | Active      |
| technologymonastery.us                    | eNom, LLC | 2026-12-30     | False  | Live        | Expired     |
| thetrendylittlegeek.com                   | eNom, LLC | 2026-08-07     | False  | Live        | Active      |
| trendylittlegeek.com                      | eNom, LLC | 2026-08-07     | False  | Live        | Active      |
| youngfatherscare.org                      | eNom, LLC | 2026-08-16     | False  | Live        | Active      |
| 3dmc2.org                                 | eNom, LLC | 2030-06-28     | False  | Unreachable | Active      |
| apollonianfp.org                          | eNom, LLC | 2026-12-13     | False  | Error       | Active      |
| armstrongacesbaseball.org                 | eNom, LLC | 2026-08-15     | False  | Unreachable | Fraud       |
| bucktownbullsbaseball.org                 | eNom, LLC | 2026-12-11     | False  | Redirect    | Active      |
| canadatokeywestcoastalrun.com             | eNom, LLC | 2027-03-31     | False  | Unreachable | Unknown     |
| cochiserobotics.org                       | eNom, LLC | 2026-10-02     | False  | Unreachable | Active      |
| exceptionalridersprogram.com              | eNom, LLC | 2028-03-26     | False  | Unreachable | Active      |
| ffc.ngo                                   | eNom, LLC | 2027-02-05     | False  | Redirect    | Unknown     |
| ffcdomains.com                            | eNom, LLC | 2026-09-06     | False  | Redirect    | Active      |
| ffcdomains.org                            | eNom, LLC | 2026-09-06     | False  | Unreachable | Active      |
| ffchosting.com                            | eNom, LLC | 2027-01-08     | False  | Redirect    | Active      |
| ffchosting.org                            | eNom, LLC | 2027-01-08     | False  | Redirect    | Active      |
| ffcsites.org                              | eNom, LLC | 2027-02-21     | False  | Redirect    | Active      |
| freeforcharity.ngo                        | eNom, LLC | 2027-02-05     | False  | Redirect    | Unknown     |
| freetoolsfornonprofits.org                | eNom, LLC | 2026-10-24     | False  | Unreachable | Active      |
| hasbeensandgonnabees.org                  | eNom, LLC | 2026-09-19     | False  | Unreachable | Active      |
| ieeeforthuachuca.org                      | eNom, LLC | 2027-02-28     | False  | Unreachable | Active      |
| ihadrousa.com                             | eNom, LLC | 2026-09-22     | False  | Error       | Active      |
| ihadrousa.org                             | eNom, LLC | 2026-09-22     | False  | Error       | Active      |
| letsdanceactivities.com                   | eNom, LLC | 2026-09-17     | False  | Redirect    | Active      |
| makeacalendarinvite.org                   | eNom, LLC | 2026-09-20     | False  | Unreachable | Active      |
| nittanypost245.org                        | eNom, LLC | 2026-10-08     | False  | Unreachable | Cancelled   |
| ohiomtawest.org                           | eNom, LLC | 2026-09-09     | False  | Redirect    | Active      |
| paaboosterclub.org                        | eNom, LLC | 2026-10-04     | False  | Error       | Active      |
| pantryforadaircounty.org                  | eNom, LLC | 2026-10-16     | False  | Unreachable | Active      |
| postpartumcarefoundation.com              | eNom, LLC | 2027-05-21     | False  | Redirect    | Cancelled   |
| soulfoodwhangarei.org                     | eNom, LLC | 2026-09-25     | False  | Unreachable | Active      |
| southamptonfriends.com                    | eNom, LLC | 2027-05-20     | False  | Unreachable | Active      |
| theafghanistanaffairs.org                 | eNom, LLC | 2026-09-06     | False  | Error       | Active      |
| thekccf.org                               | eNom, LLC | 2027-03-11     | False  | Unreachable | Active      |
| thelastchancesanctuary.com                | eNom, LLC | 2027-02-24     | False  | Unreachable | Active      |

## ENOM_LOCKED (13)

_eNom — `clientTransferProhibited` → unlock at eNom first, then transfer_

| domain                       | registrar | registryExpiry | locked | siteHealth  | whmcsStatus |
| ---------------------------- | --------- | -------------- | ------ | ----------- | ----------- |
| browncanyonranch.org         | eNom, LLC | 2027-05-14     | True   | Live        | Active      |
| cvrs-us.org                  | eNom, LLC | 2027-07-16     | True   | Live        | Active      |
| facinggiantsnc.org           | eNom, LLC | 2027-07-01     | True   | Live        | Active      |
| falloutshelterecovillage.org | eNom, LLC | 2027-05-30     | True   | Live        | Active      |
| graftonareahistory.org       | eNom, LLC | 2027-07-12     | True   | Live        | Active      |
| harmonycenterfoundation.org  | eNom, LLC | 2027-04-16     | True   | Live        | Active      |
| postpartumcarefoundation.org | eNom, LLC | 2027-05-21     | True   | Live        | Cancelled   |
| wamhelp.org                  | eNom, LLC | 2027-04-23     | True   | Live        | Active      |
| bboc4vets.org                | eNom, LLC | 2027-05-26     | True   | Unreachable | Expired     |
| bringingpeople2people.org    | eNom, LLC | 2027-03-25     | True   | Redirect    | Active      |
| dropletsoflove.org           | eNom, LLC | 2027-07-16     | True   | Unreachable | Active      |
| emc-professional.org         | eNom, LLC | 2027-06-19     | True   | Redirect    | Active      |
| roottostem.org               | eNom, LLC | 2027-05-05     | True   | Unreachable | Active      |

## OTHER (3)

_Registrar is **not** FFC (not Cloudflare/eNom) → investigate / not ours_

| domain            | registrar               | registryExpiry | locked | siteHealth  | whmcsStatus |
| ----------------- | ----------------------- | -------------- | ------ | ----------- | ----------- |
| letspiritlead.org | GoDaddy.com, LLC        | 2027-05-17     | True   | Live        | Unknown     |
| srrn.net          | Squarespace Domains LLC | 2027-08-17     | True   | Redirect    | Unknown     |
| ssrn.net          | SafeNames Ltd.          | 2026-08-06     | True   | Unreachable | Unknown     |

## UNREGISTERED (1)

_No registry record → dropped / available → investigate_

| domain                     | registrar | registryExpiry | locked | siteHealth  | whmcsStatus |
| -------------------------- | --------- | -------------- | ------ | ----------- | ----------- |
| melaninmagicfoundation.org | —         | —              |        | Unreachable | Unknown     |

## UNKNOWN (1)

_No RDAP server for the TLD (e.g. `.us.org`) → manual WHOIS_

| domain                    | registrar | registryExpiry | locked | siteHealth | whmcsStatus |
| ------------------------- | --------- | -------------- | ------ | ---------- | ----------- |
| lifelessonslearned.us.org | —         | —              |        | Live       | Active      |

## AT_CLOUDFLARE (5)

_Already at Cloudflare (migration complete)_

| domain                  | registrar        | registryExpiry | locked | siteHealth | whmcsStatus      |
| ----------------------- | ---------------- | -------------- | ------ | ---------- | ---------------- |
| aprilhansen.com         | Cloudflare, Inc. | 2027-08-07     | True   | Live       | Transferred Away |
| freeforcharity.org      | Cloudflare, Inc. | 2029-04-02     | True   | Live       | Transferred Away |
| legioninthewoods.org    | Cloudflare, Inc. | 2026-11-06     | True   | Live       | Unknown          |
| mitchellnchistory.org   | Cloudflare, Inc. | 2030-08-08     | True   | Live       | Unknown          |
| ffchostingmultisite.org | Cloudflare, Inc. | 2027-09-06     | True   | Redirect   | Transferred Away |
