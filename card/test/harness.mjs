// Screenshots the meals card with mock data: every tab of TJ's plan at phone (390) and tablet (1024)
// width, light and dark; Ted's three-recipe plan; the shared iPad's "Who's cooking?" picker and the
// plan after choosing Ted; Ted with no week file yet; plus the stale, Aldi-only, no-Coles, first-run and
// failed-call variants. Checks for horizontal overflow, logs every service call (checking its `person`),
// and checks that `meals_state_changed` only refetches for the person on screen.
// Usage (from home-assistant/): node shopping/card/test/harness.mjs [outdir] [filter]
// Mock data: .venv/bin/python shopping/card/test/make_mocks.py
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const CARD = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// puppeteer-core: installed next to the card (npm i puppeteer-core in card/test), else the floorplan
// renderer's copy in ../dashboard/render.
const puppeteer = await import("puppeteer-core").then((m) => m.default, () =>
  createRequire(path.resolve(CARD, "../../dashboard/render/package.json"))("puppeteer-core"));
const OUT = path.resolve(process.argv[2] || path.join(CARD, "test/out"));
const only = process.argv[3] || "";
fs.mkdirSync(OUT, { recursive: true });

// HA user ids, as in shopping/meals/people.yaml and the mocks.
const USERS = {
  tj: { id: "148d862cd5a04037b7f641dbc0f55c72", name: "TJ" },
  ted: { id: "b37e212b9eb2438c9fcec4e98fe55e5d", name: "Ted" },
  ipad: { id: "6f45a26dcda04f2883b018f6106049f6", name: "iPad" },
  guest: { id: "0000000000000000000000000000beef", name: "Visitor" }, // an HA user not in people.json
};

