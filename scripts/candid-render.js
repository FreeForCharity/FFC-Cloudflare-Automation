#!/usr/bin/env node
// Render Candid pages in a real, signed-out Chromium and report what a visitor sees.
//
//   node scripts/candid-render.js <shareable profile link> [...]
//
// For each link it prints one JSON line with:
//   profile: the page after its scripts ran (final URL, title, visible text) - Candid
//            profiles are a script-built app, so a plain GET returns the same empty
//            shell for every link and cannot tell a public page from a sign-in wall;
//   footer:  the footer badge exactly as a site ships it,
//              <a href="<link>"><img src="<seal widget>"></a>
//            loaded in a page, with the image's rendered size (0 = it did not load).
//
// Needs `playwright` (npm i --no-save playwright && npx playwright install chromium).
// Workflow 803 runs it on a GitHub runner; agent containers cannot reach Candid.
'use strict';

const { chromium } = require('playwright');

function profileId(url) {
  const m = /^https:\/\/app\.candid\.org\/profile\/(\d{3,12})(?:[/?#]|$)/i.exec(url);
  return m ? m[1] : '';
}

async function renderProfile(browser, url) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 2000 } });
  const page = await context.newPage();
  const out = { url, finalUrl: '', status: null, title: '', text: '' };
  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    out.status = resp ? resp.status() : null;
    await page.waitForTimeout(3000);
    out.finalUrl = page.url();
    out.title = await page.title();
    out.text = (await page.evaluate(() => document.body.innerText || ''))
      .replace(/\s+/g, ' ')
      .trim();
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
  } finally {
    await context.close();
  }
  return out;
}

async function renderFooter(browser, url, id) {
  const seal = `https://widgets.guidestar.org/prod/v1/pdp/transparency-seal/${id}/svg`;
  const context = await browser.newContext();
  const page = await context.newPage();
  const out = { seal, width: 0, height: 0, complete: false };
  try {
    const href = url.replace(/"/g, '&quot;');
    await page.setContent(
      `<!doctype html><footer><a id="badge" href="${href}"><img id="seal" src="${seal}" alt="Candid Seal of Transparency" style="width:108px;height:auto"></a></footer>`,
      { waitUntil: 'networkidle', timeout: 30000 },
    );
    const r = await page.evaluate(() => {
      const img = document.getElementById('seal');
      return { width: img.naturalWidth, height: img.naturalHeight, complete: img.complete };
    });
    Object.assign(out, r);
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
  } finally {
    await context.close();
  }
  return out;
}

async function main() {
  const urls = process.argv.slice(2).filter(Boolean);
  const browser = await chromium.launch();
  try {
    for (const url of urls) {
      const id = profileId(url);
      const profile = await renderProfile(browser, url);
      const footer = id ? await renderFooter(browser, url, id) : null;
      process.stdout.write(JSON.stringify({ url, profileId: id, profile, footer }) + '\n');
    }
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
