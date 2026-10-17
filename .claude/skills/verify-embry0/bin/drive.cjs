// drive.cjs: the browser and API half of control-embry0. Read-only: it never clicks a
// control that writes, and `api` sends GET only. JSON on stdout.
'use strict';
const { chromium } = require('playwright');

const BASE = (process.env.EMBRY0_VERIFY_URL || '').replace(/\/$/, '');
const KEY = process.env.EMBRY0_API_KEY || '';
const STORAGE_KEY = 'embry0.api-key'; // frontend/src/lib/apiKey.ts

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) out[argv[i].slice(2)] = argv[i + 1], i++;
    else out._.push(argv[i]);
  }
  return out;
}
const print = (o) => process.stdout.write(JSON.stringify(o, null, 2) + '\n');
const fail = (msg) => { print({ ok: false, error: msg }); process.exit(1); };

async function getJson(path, auth) {
  const r = await fetch(BASE + path, { headers: auth && KEY ? { Authorization: `Bearer ${KEY}` } : {} });
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  return { status: r.status, body };
}

async function openPage(route, width) {
  const browser = await chromium.launch();
  const w = Number(width || 1440);
  const ctx = await browser.newContext({ viewport: { width: w, height: w < 768 ? 844 : 900 } });
  // The dashboard's gate uses a stored key before any SSO redirect (AuthGate.tsx).
  await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch {} }, [STORAGE_KEY, KEY]);
  const page = await ctx.newPage();
  const events = [];
  page.on('pageerror', (e) => events.push(`pageerror: ${String(e).slice(0, 200)}`));
  page.on('console', (m) => { if (m.type() === 'error') events.push(`console: ${m.text().slice(0, 200)}`); });
  page.on('response', (r) => { if (r.status() >= 400) events.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '').slice(0, 120)}`); });
  await page.goto(BASE + (route || '/'), { waitUntil: 'domcontentloaded' });
  try { await page.waitForFunction(() => !/Connecting/.test(document.body.innerText) && (document.querySelector('main')?.innerText || '').trim().length > 20, null, { timeout: 20000 }); } catch {}
  try { await page.waitForLoadState('networkidle', { timeout: 8000 }); } catch {}
  return { browser, page, events };
}

async function probe(page) {
  return page.evaluate(() => {
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const sb = document.querySelector('[data-testid="sidebar"]');
    const crumbs = [...document.querySelectorAll('nav[aria-label*="readcrumb"] a, nav[aria-label*="readcrumb"] span')]
      .map((e) => e.textContent.trim()).filter((t) => t && t !== '/');
    return {
      url: location.pathname,
      title: document.title,
      h1: [...document.querySelectorAll('h1')].map((h) => h.innerText.trim()),
      breadcrumbs: [...new Set(crumbs)],
      horizontal_scroll: document.documentElement.scrollWidth > window.innerWidth,
      sidebar: sb ? { visible: vis(sb), mode: sb.dataset.mode || null } : null,
      open_menu_button: !![...document.querySelectorAll('button')].find((b) => vis(b) && b.getAttribute('aria-label') === 'Open menu'),
      table_rows: document.querySelectorAll('main table tbody tr').length,
      text_chars: (document.querySelector('main')?.innerText || '').length,
      bundle: document.querySelector('script[type=module]')?.getAttribute('src') || null,
    };
  });
}

(async () => {
  const a = args(process.argv.slice(2));
  const cmd = a._[0];
  if (!BASE) fail('EMBRY0_VERIFY_URL is empty');
  if (cmd === 'doctor') {
    const health = await getJson('/health', false).catch((e) => ({ status: 0, body: String(e) }));
    const ready = await getJson('/api/v1/health/ready', true).catch((e) => ({ status: 0, body: String(e) }));
    const authed = await getJson('/api/v1/queue', true).catch(() => ({ status: 0 }));
    const html = await fetch(BASE + '/').then((r) => r.text()).catch(() => '');
    const bundle = (html.match(/src="(\/assets\/index-[^"]+\.js)"/) || [])[1] || null;
    const served_head = process.env.EMBRY0_SERVED_HEAD || null;
    const worth = health.status === 200 && authed.status === 200 && !!bundle;
    return print({ ok: true, url: BASE, health: health.status, ready: ready.body, api_key_accepted: authed.status === 200,
      bundle, served: { head: served_head, source: served_head ? 'image label of the launched container' : 'not launched by this skill' },
      worth_driving: worth });
  }
  if (cmd === 'api') {
    const path = a._[1];
    if (!path || !path.startsWith('/api/')) fail('api takes a path under /api/');
    const r = await getJson(path, true);
    return print({ ok: r.status < 400, status: r.status, body: r.body });
  }
  if (['probe', 'shot', 'read'].includes(cmd)) {
    const { browser, page, events } = await openPage(a.route, a.width);
    try {
      const out = { ok: true, route: a.route || '/', width: Number(a.width || 1440), ...(await probe(page)) };
      if (cmd === 'shot') {
        if (!a.path) fail('shot needs --path');
        await page.screenshot({ path: a.path, fullPage: a.full === 'true' });
        out.path = a.path;
      }
      if (cmd === 'read') out.text = await page.locator(a.selector || 'main').first().innerText().catch(() => null);
      out.errors = events;
      print(out);
    } finally { await browser.close(); }
    return;
  }
  fail(`unknown command ${cmd || ''}`);
})().catch((e) => fail(String(e && e.message || e)));
