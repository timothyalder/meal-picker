# Meals card

`meals-card.js` is the Meals view in one plain-JS custom element: this week's options, the shopping list, the recipe tables, ratings, tuning and the freezer list. Design in [../PLAN.md](../PLAN.md), data shapes in [../meals/SCHEMA.md](../meals/SCHEMA.md).

**Per person** ([../meals/SCHEMA.md](../meals/SCHEMA.md), "Per person"). The card reads `/local/meals/people.json` (`{key: {name, shared, has_plan}}`, key = HA user id). If the logged-in user (`hass.user.id`) has a plan, it shows theirs. On the shared iPad login, or for a user not in `people.json`, it asks "Who's cooking?" with a button per person with a plan. The choice is remembered on that device (localStorage, `meals-card:person`), and a **Switch person** button in the header clears it. People on their own login see their own plan only, with no switch control (this isn't real privacy: `/local/` is readable on the LAN). Without a `people.json` the card falls back to the viewer's own id.

**Reads** `people.json`, the person's `week-<key>.json` and their entry in `state.json`'s `people` (config `base` changes the folder). It reloads them on load, on `meals_state_changed` (only when the event's `person` is the one on screen, or absent), when the page becomes visible again, and every 10 minutes. A missing week file shows "No plan yet: your first options arrive on Wednesday", followed by the person's settings. A missing state entry counts as empty, with default settings (TJ's). Shopping-list ticks, the chosen store and which leftovers were already added to the freezer live in the device's localStorage, keyed per person.

**Picks**: the number of picks is the size of the week file's `pairs` keys (1 to 3 slugs, sorted and joined with `+`). With no keys, it falls back to the person's `settings.recipes` (default 2). A changed setting therefore shows up with the next week file. The List tab looks up the sorted-slug key. "Cost per protein" shows the option's `cost_per_50g_protein` labelled with the person's `settings.protein_g` (e.g. "/ 40 g protein"), on the assumption that the planner computes that figure at the person's own target. Store columns are the stores the week file has keys for.

**Calls** (every call carries `person`, the key on screen):

- `pyscript.meals_pick {person, week, slugs}` (0 to `recipes` slugs)
- `pyscript.meals_rate {person, slug, rating, title, style, cuisine}` (rating `again`/`fine`/`never`/`clear`)
- `pyscript.meals_tune {person, style, weight|null}` / `{person, similar_strength}` / `{person, fade_days}` / `{person, reset: true}`
- `pyscript.meals_freezer {person, add: {cut, grams, seasoning}}` / `{person, remove: index}` / `{person, set: {index, grams}}`
- `pyscript.meals_settings {person, recipes}` (1–3) / `{person, serves}` (2–8) / `{person, protein_g}` (20–80, step 5) / `{person, swaps: {id: bool}}` (one id at a time: `cream`, `oil`, `butter`, `sugar`) / `{person, stores: [...]}` (the full list in Aldi, Woolworths, Coles order, never empty)

These are on the Tune tab under "<name>'s settings", with the note that changes apply from the next weekly plan. Each change shows at once and is reverted, with an inline error, if the call fails.

**Test** (from `home-assistant/`): `.venv/bin/python shopping/card/test/make_mocks.py` writes the mock files: `people.json` with TJ, Ted, Adam (no plan) and the shared iPad; TJ's and Ted's week files (Ted has 3 recipes, 4 serves, 40 g protein, Aldi and Coles); and a per-person `state.json`. Then `node shopping/card/test/harness.mjs [outdir] [filter]` screenshots into `shopping/card/test/out/`:

- every tab of TJ's plan at 390 and 1024 px, light and dark
- Ted's week, list, cook and tune
- the iPad picker, and the iPad after choosing Ted
- Ted with no week file
- a user who isn't in `people.json`
- the stale, Aldi-only, no-Coles, first-run and failed-call variants

The harness also checks for horizontal overflow and logs every service call, checking that its `person` is right (TJ on his login; Ted when the iPad picks him). It checks that an event for another person doesn't refetch, and that the iPad's choice survives a reload while Switch person clears it. It needs puppeteer-core (`npm i puppeteer-core` in `card/test/`, or the copy in `../dashboard/render/node_modules` if you have it) and a local Chrome (set `CHROME` to its path if it isn't in `/Applications`). The mocks carry their own copies of `qty_text`/`per_serve_with` from `plan_week.py`.

**Deploy** (by hand, from the Mac, whose key the SSH add-on accepts; SFTP is off, so pipe through `cat`):

```sh
ssh hassio@homeassistant.local 'cat > /config/www/meals/meals-card.js' < shopping/card/meals-card.js
ssh hassio@homeassistant.local 'sudo tee /config/pyscript/meals.py >/dev/null' < shopping/pyscript/meals.py   # deploy with the per-person card: the card needs people.json and the person field
.venv/bin/python ha.py '{"type":"call_service","domain":"pyscript","service":"reload","service_data":{}}'
```

After a new card file, bump `CARD_VERSION` in `../build_meals_view.py` and run it (`--dry-run` first) so phones fetch the new `?v=`. `dashboard/build_dashboard.py` keeps the Meals view when it rebuilds House and Energy. pyscript errors show in `.venv/bin/python ha.py '{"type":"system_log/list"}'`.
