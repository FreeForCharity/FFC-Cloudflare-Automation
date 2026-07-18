# Applying Corrected Email Templates in the WHMCS Admin

The `_new` files in [`../email-templates/`](../email-templates/) are the gated-journey-corrected
email bodies for [#678](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/678).
WHMCS stores email templates in its **database**, so each one must be pasted into the admin by hand.
Production keeps sending the old versions until this is done.

## Which template goes where

| File to apply                      | WHMCS target                                                             |
| ---------------------------------- | ------------------------------------------------------------------------ |
| `tmpl_115_new.html`                | Email template ID **115** — onboarding acceptance for pid 16 (Pre-501c3) |
| `tmpl_116_new.html`                | Email template ID **116** — onboarding acceptance for pid 33 (501c3)     |
| `welcome_A_domain_pid39_new.html`  | Welcome email for pid **39** (free .org domain)                          |
| `welcome_C_website_pid40_new.html` | Welcome email for pid **40** (free charity website)                      |

## Procedure (per template)

1. Log in to the WHMCS admin area (credentials from the operator's secure store — never in this
   repo).
2. Go to **Setup (Configuration) → Email Templates** and open the target template. For the ID-115 /
   ID-116 templates, confirm the `id=` parameter in the admin URL matches the intended ID before
   editing.
3. **Locate the discount-code mechanism in the LIVE template before pasting anything** (acceptance
   templates 115/116). The committed captures in this repo do **not** contain the code — they may
   predate the code line — so find how the live email delivers it. It is one of:
   - a plain-text code line in the live template body (visible in source view), or
   - a code appended/injected by WHMCS **promotion settings** rather than the template body (in
     which case nothing extra needs to be carried into the new body — but confirm this is really
     configured, don't assume it).

   > **⚠️ If the LIVE template contains no code and no promotion mechanism delivers one, STOP and
   > escalate — that is a production bug.** The public onboarding journey promises the discount code
   > arrives in this email; applying the new body would not fix (or would entrench) the breakage. Do
   > not invent a code.

4. Switch the editor to **source/HTML view** and replace the entire body with the contents of the
   `_new` file. Do not let the WYSIWYG editor "clean up" the markup — paste in source view only.
5. Resolve the placeholder tokens the file carries before saving. Keep WHMCS merge fields
   (`{$client_first_name}`, `{$whmcs_url}`, `{$signature}`, ...) intact.
   - `{DISCOUNT_CODE — copy the code line from the CURRENT live template in the admin before saving}`
     (acceptance templates 115/116): replace this whole line with the code line you located in
     step 3. If the code is delivered by promotion settings instead of the body, delete the
     placeholder line — after confirming that mechanism actually fires.
   - `{TRANSFER_PID}`, `{M365_PID}`, `{GOOGLE_PID}`: resolve to the real product ids.
6. Save, then re-open the template and confirm the saved HTML matches what you pasted (WYSIWYG
   editors sometimes rewrite entities or strip tags) — including the carried-over code line.

## Verification (mandatory): test order

After applying, verify with a real end-to-end send:

1. Place a **test order** against the relevant product (onboarding product 16/33 for the acceptance
   emails; pid 39/40 for the welcome emails) using a test client with an inbox you control.
2. Accept/activate the order so WHMCS fires the email.
3. Check the received email: correct template, merge fields expanded (no literal `{$...}` left),
   links resolve, and — for the acceptance emails — the discount code you carried over in the
   procedure above actually renders (no leftover `{DISCOUNT_CODE — ...}` placeholder). If no code
   appears, revisit step 3 of the procedure: either the code line was dropped in the paste or the
   live mechanism was never there (production bug — escalate, see the warning above).
4. Cancel/clean up the test order and client afterward.

Once all four `_new` templates are applied and verified, update
[`../email-templates/README.md`](../email-templates/README.md) to drop the PENDING status (and
retire the superseded non-`_new` files in the same PR).

## Privacy reminder

These emails carry the live FFC discount code. The code is accepted in this repo (operator decision
2026-07-12) but must never appear on a public website — see the warning in
[`../email-templates/README.md`](../email-templates/README.md).
