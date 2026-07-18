# Deploying the six_ffc Theme to Production (FTPS)

The live theme lives at `public_html/hub/templates/six_ffc/` on the WHMCS production server. The
version-controlled copy is [`../theme/six_ffc/`](../theme/six_ffc/). Changes flow **repo → PR →
production**, never the other way around (and never edited live on the server — see the Smarty
incident in [`../README.md`](../README.md)).

## Credentials

FTPS credentials come from the **operator's secure store** — they are never committed to this repo,
never placed in scripts, and never printed to logs. Ask the operator to supply them at deploy time
(e.g. via a git-ignored env file consumed by your FTPS client).

## Connection notes

- Protocol: FTPS (explicit TLS, `PROT P`), port 21.
- **Reuse one connection** for multi-file pushes. Rapid reconnect attempts trigger cPHulk
  brute-force protection and temporarily ban the client IP; if a connection drops, back off (10+
  seconds) before reconnecting, and use a bounded retry loop rather than hammering.

## Procedure

1. Merge the reviewed PR that changes files under `whmcs/theme/six_ffc/`.
2. From a clean checkout of `main`, upload **only the changed files** to
   `public_html/hub/templates/six_ffc/`, preserving the directory structure.
3. If a `.tpl` you touched contains inline JavaScript, confirm the JS is wrapped in
   `{literal}...{/literal}` **before** uploading. Bare `{` in inline JS is parsed as a Smarty tag
   and can truncate the rendered page.

## Post-deploy verification (mandatory)

Smarty failures are silent — the page just stops rendering partway. After every deploy:

1. Load the affected client-area pages (logged-out home page at minimum; the specific pages whose
   templates changed).
2. **Verify the page tail**: view source and confirm the footer markup and the closing `</html>` tag
   are present. A missing tail means Smarty truncated output — roll back the uploaded files to the
   previous `main` versions immediately, then debug the `{literal}` wrapping in the repo.
3. Check the browser console for new JS errors on the changed pages.

## Rollback

Every file is in git. To roll back, upload the prior revision of the affected files from
`git show <last-good-sha>:whmcs/theme/six_ffc/<file>` over the same FTPS procedure.
