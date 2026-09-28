# Testing website provisioning with the sample charities

`tests/fixtures/sample-charities.json` is a reusable set of **fictional** charities for testing
website provisioning (workflow 701) end to end against **either** template. Every value is fake and
visibly so: domains start with `ffc-test-`, emails use the reserved `.example` TLD, phones use the
555-01xx range reserved for fiction, EINs are `99-`/`00-` placeholders, and every social or LinkedIn
URL points at an `ffc-test` handle. `test_sample_charities.py` fails if any of that stops being
true, so a test run can never provision, email or link a real organization.

Each charity's `inputs` object uses 701's `workflow_dispatch` input names verbatim (every value a
string), so the same data drives the offline tests, workflow 748 and a live 701 run. Its `expect`
block records what the content step must produce for it.

| Charity            | Covers                                                                                                                                            |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `riverbend-pantry` | the happy path: recognized 501(c)(3), Candid links, donation and volunteer pages, five leaders                                                    |
| `st-marys-shelter` | pre-501(c)(3) (no tax claims, no Candid link: left blank, never FFC's), no donation or volunteer page (both links email), `'` and `&` in the name |
| `cafe-eclair-arts` | adversarial text: accents, non-Latin, `"`, `\`, `$`, a backtick, a multi-line mission, a `twitter.com` social URL, one leader                     |

Add a charity when you find a provisioning bug the three do not reach; give it an `id`, a `covers`
line and an `expect` block.

## Three layers

1. **Offline, every PR** (`tests/workflow-logic/`):
   - `test_sample_charities.py` validates the dataset, runs every charity through 701's real
     `resolve` parse script, and applies it with `scripts/Apply-WebsiteReactTemplate.ps1` to a
     fixture repo shaped like the templates.
   - `test_701_apply_website_template.py` and `test_701_parse.py` hold the targeted regression
     cases.
   - `test_check_provisioned_build.py` pins the built-page check that 748 runs (below).
2. **Real templates** (workflow **748**, on PRs touching the provisioning path, weekly, and on
   demand). For every template × charity pair, 748 clones the template, applies the charity exactly
   as 701's content job does, and runs that template's own checks: format, lint, unit tests,
   site-config schema, drift and build. It then reads the built pages with
   `check_provisioned_build.py`, because a template's own checks can pass over a wrong site (#1391:
   they were green while a charity's Donate section embedded Free For Charity's endowment form).
   That check fails unless the pages link the charity's own donation and volunteer pages (or, when
   it gave none, a `mailto:` to its contact address), and fails if any page other than FFC's own
   donation policy links one of FFC's donation, volunteer, Facebook or application-form targets.

   A red cell means that charity would get a site whose own CI fails (and so never deploys), one
   that still carries Free For Charity's identity, or one that sends its donors or volunteers to
   FFC. To reproduce a cell locally:

   ```bash
   python3 tests/workflow-logic/apply_sample_charity.py --charity st-marys-shelter --repo <template-clone>
   (cd <template-clone> && corepack pnpm run build)
   python3 tests/workflow-logic/check_provisioned_build.py --charity st-marys-shelter --out <template-clone>/out
   ```

3. **Live** (a real repo). Dispatch 701 with one charity's `inputs` plus `dry_run=false` and a
   `template_repo`. For example, with the GitHub MCP `run_workflow` tool, pass the `inputs` object
   as-is, adding `"dry_run": "false"` and
   `"template_repo": "FreeForCharity/FFC-IN-FFC_Single_Page_Template"`. The `repo` and `content`
   jobs wait on the `github-prod` gate. The zone is not in FFC Cloudflare, so DNS is skipped and the
   site serves on its GitHub Pages default URL. Check the new repo's own CI and the live footer,
   then archive it with **736** (dry run first; 736 requires a matching dry run within 48 hours).
