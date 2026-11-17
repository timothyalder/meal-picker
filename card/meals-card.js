/*
 * meals-card: the weekly meal picker (shopping/PLAN.md).
 *
 * Per person (meals/SCHEMA.md "Per person"): reads <base>/people.json ({key: {name, shared,
 * has_plan}}, key = HA user id), then the person's <base>/week-<key>.json and their entry in
 * <base>/state.json's `people`. The person is the logged-in user (hass.user.id) when they have a
 * plan; on a shared screen (or for a user without a plan) the card asks "Who's cooking?" and
 * remembers the answer on that device. Every service call carries `person`. Every tap calls a
 * pyscript service; the card applies the change optimistically and reverts it if the call fails.
 * Refetches on the HA event `meals_state_changed` (only when its `person` is this one or
 * absent), when the page becomes visible again, and every 10 minutes.
 *
 * Config:
 *   type: custom:meals-card
 *   base: /local/meals       # where people.json, week-<key>.json and state.json are served (default)
 *   tab: week                # optional starting tab: week | list | cook | rate | tune | freezer
 *
 * The person picked on a shared screen, tick marks on the shopping list, the store chosen for
 * the list and which leftovers were already added to the freezer this week are per-device
 * conveniences kept in localStorage (per person); everything else goes through the services.
 */

const TABS = [
  ["week", "This week"], ["list", "List"], ["cook", "Cook"],
  ["rate", "Rate"], ["tune", "Tune"], ["freezer", "Freezer"],
];
const STORES = { aldi: "Aldi", woolworths: "Woolworths", coles: "Coles", split: "Split" };
const SHOPS = ["aldi", "woolworths", "coles"]; // planner STORES order; a week file may carry fewer
const nameList = (ss) => ss.map((s) => STORES[s] || esc(s)).join(" and ");
const RATINGS = [["again", "Again"], ["fine", "Fine"], ["never", "Never"]];
const REFRESH_MS = 10 * 60 * 1000;
const LS = "meals-card:";
// A person's settings when state.json has none yet (TJ's, per SCHEMA.md).
const DEFAULT_SETTINGS = { recipes: 2, serves: 6, protein_g: 50,
  swaps: { cream: true, oil: true, butter: true, sugar: true }, stores: ["aldi", "woolworths", "coles"] };
const SWAPS = [["cream", "Cream → Greek yoghurt"], ["oil", "Oil → spray oil"],
  ["butter", "Butter → spray oil, or left out"], ["sugar", "Sugar removed"]];
const NUM = ["none", "one", "two", "three"];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null || isNaN(v) ? "—" : `$${Number(v).toFixed(2)}`);
const comboKey = (slugs) => [...slugs].sort().join("+"); // a week file `pairs` key: 1 to 3 slugs
const possessive = (name) => `${esc(name)}${/s$/i.test(name) ? "’" : "’s"}`;
const weight = (g) => (g >= 1000 ? `${+(g / 1000).toFixed(1)} kg` : `${Math.round(g)} g`);
// Extra meat from whole packs that is cooked this week rather than frozen (planner `extra` entries).
const extraName = (e) => e.cut || e.name;
const perServeLine = (ps, serves, meat) => [`${serves} serves`, ps.protein_g != null ? `${ps.protein_g} g protein` : null,
  ps.kcal != null ? `${ps.kcal} kcal` : null, ps.meat_g != null ? `${ps.meat_g} g raw ${esc(meat || "meat")}` : null]
  .filter(Boolean).join(" · ");
const leftover = (i) => (i.unit === "g" ? weight(i.freeze_qty) : i.unit === "ml"
  ? (i.freeze_qty >= 1000 ? `${(i.freeze_qty / 1000).toFixed(1)} L` : `${Math.round(i.freeze_qty)} ml`) : `${+i.freeze_qty.toFixed(1)}`);
