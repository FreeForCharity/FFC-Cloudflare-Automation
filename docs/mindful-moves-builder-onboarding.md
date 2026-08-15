# Mindful Moves — builder onboarding (AI-assisted)

Hand-off document for the Mindful Moves Gold Award builder. It is written **to be given to her**,
not about her, so it can be pasted into her repo as `START-HERE.md` or linked from the first issue.

Companion to [`mindful-moves-intake-review.md`](./mindful-moves-intake-review.md) (client 432) and
tracker [issue #1203](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/1203).

**Why this exists:** the Gold Award project is explicitly a technical-learning exercise, and
building with AI is how this work is actually done now. So the goal is not to hand over a finished
site — it is to hand over a live site plus the skill to change it. She builds; FFC supplies the
domain, the hosting, and the tooling.

---

## What you have

| Thing              | Where                                                              |
| ------------------ | ------------------------------------------------------------------ |
| Your code          | `github.com/FreeForCharity/FFC-EX-mindfulmovesproject.org`         |
| Your live site     | `https://freeforcharity.github.io/FFC-EX-mindfulmovesproject.org/` |
| Your custom domain | `mindfulmovesproject.org` — not connected yet, see "Later" below   |

The live URL works the moment the repo is created. Every time you merge a change, the site rebuilds
and redeploys itself in a couple of minutes. Nothing to install, nothing to pay for.

## What it's built from

A **Next.js** site that exports to plain HTML and is served by **GitHub Pages**. Two consequences
worth knowing up front:

- There is no server and no database. Everything is built ahead of time into static files. This is
  why it's free to host and very hard to break in a way that costs money.
- Most of what you'll want to change lives in a small number of files. You do not need to understand
  the whole repository to make real changes to your site.

Start by looking at `src/lib/site.config.ts` — organization name, tagline, description, contact
details. Changing that file changes your site's identity everywhere it appears.

## Making your first change

The whole loop runs in a browser. You never have to install anything.

1. Go to your repo on github.com and press `.` (a period). This opens **github.dev**, a full code
   editor in your browser.
2. Open `src/lib/site.config.ts` and change the tagline to something you like.
3. In the left sidebar, click the source-control icon, write a short message describing what you
   did, and choose **Create a new branch and commit**. Name the branch something like
   `update-tagline`.
4. Back on github.com, GitHub will offer to open a **pull request**. Open it.
5. The checks run automatically. When they pass, merge it.
6. Wait ~2 minutes, reload your live URL, and your change is there.

That is the entire workflow, and it is the same one professional teams use. A pull request is just
"here is a change, please look at it before it goes live."

**Work on a branch, not on `main`.** `main` is what the public sees. Branches are free and
disposable — make one per change, and if you make a mess, delete the branch and start again. Nothing
you do on a branch can break the live site.

## Building with AI

This is the part worth learning properly, because it is how the work is done now.

### Getting the tools for free

- **GitHub Copilot Free** is available on any GitHub account and is enough to start — code
  completions and a chat panel, with monthly limits.
- **Apply for the [GitHub Student Developer Pack](https://education.github.com/pack)**. You qualify
  as a high-school student aged 13+ with proof of enrollment (a school email or a dated student ID).
  It's free and carries a lot besides Copilot.
  - ⚠️ **Honest caveat:** GitHub **paused new sign-ups for the free Copilot Pro student plan in
    April 2026** because of demand. Students verified before the pause kept it; new members
    currently get Copilot Free until sign-ups reopen. So apply — but expect Copilot Free for now,
    and don't count on Pro for your project timeline.
- **Claude** and similar assistants have free tiers that are good at explaining code you don't
  understand yet.

None of this requires a credit card, which matters given the Gold Award rule about not spending your
own money.

### How to actually prompt for code changes

The difference between AI being useful and being frustrating is almost entirely in how much context
you give it. A few things that work:

**Say where you are.** "In `src/lib/site.config.ts`, change the tagline to X" beats "change the
tagline." The assistant can't see your screen.

**Describe the outcome, not the code.** "Add a section to the homepage listing three warning signs
of an overdose, styled like the existing cards" is a better prompt than trying to specify the JSX
yourself. Let it propose the code, then read it.

**Always read what it gives you before merging.** This is the actual skill. AI writes plausible code
confidently, and plausible is not the same as correct. If you don't understand a line, ask it to
explain that line. "Why did you use `map` here?" is a completely reasonable question and you will
learn a lot from the answers.

**When something breaks, paste the error.** The full error text, not a summary of it. Errors are the
highest-information thing you can give an assistant.

**Ask it to teach, not just to do.** "Explain what this file does, as if I'm new to React" is one of
the highest-value prompts available to you, and it's the one most beginners skip.

### A caution specific to your subject matter

Your site carries health information about substance use, Narcan, and recovery resources — content
where being wrong could genuinely hurt someone. **Do not let an AI assistant write or edit the
medical or harm-reduction content.** Use it for layout, styling, components, and debugging; keep the
health content sourced from your Johns Hopkins reviewer. Your application already names that review
step, which is exactly the right instinct — this note just draws the line explicitly so it survives
contact with a tool that will happily generate confident paragraphs about naloxone dosing.

Same goes for phone numbers and links to recovery centers: verify every one by hand. A hallucinated
hotline number on a harm-reduction site is the worst possible failure mode.

## If you get stuck

Open an issue in your own repo describing what you tried and what happened. FFC gets notified and
can help. Issues are enabled on your repo by default.

## Later (FFC handles these, not you)

- **Connecting `mindfulmovesproject.org`** to the site, so the public URL is yours rather than the
  long github.io one. FFC registers the domain and points the DNS.
- **Analytics** (GA4 + Google Tag Manager), so you can see how many people the site reaches — useful
  evidence for a Gold Award write-up.
- **A footer with your public contact details**, once you decide what should be public. Don't put a
  personal phone number on a public website; FFC can advise on what belongs there.

## What we still need from you

- **Your GitHub username** — so we can add you as a maintainer on the repo. Without it you can't
  merge your own pull requests. This is the one thing blocking you from full control.
- **Which domain you want** — see the intake review; `mindfulmovesproject.org` is the working
  assumption but the choice is yours.
- **The Terms of Service acceptance**, from whoever is the right person to give it.
