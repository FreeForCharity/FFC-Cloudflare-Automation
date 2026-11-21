# Volunteer site builders: who builds which charity

Approved by Clarke on 2026-11-21 in the project thread "Design volunteer charity assignment
process". This page says how FFC decides which accepted charity is ready, gives it to a volunteer,
and makes sure no charity is built twice, whether by two volunteers or by a volunteer and the
automated Site Builder ([charity-lane-pipeline.md](charity-lane-pipeline.md)).

## The record

Every accepted charity has exactly one hub issue, `[CHARITY-LANE] <domain or org>`, labelled
`charity-lane` and one `lane:*` stage label. **That issue is the record of who builds the charity.**
Everything else only displays it.

| Shows the holder                                    | Written by                                  |
| --------------------------------------------------- | ------------------------------------------- |
| `sites-list/build_queue.csv` / `.json`              | `703` each week, via `volunteer_assign.py`  |
| The ffcadmin work order (assignee, `status:*`)      | the assignment step below; the Site Builder |
| The ffcadmin roadmap and Sites List Development tab | ffcadmin's own sync from the files above    |

When anyone needs to know whether a charity is free, read the hub issue live. The build queue is a
weekly snapshot and is never enough on its own.

A volunteer's hold is three things on the hub issue, written together by the script:

- the label `builder:volunteer`, which the Site Builder and the Onboarding Conductor skip;
- a claim comment `<!-- charity-lane:claim holder=volunteer:<login> until=<UTC ISO> -->`;
- the volunteer as assignee. GitHub drops an assignee who cannot see the repository yet (for example
  an org invite not yet accepted), so the claim comment, not the assignee, is the record.

## Ready to build

A charity can be given to a volunteer when all of these hold:

1. Gate 1 accepted through `226`: the pre-acceptance completeness review passed, or Clarke
   adjudicated the order and the gaps ticket was sent. An incomplete application is never built.
2. `750` found no existing `FFC-EX-*` repo, live portfolio site or open work for it, or the hub
   issue links to what it found.
3. The hub issue is `lane:build-ready` and nobody holds it (no `builder:volunteer`, no in-date Site
   Builder lease).

Order: food, water and shelter first, then veterans, then all others; oldest first within each. The
category is a `- Category: <food_water_shelter|veterans|other>` line on the hub issue.

## Giving a charity to a volunteer

Clarke assigns, or a thread acting on his word. One command does the hub side and refuses a held
charity, the automated lane's items, and a volunteer over their limit:

```bash
python3 scripts/volunteer_assign.py status                                   # what is open, who holds what
python3 scripts/volunteer_assign.py claim --item <n> --volunteer <login>       # dry run: prints the plan
python3 scripts/volunteer_assign.py claim --item <n> --volunteer <login> --apply
```

`--adopt` records a volunteer who is **already** building a charity whose hub issue is past
`lane:build-ready`. It never takes a charity from another volunteer. The claim comment lists the two
follow-up steps, done in the same sitting:

- **Repo access:** `729` with `permission=push` on that one `FFC-EX-*` repo (the `github-prod`
  gate). `701` gives the application's maintainer `maintain`; a volunteer gets `push`. Write from
  day one is safe even when the charity has a live site: the domain, DNS and cutover all stay in
  this hub behind their own gates.
- **ffcadmin work order:** assign the volunteer and set `status:active-build` (`status:live` if the
  site already serves its domain).

## Verifying a volunteer: the `sponsoring-admins` team

ffcadmin treats membership of the org team `sponsoring-admins` as FFC's record that a volunteer was
verified (program plan section 8), and its `verify-assignment.yml` reverts a work-order assignee who
is not on it. The team grants no repository access; `729` does that per repo. Before 2026-11-21 the
team was planned but had never been created.

`754. Org - Team Membership` manages it. Dispatch with `action=list` first (read only, no gate) to
see every org team and its members. Adding someone runs behind the `github-prod` gate:

