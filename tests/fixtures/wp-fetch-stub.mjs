/**
 * wp-fetch-stub.mjs — a synthetic WordPress, served by replacing `fetch`.
 *
 * Loaded with `node --import`, so it runs before the capture's own module body
 * and needs no production code to know it exists. That is the whole point:
 * the capture keeps talking to `https://<domain>` with its real request path,
 * its real redirect handling and its real timeouts, and this file decides what
 * comes back. No listening socket, no DNS, no loopback address — so none of
 * the private-host and SSRF guards have to be weakened to make the pipeline
 * testable, which is the usual price of an `--origin` override.
 *
 * What it exists to catch: `capture()` is ~750 lines that the offline
 * `--self-test` never enters, because the self-test exercises pure functions.
 * Two defects have now shipped in that gap. The one that prompted this file
 * was a tally declared beside the pass that fills it in and reported on in an
 * earlier pass — a temporal dead zone `node --check` cannot see, which
 * surfaced as a ReferenceError six minutes into a live crawl of a charity's
 * site. Everything here runs in about a second.
 *
 * The fixture is deliberately a *hostile* WordPress rather than a tidy one. It
 * reproduces, in miniature, every shape this migration has actually been bitten
 * by: a stale `home` pointing at a domain the site does not serve, a front page
 * that redirects there, animated content hidden behind JavaScript, a stylesheet
 * that only an inline script names, an oversized PNG, and a reference the
 * origin 404s. A fixture that only serves well-formed pages would pass whether
 * or not any of those are handled.
 */
const DOMAIN = 'fixture.test';
const STALE = 'parked.example';
const ORIGIN = `https://${DOMAIN}`;

const PAGES = [
  { id: 2, slug: '', title: 'Home', type: 'page' },
  { id: 3, slug: 'about-us', title: 'About Us', type: 'page' },
  { id: 4, slug: 'contact', title: 'Contact', type: 'page' },
];
const POSTS = [{ id: 9, slug: 'first-post', title: 'First Post', type: 'post' }];

/** A 1366x768 PNG that is over any sane per-file budget, and is photographic. */
function bigPng() {
  // Sized so the stored-deflate payload clears the 400 KB per-file budget the
  // FFC-EX repos enforce: under it, the re-encode branch is never entered and
  // the smoke run would report a confident zero for a path it never reached.
  const w = 420;
  const h = 380;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  // Pseudo-random RGB so it does not compress away — a flat image would shrink
  // losslessly and never exercise the re-encode path.
  let seed = 1;
  for (let y = 0; y < h; y += 1) {
    const row = y * (w * 3 + 1);
    raw[row] = 0;
    for (let x = 0; x < w * 3; x += 1) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      raw[row + 1 + x] = (seed >> 16) & 0xff;
    }
  }
  const zlib = require('node:zlib');
  const idat = zlib.deflateSync(raw, { level: 0 }); // stored: guarantees bulk
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crcBuf = Buffer.alloc(4);
    crcBuf.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([len, body, crcBuf]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;
  ihdr[9] = 2; // RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

let CRC_TABLE;
function crc32(buf) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Int32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c;
    }
  }
  let c = -1;
  for (let i = 0; i < buf.length; i += 1) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return c ^ -1;
}

const { createRequire } = await import('node:module');
const require = createRequire(import.meta.url);

function html(entry) {
  const rel = entry.slug ? '../' : './';
  return (
    `<!DOCTYPE html><html><head>
<title>${entry.title} | Fixture</title>
<link rel="canonical" href="${ORIGIN}/${entry.slug ? entry.slug + '/' : ''}">
<link rel="EditURI" type="application/rsd+xml" href="${ORIGIN}/xmlrpc.php?rsd">
<link rel="https://api.w.org/" href="${ORIGIN}/wp-json/">
<link rel="alternate" type="application/json+oembed" href="${ORIGIN}/wp-json/oembed/1.0/embed?url=x">
<link rel="alternate" hreflang="fr" href="${ORIGIN}/fr/">
<link rel="modulepreload" href="${ORIGIN}/wp-includes/js/interactivity.js">
<meta name="generator" content="WordPress 6.8.1">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0">
<style>.et-waypoint:not(.et_pb_counters){opacity:0}.hero{background:url(${ORIGIN}/wp-content/uploads/bg.png)}</style>
<script type="application/ld+json">{"@type":"Organization","name":"Fixture"}</script>
<script src="${ORIGIN}/wp-includes/js/jquery/jquery.min.js"></script>
<script>var monsterinsights_frontend={"js_events_tracking":"true"};</script>
</head><body>
<div id="main-content" data-role="content">
<div class="et_pb_menu"><div class="et_pb_menu__menu"><nav class="et-menu-nav"><ul id="menu-main" class="et-menu nav">
<li class="menu-item"><a href="https://${STALE}/">Home</a></li>
<li class="menu-item"><a href="${ORIGIN}/about-us/">About Us</a></li>
<li class="menu-item"><a href="/contact/">Contact</a></li>
</ul></nav></div><div class="et_mobile_nav_menu"><div class="mobile_nav closed"><span class="mobile_menu_bar"></span></div></div></div>
<div class="et-waypoint et_pb_animation_fade_in"><h1>${entry.title}</h1><p>Body text for ${entry.title}.</p></div>
<img src="${ORIGIN}/wp-content/uploads/flyer.png" alt="flyer">
<img src="${ORIGIN}/wp-content/uploads/gone.png" alt="missing on the origin too">
<form action="${ORIGIN}/wp-comments-post.php" method="post"><input name="x"><button>Send</button></form>
<script>(function(){var file=["${ORIGIN}\\/wp-content\\/et-cache\\/${entry.id}\\/late.css"];
var link=document.createElement('link');link.rel='stylesheet';link.href=file;})();</script>
<script src="${ORIGIN}/wp-content/themes/Divi/js/scripts.min.js"></script>
</div>
</body></html>`.replace(/\n/g, '\n') + `<!-- ${rel} -->`
  );
}

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

