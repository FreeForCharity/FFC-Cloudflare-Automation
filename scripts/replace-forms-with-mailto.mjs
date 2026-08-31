#!/usr/bin/env node
/**
 * replace-forms-with-mailto.mjs — neutralize forms in a captured static site.
 *
 * A WordPress form (Forminator, CF7, Gravity, Ninja) posts to a PHP endpoint
 * that does not exist once the site is static. Shipping the markup unchanged
 * produces a form that looks live, accepts a visitor's message, and drops it —
 * the single worst failure mode of a static migration, because it is invisible
 * to every check that only asks whether pages render. `verify-no-legacy.mjs`
 * cannot see it either: nothing loads at page load, so there is no request to
 * fail on. Only a submission would reveal it, and by then a real person's
 * message is gone.
 *
 * So every <form> is replaced, in the markup, with a visible contact block
 * carrying a mailto: link. That is a deliberate downgrade, not a port: the
 * visitor loses in-page submission and gains a channel that actually delivers.
 *
 * A form we cannot replace is a FAILURE, not a warning. Exit 3 leaves the
 * decision with an operator rather than shipping a dead form quietly.
 *
 * Read-only against the network; only rewrites files under --dir.
 *
 * Usage:
 *   node scripts/replace-forms-with-mailto.mjs --dir <siteRoot> --email <addr>
 *        [--subject "<line>"] [--dry-run]
 *   node scripts/replace-forms-with-mailto.mjs --self-test
 */
import {
  readdirSync,
  statSync,
  readFileSync,
  writeFileSync,
  mkdtempSync,
  mkdirSync,
  rmSync,
} from 'node:fs';
import { join, extname, relative, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

/**
 * Is this a syntactically valid, deliverable-looking address?
 *
 * Deliberately strict: the address is written into published markup as the
 * charity's only contact channel, so a typo does not degrade the page, it
 * silently ends the conversation. Better to refuse than to publish a mailto:
 * that bounces.
 */
export function isPlausibleEmail(value) {
  if (typeof value !== 'string') return false;
  const v = value.trim();
  if (v.length === 0 || v.length > 254) return false;
  // No separate whitespace guard: v is trimmed and the pattern's character
  // classes exclude whitespace, so such a line could never be the reason a
  // value is rejected. Mutation-testing is what surfaced it as unreachable.
  return /^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i.test(
    v,
  );
}

/** Escape a value for interpolation into HTML text or an attribute. */
export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Find every top-level <form>…</form> span in the document.
 *
 * Returns [{start, end}] over the ORIGINAL string, non-overlapping and in
 * order, so a caller can splice from the end backwards without invalidating
 * earlier offsets.
 *
 * HTML forbids nested forms, and browsers drop the inner one, so a naive
 * non-greedy `<form.*?</form>` is nearly right. It is wrong in one way that
 * matters: an UNCLOSED <form> (WordPress builders emit these, and so does any
 * page truncated mid-capture) makes the non-greedy match run to the NEXT
 * form's closing tag, swallowing everything between two forms — including real
 * page content. So each opening tag is paired with the next closing tag only
 * if one exists before the next opening tag; otherwise the form is reported
 * unclosed and left alone for a human, never guessed at.
 */
export function findFormSpans(html) {
  const spans = [];
  const unclosed = [];
  // `<form\b` is WRONG here: \b matches between 'm' and '-', so the custom
  // element <form-widget> scores as a form, and since it has no </form> it is
  // reported as an unclosed form — a hard failure over markup that is not a
  // form at all. Require a real tag-name terminator.
  const opens = [...html.matchAll(/<form(?=[\s/>])[^>]*>/gi)];
  for (let i = 0; i < opens.length; i++) {
    const open = opens[i];
    const openStart = open.index;
    const searchFrom = openStart + open[0].length;
    const nextOpen = i + 1 < opens.length ? opens[i + 1].index : html.length;
    const closeIdx = html.toLowerCase().indexOf('</form>', searchFrom);
    if (closeIdx === -1 || closeIdx > nextOpen) {
      unclosed.push(openStart);
      continue;
    }
    spans.push({ start: openStart, end: closeIdx + '</form>'.length });
  }
  return { spans, unclosed };
}

/**
 * The replacement block. Kept to plain semantic HTML with inline styles: the
 * captured theme's stylesheets are localized but we cannot know which class
 * names survive, and a contact block that inherits nothing is more reliable
 * than one styled by a class that may not exist.
 */
export function mailtoBlock(email, subject) {
  const addr = escapeHtml(email);
  const subj = subject ? `?subject=${encodeURIComponent(subject)}` : '';
  return (
    '<div class="ffc-contact-fallback" style="border:1px solid #ccc;border-radius:6px;padding:1rem;margin:1rem 0">' +
    '<p style="margin:0 0 .5rem">This form has moved to email. We read every message.</p>' +
    `<p style="margin:0"><a href="mailto:${addr}${subj}">${addr}</a></p>` +
    '</div>'
  );
}

/**
 * Replace every closed <form> in `html`. Returns {html, replaced, unclosed}.
 *
 * `replaced` counts forms actually rewritten; `unclosed` counts forms left in
 * place because their extent could not be determined. A caller that treats
 * `unclosed > 0` as success ships a live-looking dead form, which is the whole
 * thing this script exists to prevent — so the CLI exits non-zero on it.
 */
export function replaceForms(html, email, subject) {
  const { spans, unclosed } = findFormSpans(html);
  if (!spans.length) return { html, replaced: 0, unclosed: unclosed.length };
  const block = mailtoBlock(email, subject);
  let out = html;
  // Splice from the end so earlier offsets stay valid.
  for (let i = spans.length - 1; i >= 0; i--) {
    out = out.slice(0, spans[i].start) + block + out.slice(spans[i].end);
  }
  return { html: out, replaced: spans.length, unclosed: unclosed.length };
}

/** Every .html/.htm file under root, depth-first. */
export function htmlFilesUnder(root) {
  const out = [];
  const walk = (dir) => {
    let entries;
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const e of entries) {
      const p = join(dir, e);
      let st;
      try {
        st = statSync(p);
      } catch {
        continue;
      }
      if (st.isDirectory()) walk(p);
      else if (/^\.html?$/i.test(extname(e))) out.push(p);
    }
  };
  walk(root);
  return out.sort();
}

