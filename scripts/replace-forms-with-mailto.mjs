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
 * So every form that sends a message is replaced, in the markup, with a
 * visible contact block carrying a mailto: link. That is a deliberate
 * downgrade, not a port: the visitor loses in-page submission and gains a
 * channel that actually delivers. Search and sign-in forms send no message and
 * are handled differently (see formKind).
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
 * What a form is for, judged from its own markup.
 *
 * - `search`: a GET with an `s` field or `role="search"`. It sends nothing to
 *   anyone, so it stays: a mailto: block in the header search slot reads as a
 *   contact box on every page, and the site decides separately whether to back
 *   search with a static index or remove it.
 * - `login`: a sign-in, registration or password-reset form (wp-login.php, a
 *   password field, or WordPress's `log`/`user_login` fields). It is removed
 *   with no replacement: there is no account to sign in to, and offering email
 *   in place of a password box invites people to send credentials.
 * - `message`: everything else, replaced with the mailto: block.
 */
export function formKind(formHtml) {
  const open = (formHtml.match(/^<form[^>]*>/i) || [''])[0];
  if (
    /wp-login\.php/i.test(open) ||
    /<input\b[^>]*\stype\s*=\s*["']?password\b/i.test(formHtml) ||
    /<input\b[^>]*\sname\s*=\s*["']?(log|pwd|user_login)["'\s/>]/i.test(formHtml)
  ) {
    return 'login';
  }
  // `\s` before each attribute name, not `\b`: `\b` also matches after the
  // hyphen in `data-name="user_login"`, which is not a field at all.
  const isGet = !/\smethod\s*=\s*["']?post\b/i.test(open);
  const hasQueryField = /<input\b[^>]*\sname\s*=\s*["']?s["'\s/>]/i.test(formHtml);
  if (isGet && (hasQueryField || /\srole\s*=\s*["']?search\b/i.test(open))) return 'search';
  return 'message';
}

/**
 * Neutralize every closed <form> in `html`. Returns
 * {html, replaced, removed, kept, unclosed}.
 *
 * `replaced` counts message forms rewritten to the mailto: block, `removed`
 * sign-in forms dropped, and `kept` search forms left as they are. `unclosed`
 * counts forms left in place because their extent could not be determined. A
 * caller that treats `unclosed > 0` as success ships a live-looking dead form,
 * which is the whole thing this script exists to prevent — so the CLI exits
 * non-zero on it.
 */
export function replaceForms(html, email, subject) {
  const { spans, unclosed } = findFormSpans(html);
  const counts = { replaced: 0, removed: 0, kept: 0 };
  if (!spans.length) return { html, ...counts, unclosed: unclosed.length };
  const block = mailtoBlock(email, subject);
  let out = html;
  // Splice from the end so earlier offsets stay valid.
  for (let i = spans.length - 1; i >= 0; i--) {
    const kind = formKind(html.slice(spans[i].start, spans[i].end));
    if (kind === 'search') {
      counts.kept++;
      continue;
    }
    const replacement = kind === 'login' ? '' : block;
    counts[kind === 'login' ? 'removed' : 'replaced']++;
    out = out.slice(0, spans[i].start) + replacement + out.slice(spans[i].end);
  }
  return { html: out, ...counts, unclosed: unclosed.length };
}

/**
 * The forms a neutralized page must not still carry: anything but a search
 * form, plus any form whose extent cannot be determined. The delivery step
 * re-checks the downloaded capture with this before it becomes a published
 * page.
 */
export function messageFormCount(html) {
  const { spans } = findFormSpans(html);
  return spans.filter((span) => formKind(html.slice(span.start, span.end)) === 'message').length;
}

export function unsafeForms(html) {
  const { spans, unclosed } = findFormSpans(html);
  const kinds = spans
    .map((span) => formKind(html.slice(span.start, span.end)))
    .filter((kind) => kind !== 'search');
  return [...kinds, ...unclosed.map(() => 'unclosed')];
}

/**
 * Every .html/.htm file under root, depth-first.
 *
 * A directory this cannot read is a HARD ERROR, not an empty result. Swallowing
 * it returns [], the caller replaces nothing, and the run reports success —
 * which is the same shape as the grep bug this script's workflow step already
 * had: a search that never ran reading as "nothing to do", ending in a
 * published page with a live-looking dead form on it. A wrong --dir, a
 * permission problem and a genuinely form-free site must not look alike.
 */
export function htmlFilesUnder(root) {
  const out = [];
  const walk = (dir) => {
    let entries;
    try {
      entries = readdirSync(dir);
    } catch (err) {
      throw new Error(`cannot read directory ${dir}: ${err.message}`, { cause: err });
    }
    for (const e of entries) {
      const p = join(dir, e);
      let st;
      try {
        st = statSync(p);
      } catch (err) {
        // An entry readdir just listed that stat cannot see is a race, not a
        // tree we failed to read — but it still means one file went unscanned,
        // so it is surfaced rather than silently dropped.
        throw new Error(`cannot stat ${p}: ${err.message}`, { cause: err });
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

  // Real markup from newheightseducation.org (Jupiter on the apex and school.,
  // Astra on publications.), where every page's header search became a
  // contact block and school.'s sign-in popups became three of them.
  const jupiterSearch =
    '<form class="responsive-searchform" method="get" action="https://example.org/">' +
    '<input type="text" class="text-input" value="" name="s" id="s" placeholder="Search.." />' +
    '<i><input value="" type="submit" /></i></form>';
  const overlaySearch =
    '<form method="get" id="mk-fullscreen-searchform" action="https://example.org/">' +
    '<input type="text" value="" name="s" id="mk-fullscreen-search-input" /></form>';
  const astraSearch =
    '<form role="search" method="get" class="search-form" action="https://example.org/">' +
    '<label for="search-field"><input type="search" id="search-field" class="search-field" ' +
    'placeholder="Search..." value="" name="s"></label></form>';
  const login =
    '<form id="mk_login_form" method="post" class="mk-login-form" action="https://example.org/wp-login.php">' +
    '<input type="text" id="username" name="log"><input type="password" id="password" name="pwd"></form>';
  const register =
    '<form id="register_form" method="post" action="https://example.org/wp-login.php?action=register">' +
    '<input type="text" name="user_login"><input type="text" name="user_email"></form>';
  const lostPassword =
    '<form id="forgot_form" method="post" action="https://example.org/wp-login.php?action=lostpassword">' +
    '<input type="text" name="user_login"></form>';
  const caldera =
    '<form class="CF57ed5808ea4f5 caldera_forms_form" method="POST" data-form-id="CF57ed5808ea4f5">' +
    '<input type="text" name="fld_8768091"><textarea name="fld_7683514"></textarea></form>';

  eq(
    'formKind keeps the three header search forms as search',
    [jupiterSearch, overlaySearch, astraSearch].map(formKind),
    ['search', 'search', 'search'],
  );
  eq(
    'formKind marks sign-in, registration and password-reset forms as login',
    [login, register, lostPassword].map(formKind),
    ['login', 'login', 'login'],
  );
  eq('formKind leaves a contact form as a message form', formKind(caldera), 'message');
  eq(
    'a POST form with a field named s still counts as a message form',
    formKind('<form method="post"><input name="s"><textarea name="m"></textarea></form>'),
    'message',
  );
  eq(
    'a GET form with no search field is still a message form',
    formKind('<form action="/x"><input name="email"></form>'),
    'message',
  );
  eq(
    'replaceForms keeps search, removes sign-in and replaces only the contact form',
    (() => {
      const block = mailtoBlock('i@e.org', '');
      const r = replaceForms(
        `<nav>${jupiterSearch}</nav><div>${login}</div><main>${caldera}</main>${overlaySearch}`,
        'i@e.org',
        '',
      );
      return [
        r.replaced,
        r.removed,
        r.kept,
        r.html === `<nav>${jupiterSearch}</nav><div></div><main>${block}</main>${overlaySearch}`,
      ];
    })(),
    [1, 1, 2, true],
  );

  eq(
    'a data- attribute that ends in a field name is not that field',
    [
      formKind('<form method="post"><input data-name="user_login" name="email"></form>'),
      formKind('<form><input data-type="password" name="code"></form>'),
      formKind('<form data-method="post"><input name="s"></form>'),
    ],
    ['message', 'message', 'search'],
  );
  eq(
    'messageFormCount counts only forms that send a message',
    [
      messageFormCount(`${jupiterSearch}${login}`),
      messageFormCount(`${jupiterSearch}${caldera}${register}`),
    ],
    [0, 1],
  );
  eq(
    'unsafeForms allows search forms and reports every other kind',
    [
      unsafeForms(`<nav>${jupiterSearch}</nav>${astraSearch}`),
      unsafeForms(`${jupiterSearch}${caldera}${login}<form>open`),
    ],
    [[], ['message', 'login', 'unclosed']],
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

  eq(
    'htmlFilesUnder throws on an unreadable tree instead of returning nothing',
    (() => {
      try {
        htmlFilesUnder(join(tmpdir(), `ffc-does-not-exist-${Date.now()}`));
        return 'returned normally';
      } catch (err) {
        return /cannot read directory/.test(err.message) ? 'threw' : `wrong error: ${err.message}`;
      }
    })(),
    'threw',
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
  } else if (process.argv.includes('--count-message-forms')) {
    // How many forms would need the mailto: block. The workflow asks before it
    // insists on a contact address: a capture whose only forms are search or
    // sign-in forms needs none.
    const dir = arg('dir');
    if (!dir) {
      console.error(
        'Usage: node scripts/replace-forms-with-mailto.mjs --count-message-forms --dir <siteRoot>',
      );
      process.exit(64);
    }
    let total = 0;
    for (const f of htmlFilesUnder(dir)) total += messageFormCount(readFileSync(f, 'utf8'));
    console.log(total);
  } else if (process.argv.includes('--check')) {
    const dir = arg('dir');
    if (!dir) {
      console.error('Usage: node scripts/replace-forms-with-mailto.mjs --check --dir <siteRoot>');
      process.exit(64);
    }
    const offenders = [];
    for (const f of htmlFilesUnder(dir)) {
      const kinds = unsafeForms(readFileSync(f, 'utf8'));
      if (kinds.length) offenders.push(`${relative(dir, f)}: ${kinds.join(', ')}`);
    }
    if (offenders.length) {
      console.error(
        `::error::${offenders.length} page(s) still carry a form that is not a search form. A static export has no form backend, so this would publish a form that accepts a visitor's message and drops it.\n` +
          offenders.slice(0, 20).join('\n'),
      );
      process.exit(1);
    }
    console.log('No message, sign-in or unclosed forms remain; only search forms.');
  } else {
    const dir = arg('dir');
    const email = arg('email');
    const subject = arg('subject', '');
    const dryRun = process.argv.includes('--dry-run');

    if (!dir) {
      console.error(
        'Usage: node scripts/replace-forms-with-mailto.mjs --dir <siteRoot> [--email <addr>] [--subject "<line>"] [--dry-run]',
      );
      process.exit(64);
    }
    if (email && !isPlausibleEmail(email)) {
      console.error(
        `::error::--email '${email}' is not a plausible address. It becomes the site's only contact channel; a typo here silently ends every conversation.`,
      );
      process.exit(64);
    }

    let htmlFiles;
    try {
      htmlFiles = htmlFilesUnder(dir);
    } catch (err) {
      console.error(
        `::error::${err.message}. Refusing to report "no forms" for a tree that could not be read.`,
      );
      process.exit(66);
    }
    // Zero HTML files under a directory we were asked to rewrite is a wrong
    // --dir, not a form-free site: the caller only invokes this after finding
    // forms. Exiting 0 here would report success for a run that examined
    // nothing.
    if (htmlFiles.length === 0) {
      console.error(
        `::error::no .html/.htm files under ${dir}. Nothing was scanned, so "no forms found" would be a false negative.`,
      );
      process.exit(66);
    }
    // Only a message form needs the address. Without one, search and sign-in
    // forms are still handled; a message form is refused rather than replaced
    // with a mailto: that goes nowhere.
    if (!email && htmlFiles.some((f) => messageFormCount(readFileSync(f, 'utf8')) > 0)) {
      console.error(
        '::error::--email is required: at least one form sends a message and needs the mailto: block.',
      );
      process.exit(64);
    }

    let files = 0;
    let replaced = 0;
    let removed = 0;
    let kept = 0;
    let unclosed = 0;
    const unclosedFiles = [];
    for (const f of htmlFiles) {
      const src = readFileSync(f, 'utf8');
      const r = replaceForms(src, email, subject);
      if (r.unclosed) {
        unclosed += r.unclosed;
        unclosedFiles.push(f);
      }
      kept += r.kept;
      if (r.replaced || r.removed) {
        files++;
        replaced += r.replaced;
        removed += r.removed;
        if (!dryRun) writeFileSync(f, r.html, 'utf8');
      }
    }

    const verb = dryRun ? 'would replace' : 'replaced';
    console.log(
      `${verb} ${replaced} form(s) and removed ${removed} sign-in form(s) across ${files} file(s) under ${dir}; kept ${kept} search form(s)`,
    );
    if (process.env.GITHUB_STEP_SUMMARY) {
      writeFileSync(
        process.env.GITHUB_STEP_SUMMARY,
        `\n### Forms\n\n- ${verb} **${replaced}** form(s) across **${files}** file(s) with a mailto: block for \`${email}\`\n` +
          `- removed **${removed}** sign-in form(s); kept **${kept}** search form(s)\n` +
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