```text
754  action=add  team=sponsoring-admins  username=<login>  dry_run=true    # the plan
754  action=add  team=sponsoring-admins  username=<login>  dry_run=false   # approve the gate
```

`create_team=true` on the first add creates the team (closed, no repository access). Someone who is
not yet an org member gets an org invitation and stays `pending` on the team until they accept.
`action=remove` takes a person off the team only; their org membership and repo access are
untouched.

**Limits.** One charity per volunteer until one of theirs reaches the charity's review
(`lane:charity-review` or later), then up to three, ffcadmin's sponsor cap.

## Stepping back and stale holds

- A volunteer can step back at any time.
  `volunteer_assign.py release --item <n> --volunteer <login> --apply` returns a `lane:building`
  item to `lane:build-ready`; their work stays in the repo. Then lower their access with `729` and
  set the work order back to `status:needs-admin`.
- Each weekly `703` run reads every volunteer-held charity's repo. With no commit for 14 days the
  build queue marks the hold `stale=yes` and the run warns. **A stale hold is flagged to Clarke and
  never released without his yes.** An unreadable repo is `unknown`, never stale.

## Charity-side site managers

Many charity repos already have someone from the charity working in them: an officer, a staff
member, or the charity's own volunteer. They are **not holders**. They never use an FFC volunteer's
slot, are never flagged stale and are never released; the site is theirs. They are listed in
`sites-list/site_maintainers.csv` (repo, login, side, PR count and dates, evidence), with `side`:

- `charity`: confirmed from the charity's side, because the account is named for the charity or the
  person is named on the charity's own site;
- `charity-likely`: works only in that charity's repo, with nothing tying the account to FFC;
- `ffc-volunteer`: an FFC volunteer builder, who also holds the charity on its hub issue.

The build queue shows each charity's charity-side maintainers, and `claim` prints them (and puts
them in the claim comment, without an `@`) so the FFC volunteer coordinates with them, marking
anyone with a PR in the last 30 days. Add a row when someone new starts working in a charity's repo;
move a `charity-likely` row to `charity` when the evidence firms up. The first list (2026-11-21)
came from every pull request in the org's charity repos not authored by FFC staff or a bot.

## What the automated lane does with a volunteer's charity

Nothing. The Site Builder skips any item labelled `builder:volunteer`
([site-builder.md](../.claude/routines/site-builder.md) step 1), and the Onboarding Conductor lists
it and leaves it alone. When the Site Builder takes a charity itself, it sets the ffcadmin work
order to `status:active-build`, so the public roadmap no longer offers that charity to volunteers.

## What a new volunteer is told

- FFC gives you one charity at a time to start; you do not pick from the roadmap on your own.
- Your charity lives in `FFC-EX-<domain>`; its preview is
  `https://freeforcharity.github.io/FFC-EX-<domain>/`.
- Start with the repo's own `START-HERE` / `TEMPLATE_SETUP_CHECKLIST.md` and
  `.claude/agents/onboarding.md` (see
  [mindful-moves-builder-onboarding.md](mindful-moves-builder-onboarding.md)).
- Work through pull requests. The site's facts come only from the charity's application and the
  charity itself; never invent anything.
- Ask on your charity's hub issue. The domain, DNS, and anything said to the charity stay with FFC.
- If you need to step back, say so; that is normal.

## First run (2026-11-21)

Clarke asked for a test on the volunteers already working a site. The method was applied by
`--adopt` to:

| Hub issue | Charity                           | Volunteer        | Work order     |
| --------- | --------------------------------- | ---------------- | -------------- |
| #1769     | Genesis Bridge Charities          | `yojoe26`        | ffcadmin #1298 |
| #1787     | Cactus Wren Cooperative Preschool | `Tyler-Carlotto` | ffcadmin #491  |
| #1788     | New Heights Educational Group     | `phoganuci`      | ffcadmin #1187 |

Before the run, all three work orders read as unclaimed (`status:needs-admin`, or `status:intake`
for a site that has been live for months), and #1769 read as the automated builder's own claim.