function selfTest() {
  let failed = 0;
  const eq = (name, actual, expected) => {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a === b) {
      console.log(`ok   ${name}`);
    } else {
      failed++;
      console.error(`FAIL ${name}\n  expected ${b}\n  actual   ${a}`);
    }
  };

  eq('isPlausibleEmail accepts a normal address', isPlausibleEmail('info@example.org'), true);
  eq('isPlausibleEmail accepts a subdomain host', isPlausibleEmail('a.b@mail.example.co.uk'), true);
  eq('isPlausibleEmail rejects a missing @', isPlausibleEmail('info.example.org'), false);
  eq('isPlausibleEmail rejects a bare hostname after @', isPlausibleEmail('info@example'), false);
  eq('isPlausibleEmail rejects embedded whitespace', isPlausibleEmail('in fo@example.org'), false);
  eq('isPlausibleEmail rejects an empty value', isPlausibleEmail(''), false);
  eq('isPlausibleEmail rejects a non-string', isPlausibleEmail(null), false);

  eq(
    'escapeHtml neutralizes a quote-and-tag injection',
    escapeHtml('"><script>x</script>'),
    '&quot;&gt;&lt;script&gt;x&lt;/script&gt;',
  );

  // A single closed form is replaced and the surrounding page survives.
  eq(
    'replaceForms swaps one form and keeps the surrounding markup',
    (() => {
      const r = replaceForms(
        '<h1>Hi</h1><form action="/x"><input></form><p>Bye</p>',
        'i@e.org',
        '',
      );
      return [
        r.replaced,
        r.unclosed,
        /<form/i.test(r.html),
        r.html.startsWith('<h1>Hi</h1>'),
        r.html.endsWith('<p>Bye</p>'),
      ];
    })(),
    [1, 0, false, true, true],
  );
  // Asserted as an EXACT string, not as a count plus a substring. Splicing
  // forwards rather than backwards invalidates every span after the first, so
  // the output is corrupt while `replaced` is still 2 and the surrounding text
  // is still findable somewhere in the wreckage — a weaker assertion here
  // passes on rubble.
  eq(
    'replaceForms rewrites two forms in place without disturbing the text between or around them',
    (() => {
      const block = mailtoBlock('i@e.org', '');
      const r = replaceForms(
        '<p>A</p><form>f1</form><p>B</p><form>f2</form><p>C</p>',
        'i@e.org',
        '',
      );
      return [r.replaced, r.html === `<p>A</p>${block}<p>B</p>${block}<p>C</p>`];
    })(),
    [2, true],
  );
  // The reason findFormSpans is not a plain non-greedy regex. An unclosed form
  // followed by a real one: a `<form.*?</form>` match starting at the FIRST
  // open tag ends at the SECOND form's close tag, deleting the content between
  // them. That content is the page.
  eq(
    'an unclosed form does not swallow the next form and the page content between them',
    (() => {
      const r = replaceForms('<form>A<p>KEEP THIS</p><form>B</form>', 'i@e.org', '');
      return [r.replaced, r.unclosed, r.html.includes('KEEP THIS')];
    })(),
    [1, 1, true],
  );
  eq(
    'a page with no form is returned untouched',
    (() => {
      const src = '<h1>Hi</h1>';
      const r = replaceForms(src, 'i@e.org', '');
      return [r.replaced, r.unclosed, r.html === src];
    })(),
    [0, 0, true],
  );
  eq(
    'FORM in uppercase is matched too',
    replaceForms('<FORM ACTION="/x"></FORM>', 'i@e.org', '').replaced,
    1,
  );
  eq(
    'a tag merely starting with form- is not treated as a form',
    (() => {
      const src = '<div class="formidable"></div><form-widget></form-widget>';
      const r = replaceForms(src, 'i@e.org', '');
      return [r.replaced, r.unclosed];
    })(),
    [0, 0],
  );
  eq(
    'the replacement carries the address as a mailto link',
    replaceForms('<form></form>', 'info@example.org', '').html.includes(
      'href="mailto:info@example.org"',
    ),
    true,
  );
  eq(
    'a subject line is percent-encoded into the mailto',
    replaceForms('<form></form>', 'i@e.org', 'Website enquiry').html.includes(
      'mailto:i@e.org?subject=Website%20enquiry',
    ),
    true,
  );

  // htmlFilesUnder touches the filesystem, so it gets a real tree rather than
  // no coverage. The consequence of a widened filter is not a wrong count: the
  // caller REWRITES every path this returns, so a stylesheet or a minified
  // bundle would be spliced as if it were markup.
  eq(
    'htmlFilesUnder returns only .html/.htm, recursively, and nothing else',
    (() => {
      const root = mkdtempSync(join(tmpdir(), 'ffc-htmlfiles-'));
      try {
        mkdirSync(join(root, 'about'), { recursive: true });
        mkdirSync(join(root, 'assets'), { recursive: true });
        for (const f of [
          'index.html',
          'legacy.htm',
          'style.css',
          'app.js',
          'notes.txt',
          'photo.html.png',
          join('about', 'index.html'),
          join('assets', 'bundle.css'),
        ]) {
          writeFileSync(join(root, f), 'x');
        }
        return htmlFilesUnder(root)
          .map((f) => relative(root, f).split(sep).join('/'))
          .sort();
      } finally {
        rmSync(root, { recursive: true, force: true });
      }
    })(),
    ['about/index.html', 'index.html', 'legacy.htm'],
  );

  if (failed) {
    console.error(`\n${failed} self-test failure(s)`);
    process.exit(2);
  }
  console.log('\nall self-tests passed');
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