const titleCase = (slug) => String(slug).replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function lsGet(key, fallback) {
  try { const v = localStorage.getItem(LS + key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
}
function lsSet(key, value) {
  try { localStorage.setItem(LS + key, JSON.stringify(value)); } catch (e) { /* private window, blocked storage */ }
}

function parseDate(s) {
  if (!s) return null;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T00:00:00` : s);
  return isNaN(d) ? null : d;
}
function fmtDay(s) {
  const d = parseDate(s);
  return d ? d.toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short" }) : esc(s || "");
}
function fmtWhen(s) {
  const d = parseDate(s);
  if (!d) return esc(s || "");
  return `${fmtDay(s)}, ${d.toLocaleTimeString("en-AU", { hour: "numeric", minute: "2-digit" }).replace(" ", "")}`;
}
function addDays(iso, n) {
  const d = parseDate(iso);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const CSS = `
:host { display: block; container-type: inline-size; --gap: 12px;
  --m-bg: var(--ha-card-background, var(--card-background-color, #fff));
  --m-fg: var(--primary-text-color, #212121); --m-dim: var(--secondary-text-color, #727272);
  --m-line: var(--divider-color, rgba(0,0,0,.12)); --m-soft: var(--secondary-background-color, #f0f0f0);
  --m-accent: var(--primary-color, #03a9f4); --m-on-accent: var(--text-primary-color, #fff);
  --m-warn: var(--warning-color, #ffa600); --m-err: var(--error-color, #db4437); --m-ok: var(--success-color, #43a047);
  --m-radius: var(--ha-card-border-radius, 12px);
  font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif));
  color: var(--m-fg); }
* { box-sizing: border-box; }
.root { max-width: 1100px; margin: 0 auto; padding: 0 0 24px; }
button { font: inherit; color: inherit; cursor: pointer; }
a { color: var(--m-accent); }
.tabs { position: sticky; top: 0; z-index: 2; display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none;
  padding: 8px 6px; background: var(--primary-background-color, var(--m-bg)); border-bottom: 1px solid var(--m-line); }
.tabs::-webkit-scrollbar { display: none; }
.tab { flex: 0 0 auto; border: 0; background: none; padding: 8px 9px; border-radius: 18px; font-size: 14px;
  font-weight: 500; color: var(--m-dim); min-height: 36px; }
.tab.on { background: var(--m-accent); color: var(--m-on-accent); }
.tab .n { display: inline-block; min-width: 18px; margin-left: 3px; padding: 0 5px; border-radius: 9px; font-size: 11px;
  line-height: 18px; background: var(--m-soft); color: var(--m-fg); }
.body { padding: 12px; }
h2 { font-size: 18px; font-weight: 600; margin: 4px 0 10px; }
h3 { font-size: 15px; font-weight: 600; margin: 16px 0 8px; }
.dim { color: var(--m-dim); }
.small { font-size: 13px; }
.banner { border-radius: 10px; padding: 10px 12px; margin: 0 0 10px; font-size: 14px; line-height: 1.4;
  background: color-mix(in srgb, var(--m-warn) 18%, var(--m-bg)); border-left: 4px solid var(--m-warn); }
.banner.err { background: color-mix(in srgb, var(--m-err) 15%, var(--m-bg)); border-left-color: var(--m-err);
  display: flex; gap: 8px; align-items: center; position: sticky; top: 60px; z-index: 1; }
.banner.err span { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.banner.info { background: var(--m-soft); border-left-color: var(--m-accent); }
.x { border: 0; background: none; font-size: 20px; line-height: 1; min-width: 36px; min-height: 36px; padding: 0; color: var(--m-dim); }
.empty { text-align: center; padding: 40px 16px; color: var(--m-dim); line-height: 1.5; }
.empty b { display: block; color: var(--m-fg); font-size: 17px; margin-bottom: 6px; }
.hint { font-size: 14px; color: var(--m-dim); margin: 0 0 10px; }

/* options */
.grid { display: grid; gap: var(--gap); grid-template-columns: minmax(0, 1fr); }
@container (min-width: 700px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.opt { position: relative; background: var(--m-bg); border: 2px solid var(--m-line); border-radius: var(--m-radius);
  padding: 12px; cursor: pointer; -webkit-tap-highlight-color: transparent; transition: border-color .15s; }
.opt.on { border-color: var(--m-accent); box-shadow: 0 0 0 1px var(--m-accent) inset; }
.opt.busy { opacity: .7; }
.opt h4 { margin: 0 0 6px; font-size: 16px; line-height: 1.3; padding-right: 74px; overflow-wrap: anywhere; }
.opt h4 a { color: var(--m-fg); text-decoration: none; }
.opt h4 a::after { content: " ↗"; color: var(--m-accent); font-size: 13px; }
.pick { position: absolute; top: 10px; right: 10px; border: 1px solid var(--m-line); background: var(--m-soft);
  border-radius: 16px; padding: 4px 10px; font-size: 13px; font-weight: 600; min-height: 30px; }
.opt.on .pick { background: var(--m-accent); color: var(--m-on-accent); border-color: var(--m-accent); }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 10px; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--m-soft); color: var(--m-fg); white-space: nowrap; }
.chip.new { background: var(--m-ok); color: #fff; font-weight: 600; }
.chip.sp { background: color-mix(in srgb, var(--m-ok) 20%, var(--m-bg)); }
.costs { display: grid; grid-template-columns: repeat(var(--n, 2), minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
.cost { background: var(--m-soft); border-radius: 8px; padding: 6px 7px; min-width: 0; overflow-wrap: anywhere; }
.cost .s { font-size: 12px; color: var(--m-dim); }
.cost .v { font-size: 18px; font-weight: 600; }
.cost .ps, .cost .pp { font-size: 12px; color: var(--m-dim); line-height: 1.3; }
@container (max-width: 480px) { .costs.n3 .cost .v { font-size: 16px; } .costs.n3 .cost .ps, .costs.n3 .cost .pp, .costs.n3 .cost .eff { font-size: 11px; } }
.cost.best .v { color: var(--m-ok); }
.macros { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13px; margin-bottom: 6px; }
.macros b { font-weight: 600; }
.specials, .warn { font-size: 13px; line-height: 1.4; margin-top: 4px; overflow-wrap: anywhere; }
.warn { color: var(--m-err); }
.alerts { margin-top: 16px; }
.alert { font-size: 14px; padding: 8px 10px; border-radius: 8px; background: var(--m-soft); margin-bottom: 6px; overflow-wrap: anywhere; }

/* list */
.totals { display: grid; grid-template-columns: repeat(var(--n, 3), minmax(0, 1fr)); gap: 6px; margin-bottom: 12px; }
.total { border: 2px solid var(--m-line); background: var(--m-bg); border-radius: 10px; padding: 8px 4px; text-align: center; min-height: 64px; min-width: 0; overflow-wrap: anywhere; }
.total .r .short { display: none; }
@container (max-width: 480px) { .totals.n4 .total .s { font-size: 12px; } .totals.n4 .total .v { font-size: 16px; }
  .totals.n4 .total .r .long { display: none; } .totals.n4 .total .r .short { display: inline; } }
.total .sub { font-size: 11px; color: var(--m-dim); }
.total.on { border-color: var(--m-accent); background: color-mix(in srgb, var(--m-accent) 12%, var(--m-bg)); }
.total:disabled { opacity: .5; cursor: default; }
.total .s { font-size: 13px; color: var(--m-dim); }
.total .v { font-size: 18px; font-weight: 600; }
.total .r { font-size: 11px; color: var(--m-ok); font-weight: 600; min-height: 14px; }
.total .fz { font-size: 11px; color: var(--m-dim); }
.cost .eff { font-size: 12px; color: var(--m-fg); margin-top: 2px; }
.fz-note { color: var(--m-accent); white-space: nowrap; }
.cook-note { font-size: 12px; margin-top: 4px; padding: 3px 8px; border-radius: 6px; line-height: 1.4;
  background: color-mix(in srgb, var(--m-accent) 12%, var(--m-bg)); color: var(--m-fg); }
.extra { font-size: 13px; color: var(--m-dim); margin: -2px 0 6px; line-height: 1.4; }
.useall { font-size: 14px; line-height: 1.4; padding: 8px 10px; margin: 0 0 10px; border-radius: 0 8px 8px 0;
  border-left: 3px solid var(--m-accent); background: color-mix(in srgb, var(--m-accent) 12%, var(--m-bg)); }
.rec { display: block; font-size: 12px; color: var(--m-dim); }
.keep { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 8px 12px; border-top: 1px solid var(--m-line); }
.keep:first-child { border-top: 0; }
.keep .t { font-size: 15px; min-width: 0; overflow-wrap: anywhere; }
.keep .btn { flex: 0 0 auto; padding: 6px 12px; min-height: 34px; }
.items { list-style: none; margin: 0 0 8px; padding: 0; border: 1px solid var(--m-line); border-radius: 10px; overflow: hidden; }
.item { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border-top: 1px solid var(--m-line); cursor: pointer; }
.item:first-child { border-top: 0; }
.box { flex: 0 0 22px; height: 22px; border: 2px solid var(--m-dim); border-radius: 5px; margin-top: 1px; display: grid; place-items: center; font-size: 14px; }
.item.done .box { background: var(--m-accent); border-color: var(--m-accent); color: var(--m-on-accent); }
.item.done .nm { text-decoration: line-through; color: var(--m-dim); }
.item .mid { flex: 1; min-width: 0; }
.item .nm { font-size: 15px; overflow-wrap: anywhere; }
.item .sub { font-size: 12px; color: var(--m-dim); margin-top: 2px; }
.item .pr { flex: 0 0 auto; text-align: right; font-size: 15px; font-weight: 500; }
.sp-tag { display: inline-block; font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 8px; margin-left: 4px;
  background: var(--m-ok); color: #fff; vertical-align: 1px; }
.listhead { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin: 14px 0 6px; }
.listhead h3 { margin: 0; }
.linkbtn { border: 0; background: none; color: var(--m-accent); font-size: 13px; padding: 4px; }
.cols { display: grid; gap: 0 var(--gap); grid-template-columns: minmax(0, 1fr); }
@container (min-width: 700px) { .cols.two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* cook */
.recipe { margin-bottom: 24px; }
.recipe h2 a { color: var(--m-fg); text-decoration: none; }
.recipe h2 a::after { content: " ↗"; color: var(--m-accent); font-size: 14px; }
.notes { margin: 0 0 10px; padding: 0; list-style: none; }
.notes li { font-size: 14px; padding: 6px 10px; border-left: 3px solid var(--m-warn); background: var(--m-soft); border-radius: 0 6px 6px 0; margin-bottom: 4px; }
table.rt { width: 100%; border-collapse: collapse; font-size: 14px; line-height: 1.4; }
table.rt td { border: 1px solid var(--m-line); padding: 6px 8px; vertical-align: top; }
table.rt td.amt { text-align: right; white-space: nowrap; width: 1%; font-variant-numeric: tabular-nums; }
table.rt td.ing { width: 32%; }
table.rt td.stp { background: var(--m-soft); }
.stp .n, .nstep .n { font-weight: 700; color: var(--m-accent); margin-right: 4px; }
.prep { color: var(--m-dim); font-size: 12px; }
.was { color: var(--m-dim); font-size: 12px; }
.swapnote { display: block; font-style: italic; color: var(--m-dim); font-size: 12px; }
.gone { text-decoration: line-through; color: var(--m-dim); }
.narrow { display: none; }
@container (max-width: 599px) { table.rt { display: none; } .narrow { display: block; } }
.nstep { border: 1px solid var(--m-line); border-radius: 10px; margin-bottom: 8px; overflow: hidden; }
.nstep .txt { padding: 8px 10px; background: var(--m-soft); font-size: 14px; line-height: 1.45; }
.nstep .row { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; padding: 6px 10px; border-top: 1px solid var(--m-line); font-size: 14px; }
.nstep .row .amt { text-align: right; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }

/* rate */
.rrow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 10px 0; border-top: 1px solid var(--m-line); }
.rrow:first-of-type { border-top: 0; }
.rrow .who { flex: 1 1 180px; min-width: 0; }
.rrow .who .t { font-size: 15px; overflow-wrap: anywhere; }
.rrow .who .d { font-size: 12px; color: var(--m-dim); }
.seg { display: inline-flex; border: 1px solid var(--m-line); border-radius: 18px; overflow: hidden; flex: 0 0 auto; }
.seg button { border: 0; background: none; padding: 6px 12px; font-size: 14px; min-height: 36px; border-left: 1px solid var(--m-line); }
.seg button:first-child { border-left: 0; }
.seg button.on { background: var(--m-accent); color: var(--m-on-accent); }
.seg button.on.never { background: var(--m-err); }
.card { background: var(--m-bg); border: 1px solid var(--m-line); border-radius: var(--m-radius); padding: 4px 12px; margin-bottom: 12px; }

/* tune */
.sliders { display: grid; gap: 0 24px; grid-template-columns: minmax(0, 1fr); }
@container (min-width: 700px) { .sliders { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.sl { display: grid; grid-template-columns: minmax(80px, 34%) minmax(0, 1fr) 48px; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--m-line); }
.sl label { font-size: 14px; overflow-wrap: anywhere; }
.sl label.cap { text-transform: capitalize; }
/* -1..1 sliders: a neutral track with a centre mark, so zero doesn't look half on. */
input.c { -webkit-appearance: none; appearance: none; background: transparent; height: 32px; }
input.c::-webkit-slider-runnable-track { height: 6px; border-radius: 3px;
  background: linear-gradient(90deg, transparent calc(50% - 1px), var(--m-dim) calc(50% - 1px), var(--m-dim) calc(50% + 1px), transparent calc(50% + 1px)), var(--m-soft); }
input.c::-moz-range-track { height: 6px; border-radius: 3px; background: var(--m-soft); }
input.c::-webkit-slider-thumb { -webkit-appearance: none; width: 22px; height: 22px; margin-top: -8px; border-radius: 50%; background: var(--m-dim); border: 2px solid var(--m-bg); }
input.c::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--m-dim); border: 2px solid var(--m-bg); }
input.c.set::-webkit-slider-thumb { background: var(--m-accent); }
input.c.set::-moz-range-thumb { background: var(--m-accent); }
.sl .val { text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; color: var(--m-dim); }
.sl .val.set { color: var(--m-fg); font-weight: 600; }
input[type=range] { width: 100%; margin: 0; accent-color: var(--m-accent); min-height: 32px; }
.btn { border: 1px solid var(--m-line); background: var(--m-soft); border-radius: 18px; padding: 8px 14px; font-size: 14px; min-height: 38px; }
.btn.primary { background: var(--m-accent); border-color: var(--m-accent); color: var(--m-on-accent); }
.btn.danger { background: var(--m-err); border-color: var(--m-err); color: #fff; }
.btn:disabled { opacity: .5; cursor: default; }
.confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px; border-radius: 10px; background: color-mix(in srgb, var(--m-err) 12%, var(--m-bg)); font-size: 14px; }

/* freezer */
.frow { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; align-items: center; padding: 10px 0; border-top: 1px solid var(--m-line); }
.frow:first-of-type { border-top: 0; }
.frow .t { font-size: 15px; text-transform: capitalize; overflow-wrap: anywhere; }
.frow .d { font-size: 12px; color: var(--m-dim); overflow-wrap: anywhere; }
.grams { display: inline-flex; align-items: center; gap: 4px; font-size: 14px; }
input:not([type=range]), select { font: inherit; font-size: 16px; color: var(--m-fg); background: var(--m-bg); border: 1px solid var(--m-line); border-radius: 8px; padding: 6px 8px; min-height: 38px; min-width: 0; }
.grams input { width: 84px; text-align: right; }
.form { display: grid; gap: 8px; grid-template-columns: minmax(0, 1fr); padding: 10px 0 4px; }
@container (min-width: 700px) { .form { grid-template-columns: 2fr 1fr 2fr auto; align-items: end; } }
.form label { display: grid; gap: 2px; font-size: 12px; color: var(--m-dim); min-width: 0; }
.form input, .form select { width: 100%; }

/* person */
.whobar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px 0; font-size: 14px; min-height: 40px; }
.whobar .nm { min-width: 0; overflow-wrap: anywhere; }
.whobar .btn { flex: 0 0 auto; padding: 4px 12px; min-height: 34px; font-size: 13px; }
.picker { max-width: 560px; margin: 0 auto; padding: 24px 16px; text-align: center; }
.picker h2 { font-size: 24px; margin: 8px 0; }
.people { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); margin-top: 20px; }
.person { border: 2px solid var(--m-line); background: var(--m-bg); border-radius: var(--m-radius); min-height: 88px;
  font-size: 22px; font-weight: 600; padding: 12px; overflow-wrap: anywhere; }
.person:hover, .person:focus-visible { border-color: var(--m-accent); }
.person.on { border-color: var(--m-accent); background: color-mix(in srgb, var(--m-accent) 12%, var(--m-bg)); }
.setrow { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; }
.setrow > label, .setrow > span { font-size: 14px; }
.setrow .seg button { min-width: 48px; }
.set h4 { font-size: 13px; font-weight: 600; color: var(--m-dim); margin: 12px 0 2px; text-transform: uppercase; letter-spacing: .04em; }
.checks { display: grid; grid-template-columns: minmax(0, 1fr); }
@container (min-width: 700px) { .checks { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; } }
.check { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--m-line); font-size: 14px; cursor: pointer; min-height: 44px; }
.check input { width: 22px; height: 22px; min-height: 0; margin: 0; flex: 0 0 auto; accent-color: var(--m-accent); }
.check.off { cursor: default; }
.check .d { color: var(--m-dim); font-size: 12px; }
.set .sl:first-of-type { border-top: 1px solid var(--m-line); }

footer { padding: 4px 12px 0; font-size: 12px; color: var(--m-dim); line-height: 1.6; }
footer details { margin-top: 2px; }
footer summary { cursor: pointer; }
footer ul { margin: 4px 0; padding-left: 18px; columns: 2 160px; }
`;

class MealsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("change", (e) => this._change(e));
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this._week = undefined;     // undefined loading, null missing
    this._people = undefined;   // people.json; undefined loading, null missing
    this._person = null;        // the person key whose plan is showing; null = ask
    this._own = false;          // true when that person is the logged-in user
    this._chosen = undefined;   // the shared-screen choice, in memory in case localStorage is blocked
    this._state = {};           // this person's entry in state.json `people`
    this._loadError = null;
    this._error = null;         // last failed service call
    this._busy = new Set();
    this._confirmReset = false;
    this._form = { cut: "", other: "", grams: "", seasoning: "" };
    this._sig = "";
  }

  setConfig(config) {
    this._config = { base: "/local/meals", ...(config || {}) };
    this._base = String(this._config.base).replace(/\/$/, "");
    if (!this._tab) this._tab = TABS.some(([t]) => t === this._config.tab) ? this._config.tab : null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._subscribe(); this._load(); }
  }
  get hass() { return this._hass; }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), REFRESH_MS);
    this._onVisible = () => { if (document.visibilityState === "visible") this._load(); };
    document.addEventListener("visibilitychange", this._onVisible);
    if (this._hass && !this._unsub) this._subscribe();
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._onVisible);
    if (this._unsub) { this._unsub.then((u) => u && u()).catch(() => {}); this._unsub = null; }
  }

  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }

  _subscribe() {
    const conn = this._hass && this._hass.connection;
    if (!conn || !conn.subscribeEvents || this._unsub) return;
    this._unsub = conn.subscribeEvents((ev) => {
      const who = ev && ev.data && ev.data.person;
      if (who && who !== this._person) return; // someone else's plan changed
      this._load();
    }, "meals_state_changed").catch(() => null);
  }

  // ------------------------------------------------------------- data

  async _fetchJson(name) {
    const r = await fetch(`${this._base}/${name}?_=${Date.now()}`, { cache: "no-store", credentials: "same-origin" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  }

  async _load() {
    if (!this._config || !this._hass) return;
    const seq = (this._seq = (this._seq || 0) + 1);
    let people = this._people;
    try { people = await this._fetchJson("people.json"); } catch (e) { if (people === undefined) people = null; }
    if (seq !== this._seq || this._busy.size) return;
    this._people = people && typeof people === "object" ? people : null;
    const who = this._resolvePerson();
    if (who.key !== this._person) this._reset(who.key);
    this._own = who.own;
    if (!this._person) { this._render(); return; } // ask who's cooking
    const key = this._person;
    let week, state, err = null;
    try { week = await this._fetchJson(`week-${encodeURIComponent(key)}.json`); } catch (e) { err = e.message; week = this._week ?? null; }
    try { state = await this._fetchJson("state.json"); } catch (e) { state = undefined; }
    if (seq !== this._seq || this._busy.size || key !== this._person) return; // a newer load, a switch or a call in flight wins
    this._loadError = err;
    this._week = week;
    if (state !== undefined) {
      const mine = state && typeof state === "object" && state.people && state.people[key];
      this._state = mine && typeof mine === "object" ? mine : {};
    }
    if (!this._tab) this._tab = this._week && this._picks().length === this._need() ? "list" : "week";
    this._render();
  }

  _viewer() {
    const u = (this._hass && this._hass.user) || {};
    return { id: u.id || null, name: u.name || "" };
  }
  // People with a plan of their own, in people.json order: [[key, {name, …}], …].
  _planPeople() {
    return Object.entries(this._people || {}).filter(([, p]) => p && typeof p === "object" && !p.shared && p.has_plan !== false);
  }
  // Whose plan to show: the viewer's own, else the choice remembered on this device, else ask (key null).
  _resolvePerson() {
    const v = this._viewer();
    if (!this._people) return { key: v.id, own: !!v.id }; // no people.json yet: the viewer's own plan
    if (this._planPeople().some(([k]) => k === v.id)) return { key: v.id, own: true };
    const saved = this._chosen !== undefined ? this._chosen : lsGet("person", null);
    if (saved && this._planPeople().some(([k]) => k === saved)) return { key: saved, own: false };
    return { key: null, own: false };
  }
  _reset(key) {
    this._person = key;
    this._week = undefined;
    this._state = {};
    this._loadError = null;
    this._error = null;
    this._confirmReset = false;
    this._form = { cut: "", other: "", grams: "", seasoning: "" };
    this._tab = TABS.some(([t]) => t === this._config.tab) ? this._config.tab : null;
  }
  _choose(key) {
    this._chosen = key;
    lsSet("person", key);
    this._seq = (this._seq || 0) + 1; // drop any load in flight
    this._reset(key);
    this._render();
    this._load();
  }
  _personName(key = this._person) {
    const p = (this._people || {})[key];
    if (p && p.name) return String(p.name);
    if (key && key === this._viewer().id && this._viewer().name) return this._viewer().name;
    return "Someone";
  }
  _settings() {
    const s = (this._state && this._state.settings) || {};
    const stores = Array.isArray(s.stores) && s.stores.length ? s.stores : DEFAULT_SETTINGS.stores;
    return { ...DEFAULT_SETTINGS, ...s, swaps: { ...DEFAULT_SETTINGS.swaps, ...(s.swaps || {}) }, stores };
  }
  // How many recipes make up a week: the size of the week file's pick keys (what the list can
  // show), else the person's `recipes` setting. A new setting applies from the next week file.
  _need() {
    const keys = Object.keys((this._week && this._week.pairs) || {});
    if (keys.length) return Math.min(3, Math.max(1, keys[0].split("+").length));
    const n = Math.round(+this._settings().recipes);
    return n >= 1 && n <= 3 ? n : 2;
  }
  _ls(k) { return `${this._person}:${k}`; } // per-person localStorage key

  _picks() {
    const w = this._week && this._week.week;
    const p = w && this._state.picks && this._state.picks[w];
    return Array.isArray(p) ? p.slice(0, this._need()) : [];
  }
  // The stores this week file covers, in planner order (older files have no Coles).
  _shops() {
    const w = this._week || {}, known = { ...(w.stores_ok || {}), ...(w.prices_weeks || {}) };
    const got = SHOPS.filter((s) => s in known);
    return got.length ? got : SHOPS.slice(0, 2);
  }
  _option(slug) { return ((this._week && this._week.options) || []).find((o) => o.slug === slug); }
  _tuning() { return this._state.tuning || {}; }

  // Optimistic service call: mutate a copy of state, render, call; revert on failure.
  async _call(service, data, mutate, key) {
    if (!this._person) return false;
    data = { person: this._person, ...data }; // every service needs the person key (SCHEMA.md "Per person")
    const before = JSON.parse(JSON.stringify(this._state || {}));
    const next = JSON.parse(JSON.stringify(this._state || {}));
    if (mutate) mutate(next);
    this._state = next;
    this._error = null;
    if (key) this._busy.add(key);
    this._render();
    let ok = true;
    try {
      if (!this._hass || !this._hass.callService) throw new Error("not connected to Home Assistant");
      await this._hass.callService("pyscript", service, data);
    } catch (e) {
      ok = false;
      this._state = before;
      this._error = `Couldn't save (${service}): ${(e && (e.message || e.code)) || e}`;
    } finally {
      if (key) this._busy.delete(key);
      this._render();
    }
    return ok;
  }

  // ------------------------------------------------------------- events

  _click(e) {
    const t = e.target.closest("[data-a]");
    if (!t) return;
    const a = t.dataset.a, v = t.dataset.v;
    if (a === "link") return; // recipe links open normally, without picking
    if (t.tagName === "A") e.preventDefault();
    if (a === "tab") {
      this._tab = v; this._confirmReset = false; this._render();
      if (this.getBoundingClientRect().top < 0) this.scrollIntoView({ block: "start" });
      return;
    }
    if (a === "dismiss") { this._error = null; this._render(); return; }
    if (a === "choose") return this._choose(v);
    if (a === "switch") { this._chosen = null; lsSet("person", null); this._seq = (this._seq || 0) + 1; this._reset(null); this._render(); return; }
    if (a === "recipes") return this._setting({ recipes: +v });
    if (a === "pick") return this._pick(v);
    if (a === "store") { lsSet(this._ls(`store:${this._week.week}:${this._pairKey()}`), v); this._render(); return; }
    if (a === "tick") return this._tick(v);
    if (a === "untick") { lsSet(this._ls(`ticks:${this._week.week}`), {}); this._render(); return; }
    if (a === "rate") return this._rate(v, t.dataset.r);
    if (a === "reset") { this._confirmReset = true; this._render(); return; }
    if (a === "reset-no") { this._confirmReset = false; this._render(); return; }
    if (a === "reset-yes") {
      this._confirmReset = false;
      return this._call("meals_tune", { reset: true }, (s) => {
        s.ratings = {};
        s.tuning = { ...(s.tuning || {}), styles: {} };
      }, "reset");
    }
    if (a === "fz-add") return this._freezerAdd();
    if (a === "fz-keep") return this._freezeKeep(+v);
    if (a === "fz-del") {
      const i = +v;
      return this._call("meals_freezer", { remove: i }, (s) => { s.freezer = (s.freezer || []).filter((_, k) => k !== i); }, `fz${i}`);
    }
  }

  _input(e) {
    const t = e.target;
    if (t.type === "range") {
      const out = t.closest(".sl")?.querySelector(".val");
      if (out) out.textContent = this._sliderText(t.dataset.k, +t.value);
      if (t.classList.contains("c")) t.classList.toggle("set", +t.value !== 0);
    }
    if (t.dataset.f) this._form[t.dataset.f] = t.value; // freezer form: keep typing across re-renders
  }

  _change(e) {
    const t = e.target;
    if (t.dataset.f === "cut") { this._form.cut = t.value; this._render(); return; }
    if (t.dataset.s === "swap") return this._setting({ swaps: { [t.dataset.v]: t.checked } });
    if (t.dataset.s === "shop") {
      const on = new Set(this._settings().stores);
      if (t.checked) on.add(t.dataset.v); else on.delete(t.dataset.v);
      const stores = SHOPS.filter((s) => on.has(s));
      if (!stores.length) { this._error = "Keep at least one store."; this._render(); return; }
      return this._setting({ stores });
    }
    if (t.type === "range") {
      const k = t.dataset.k, v = +t.value;
      if (k === "serves" || k === "protein_g") return this._setting({ [k]: v });
      if (k === "style") {
        const style = t.dataset.v;
        return this._call("meals_tune", { style, weight: v === 0 ? null : v }, (s) => {
          s.tuning = s.tuning || {};
          const styles = { ...(s.tuning.styles || {}) };
          for (const key of Object.keys(styles)) if (key.toLowerCase() === style) delete styles[key];
          if (v !== 0) styles[style] = v;
          s.tuning.styles = styles;
        });
      }
      return this._call("meals_tune", { [k]: v }, (s) => { s.tuning = { ...(s.tuning || {}), [k]: v }; });
    }
    if (t.dataset.a === "fz-grams") {
      const i = +t.dataset.v, grams = Math.round(+t.value);
      if (!(grams > 0)) { this._render(); return; }
      return this._call("meals_freezer", { set: { index: i, grams } }, (s) => {
        if (s.freezer && s.freezer[i]) s.freezer[i] = { ...s.freezer[i], grams };
      }, `fz${i}`);
    }
  }

  _pick(slug) {
    const w = this._week.week;
    let picks = this._picks();
    if (picks.includes(slug)) picks = picks.filter((s) => s !== slug);
    else picks = [...picks, slug].slice(-this._need()); // one tap too many replaces the earliest pick
    this._call("meals_pick", { week: w, slugs: picks }, (s) => { s.picks = { ...(s.picks || {}), [w]: picks }; }, "pick");
  }

  // A partial settings change: {recipes} | {serves} | {protein_g} | {swaps: {id: bool}} | {stores: [...]}.
  _setting(patch) {
    return this._call("meals_settings", patch, (s) => {
      const cur = s.settings || {};
      s.settings = { ...cur, ...patch };
      if (patch.swaps) s.settings.swaps = { ...(cur.swaps || {}), ...patch.swaps };
    }, "settings");
  }

  _tick(id) {
    const key = this._ls(`ticks:${this._week.week}`), ticks = lsGet(key, {}) || {};
    if (ticks[id]) delete ticks[id]; else ticks[id] = 1;
    lsSet(key, ticks);
    this._render();
  }

  _rate(slug, rating) {
    const s0 = this._state.ratings && this._state.ratings[slug];
    const meta = this._meta(slug);
    if (s0 && s0.rating === rating && rating !== "clear") rating = "clear"; // tapping the current rating clears it
    const data = { slug, rating, title: meta.title, style: meta.style, cuisine: meta.cuisine };
    const today = new Date().toISOString().slice(0, 10);
    this._call("meals_rate", data, (s) => {
      s.ratings = { ...(s.ratings || {}) };
      if (rating === "clear") delete s.ratings[slug];
      else s.ratings[slug] = { rating, date: today, title: meta.title, style: meta.style, cuisine: meta.cuisine };
    }, `rate:${slug}`);
  }

  _freezerAdd() {
    const f = this._form;
    const cut = (f.cut === "__other" ? f.other : f.cut).trim().toLowerCase();
    const grams = Math.round(+f.grams);
    if (!cut || !(grams > 0)) { this._error = "Freezer: pick a cut and enter the grams."; this._render(); return; }
    const item = { cut, grams, seasoning: f.seasoning.trim() };
    this._form = { cut: f.cut === "__other" ? "" : f.cut, other: "", grams: "", seasoning: "" };
    const today = new Date().toISOString().slice(0, 10);
    this._call("meals_freezer", { add: item }, (s) => { s.freezer = [...(s.freezer || []), { ...item, date: today }]; }, "fz-add");
  }

  // A leftover from the list into the freezer; marked as added (per week, this viewer) once it saves.
  async _freezeKeep(i) {
    const f = (this._keepShown || [])[i];
    if (!f) return;
    const item = { cut: f.cut, grams: f.grams, seasoning: "" }, mark = f.mark;
    const today = new Date().toISOString().slice(0, 10);
    const key = this._ls(`kept:${this._week.week}`), kept = lsGet(key, {}) || {};
    kept[mark] = 1;
    lsSet(key, kept); // optimistic, like the freezer list itself
    const ok = await this._call("meals_freezer", { add: item }, (s) => { s.freezer = [...(s.freezer || []), { ...item, date: today }]; }, `keep:${mark}`);
    if (!ok) { const k = lsGet(key, {}) || {}; delete k[mark]; lsSet(key, k); this._render(); }
  }

  // Title/style/cuisine for a slug: from its rating, this week's options, or a local cache.
  _meta(slug) {
    const r = (this._state.ratings || {})[slug], o = this._option(slug);
    const cache = lsGet("meta", {}) || {};
    const past = ((this._week && this._week.titles) || {})[slug];
    const src = r || o || past || cache[slug] || {};
    return { title: src.title || titleCase(slug), style: src.style || null, cuisine: src.cuisine || null };
  }
  _rememberMeta() {
    const opts = (this._week && this._week.options) || [];
    if (!opts.length) return;
    const cache = lsGet("meta", {}) || {};
    let changed = false;
    for (const o of opts) {
      if (!cache[o.slug]) { cache[o.slug] = { title: o.title, style: o.style, cuisine: o.cuisine }; changed = true; }
    }
    if (changed) lsSet("meta", cache);
  }

  _pairKey() { const p = this._picks(); return p.length && p.length === this._need() ? comboKey(p) : null; }

  // ------------------------------------------------------------- render

  _render() {
    if (!this.shadowRoot) return;
    // Keep the scroll position and focused input across re-renders.
    const focus = this.shadowRoot.activeElement;
    const focusSel = focus && focus.dataset && focus.dataset.f ? `[data-f="${focus.dataset.f}"]` : null;
    if (this._people === undefined && !this._person) {
      this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="root"><div class="empty">Loading…</div></div>`;
      return;
    }
    if (!this._person) { this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="root">${this._picker()}</div>`; return; }
    let body;
    if (this._week === undefined && !this._loadError) body = `<div class="empty">Loading…</div>`;
    else body = this._tabBody();
    const tabs = TABS.map(([t, label]) => {
      let n = "";
      if (t === "week" && this._week) n = `<span class="n">${this._picks().length}/${this._need()}</span>`;
      if (t === "freezer" && (this._state.freezer || []).length) n = `<span class="n">${this._state.freezer.length}</span>`;
      return `<button class="tab${t === this._tab ? " on" : ""}" data-a="tab" data-v="${t}">${label}${n}</button>`;
    }).join("");
    const err = this._error ? `<div class="banner err"><span>${esc(this._error)}</span><button class="x" data-a="dismiss" aria-label="Dismiss">×</button></div>` : "";
    const loadErr = this._loadError ? `<div class="banner err"><span>Couldn't load the week: ${esc(this._loadError)}</span></div>` : "";
    const wk = this._week && this._week.week ? ` <span class="dim">· week of ${fmtDay(this._week.week)}</span>` : "";
    const whobar = `<div class="whobar"><span class="nm"><b>${possessive(this._personName())}</b> plan${wk}</span>${this._own ? ""
      : `<button class="btn" data-a="switch">Switch person</button>`}</div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="root">${whobar}
      <nav class="tabs">${tabs}</nav>
      <div class="body">${err}${loadErr}${body}</div>${this._footer()}</div>`;
    const nav = this.shadowRoot.querySelector(".tabs"), on = nav && nav.querySelector(".tab.on");
    if (on) nav.scrollLeft = Math.max(0, on.offsetLeft + on.offsetWidth - nav.clientWidth + 8);
    if (focusSel) {
      const el = this.shadowRoot.querySelector(focusSel);
      if (el) { el.focus(); try { el.setSelectionRange?.(el.value.length, el.value.length); } catch (e) { /* select */ } }
    }
    this._rememberMeta();
  }

  _tabBody() {
    const tab = this._tab || "week";
    if (tab === "rate") return this._rateTab();
    if (tab === "tune") return this._tuneTab();
    if (tab === "freezer") return this._freezerTab();
    if (!this._week) {
      const empty = `<div class="empty"><b>No plan yet</b>Your first options arrive on Wednesday morning.
        Ratings, tuning and the freezer list work already.</div>`;
      return tab === "week" ? `${empty}<h2>${possessive(this._personName())} settings</h2>${this._settingsBlock()}` : empty;
    }
    if (tab === "list") return this._listTab();
    if (tab === "cook") return this._cookTab();
    return this._weekTab();
  }

  _banners() {
    const w = this._week, out = [];
    const ok = w.stores_ok || {}, used = w.prices_weeks || {}, shops = this._shops();
    const missing = shops.filter((s) => ok[s] === false);
    const partial = missing.length && missing.length < shops.length;
    if (w.stale && !partial) out.push(`<b>Prices from last week.</b> This week's prices couldn't be read; costs use the ${fmtDay(w.prices_week)} snapshot.`);
    else if (!partial && missing.length) out.push(`<b>No prices this week.</b> No store could be read.`);
    if (partial) {
      for (const s of missing.filter((x) => used[x])) {
        out.push(`<b>No ${STORES[s]} prices this week.</b> ${STORES[s]} costs use the ${fmtDay(used[s])} snapshot.`);
      }
      const gone = missing.filter((x) => !used[x]);
      if (gone.length) out.push(`<b>No ${gone.map((x) => STORES[x]).join(" or ")} prices this week.</b> Costs and lists cover ${nameList(shops.filter((x) => !gone.includes(x)))} only.`);
    }
    let html = out.map((t) => `<div class="banner">${t}</div>`).join("");
    if ((w.relaxed || []).length) html += `<div class="banner info">Rules relaxed to find four options: ${w.relaxed.map(esc).join("; ")}.</div>`;
    return html;
  }

  _weekTab() {
    const w = this._week, picks = this._picks(), shops = this._shops(), need = this._need();
    const target = this._settings().protein_g || 50;
    const opts = w.options || [];
    const cheapest = (o) => {
      const c = Object.entries(o.cost_effective || o.cost || {}).filter(([, v]) => v != null);
      return c.length > 1 ? c.reduce((a, b) => (b[1] < a[1] ? b : a))[0] : null;
    };
    const cards = opts.map((o) => {
      const on = picks.includes(o.slug), n = picks.indexOf(o.slug) + 1;
      const best = cheapest(o), serves = o.serves || 6, byStore = o.per_serve_by_store || {};
      const psStore = best || shops.find((s) => byStore[s]);
      const ps = byStore[psStore] || o.per_serve || {};
      const extraNote = (ps.extra || []).map((e) => {
        const whole = e.bought_g ? (e.frozen_g ? ` (the ${weight(e.bought_g)} less ${weight(e.frozen_g)} to freeze)` : ` (uses the whole ${weight(e.bought_g)})`) : "";
        return `<div class="extra">+${Math.round(e.grams_per_serve)} g ${esc(extraName(e))} a serve${whole}</div>`;
      }).join("");
      const cost = (s) => {
        const v = (o.cost || {})[s], eff = (o.cost_effective || {})[s], pp = (o.cost_per_50g_protein || {})[s];
        const after = v != null && eff != null && v - eff > 1
          ? `<div class="eff">${money(eff)} after freezing ${money(v - eff)}</div>` : "";
        return `<div class="cost${best === s ? " best" : ""}"><div class="s">${STORES[s]}</div><div class="v">${money(v)}</div>
          <div class="ps">${v == null ? "can't price" : `${money(v / serves)} a serve`}</div>
          ${v != null && pp != null ? `<div class="pp">${money(pp)} / ${target} g protein</div>` : ""}${after}</div>`;
      };
      const chips = [o.new ? `<span class="chip new">New</span>` : "",
        o.style ? `<span class="chip">${esc(o.style)}</span>` : "", o.cuisine ? `<span class="chip">${esc(o.cuisine)}</span>` : "",
        o.cut ? `<span class="chip">${esc(o.cut)}</span>` : ""].join("");
      const specials = (o.specials || []).length
        ? `<div class="specials">On special: ${o.specials.map((s) => `${esc(s.name)} <span class="dim">(${STORES[s.store] || esc(s.store)}, ${money(s.price)}${s.usual_price ? `, usually ${money(s.usual_price)}` : ""})</span>`).join(" · ")}</div>` : "";
      const missing = Object.entries(o.missing || {}).filter(([s, v]) => v && v.length && shops.includes(s))
        .map(([s, v]) => `<div class="small dim">${STORES[s] || esc(s)} doesn't stock: ${v.map(esc).join(", ")}</div>`).join("");
      const unmatched = (o.unmatched || []).length
        ? `<div class="warn">⚠ Not priced (no product match): ${o.unmatched.map(esc).join(", ")}</div>` : "";
      return `<div class="opt${on ? " on" : ""}${this._busy.has("pick") ? " busy" : ""}" data-a="pick" data-v="${esc(o.slug)}" role="button" aria-pressed="${on}">
        <button class="pick" data-a="pick" data-v="${esc(o.slug)}">${on ? (need === 1 ? "✓ Picked" : `✓ Pick ${n}`) : "Pick"}</button>
        <h4><a href="${esc(o.url)}" target="_blank" rel="noopener" data-a="link">${esc(o.title)}</a></h4>
        <div class="chips">${chips}</div>
        <div class="costs n${shops.length}" style="--n:${shops.length}">${shops.map(cost).join("")}</div>
        <div class="macros"><span><b>${ps.protein_g ?? "?"} g</b> protein</span><span><b>${ps.kcal ?? "?"}</b> kcal</span>
          <span><b>${ps.meat_g ?? "?"} g</b> raw meat</span><span class="dim">per serve · ${serves} serves</span></div>
        ${extraNote}${specials}${missing}${unmatched}</div>`;
    }).join("");
    const listLink = `<a href="#" data-a="tab" data-v="list">list</a>`;
    const hint = picks.length === need
      ? (need === 1 ? `Picked. Tap another to swap it, or open the ${listLink}.`
        : `${NUM[need][0].toUpperCase()}${NUM[need].slice(1)} picked. Tap another to swap it for your first pick, or open the ${listLink}.`)
      : `Pick ${NUM[need]} for the week (${picks.length} of ${need} picked).`;
    const alerts = (w.alerts || []).filter((a) => a.kind === "bulk_meat" || !a.kind);
    const alertHtml = alerts.length ? `<div class="alerts"><h3>Bulk meat worth freezing</h3>${alerts.map((a) => {
      const off = a.best_per_kg ? Math.round((1 - a.per_kg / a.best_per_kg) * 100) : null;
      return `<div class="alert"><b>${esc(a.name)}</b> at ${STORES[a.store] || esc(a.store)}: ${money(a.per_kg)}/kg
        <span class="dim">(best lately ${money(a.best_per_kg)}/kg${off ? `, ${off}% off` : ""})</span></div>`;
    }).join("")}</div>` : "";
    const none = opts.length ? "" : `<div class="empty"><b>No options this week</b>The planner ran but found nothing it could price.</div>`;
    return `${this._banners()}<p class="hint">${hint}</p><div class="grid">${cards}</div>${none}${alertHtml}`;
  }

  _listTab() {
    const w = this._week, key = this._pairKey(), need = this._need();
    if (!key) {
      return `${this._banners()}<div class="empty"><b>Pick ${NUM[need]} meal${need === 1 ? "" : "s"} first</b>The shopping list appears here once ${NUM[need]} ${need === 1 ? "option is" : "options are"} picked on
        <a href="#" data-a="tab" data-v="week">This week</a>. (${this._picks().length} of ${need} picked.)</div>`;
    }
    const pair = (w.pairs || {})[key];
    if (!pair) return `<div class="empty"><b>No list for these picks</b>The week file has no prices for ${esc(key)}.</div>`;
    const store = this._store(pair), shops = this._shops(), choices = [...shops, "split"];
    const splitStores = (sp) => (sp && (sp.stores || Object.keys(sp.items || {}))) || [];
    const totals = choices.map((s) => {
      const p = pair[s];
      const sub = s === "split" && p ? `<div class="sub">${splitStores(p).map((x) => STORES[x] || esc(x)).join(" + ")}</div>` : "";
      return `<button class="total${s === store ? " on" : ""}" data-a="store" data-v="${s}" ${p ? "" : "disabled"}>
        <div class="s">${STORES[s]}</div>${sub}<div class="v">${money(p && p.total)}</div>
        ${p && p.freeze_value > 0 ? `<div class="fz">${money(p.freeze_value)} to freezer</div>` : ""}
        <div class="r">${s === pair.choice ? `<span class="long">Recommended</span><span class="short">✓ Best</span>` : ""}</div></button>`;
    }).join("");
    const titles = Object.fromEntries((w.options || []).map((o) => [o.slug, o.title]));
    const ticks = lsGet(this._ls(`ticks:${w.week}`), {}) || {};
    const extras = this._extrasById(pair[store]);
    const list = (items, s) => {
      if (!items || !items.length) return `<p class="dim small">Nothing from ${STORES[s]}.</p>`;
      const done = items.filter((i) => ticks[`${s}:${i.id || i.name}`]).length;
      return `<div class="listhead"><h3>${STORES[s]}</h3><span class="dim small">${done} of ${items.length} · ${money(items.reduce((a, i) => a + (i.cost || 0), 0))}</span></div>
        <ul class="items">${items.map((i) => {
          const id = `${s}:${i.id || i.name}`;
          const amount = i.qty_text || "";
          const qty = i.per_kg ? `${amount} at ${money(i.price)}/${i.unit === "ml" ? "L" : "kg"}`
            : `${i.packs ?? 1} × ${money(i.price)}${amount && i.unit !== "ea" ? ` · need ${amount}` : ""}`;
          const cook = (extras[i.id] || []).map(({ slug, e }) => `<div class="cook-note">Cook all of it: +${weight(e.grams_total)} ${esc(extraName(e))}${e.shared ? ` in ${esc(titles[slug] || titleCase(slug))}` : ""}, ${Math.round(e.grams_per_serve)} g more a serve</div>`).join("");
          const forWhat = (i.for || []).map((f) => esc(titles[f] || titleCase(f))).join(" + ");
          const sp = i.special ? `<span class="sp-tag">special</span>` : "";
          const was = i.special && i.usual_price ? ` · usually ${money(i.usual_price)}` : "";
          const fz = i.freeze_qty > 0 ? ` · <span class="fz-note">freeze ${leftover(i)}</span>` : "";
          return `<li class="item${ticks[id] ? " done" : ""}" data-a="tick" data-v="${esc(id)}">
            <span class="box">${ticks[id] ? "✓" : ""}</span>
            <div class="mid"><div class="nm">${esc(i.name)}${sp}</div><div class="sub">${qty}${was}${forWhat ? ` · ${forWhat}` : ""}${fz}</div>${cook}</div>
            <div class="pr">${money(i.cost)}</div></li>`;
        }).join("")}</ul>`;
    };
    let lists;
    const p = pair[store];
    if (!p) lists = `<div class="empty">No store can price these picks.</div>`;
    else if (store === "split") lists = `<div class="cols two">${splitStores(p).map((s) => `<div>${list(p.items[s], s)}</div>`).join("")}</div>`;
    else lists = list(p.items, store);
    const eff = (e) => e.effective ?? e.total;
    const singles = shops.filter((s) => pair[s]).map((s) => eff(pair[s]));
    const note = pair.split && singles.length
      ? `<p class="hint">Splitting between ${nameList(splitStores(pair.split))} saves ${money(Math.min(...singles) - eff(pair.split))} against the cheapest single store${pair.choice === "split" ? "." : "; it's only recommended at $10 or more."}</p>` : "";
    const names = this._picks().map((s) => esc(titles[s] || titleCase(s))).join(" + ");
    this._listStore = store;
    return `${this._banners()}<h2>${names}</h2><div class="totals n${choices.length}" style="--n:${choices.length}">${totals}</div>${note}${lists}${this._keepBlock(p, store, key)}
      ${Object.keys(ticks).length ? `<button class="linkbtn" data-a="untick">Clear ticks</button>` : ""}`;
  }

  // The store whose list is showing: the viewer's choice if still priced, else the recommendation.
  _store(pair) {
    const saved = lsGet(this._ls(`store:${this._week.week}:${this._pairKey()}`), null);
    let store = saved && pair[saved] ? saved : pair.choice;
    if (!store || !pair[store]) store = [...this._shops(), "split"].find((s) => pair[s]) || null;
    return store;
  }

  // List item id -> [{slug, e}] of the cooked extra in a pair store entry's per_serve.
  _extrasById(p) {
    const out = {};
    for (const [slug, ps] of Object.entries((p && p.per_serve) || {})) {
      for (const e of ps.extra || []) if (e.id) (out[e.id] = out[e.id] || []).push({ slug, e });
    }
    return out;
  }

  _keepBlock(p, store, key) {
    const fz = ((p && p.freeze) || []).filter((f) => f.grams > 0 && f.grams % 500 === 0);
    this._keepShown = fz.map((f) => ({ ...f, mark: `${key}:${store}:${f.cut}:${f.grams}` }));
    if (!fz.length) return "";
    const kept = lsGet(this._ls(`kept:${this._week.week}`), {}) || {};
    const rows = this._keepShown.map((f, i) => {
      const done = !!kept[f.mark], busy = this._busy.has(`keep:${f.mark}`);
      return `<div class="keep"><div class="t">${weight(f.grams)} ${esc(f.cut)}<div class="dim small">${esc(f.name)}</div></div>
        <button class="btn${done ? "" : " primary"}" data-a="fz-keep" data-v="${i}" ${done || busy ? "disabled" : ""}>${done ? "✓ Added" : "Add to freezer"}</button></div>`;
    }).join("");
    return `<div class="listhead"><h3>Freeze for later</h3><span class="dim small">${money(p.freeze_value)}</span></div>
      <div class="items">${rows}</div><p class="hint">Whole packs leave these over, in 500 g lots; anything less is cooked this week (see the Cook tab). Costs above count these as going in the freezer.</p>`;
  }

  _cookTab() {
    const picks = this._picks();
    if (!picks.length) {
      return `<div class="empty"><b>Nothing to cook yet</b>Pick ${NUM[this._need()]} meal${this._need() === 1 ? "" : "s"} on <a href="#" data-a="tab" data-v="week">This week</a>.</div>`;
    }
    const key = this._pairKey(), pair = key && ((this._week.pairs || {})[key]);
    const store = pair ? this._store(pair) : null;
    return picks.map((slug) => {
      const o = this._option(slug);
      if (!o) return `<div class="recipe"><h2>${esc(titleCase(slug))}</h2><p class="dim">Not in this week's file.</p></div>`;
      const t = o.table || {};
      const adj = store && pair[store] && (pair[store].per_serve || {})[slug];
      const ps = adj || t.per_serve || o.per_serve || {};
      const line = perServeLine(ps, t.serves || o.serves || 6, t.cut || o.cut);
      const extra = (adj && adj.extra) || [];
      const useAll = extra.map((e) => {
        const ing = (t.ingredients || []).find((it) => it.name === e.name && !it.removed);
        const recipe = ing ? (ing.base_qty ?? (ing.unit === "g" ? ing.amount : null)) : null;
        const more = `${weight(e.grams_total)} more than the recipe, ${Math.round(e.grams_per_serve)} g a serve`;
        const name = esc(extraName(e));
        if (e.bought_g && !e.shared && !e.frozen_g) return `<div class="useall">Use the whole ${weight(e.bought_g)} of ${name} (${more}).</div>`;
        const total = recipe != null ? `${weight(recipe + e.grams_total)} of ${name}` : `${weight(e.grams_total)} more ${name}`;
        const rest = e.frozen_g ? `; ${weight(e.frozen_g)} goes in the freezer` : e.shared ? "; the rest of the pack is in the other recipe" : "";
        return `<div class="useall">Use ${total} (${more})${rest}.</div>`;
      }).join("");
      const notes = (t.notes || []).length ? `<ul class="notes">${t.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
      return `<div class="recipe"><h2><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a></h2>
        <p class="dim small">Per serve: ${line}${t.recipe_serves ? ` · scaled from ${t.recipe_serves}` : ""}${adj && store ? ` · ${store === "split" && pair[store].stores ? `${nameList(pair[store].stores)} split` : STORES[store]} list` : ""}</p>${useAll}${notes}${this._table(t, extra)}</div>`;
    }).join("") + (picks.length < this._need() ? `<p class="hint">${picks.length} of ${this._need()} picked so far.</p>` : "");
  }

  _ingName(it) {
    let name = esc(it.name);
    if (it.original && !it.removed && it.original !== it.name) name += ` <span class="was">(${esc(it.original)})</span>`;
    if (it.prep && !it.removed) name += `<span class="prep">, ${esc(it.prep)}</span>`;
    if (it.removed) name = `<span class="gone">${esc(it.original || it.name)}</span> <span class="was">removed</span>`;
    if (it.note && !it.removed) name += `<span class="swapnote">${esc(it.note)}</span>`;
    return name;
  }

  _table(t, extra = []) {
    const ings = t.ingredients || [];
    let steps = t.steps || [];
    if (!steps.length) steps = [{ n: 1, text: "", ingredients: ings.map((_, i) => i) }];
    // The row an extra belongs to (first live ingredient of that name) shows the total actually cooked.
    const cooked = new Map();
    for (const e of extra) {
      const i = ings.findIndex((it) => it.name === e.name && !it.removed);
      const it = ings[i], base = it && (it.base_qty ?? (it.unit === "g" ? it.amount : null));
      if (i >= 0 && base != null) {
        const c = cooked.get(i) || { total: base, extra: 0 };
        cooked.set(i, { total: c.total + e.grams_total, extra: c.extra + e.grams_total });
      }
    }
    const name = (i) => this._ingName(ings[i]) + (cooked.has(i)
      ? `<span class="rec">recipe ${esc(ings[i].text || "")} + ${weight(cooked.get(i).extra)} from the whole packs</span>` : "");
    const amt = (it) => {
      if (it.removed) return `<span class="gone">${esc(it.text || "")}</span>`;
      const i = ings.indexOf(it);
      if (cooked.has(i)) return `<b>${weight(cooked.get(i).total)}</b>`;
      return esc(it.text || "");
    };
    const rows = steps.map((s) => {
      const idx = (s.ingredients || []).filter((i) => ings[i]);
      const list = idx.length ? idx : [null];
      return list.map((i, k) => {
        const cells = i == null ? `<td class="amt"></td><td class="ing"></td>` : `<td class="amt">${amt(ings[i])}</td><td class="ing">${name(i)}</td>`;
        const step = k === 0 ? `<td class="stp" rowspan="${list.length}"><span class="n">${s.n}.</span>${esc(s.text)}</td>` : "";
        return `<tr>${cells}${step}</tr>`;
      }).join("");
    }).join("");
    const narrow = steps.map((s) => {
      const idx = (s.ingredients || []).filter((i) => ings[i]);
      return `<div class="nstep"><div class="txt"><span class="n">${s.n}.</span>${esc(s.text)}</div>
        ${idx.map((i) => `<div class="row"><div class="amt">${amt(ings[i])}</div><div>${name(i)}</div></div>`).join("")}</div>`;
    }).join("");
    return `<table class="rt"><tbody>${rows}</tbody></table><div class="narrow">${narrow}</div>`;
  }

  _rateTab() {
    const st = this._state, ratings = st.ratings || {};
    const cur = this._week && this._week.week;
    const keys = Object.keys(st.picks || {}).filter((k) => !cur || k < cur).sort();
    const prevKey = cur ? (keys.includes(addDays(cur, -7)) ? addDays(cur, -7) : keys[keys.length - 1]) : keys[keys.length - 1];
    const prev = prevKey ? (st.picks[prevKey] || []) : [];
    const seg = (slug, r) => `<div class="seg">${RATINGS.map(([k, label]) =>
      `<button class="${r === k ? `on ${k}` : ""}" data-a="rate" data-v="${esc(slug)}" data-r="${k}" ${this._busy.has(`rate:${slug}`) ? "disabled" : ""}>${label}</button>`).join("")}</div>`;
    const lastWeek = prev.length ? `<div class="card">${prev.map((slug) => {
      const m = this._meta(slug), r = ratings[slug];
      return `<div class="rrow"><div class="who"><div class="t">${esc(m.title)}</div>
        <div class="d">${[m.style, m.cuisine].filter(Boolean).map(esc).join(" · ")}${r ? ` · rated ${fmtDay(r.date)}` : ""}</div></div>${seg(slug, r && r.rating)}</div>`;
    }).join("")}</div>` : `<p class="hint">Nothing picked last week to rate.</p>`;
    const rated = Object.entries(ratings).sort((a, b) => String(b[1].date || "").localeCompare(String(a[1].date || "")));
    const all = rated.length ? `<div class="card">${rated.map(([slug, r]) => `<div class="rrow"><div class="who">
        <div class="t">${esc(r.title || titleCase(slug))}</div>
        <div class="d">${[r.style, r.cuisine].filter(Boolean).map(esc).join(" · ")}${r.date ? ` · ${fmtDay(r.date)}` : ""}</div></div>
        ${seg(slug, r.rating)}<button class="x" data-a="rate" data-v="${esc(slug)}" data-r="clear" aria-label="Clear rating" title="Clear rating">×</button></div>`).join("")}</div>`
      : `<p class="hint">No ratings yet.</p>`;
    return `<h2>${prevKey ? `Week of ${fmtDay(prevKey)}` : "Last week"}</h2>${lastWeek}
      <p class="hint">Again brings a meal back sooner; never removes it. Tap the current rating to clear it.</p>
      <h3>Everything rated (${rated.length})</h3>${all}`;
  }

  _picker() {
    const v = this._viewer(), me = (this._people || {})[v.id];
    const ppl = this._planPeople();
    const hint = me && me.shared
      ? "Pick whose meal plan to show. This screen remembers it until you switch."
      : `${v.name ? `${esc(v.name)} doesn’t have a meal plan. ` : ""}Pick whose plan to show; this device remembers it.`;
    if (!ppl.length) {
      return `<div class="picker"><h2>No meal plans yet</h2><p class="hint">${this._people ? "Nobody has a plan set up."
        : "The list of people (people.json) hasn’t been published yet."}</p></div>`;
    }
    return `<div class="picker"><h2>Who’s cooking?</h2><p class="hint">${hint}</p><div class="people">${ppl.map(([k, p]) =>
      `<button class="person" data-a="choose" data-v="${esc(k)}">${esc(p.name || "Someone")}</button>`).join("")}</div></div>`;
  }

  // The person's planner settings (meals_settings). Applied from the next weekly plan.
  _settingsBlock() {
    const st = this._settings(), busy = this._busy.has("settings") ? "disabled" : "";
    const slider = (label, k, min, max, step, val, text) => `<div class="sl"><label>${label}</label>
      <input type="range" min="${min}" max="${max}" step="${step}" value="${val}" data-k="${k}" aria-label="${label}" ${busy}>
      <span class="val set">${text}</span></div>`;
    const seg = `<div class="seg">${[1, 2, 3].map((n) => `<button class="${+st.recipes === n ? "on" : ""}" data-a="recipes" data-v="${n}"
      aria-pressed="${+st.recipes === n}" ${busy}>${n}</button>`).join("")}</div>`;
    const swaps = SWAPS.map(([id, label]) => `<label class="check"><input type="checkbox" data-s="swap" data-v="${id}"
      ${st.swaps[id] !== false ? "checked" : ""} ${busy}><span>${label}</span></label>`).join("");
    const only = st.stores.length === 1;
    const shops = SHOPS.map((s) => {
      const on = st.stores.includes(s), lock = on && only;
      return `<label class="check${lock ? " off" : ""}"><input type="checkbox" data-s="shop" data-v="${s}" ${on ? "checked" : ""}
        ${lock || busy ? "disabled" : ""}><span>${STORES[s]}${lock ? ` <span class="d">(at least one)</span>` : ""}</span></label>`;
    }).join("");
    return `<div class="card set">
        <div class="setrow"><span>Recipes a week</span>${seg}</div>
        ${slider("Serves per recipe", "serves", 2, 8, 1, st.serves, this._sliderText("serves", st.serves))}
        ${slider("Protein a serve", "protein_g", 20, 80, 5, st.protein_g, this._sliderText("protein_g", st.protein_g))}
        <h4>Swaps</h4><div class="checks">${swaps}</div>
        <h4>Stores</h4><div class="checks">${shops}</div></div>
      <p class="hint">Changes apply from the next weekly plan (Wednesday); this week’s options stay as they are.</p>`;
  }

  _sliderText(k, v) {
    if (k === "serves") return `${v}`;
    if (k === "protein_g") return `${v} g`;
    if (k === "fade_days") return v === 0 ? "off" : `${v} d`;
    if (k === "similar_strength") return v.toFixed(2);
    return v === 0 ? "0" : `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
  }

  _tuneTab() {
    const tun = this._tuning();
    const weights = {};
    for (const [k, v] of Object.entries(tun.styles || {})) weights[k.toLowerCase()] = v;
    const names = new Map();
    const add = (s) => { if (s && !names.has(String(s).toLowerCase())) names.set(String(s).toLowerCase(), String(s)); };
    const ws = this._week && this._week.styles;
    (Array.isArray(ws) ? ws : Object.keys(ws || {})).forEach(add);
    ((this._week && this._week.options) || []).forEach((o) => { add(o.style); add(o.cuisine); });
    Object.keys(tun.styles || {}).forEach(add);
    const sorted = [...names.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const slider = (label, k, v, min, max, step, val, extra = "") => `<div class="sl"><label class="${k === "style" ? "cap" : ""}">${label}</label>
      <input type="range" class="${k === "style" ? `c${val !== 0 ? " set" : ""}` : ""}" min="${min}" max="${max}" step="${step}" value="${val}" data-k="${k}" ${extra}>
      <span class="val${val !== 0 ? " set" : ""}">${this._sliderText(k, val)}</span></div>`;
    const styleRows = sorted.map(([key, label]) => slider(esc(label), "style", key, -1, 1, 0.1, weights[key] ?? 0, `data-v="${esc(key)}" aria-label="${esc(label)} weight"`)).join("");
    const ss = tun.similar_strength ?? 0.3, fd = tun.fade_days ?? 365;
    const nRatings = Object.keys(this._state.ratings || {}).length, nWeights = Object.keys(tun.styles || {}).length;
    const reset = this._confirmReset
      ? `<div class="confirm"><span>Clear all ${nRatings} ratings and ${nWeights} style weights? This can't be undone.</span>
          <button class="btn danger" data-a="reset-yes">Reset</button><button class="btn" data-a="reset-no">Cancel</button></div>`
      : `<button class="btn" data-a="reset" ${this._busy.has("reset") ? "disabled" : ""}>Reset all ratings and weights</button>`;
    return `<h2>${possessive(this._personName())} settings</h2>${this._settingsBlock()}
      <h3>Ratings</h3><div class="card"><div class="sliders">
        ${slider("Nudge to similar", "similar_strength", null, 0, 1, 0.05, ss)}
        ${slider("Ratings fade over", "fade_days", null, 0, 730, 15, fd)}</div></div>
      <p class="hint">How strongly a rating nudges recipes of the same style or cuisine, and how long before a rating stops counting (0 = never fades).</p>
      <h3>Styles and cuisines</h3><p class="hint">Drag right for more, left for less. Saved when you let go; 0 removes the weight.</p>
      <div class="card"><div class="sliders">${styleRows || `<p class="dim">No styles yet.</p>`}</div></div>${reset}`;
  }

  _freezerTab() {
    const fz = this._state.freezer || [];
    const cuts = new Set();
    ((this._week && this._week.options) || []).forEach((o) => ((o.table && o.table.cuts) || [o.cut]).forEach((c) => c && cuts.add(c)));
    fz.forEach((f) => f.cut && cuts.add(f.cut));
    const f = this._form;
    const rows = fz.length ? `<div class="card">${fz.map((it, i) => `<div class="frow">
        <div><div class="t">${esc(it.cut)}</div><div class="d">${[it.seasoning, it.date ? `frozen ${fmtDay(it.date)}` : ""].filter(Boolean).map(esc).join(" · ")}</div></div>
        <span class="grams"><input type="number" inputmode="numeric" min="1" step="50" value="${esc(it.grams)}" data-a="fz-grams" data-v="${i}" aria-label="Grams" ${this._busy.has(`fz${i}`) ? "disabled" : ""}> g</span>
        <button class="x" data-a="fz-del" data-v="${i}" aria-label="Remove" title="Remove">×</button></div>`).join("")}</div>`
      : `<p class="hint">The freezer is empty.</p>`;
    const total = fz.reduce((a, it) => a + (+it.grams || 0), 0);
    const opts = [...cuts].sort().map((c) => `<option value="${esc(c)}" ${f.cut === c ? "selected" : ""}>${esc(c)}</option>`).join("");
    return `<h2>Freezer${fz.length ? ` <span class="dim small">${(total / 1000).toFixed(1)} kg</span>` : ""}</h2>${rows}
      <h3>Add meat</h3><div class="card"><div class="form">
        <label>Cut<select data-f="cut"><option value="" ${f.cut ? "" : "selected"} disabled>Choose…</option>${opts}
          <option value="__other" ${f.cut === "__other" ? "selected" : ""}>Other…</option></select></label>
        ${f.cut === "__other" ? `<label>Cut name<input data-f="other" value="${esc(f.other)}" placeholder="e.g. pork shoulder"></label>` : ""}
        <label>Grams<input data-f="grams" type="number" inputmode="numeric" min="1" step="50" value="${esc(f.grams)}" placeholder="1000"></label>
        <label>Seasoning<input data-f="seasoning" value="${esc(f.seasoning)}" placeholder="optional"></label>
        <button class="btn primary" data-a="fz-add" ${this._busy.has("fz-add") ? "disabled" : ""}>Add</button></div></div>
      <p class="hint">Recipes using what's in the freezer score higher, and that meat is left off the list.</p>`;
  }

  _footer() {
    const w = this._week;
    if (!w) return "";
    const um = w.unmatched || [];
    const prices = w.prices_week ? `prices week ${fmtDay(w.prices_week)}${w.stale ? " (stale)" : ""}` : "no prices";
    return `<footer>Generated ${fmtWhen(w.generated)} · ${prices}${this._state.updated ? ` · saved ${fmtWhen(this._state.updated)}` : ""}
      ${um.length ? `<details><summary>${um.length} unmatched ingredient${um.length === 1 ? "" : "s"}</summary>
        <ul>${um.map((u) => `<li>${esc(u)}</li>`).join("")}</ul>Add them to products.yaml to price them.</details>` : ""}</footer>`;
  }
}

customElements.define("meals-card", MealsCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "meals-card", name: "Meals", description: "Weekly meal picker: options, shopping list, recipes, ratings, freezer." });
