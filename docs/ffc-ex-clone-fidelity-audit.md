# FFC-EX clone fidelity audit (live vs staging)

_Generated 2026-06-22. For each FFC-EX cutover domain (the workflow 120 default list), the live apex
(current WordPress) and the GitHub Pages `staging.` build were loaded in a headless browser and
their image counts compared. The staging sites are required to be **faithful static clones of the
live sites with exact visuals and all assets localized into the repo** (WordPress is being
decommissioned)._

> **Finding:** most staging builds are **Next.js template scaffolds**, not static clones. They
> render the charity's text but pull **zero images** and do not match the live visuals. A bulk
> cutover (workflow 120) would publish broken/incomplete sites for the majority of the batch.
> **Cutover is on hold pending real clones.**

| domain                                    | live images | staging images | live engine | verdict            |
| ----------------------------------------- | ----------: | -------------: | ----------- | ------------------ |
| coronadonationalforestheritagesociety.org |          12 |              0 | Divi        | ❌ scaffold        |
| browncanyonranch.org                      |           2 |              0 | Divi        | ❌ scaffold        |
| falloutshelterecovillage.org              |           3 |              0 | WordPress   | ❌ scaffold        |
| nj4israel.org                             |           3 |              0 | Divi        | ❌ scaffold        |
| bucktownbullsbaseball.org                 |           1 |              0 | ?           | ❌ scaffold        |
| instituteofforgiveness.org                |           4 |              0 | WordPress   | ❌ scaffold        |
| southamptonfriends.org                    |           4 |              0 | ?           | ❌ scaffold        |
| armstrongacesbaseball.org                 |           6 |              1 | WordPress   | ⚠️ partial         |
| bulldogztowing.com                        |          42 |             20 | Divi        | ⚠️ partial         |
| aprilhansen.com                           |           1 |              1 | Divi        | 🔶 verify visually |
| savewatersaveplanet.org                   |           8 |             13 | Divi        | 🔶 verify visually |
| americanlegionpost64.org                  |           3 |              3 | Divi        | 🔶 verify visually |
| foxtrotenterprises.com                    |    live 522 | no staging DNS | ?           | ⚠️ broken          |

**Summary:** 7 scaffolds (0 images) + 2 partial = **9 of 13 not faithful clones**; 3 plausible
(image-count parity only — still need a visual diff); 1 broken. Image count is a heuristic; visual
diffing against live is the authoritative gate.