const server = http.createServer((req, res) => {
  const p = path.join(CARD, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(CARD) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  const type = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json" }[path.extname(p)];
  res.writeHead(200, { "content-type": type || "application/octet-stream", "cache-control": "no-store" });
  fs.createReadStream(p).pipe(res);
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const ORIGIN = `http://127.0.0.1:${server.address().port}`;

const TABS = ["week", "list", "cook", "rate", "tune", "freezer"];
const shots = [];
for (const width of [390, 1024]) for (const dark of [false, true]) for (const tab of TABS) shots.push({ variant: "base", tab, width, dark });
for (const dark of [false, true]) {
  shots.push({ variant: "stale", tab: "week", width: 390, dark });
  shots.push({ variant: "aldi-only", tab: "week", width: 390, dark });
  shots.push({ variant: "aldi-only", tab: "list", width: 390, dark });
  shots.push({ variant: "first-run", tab: "week", width: 390, dark });
  shots.push({ variant: "no-coles", tab: "week", width: 390, dark });
  shots.push({ variant: "no-coles", tab: "list", width: 390, dark });
  // Per person
  for (const width of [390, 1024]) shots.push({ variant: "base", tab: "week", width, dark, user: "ted", name: "ted-week" });
  shots.push({ variant: "base", tab: "list", width: 390, dark, user: "ted", name: "ted-list" });
  shots.push({ variant: "base", tab: "tune", width: 390, dark, user: "ted", name: "ted-tune" });
  shots.push({ variant: "base", tab: "week", width: 390, dark, user: "ipad", name: "ipad-picker" });
  shots.push({ variant: "base", tab: "week", width: 390, dark, user: "ipad", name: "ipad-ted", act: "choose-ted" });
  shots.push({ variant: "ted-new", tab: "week", width: 390, dark, user: "ted", name: "ted-new" });
}
shots.push({ variant: "base", tab: "week", width: 1024, dark: false, user: "ipad", name: "ipad-picker" });
shots.push({ variant: "base", tab: "week", width: 1024, dark: false, user: "ipad", name: "ipad-ted", act: "choose-ted" });
shots.push({ variant: "base", tab: "cook", width: 390, dark: false, user: "ted", name: "ted-cook" });
shots.push({ variant: "ted-new", tab: "tune", width: 390, dark: false, user: "ted", name: "ted-new-tune" });
shots.push({ variant: "base", tab: "week", width: 390, dark: false, user: "guest", name: "guest-picker" });
shots.push({ variant: "aldi-only", tab: "cook", width: 1024, dark: false });
shots.push({ variant: "first-run", tab: "rate", width: 390, dark: false });
shots.push({ variant: "base", tab: "list", width: 390, dark: false, name: "list-ticked", act: "ticks" });
shots.push({ variant: "base", tab: "tune", width: 390, dark: false, name: "tune-confirm", act: "confirm" });
shots.push({ variant: "base", tab: "freezer", width: 390, dark: true, name: "freezer-other", act: "other" });
shots.push({ variant: "base", tab: "list", width: 390, dark: false, name: "list-nopicks", act: "unpick" });
shots.push({ variant: "fail", tab: "week", width: 390, dark: false, name: "fail-pick", act: "pick-fail" });
shots.push({ variant: "base", tab: "list", width: 390, dark: false, name: "list-kept", act: "keep" });
shots.push({ variant: "fail", tab: "list", width: 390, dark: false, name: "fail-keep", act: "keep" });
shots.push({ variant: "fail", tab: "tune", width: 390, dark: false, user: "ted", name: "fail-settings", act: "settings-fail" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const problems = [];
const settled = () => { const r = window.__card?.shadowRoot; return r && (r.querySelector(".tabs") || r.querySelector(".picker")) && !r.textContent.includes("Loading…"); };

async function open(browser, s, { keepStorage = false } = {}) {
  const page = await browser.newPage();
  page.on("pageerror", (e) => problems.push(`[${s.variant}/${s.tab}] pageerror ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !m.text().includes("404") && problems.push(`[${s.variant}/${s.tab}] ${m.text()}`));
  await page.setViewport({ width: s.width, height: s.width < 600 ? 844 : 768, deviceScaleFactor: s.width < 600 ? 2 : 1 });
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: s.dark ? "dark" : "light" }]);
  await page.goto(`${ORIGIN}/test/index.html`);
  await page.waitForFunction("window.__ready");
  if (!keepStorage) await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await mount(page, s);
  return page;
}
async function mount(page, s) {
  await page.evaluate((s, user) => window.__mount({ type: "custom:meals-card", base: `/test/mock/${s.variant}`, tab: s.tab },
    { dark: s.dark, fail: s.variant === "fail", user }), s, USERS[s.user || "tj"]);
  await page.waitForFunction(settled);
}

const click = (page, sel, i = 0) => page.evaluate((sel, i) => {
  const el = window.__card.shadowRoot.querySelectorAll(sel)[i];
  if (!el) throw new Error(`no ${sel} [${i}]`);
  el.click();
}, sel, i);
const choose = async (page, name) => {
  await page.evaluate((name) => [...window.__card.shadowRoot.querySelectorAll(".person")].find((b) => b.textContent.trim() === name).click(), name);
  await page.waitForFunction(settled);
};
// Set an input's value and fire input + change, as a slider release or checkbox tap does.
const fire = (page, sel, v) => page.evaluate((sel, v) => {
  const el = window.__card.shadowRoot.querySelector(sel);
  if (!el) throw new Error(`no ${sel}`);
  if (el.type === "checkbox") el.checked = v; else el.value = v;
  el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
}, sel, v);
const text = (page, sel) => page.evaluate((sel) => [...window.__card.shadowRoot.querySelectorAll(sel)].map((e) => e.textContent.trim()), sel);

async function overflow(page, label) {
  const bad = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth, out = [];
    if (document.documentElement.scrollWidth > vw) out.push(`page scrollWidth ${document.documentElement.scrollWidth} > ${vw}`);
    for (const el of window.__card.shadowRoot.querySelectorAll("*")) {
      if (el.closest(".tabs") || el.tagName === "STYLE") continue;
      const r = el.getBoundingClientRect();
      if (r.width && r.right > vw + 0.5) out.push(`${el.tagName.toLowerCase()}.${el.className} right=${Math.round(r.right)}`);
    }
    return out.slice(0, 5);
  });
  if (bad.length) problems.push(`[${label}] overflow: ${bad.join("; ")}`);
}
const expect = (cond, msg) => { if (!cond) problems.push(msg); };

const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
try {
  for (const s of shots) {
    const label = s.name || `${s.variant}-${s.tab}`;
    const file = `${label}-${s.width}-${s.dark ? "dark" : "light"}.png`;
    if (only && !file.includes(only)) continue;
    const page = await open(browser, s);
    if (s.act === "ticks") { await click(page, ".item", 0); await click(page, ".item", 2); await click(page, ".total", 0); await click(page, ".item", 1); }
    if (s.act === "confirm") await click(page, '[data-a="reset"]');
    if (s.act === "other") {
      await page.evaluate(() => { const sel = window.__card.shadowRoot.querySelector('[data-f="cut"]'); sel.value = "__other"; sel.dispatchEvent(new Event("change", { bubbles: true, composed: true })); });
    }
    if (s.act === "unpick") { await click(page, '[data-a="tab"][data-v="week"]'); await click(page, ".opt .pick", 0); await click(page, '[data-a="tab"][data-v="list"]'); }
    if (s.act === "pick-fail") { await click(page, ".opt .pick", 1); await sleep(300); }
    if (s.act === "keep") { await click(page, '[data-a="fz-keep"]', 0); await sleep(300); }
    if (s.act === "choose-ted") await choose(page, "Ted");
    if (s.act === "settings-fail") { await click(page, '[data-a="recipes"][data-v="1"]'); await sleep(300); }
    await sleep(150);
    if (s.act === "keep") {
      const added = await page.evaluate(() => [...window.__card.shadowRoot.querySelectorAll('[data-a="fz-keep"]:disabled')].length);
      expect(added === (s.variant === "fail" ? 0 : 1), `[${s.variant}] keep: ${added} freeze buttons disabled after add`);
    }
    if (s.variant === "fail" && s.act === "pick-fail") {
      const picked = await page.evaluate(() => [...window.__card.shadowRoot.querySelectorAll(".opt.on")].length);
      expect(picked === 2, `[fail] expected the pick to revert to 2 picked, got ${picked}`);
    }
    if (s.act === "settings-fail") {
      const on = await text(page, '[data-a="recipes"].on');
      expect(on.join() === "3", `[fail] settings: expected recipes to revert to 3, got ${on}`);
    }
    if (label === "ted-week" || (label === "ipad-ted")) {
      const picks = await text(page, ".opt.on .pick"), head = await text(page, ".whobar .nm");
      expect(picks.join() === "✓ Pick 1,✓ Pick 2,✓ Pick 3", `[${file}] Ted's picks: ${picks}`);
      expect(head[0]?.startsWith("Ted’s plan"), `[${file}] header: ${head}`);
      const pp = await text(page, ".cost .pp");
      expect(pp.length && pp.every((t) => t.endsWith("/ 40 g protein")), `[${file}] per-protein line: ${pp[0]}`);
    }
    if (label === "ted-list") {
      const h = await text(page, "h2");
      expect(h[0] && h[0].split(" + ").length === 3, `[${file}] list heading: ${h[0]}`);
    }
    if (label === "ipad-picker" || label === "guest-picker") {
      const names = await text(page, ".person");
      expect(names.join() === "TJ,Ted", `[${file}] picker shows ${names}`);
    }
    if (label === "ted-new") {
      const t = await page.evaluate(() => window.__card.shadowRoot.textContent);
      expect(t.includes("No plan yet") && t.includes("Recipes a week"), `[${file}] first-run text/settings missing`);
    }
    await overflow(page, file);
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
    console.log("ok", file);
    await page.close();
  }

  if (!only || only === "calls") {
    // TJ, on his own login: drive each action once and print what the card sent.
    const page = await open(browser, { variant: "base", tab: "week", width: 390, dark: false });
    await click(page, '[data-a="tab"][data-v="list"]');
    await click(page, '[data-a="fz-keep"]', 0);       // a leftover into the freezer
    await click(page, '[data-a="tab"][data-v="week"]');
    await click(page, ".opt .pick", 1);               // third pick replaces the first
    await click(page, ".opt .pick", 1);               // unpick it again
    await click(page, '[data-a="tab"][data-v="rate"]');
    await click(page, '[data-a="rate"][data-r="again"]', 0);
    await click(page, '[data-a="rate"][data-r="clear"]', 0);
    await click(page, '[data-a="tab"][data-v="tune"]');
    await fire(page, '[data-k="style"][data-v="curry"]', "0");
    await fire(page, '[data-k="style"][data-v="pasta"]', "0.6");
    await fire(page, '[data-k="similar_strength"]', "0.5");
    await fire(page, '[data-k="fade_days"]', "0");
    await click(page, '[data-a="reset"]'); await click(page, '[data-a="reset-yes"]');
    await click(page, '[data-a="recipes"][data-v="3"]');
    await fire(page, '[data-k="serves"]', "4");
    await fire(page, '[data-k="protein_g"]', "45");
    await fire(page, '[data-s="swap"][data-v="sugar"]', false);
    await fire(page, '[data-s="shop"][data-v="woolworths"]', false);
    await click(page, '[data-a="tab"][data-v="freezer"]');
    await fire(page, '[data-a="fz-grams"]', "1500");
    await page.evaluate(() => {
      const r = window.__card.shadowRoot, set = (sel, v, ev = "input") => { const el = r.querySelector(sel); el.value = v; el.dispatchEvent(new Event(ev, { bubbles: true, composed: true })); };
      set('[data-f="cut"]', "chicken thigh", "change"); set('[data-f="grams"]', "1200"); set('[data-f="seasoning"]', "lemongrass");
    });
    await click(page, '[data-a="fz-add"]');
    await click(page, '[data-a="fz-del"]', 0);
    const tj = await page.evaluate(() => ({ calls: window.__calls, event: window.__eventType }));
    console.log("\nsubscribed to:", tj.event);
    console.log("TJ (own login):");
    for (const c of tj.calls) {
      console.log("  call", `${c.domain}.${c.service}`, JSON.stringify(c.data));
      expect(c.data.person === USERS.tj.id, `[calls] ${c.service} sent person ${c.data.person}, expected TJ`);
    }
    expect(!tj.calls.some((c) => c.service === "meals_freezer" && c.data.set == null && c.data.add == null && c.data.remove == null), "[calls] freezer call with no action");
    await page.close();

    // The shared iPad: choose Ted, act, and check every call is for Ted.
    const ip = await open(browser, { variant: "base", tab: "week", width: 390, dark: false, user: "ipad" });
    await choose(ip, "Ted");
    await click(ip, ".opt:not(.on) .pick", 0);        // a fourth pick replaces Ted's first
    await click(ip, '[data-a="tab"][data-v="tune"]');
    await click(ip, '[data-a="recipes"][data-v="2"]');
    await fire(ip, '[data-k="protein_g"]', "35");
    await fire(ip, '[data-s="swap"][data-v="oil"]', true);
    await fire(ip, '[data-s="shop"][data-v="coles"]', false); // leaves Aldi only
    const lastShop = await ip.evaluate(() => window.__card.shadowRoot.querySelector('[data-s="shop"][data-v="aldi"]').disabled);
    expect(lastShop, "[calls] the last store left ticked should be disabled");
    const ted = await ip.evaluate(() => window.__calls);
    console.log("iPad, choosing Ted:");
    for (const c of ted) {
      console.log("  call", `${c.domain}.${c.service}`, JSON.stringify(c.data));
      expect(c.data.person === USERS.ted.id, `[calls] iPad ${c.service} sent person ${c.data.person}, expected Ted`);
    }
    expect(ted.find((c) => c.service === "meals_pick")?.data.slugs.length === 3, "[calls] Ted's pick should send three slugs");

    // meals_state_changed: refetch only for this person (or when the event names nobody).
    const refetches = async (data) => {
      await ip.evaluate(() => { window.__fetches.length = 0; });
      await ip.evaluate((data) => window.__fire({ event_type: "meals_state_changed", data }), data);
      await sleep(200);
      return ip.evaluate(() => window.__fetches.filter((u) => u.endsWith("state.json")).length);
    };
    const other = await refetches({ file: "state", person: USERS.tj.id });
    const mine = await refetches({ file: "state", person: USERS.ted.id });
    const anyone = await refetches({ file: "state" });
    console.log(`event refetches: TJ's event ${other}, Ted's ${mine}, no person ${anyone}`);
    expect(other === 0 && mine === 1 && anyone === 1, `[event] refetch counts TJ/Ted/none = ${other}/${mine}/${anyone}, expected 0/1/1`);

    // The choice survives a reload on the same device; Switch person goes back to the picker.
    await mount(ip, { variant: "base", tab: "week", width: 390, dark: false, user: "ipad" });
    const head = await text(ip, ".whobar .nm");
    expect(head[0]?.startsWith("Ted’s plan"), `[remember] after remount the header is ${head}`);
    await click(ip, '[data-a="switch"]');
    expect((await text(ip, ".person")).length === 2, "[switch] picker didn't come back");
    await mount(ip, { variant: "base", tab: "week", width: 390, dark: false, user: "ipad" });
    expect((await text(ip, ".person")).length === 2, "[switch] picker should stay after a switch and remount");
    await ip.close();
  }
} finally {
  await browser.close();
  server.close();
}
if (problems.length) { console.log("\nPROBLEMS:"); problems.forEach((p) => console.log(" ", p)); process.exitCode = 1; }
else console.log("\nno overflow, no page errors, every call carried the right person; screenshots in", OUT);