function arg(name, def = '') {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : def;
}

if (isMain) {
  if (process.argv.includes('--self-test')) {
    selfTest();
  } else {
    const dir = arg('dir');
    const email = arg('email');
    const subject = arg('subject', '');
    const dryRun = process.argv.includes('--dry-run');

    if (!dir || !email) {
      console.error(
        'Usage: node scripts/replace-forms-with-mailto.mjs --dir <siteRoot> --email <addr> [--subject "<line>"] [--dry-run]',
      );
      process.exit(64);
    }
    if (!isPlausibleEmail(email)) {
      console.error(
        `::error::--email '${email}' is not a plausible address. It becomes the site's only contact channel; a typo here silently ends every conversation.`,
      );
      process.exit(64);
    }

    let files = 0;
    let replaced = 0;
    let unclosed = 0;
    const unclosedFiles = [];
    for (const f of htmlFilesUnder(dir)) {
      const src = readFileSync(f, 'utf8');
      const r = replaceForms(src, email, subject);
      if (r.unclosed) {
        unclosed += r.unclosed;
        unclosedFiles.push(f);
      }
      if (r.replaced) {
        files++;
        replaced += r.replaced;
        if (!dryRun) writeFileSync(f, r.html, 'utf8');
      }
    }

    const verb = dryRun ? 'would replace' : 'replaced';
    console.log(`${verb} ${replaced} form(s) across ${files} file(s) under ${dir}`);
    if (process.env.GITHUB_STEP_SUMMARY) {
      writeFileSync(
        process.env.GITHUB_STEP_SUMMARY,
        `\n### Forms\n\n- ${verb} **${replaced}** form(s) across **${files}** file(s) with a mailto: block for \`${email}\`\n` +
          (unclosed
            ? `- ⚠️ **${unclosed}** unclosed form tag(s) left in place — see the job log\n`
            : ''),
        { flag: 'a' },
      );
    }
    if (unclosed) {
      console.error(
        `::error::${unclosed} unclosed <form> tag(s) could not be replaced and are still live-looking but dead. Files:\n` +
          unclosedFiles.slice(0, 20).join('\n'),
      );
      process.exit(3);
    }
  }
}
