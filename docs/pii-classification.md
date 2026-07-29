# Disclosure classification for charity records

What FFC automation may print from a charity's WHMCS record, and why. This is policy; the
implementation is `Get-WhmcsFieldClass` / `Format-WhmcsFieldValue` in
[`scripts/whmcs-api-common.ps1`](../scripts/whmcs-api-common.ps1).

## The principle

The line is **not** "organizational vs. personal". It is **what the charity has already published,
or is legally required to disclose**. A charity's governance is a matter of public record; the
private contact routes of the people involved are not.

Getting this wrong in either direction has a cost. Over-masking is not free: masking the EIN in
workflow 221 did not protect anything — it just sent the operator to workflow 219 to read the same
value, and a control that people route around is worse than no control, because it looks like one.
Under-masking hands out board members' direct phone numbers.

## Classes

### `public` — printed in full

| Data                                                                  | Why it is public                                                                                                                                             |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **EIN**                                                               | IRS Publication 78 and the Business Master File publish it. It appears in the charity's own Candid profile URL. It identifies an organization, not a person. |
| **Officer and director names, titles, roles**                         | **Form 990 Part VII** requires disclosure of officers, directors, trustees and key employees by name and title. Form 990 is a public-disclosure document.    |
| **LinkedIn profile URLs**                                             | Self-published by their owner, on a platform whose purpose is professional visibility.                                                                       |
| **Organization name, mission, legal status**                          | Published by the charity and in its 990.                                                                                                                     |
| **Candid / GuideStar profile links**                                  | Public profiles.                                                                                                                                             |
| **Charity social accounts** (Facebook, Instagram, X, YouTube, TikTok) | Published by the charity.                                                                                                                                    |
| **Footer-designated contact details**                                 | Fields whose names say `public` or `footer` — the charity explicitly nominated these for publication on its own website.                                     |
| **City and state, time zone**                                         | Organizational location; in the 990.                                                                                                                         |

### `personal-contact` — masked

Individual **email addresses** and **phone numbers** — including those of officers whose _names and
roles are public_.

This is the boundary that makes the policy defensible: **Form 990 Part VII discloses who someone is,
not how to reach them privately.** It lists name, title, average hours and compensation. It does not
publish their personal email or mobile number. So `Board President/Chair Individual Email` masks
even though `Board President/Chair LinkedIn Link` does not, and both belong to the same person.

Emails mask to `***@domain` (the domain is usually the charity's own and is not identifying); phone
numbers mask entirely.

### `person-name` — masked to an initial

A natural person's name in a field that is **not** an officer role and not an organization name —
e.g. the individual who happened to file the application. Rendered as `J***`.

## Free text is classified by shape, not by field name

A field name cannot vouch for what someone typed into it. Mission statements and notes are `public`
by name, so their **values** are additionally shape-checked: anything matching an email address is
masked, anything matching a phone number is masked, and EIN-shaped values (`NN-NNNNNNN`) are passed
through explicitly — nine digits with a hyphen would otherwise trip the phone matcher.

Client-level custom fields come back from WHMCS **without names**, so they are classified on value
shape alone. That is what passing an empty field name selects.

## Rule order matters

Email/phone is decided **first**, so `Board President/Chair Individual Email` masks despite naming a
public role. The footer exception is checked inside that branch, so an explicitly-public footer
email still prints.

## One classifier, not several

Before this document, two implementations disagreed: workflow 221 masked the EIN, workflow 219
printed it. Same field, same charity, two answers, and the doc that explained the reasoning did not
exist. Any new caller uses `Format-WhmcsFieldValue`; adding a second copy re-creates that split.

`scripts/whmcs-fraud-review.ps1` keeps its own name masking — fraud review deals with _donor and
client_ identities under a different sensitivity, not charity governance data — and is deliberately
out of scope here.

## Changing the policy

Widening `public` is a governance decision, not a code cleanup. Record the disclosure basis in the
table above in the same change; if there is no statutory or self-publication basis to cite, the data
does not belong in `public`.
