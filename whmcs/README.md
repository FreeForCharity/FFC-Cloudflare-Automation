# WHMCS Production Assets (Version-Controlled Mirror)

This folder brings the production WHMCS customizations under version control so they can be
reviewed, diffed, and restored. Until now the live theme and email templates existed **only** on the
production server / in the WHMCS database, with no history and no backup in source control.

Refs: [#697](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/697) (overnight
block 13) and [#678](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/678) (gated
onboarding journey email corrections).

## Why this exists: the unversioned-theme Smarty incident

The `six_ffc` theme was edited directly on the server for years with no version control. That led to
a production incident that was hard to diagnose precisely because there was no known-good copy to
diff against:

- **Inline JavaScript in `.tpl` files MUST be wrapped in `{literal}...{/literal}`.** WHMCS templates
  are Smarty templates: any bare `{` in inline JS (object literals, arrow functions, template
  strings) is parsed as a Smarty tag. When Smarty hits an invalid tag it can silently **truncate the
  rendered page output** at that point — pages appear half-rendered with no error.
- After any theme deploy, verify the **tail of the rendered page** (footer markup present, closing
  `</html>` tag) — see `docs/deploy-theme.md`.

Having the theme in git means future edits are reviewed, diffable, and revertible.

## What is (and is not) in here

| Path                            | Contents                                                                                                                                                                                        |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `theme/six_ffc/`                | Complete production copy of the custom WHMCS client-area theme (fetched from production over FTPS).                                                                                             |
| `email-templates/`              | HTML bodies of the custom onboarding/welcome email templates (managed in the WHMCS admin, stored in the DB). See that folder's README for the ID/product mapping and an important privacy note. |
| `docs/deploy-theme.md`          | How to push theme changes back to production over FTPS.                                                                                                                                         |
| `docs/apply-email-templates.md` | How to apply the corrected `_new` email templates in the WHMCS admin.                                                                                                                           |

**Not in here:** WHMCS knowledge-base articles. KB articles are TinyMCE-managed content stored in
the WHMCS **database**, not files on disk — they cannot be mirrored by copying files and are out of
scope for this folder.

## Discount code notice (read before copying anything out of this folder)

The email templates in `email-templates/` contain/reference the **live FFC discount code** used
during charity onboarding. Per operator decision (2026-07-12) the code is accepted in this public
**repository**, but it must **never appear on a public website**. The code lives in **emails only**.
Do not copy these templates (or excerpts of them) into website repos, GitHub Pages content, or any
public page. Note the FFC websites' banned-phrase guards do **not** cover the code itself, so
nothing will catch it automatically — this warning is the control.

## Editing rules

1. **Theme (`theme/six_ffc/`)**: edit here, get the change reviewed via PR, then deploy per
   `docs/deploy-theme.md`. Never edit directly on the server again — that is how the Smarty incident
   happened.
2. **Email templates (`email-templates/`)**: the files here are the source of truth for the HTML
   bodies, but WHMCS reads them from its database. Any change here must be manually applied in the
   WHMCS admin per `docs/apply-email-templates.md`.
3. **No credentials, ever.** FTPS/WHMCS credentials live in the operator's secure store — never in
   this repo, never in these docs.