const ROUTES = new Map();
ROUTES.set('/wp-json/', () =>
  json({
    name: 'Fixture',
    url: `https://${STALE}`,
    home: `https://${STALE}`,
    namespaces: ['wp/v2'],
  }),
);
ROUTES.set('/sitemap.xml', () => {
  const urls = [...PAGES, ...POSTS]
    .map((e) => `<url><loc>${ORIGIN}/${e.slug ? e.slug + '/' : ''}</loc></url>`)
    .join('');
  return new Response(`<?xml version="1.0"?><urlset>${urls}</urlset>`, {
    status: 200,
    headers: { 'content-type': 'application/xml' },
  });
});

function collection(items, total) {
  return json(
    items.map((e) => ({
      id: e.id,
      slug: e.slug || 'home',
      link: `${ORIGIN}/${e.slug ? e.slug + '/' : ''}`,
      title: { rendered: e.title },
      type: e.type,
    })),
    200,
    { 'x-wp-total': String(total ?? items.length) },
  );
}

const originalFetch = globalThis.fetch;
globalThis.fetch = async function stubbedFetch(input, init) {
  const url = typeof input === 'string' ? input : (input?.url ?? String(input));
  let u;
  try {
    u = new URL(url);
  } catch {
    return originalFetch(input, init);
  }
  const host = u.hostname.replace(/^www\./, '');
  const path = u.pathname;

  // The stale host the CMS names but does not serve. Anything that lands here
  // is the parked page — which is exactly what the capture must refuse.
  if (host === STALE) {
    return new Response(
      '<html><head><title>parked.example</title></head><body>parked</body></html>',
      {
        status: 200,
        headers: { 'content-type': 'text/html' },
      },
    );
  }
  if (host !== DOMAIN) return new Response('nope', { status: 404 });

  const exact = ROUTES.get(path);
  if (exact) return exact();

  if (path === '/wp-sitemap.xml' || path === '/robots.txt')
    return new Response('', { status: 404 });
  if (path === '/wp-json/wp/v2/pages') return collection(PAGES);
  if (path === '/wp-json/wp/v2/posts') return collection(POSTS);
  if (path === '/wp-json/wp/v2/media')
    return json([{ source_url: `${ORIGIN}/wp-content/uploads/flyer.png` }], 200, {
      'x-wp-total': '1',
    });
  if (path.startsWith('/wp-json/')) return json([], 200, { 'x-wp-total': '0' });

  if (path === '/wp-content/uploads/flyer.png' || path === '/wp-content/uploads/bg.png')
    return new Response(bigPng(), { status: 200, headers: { 'content-type': 'image/png' } });
  // A reference the origin itself 404s: reproduced faithfully, never a gate failure.
  if (path === '/wp-content/uploads/gone.png') return new Response('', { status: 404 });
  if (path.endsWith('.css'))
    return new Response('.late{color:red}.et-waypoint{opacity:0}', {
      status: 200,
      headers: { 'content-type': 'text/css' },
    });
  if (path.endsWith('.js'))
    return new Response('/* vendor bundle */', {
      status: 200,
      headers: { 'content-type': 'text/javascript' },
    });

  // Pages. The FRONT page redirects off-site, which is the defect this whole
  // migration started from: `redirect: 'follow'` means a 200 says nothing
  // about whose page came back.
  const slug = path.replace(/^\/+|\/+$/g, '');
  if (slug === '') {
    const res = new Response(
      '<html><head><title>parked.example</title></head><body>parked</body></html>',
      { status: 200, headers: { 'content-type': 'text/html' } },
    );
    Object.defineProperty(res, 'url', { value: `https://${STALE}/` });
    return res;
  }
  const entry = [...PAGES, ...POSTS].find((e) => e.slug === slug);
  if (!entry) return new Response('', { status: 404 });
  const res = new Response(html(entry), {
    status: 200,
    headers: { 'content-type': 'text/html' },
  });
  Object.defineProperty(res, 'url', { value: `${ORIGIN}${path}` });
  return res;
};
